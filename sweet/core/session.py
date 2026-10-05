"""Session: the shared surface through which people and agents work on data.

A `Session` wraps a `Workspace` with the things collaboration needs:

- a `Policy` (agent permission mode and column masks), applied to everything
  an agent reads
- shared attention: the human's selection, and the agent's highlights and
  annotations
- agent presence, narration, and demonstration playback with human take-over
- an event stream, so UIs and remote clients see each other's changes

It works headless (an agent working alone) or attached to the viewer, which
supplies a `ui` adapter for screen state and commands. The session server
(`sweet.session.server`) exposes these methods to other processes.

Every method takes `author`: "human" for the person at the keyboard, or
"agent:<name>" for agents. Permissions and masking depend on it.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import polars as pl

from .diff import CHANGED_PREFIX, OLD_PREFIX, TableDiff, diff_frames, old_column
from .policy import MODES, Policy, PolicyError, detect_pii, is_agent
from .steps import Step, StepError, duckdb_division, sql_expr
from .view import ROW_ID, TableView
from .alerts import Alert, AlertMonitor, rule_from_dict
from .stream import pump
from .workspace import Workspace

EventListener = Callable[[str, dict[str, Any]], None]


class SessionError(Exception):
    """A request couldn't be carried out."""


class ControlError(SessionError):
    """The person at the keyboard has taken control; agents must wait for them to resume."""


@dataclass
class Highlight:
    """Something an agent (or person) points at: a cell, a row, or a column."""

    id: str
    sheet: str
    kind: str  # "cell" | "row" | "column"
    row: int | None  # Row id (position in the sheet's data) for cell/row highlights
    column: str | None
    color: str = "yellow"
    note: str = ""
    author: str = "human"


@dataclass
class Selection:
    """The person's current selection: view rows [start, end) of some columns."""

    sheet: str
    start: int
    end: int
    columns: list[str]
    row_ids: list[int] = field(default_factory=list)


class Session:
    """See the module docstring."""

    def __init__(
        self,
        workspace: Workspace | None = None,
        *,
        policy: Policy | None = None,
        ui: Any = None,
        name: str = "sweet",
    ) -> None:
        self.workspace = workspace or Workspace()
        self.policy = policy or Policy()
        self.ui = ui
        self.name = name
        self.highlights: dict[str, Highlight] = {}
        self.selection: Selection | None = None
        self.agents: dict[str, dict[str, Any]] = {}
        self.narration: str = ""
        self.control = "shared"  # "human" once the person takes over a demo
        self.demo: dict[str, Any] | None = None  # {"mode": "step"|"continuous", "speed": float}
        self._listeners: list[EventListener] = []
        self._views: dict[str, TableView] = {}
        self._pii: dict[str, dict[str, str]] = {}
        self._advance = asyncio.Event()
        # Live data: alert monitors per sheet, recent alerts, and agents waiting in `watch`
        self.monitors: dict[str, AlertMonitor] = {}
        self.recent_alerts: list[Alert] = []
        self._alert_count = 0  # Alerts ever raised (recent_alerts keeps the last 200)
        self._alert_seen: dict[str, int] = {}  # author -> alerts already returned by watch
        self._watchers: list[asyncio.Queue] = []
        self._pumps: dict[str, asyncio.Task] = {}
        self.workspace.subscribe(self._on_workspace_event)

    # -- events -------------------------------------------------------------------

    def subscribe(self, listener: EventListener) -> Callable[[], None]:
        self._listeners.append(listener)
        return lambda: self._listeners.remove(listener) if listener in self._listeners else None

    def emit(self, event: str, **data: Any) -> None:
        for listener in list(self._listeners):
            try:
                listener(event, data)
            except Exception:
                pass

    def _on_workspace_event(self, event: str, details: dict[str, Any]) -> None:
        sheet = details.get("sheet")
        if event in ("step", "transform", "undo", "redo") and sheet:
            self._retaint(sheet)
            view = self._views.get(sheet)
            if view is not None:
                view.invalidate()
        self.emit(event, **details)

    # -- helpers ------------------------------------------------------------------

    def _sheet_name(self, sheet: str | None) -> str:
        name = sheet or self.workspace.current_sheet_name
        if name is None or name not in self.workspace._workbook.sheets:
            raise SessionError("No such sheet. Open data first (sheets lists what's open).")
        if self.workspace.refresh_live(name):
            self.view_for(name).invalidate()
        return name

    def view_for(self, sheet: str) -> TableView:
        """The TableView for a sheet (shared with the attached UI, if any)."""
        if self.ui is not None and hasattr(self.ui, "views") and sheet in self.ui.views:
            return self.ui.views[sheet]
        if sheet not in self._views:
            self._views[sheet] = TableView(self.workspace, sheet)
        return self._views[sheet]

    def pii(self, sheet: str) -> dict[str, str]:
        """Columns of `sheet` that look like personal data (cached)."""
        if sheet not in self._pii:
            base = self.workspace._workbook.sheets[sheet]
            frame = base.base if base.base is not None else base.lf
            try:
                self._pii[sheet] = detect_pii(frame)
            except Exception:
                self._pii[sheet] = {}
        return self._pii[sheet]

    def masked_columns(self, sheet: str) -> dict[str, str]:
        columns = self.view_for(sheet).columns
        return self.policy.masked_columns(columns, self.pii(sheet))

    def frame_for(self, sheet: str, author: str) -> pl.LazyFrame:
        """A sheet's data as `author` may see it (masked for agents)."""
        lf = self.workspace._workbook.sheets[sheet].lf
        if is_agent(author):
            lf = self.policy.mask_frame(lf, self.pii(sheet))
        return lf

    def _mask_rows(
        self, frame: pl.DataFrame, sheet: str, author: str, policy: Policy | None = None
    ) -> pl.DataFrame:
        if not is_agent(author):
            return frame
        policy = policy or self.policy
        masked = policy.masked_columns(
            [c for c in frame.columns if not c.startswith("__sweet_")], self.pii(sheet)
        )
        exprs = []
        for column, method in masked.items():
            exprs.append(policy._mask_expr(column, method))
            if old_column(column) in frame.columns:
                exprs.append(policy._mask_expr(old_column(column), method))
        return frame.with_columns(exprs) if exprs else frame

    def _retaint(self, sheet_name: str) -> None:
        """Extend masks to columns derived (by any step) from masked columns."""
        if not self.policy.active:
            return
        sheet = self.workspace._workbook.sheets.get(sheet_name)
        if sheet is None:
            return
        pii = self.pii(sheet_name)
        base = sheet.base if sheet.base is not None else None
        columns = (
            (base.collect_schema() if isinstance(base, pl.LazyFrame) else base.schema).names()
            if base is not None
            else None
        )
        for record in sheet.transform_steps:
            after = list(record.output_schema)
            step_dict = (record.metadata or {}).get("step")
            if columns is not None and step_dict:
                added = self.policy.propagate(Step.from_dict(step_dict), columns, after, pii)
                if added:
                    self.emit(
                        "policy", policy=self.policy.to_dict(), reason="derived", columns=added
                    )
            columns = after

    def _require(self, author: str, need: str) -> None:
        """Check `author` may change data ("propose" or "auto" level)."""
        if not is_agent(author):
            return
        mode = self.policy.mode
        if mode == "read-only":
            raise PolicyError(
                "The session is read-only for agents; ask the person to change the mode"
            )
        if need == "auto" and mode != "auto":
            raise PolicyError(
                "This needs 'auto' mode; in 'propose' mode, use propose_step and let the person decide"
            )

    # -- presence --------------------------------------------------------------------

    def connect(self, author: str, info: dict[str, Any] | None = None) -> dict[str, Any]:
        self.agents[author] = {"connected": True, **(info or {})}
        self._alert_seen.setdefault(author, self._alert_count)
        self.workspace.audit.append("connect", author=author, payload=info or {})
        self.emit("agent", author=author, connected=True)
        return self.status()

    def disconnect(self, author: str) -> None:
        if self.agents.pop(author, None) is not None:
            self.workspace.audit.append("disconnect", author=author)
            self.emit("agent", author=author, connected=False)

    def status(self, author: str = "human") -> dict[str, Any]:
        return {
            "session": self.name,
            "mode": self.policy.mode,
            "control": self.control,
            "demo": self.demo,
            "agents": list(self.agents),
            "sheets": self.workspace.sheet_names,
            "current_sheet": self.workspace.current_sheet_name,
            "masking": self.policy.to_dict(),
            "ui": self.ui is not None,
            "live": {name: table.status() for name, table in self.workspace._live.items()},
        }

    # -- reading ------------------------------------------------------------------

    def open(
        self,
        target: str,
        *,
        name: str | None = None,
        follow: bool = False,
        author: str = "human",
    ) -> dict[str, Any]:
        """Open data as a new sheet (allowed in every mode: it doesn't change existing data).

        With `follow` (or a ws:// URL), the sheet is live: new rows keep arriving.
        """
        from .stream import is_stream_target

        live = follow or is_stream_target(target)
        if self.ui is not None and hasattr(self.ui, "open_target"):
            if not self.ui.open_target(target, follow=live):
                raise SessionError(f"Couldn't open {target}")
        elif live:
            self.start_stream(target, name=name)
        else:
            try:
                self.workspace.read(target, name=name)
            except (FileNotFoundError, ValueError) as e:
                raise SessionError(str(e)) from e
        sheet = self.workspace.current_sheet_name
        self.workspace.audit.append("open", sheet=sheet, author=author, payload={"target": target})
        self.emit("open", sheet=sheet, author=author)
        return self.describe_sheet(sheet, author=author)

    # -- live data -------------------------------------------------------------------

    def start_stream(
        self, target: str, *, name: str | None = None, stdin: Any = None, **options: Any
    ) -> str:
        """Open a live sheet and start ingesting it on the running event loop. Returns its name."""
        try:
            table, source = self.workspace.read_stream(target, name=name, stdin=stdin, **options)
        except (FileNotFoundError, ValueError) as e:
            raise SessionError(str(e)) from e
        sheet = table.name
        self.monitors[sheet] = AlertMonitor(sheet, self._on_alert)
        table.subscribe(lambda batch, sheet=sheet: self._on_rows(sheet, batch))
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError as e:
            raise SessionError("Live sheets need a running event loop") from e
        self._pumps[sheet] = loop.create_task(pump(source, table))
        return sheet

    def stop_stream(self, sheet: str) -> None:
        task = self._pumps.pop(sheet, None)
        if task is not None:
            task.cancel()

    def _on_rows(self, sheet: str, batch: pl.DataFrame) -> None:
        monitor = self.monitors.get(sheet)
        if monitor is not None and monitor.rules:
            table = self.workspace.live_table(sheet)
            monitor.check(self._live_output(sheet, batch), table.snapshot() if table else None)
        self.emit("rows", sheet=sheet, count=batch.height)

    def _live_output(self, sheet: str, batch: pl.DataFrame) -> pl.DataFrame:
        """New rows after the sheet's steps, when those steps work row by row."""
        steps = [s for s in self.workspace.steps(sheet) if s.enabled]
        if not steps or any(s.stateful for s in steps):
            return batch
        try:
            frame = batch
            for step in steps:
                frame = step.apply(frame)
            return frame
        except StepError:
            return batch

    def _on_alert(self, alert: Alert) -> None:
        self.recent_alerts.append(alert)
        self.recent_alerts = self.recent_alerts[-200:]
        self._alert_count += 1
        self.workspace.audit.append(
            "alert",
            sheet=alert.sheet,
            author="system",
            payload={"rule": alert.rule, "message": alert.message},
        )
        self.emit("alert", alert=alert.to_dict())
        for queue in list(self._watchers):
            queue.put_nowait(alert)

    def _alert_for(self, alert: Alert, author: str) -> dict[str, Any]:
        data = alert.to_dict()
        data.pop("seqs", None)
        if data["samples"] and is_agent(author):
            masked = self._mask_rows(pl.DataFrame(data["samples"]), alert.sheet, author)
            data["samples"] = masked.to_dicts()
        return data

    def add_alert(
        self, rule: dict[str, Any], *, sheet: str | None = None, author: str = "human"
    ) -> dict[str, Any]:
        """Add a live check: ``{"sql": "temp < 60"}`` or ``{"column": "temp", "stat": "null_rate", "threshold": 0.1}``."""
        name = self._sheet_name(sheet)
        monitor = self.monitors.setdefault(name, AlertMonitor(name, self._on_alert))
        try:
            parsed = rule_from_dict(rule)
        except (TypeError, ValueError) as e:
            raise SessionError(str(e)) from e
        monitor.add(parsed)
        self.workspace.audit.append("alert_rule", sheet=name, author=author, payload=rule)
        self.emit("alert_rule", sheet=name, rule=parsed.name, author=author)
        return {"sheet": name, "rules": monitor.describe()}

    def alerts(
        self, *, sheet: str | None = None, limit: int = 20, author: str = "human"
    ) -> dict[str, Any]:
        """Rules and recent alerts (for one sheet, or all)."""
        recent = [a for a in self.recent_alerts if sheet is None or a.sheet == sheet][-limit:]
        rules = {name: m.describe() for name, m in self.monitors.items() if sheet in (None, name)}
        return {"rules": rules, "recent": [self._alert_for(a, author) for a in recent]}

    async def watch(
        self, *, sheet: str | None = None, timeout: float = 30.0, author: str = "human"
    ) -> dict[str, Any]:
        """Return alerts this author hasn't seen yet, or wait (up to `timeout` seconds)
        for the next one; otherwise return a status update."""
        # Agents' cursors start when they connect; anyone else starts from now
        unseen = self._alert_count - self._alert_seen.setdefault(author, self._alert_count)
        backlog = self.recent_alerts[-unseen:] if unseen > 0 else []
        backlog = [a for a in backlog if sheet is None or a.sheet == sheet]
        self._alert_seen[author] = self._alert_count
        if backlog:
            return {
                "alert": self._alert_for(backlog[0], author),
                "more": [self._alert_for(a, author) for a in backlog[1:]],
            }
        queue: asyncio.Queue = asyncio.Queue()
        self._watchers.append(queue)
        try:
            deadline = asyncio.get_running_loop().time() + max(0.1, min(float(timeout), 300.0))
            while True:
                remaining = deadline - asyncio.get_running_loop().time()
                if remaining <= 0:
                    break
                try:
                    alert = await asyncio.wait_for(queue.get(), remaining)
                except asyncio.TimeoutError:
                    break
                if sheet is None or alert.sheet == sheet:
                    self._alert_seen[author] = self._alert_count
                    return {"alert": self._alert_for(alert, author)}
        finally:
            self._watchers.remove(queue)
        live = {n: t.status() for n, t in self.workspace._live.items() if sheet in (None, n)}
        return {"alert": None, "note": f"No alerts in {timeout:g}s", "live": live}

    def describe_sheet(self, sheet: str, *, author: str = "human") -> dict[str, Any]:
        view = self.view_for(sheet)
        source = self.workspace._sources.get(sheet, {})
        masked = self.masked_columns(sheet) if is_agent(author) else {}
        return {
            "sheet": sheet,
            "rows": view.row_count(),
            "columns": {c: str(t) for c, t in view.schema.items() if not c.startswith("__sweet_")},
            "masked": masked,
            "pii": self.pii(sheet),
            "lazy": view.is_lazy,
            "steps": len(self.workspace.steps(sheet)),
            "source": source.get("path") or source.get("uri"),
        }

    def sheets(self, *, author: str = "human") -> list[dict[str, Any]]:
        return [self.describe_sheet(s, author=author) for s in self.workspace.sheet_names]

    def view(
        self,
        sheet: str | None = None,
        *,
        offset: int = 0,
        limit: int = 20,
        columns: list[str] | None = None,
        where: str | None = None,
        sort: list[str] | None = None,
        max_chars: int = 6000,
        author: str = "human",
    ) -> dict[str, Any]:
        """A window of rows, as data and as a token-budgeted Markdown table.

        Args:
            where: Optional SQL filter for this view only (not a step).
            sort: Optional column names to sort this view by ("-col" for descending).
            max_chars: Truncate the Markdown (by rows) to about this many characters.
        """
        name = self._sheet_name(sheet)
        lf = self.frame_for(name, author).with_row_index(ROW_ID)
        if where:
            lf = lf.filter(sql_expr(where))
        if sort:
            lf = lf.sort(
                [s.lstrip("-") for s in sort],
                descending=[s.startswith("-") for s in sort],
                nulls_last=True,
            )
        if columns:
            missing = [c for c in columns if c not in lf.collect_schema()]
            if missing:
                raise SessionError(f"Unknown column(s): {', '.join(missing)}")
            lf = lf.select([ROW_ID, *columns])
        limit = max(0, min(limit, 500))
        try:
            window = lf.slice(offset, limit).collect()
            total = (
                lf.select(pl.len()).collect(engine="streaming").item()
                if where
                else self.view_for(name).row_count()
            )
        except Exception as e:
            raise SessionError(f"Couldn't read rows: {e}") from e
        markdown, shown = _markdown(window, max_chars)
        return {
            "sheet": name,
            "offset": offset,
            "total_rows": total,
            "returned": shown,
            "columns": [c for c in window.columns if c != ROW_ID],
            "masked": self.masked_columns(name) if is_agent(author) else {},
            "markdown": markdown,
            "rows": window.head(shown).to_dicts(),
        }

    def profile(
        self, sheet: str | None = None, *, columns: list[str] | None = None, author: str = "human"
    ) -> dict[str, Any]:
        """Column statistics. Masked columns report counts only, never values."""
        name = self._sheet_name(sheet)
        stats = self.view_for(name).stats(columns)
        masked = self.masked_columns(name) if is_agent(author) else {}
        out = {}
        for column, st in stats.items():
            entry = st.to_dict()
            if column in masked:
                entry = {
                    k: entry[k]
                    for k in (
                        "name",
                        "dtype",
                        "kind",
                        "count",
                        "null_count",
                        "null_fraction",
                        "n_unique",
                    )
                }
                entry["masked"] = masked[column]
            out[column] = entry
        return {"sheet": name, "columns": out}

    def query(self, sql: str, *, limit: int = 50, author: str = "human") -> dict[str, Any]:
        """Run read-only SQL (Polars SQL) over the open sheets, each named by its sheet name.

        Agents query masked copies of the data, so results can't reveal masked values.
        """
        context = pl.SQLContext(
            {name: self.frame_for(name, author) for name in self.workspace.sheet_names}
        )
        try:
            result = (
                context.execute(duckdb_division(sql), eager=False)
                .head(max(1, min(limit, 1000)))
                .collect()
            )
        except Exception as e:
            raise SessionError(f"Query failed: {e}") from e
        markdown, shown = _markdown(result, 6000)
        return {
            "columns": result.columns,
            "returned": shown,
            "markdown": markdown,
            "rows": result.head(shown).to_dicts(),
        }

    def steps(self, sheet: str | None = None, *, author: str = "human") -> dict[str, Any]:
        name = self._sheet_name(sheet)
        return {
            "sheet": name,
            "steps": [
                {**s.to_dict(), "label": s.label, "author": s.author, "enabled": s.enabled}
                for s in self.workspace.steps(name)
            ],
            "proposals": [
                {"id": p.id, "step": p.step.to_dict(), "label": p.step.label, "author": p.author}
                for p in self.workspace.proposals
                if p.sheet == name
            ],
        }

    def diff(
        self,
        before: str,
        after: str,
        *,
        key: list[str] | None = None,
        sample: int = 5,
        author: str = "human",
    ) -> dict[str, Any]:
        """Diff two sheets (masked samples for agents)."""
        b, a = self._sheet_name(before), self._sheet_name(after)
        result = diff_frames(self.frame_for(b, author), self.frame_for(a, author), key=key)
        return result.to_dict(sample=sample)

    def preview(
        self, step: Step | dict[str, Any], *, sheet: str | None = None, author: str = "human"
    ) -> dict[str, Any]:
        """What a step would change (masked samples for agents), without applying it."""
        name = self._sheet_name(sheet)
        step = Step.from_dict(step) if isinstance(step, dict) else step
        try:
            result = self.workspace.preview_step(step, sheet_name=name)
        except (StepError, ValueError) as e:
            raise SessionError(str(e)) from e
        policy = None
        if is_agent(author) and self.policy.active:
            # Mask columns the step would derive from masked ones, before it's applied
            import copy

            policy = copy.deepcopy(self.policy)
            before = self.view_for(name).columns
            after = result.after.collect_schema().names() if result.after is not None else before
            policy.propagate(step, before, after, self.pii(name))
        return self._diff_summary(result, name, author, policy=policy)

    def _diff_summary(
        self,
        result: TableDiff,
        sheet: str,
        author: str,
        sample: int = 5,
        policy: Policy | None = None,
    ) -> dict[str, Any]:
        out = result.to_dict()
        if result.frame is not None and sample:
            rows = result.frame.filter(pl.col("__sweet_status") != "=").head(sample).collect()
            rows = self._mask_rows(rows, sheet, author, policy)
            out["samples"] = [
                {k: v for k, v in r.items() if not k.startswith(CHANGED_PREFIX) and v is not None}
                | {"status": r["__sweet_status"]}
                for r in rows.to_dicts()
            ]
            for entry in out["samples"]:
                for k in [k for k in entry if k.startswith(OLD_PREFIX)]:
                    entry["was:" + k[len(OLD_PREFIX) :]] = entry.pop(k)
                entry.pop("__sweet_status", None)
        return out

    # -- changing data --------------------------------------------------------------

    def propose_step(
        self, step: Step | dict[str, Any], *, sheet: str | None = None, author: str = "human"
    ) -> dict[str, Any]:
        """Add a step. Agents in 'propose' mode create a proposal; in 'auto' mode it applies."""
        name = self._sheet_name(sheet)
        step = Step.from_dict(step) if isinstance(step, dict) else step
        self._require(author, "propose")
        preview = self.preview(step, sheet=name, author=author)
        if is_agent(author) and self.policy.mode == "propose":
            proposal = self.workspace.propose(step, author=author, sheet_name=name)
            self.emit("proposal", sheet=name, proposal=proposal.id, label=step.label, author=author)
            return {"status": "proposed", "proposal": proposal.id, "preview": preview}
        self._apply(step, name, author)
        return {"status": "applied", "step": step.id, "preview": preview}

    def _apply(self, step: Step, sheet: str, author: str) -> None:
        previous = self.workspace.current_sheet_name
        self.workspace._workbook.set_current_sheet(sheet)
        try:
            self.workspace.apply_step(step, author=author)
        except (StepError, ValueError) as e:
            raise SessionError(str(e)) from e
        finally:
            if previous in self.workspace._workbook.sheets:
                self.workspace._workbook.set_current_sheet(previous)

    def decide(self, proposal_id: str, accept: bool, *, author: str = "human") -> dict[str, Any]:
        """Accept or reject a proposal. Agents can't decide on proposals (only people can)."""
        if is_agent(author):
            raise PolicyError("Only the person can accept or reject proposals")
        try:
            if accept:
                self.workspace.accept(proposal_id, author=author)
            else:
                self.workspace.reject(proposal_id, author=author)
        except ValueError as e:
            raise SessionError(str(e)) from e
        return {"status": "accepted" if accept else "rejected", "proposal": proposal_id}

    def step_action(
        self,
        action: str,
        step_id: str,
        *,
        sheet: str | None = None,
        index: int | None = None,
        step: dict[str, Any] | None = None,
        author: str = "human",
    ) -> dict[str, Any]:
        """toggle / remove / move (to `index`) / replace (with `step`) an existing step."""
        name = self._sheet_name(sheet)
        self._require(author, "auto")
        ws = self.workspace
        try:
            if action == "toggle":
                ws.toggle_step(step_id, author=author, sheet_name=name)
            elif action == "remove":
                ws.remove_step(step_id, author=author, sheet_name=name)
            elif action == "move":
                ws.move_step(step_id, int(index or 0), author=author, sheet_name=name)
            elif action == "replace":
                ws.replace_step(step_id, Step.from_dict(step or {}), author=author, sheet_name=name)
            else:
                raise SessionError(f"Unknown action '{action}' (toggle, remove, move, replace)")
        except (StepError, ValueError) as e:
            raise SessionError(str(e)) from e
        return self.steps(name, author=author)

    def undo(self, *, author: str = "human") -> dict[str, Any]:
        self._require(author, "auto")
        try:
            self.workspace.undo()
        except ValueError as e:
            raise SessionError(str(e)) from e
        return {"status": "undone"}

    def redo(self, *, author: str = "human") -> dict[str, Any]:
        self._require(author, "auto")
        try:
            self.workspace.redo()
        except ValueError as e:
            raise SessionError(str(e)) from e
        return {"status": "redone"}

    def export(
        self, path: str, *, what: str = "data", sheet: str | None = None, author: str = "human"
    ) -> dict[str, Any]:
        """Write data or the pipeline (as .sweet.yaml, polars, sql, dbt, or marimo).

        Agents can't export data while masks are active (the file would hold raw values).
        """
        name = self._sheet_name(sheet)
        if what == "data":
            if is_agent(author) and self.masked_columns(name):
                raise PolicyError(
                    "Data export is blocked while masks are active; export the pipeline instead"
                )
            from .io import write_file

            df = self.workspace._workbook.sheets[name].df
            write_file(df, path)
            result = {"path": path, "rows": df.height}
        else:
            pipeline = self.workspace.pipeline(name)
            text = {
                "pipeline": pipeline.to_yaml,
                "polars": pipeline.to_polars_script,
                "sql": pipeline.to_sql,
                "dbt": pipeline.to_dbt,
                "marimo": pipeline.to_marimo,
            }.get(what)
            if text is None:
                raise SessionError(
                    f"Unknown export '{what}' (data, pipeline, polars, sql, dbt, marimo)"
                )
            try:
                Path(path).write_text(text())
            except Exception as e:
                raise SessionError(str(e)) from e
            result = {"path": path, "steps": len(pipeline.active_steps)}
        self.workspace.audit.append(
            "export", sheet=name, author=author, payload={"what": what, "path": path}
        )
        self.emit("export", sheet=name, author=author, what=what, path=path)
        return result

    # -- policy -----------------------------------------------------------------------

    def set_policy(
        self,
        *,
        mask: str | None = None,
        method: str = "redact",
        unmask: str | None = None,
        mask_pii: bool | None = None,
        mode: str | None = None,
        author: str = "human",
    ) -> dict[str, Any]:
        """Change masks or the agent mode. Agents may only tighten the policy."""
        if mode is not None:
            self.policy.set_mode(mode, author=author)
        if mask is not None:
            self.policy.add_mask(mask, method, author=author)
        if unmask is not None:
            self.policy.remove_mask(unmask, author=author)
        if mask_pii is not None:
            self.policy.set_mask_pii(mask_pii, author=author)
        for sheet in self.workspace.sheet_names:
            self._retaint(sheet)
        self.workspace.audit.append("policy", author=author, payload=self.policy.to_dict())
        self.emit("policy", policy=self.policy.to_dict(), author=author)
        return self.policy.to_dict()

    # -- attention -----------------------------------------------------------------------

    def set_selection(self, selection: Selection | None) -> None:
        self.selection = selection
        self.emit("selection", selection=asdict(selection) if selection else None)

    def get_selection(self, *, author: str = "human") -> dict[str, Any]:
        """The person's selection with its values (masked for agents)."""
        sel = self.selection
        if sel is None:
            return {"selection": None}
        view = self.view_for(sel.sheet)
        window = view.fetch(sel.start, max(sel.end - sel.start, 1))
        columns = [c for c in sel.columns if c in window.columns]
        window = self._mask_rows(window.select([ROW_ID, *columns]), sel.sheet, author)
        markdown, shown = _markdown(window, 6000)
        return {
            "selection": {"sheet": sel.sheet, "rows": [sel.start, sel.end], "columns": columns},
            "markdown": markdown,
            "rows": window.head(shown).to_dicts(),
        }

    def highlight(
        self,
        *,
        sheet: str | None = None,
        row: int | None = None,
        column: str | None = None,
        color: str = "yellow",
        note: str = "",
        author: str = "human",
    ) -> dict[str, Any]:
        """Point at a row (by row id), a column, or a cell, optionally with a note."""
        name = self._sheet_name(sheet)
        if row is None and column is None:
            raise SessionError("Give a row, a column, or both")
        if column is not None and column not in self.view_for(name).columns:
            raise SessionError(f"Unknown column '{column}'")
        kind = (
            "cell"
            if row is not None and column is not None
            else ("row" if row is not None else "column")
        )
        highlight = Highlight(uuid.uuid4().hex[:8], name, kind, row, column, color, note, author)
        self.highlights[highlight.id] = highlight
        self.emit("highlight", highlight=asdict(highlight))
        return asdict(highlight)

    def clear_highlights(self, *, author: str = "human") -> dict[str, Any]:
        removed = [
            h for h in self.highlights.values() if not is_agent(author) or h.author == author
        ]
        for h in removed:
            del self.highlights[h.id]
        self.emit("highlight", cleared=[h.id for h in removed])
        return {"cleared": len(removed)}

    def narrate(self, text: str, *, author: str = "human") -> dict[str, Any]:
        self.narration = text
        self.emit("narrate", text=text, author=author)
        return {"ok": True}

    # -- interface control and demonstrations ---------------------------------------------

    def screen(self, *, author: str = "human") -> dict[str, Any]:
        """What's on screen, as data (requires an attached UI)."""
        if self.ui is None or not hasattr(self.ui, "screen_state"):
            return {"ui": False, "note": "No viewer is attached; use view for data."}
        state = self.ui.screen_state()
        if is_agent(author) and state.get("visible_window") is not None and state.get("sheet"):
            window = pl.DataFrame(state.pop("visible_window"))
            window = self._mask_rows(window, state["sheet"], author)
            state["visible_markdown"], _ = _markdown(window, 6000)
        else:
            state.pop("visible_window", None)
        state["highlights"] = [asdict(h) for h in self.highlights.values()]
        state["narration"] = self.narration
        state["control"] = self.control
        return state

    def command(self, command_id: str, *, author: str = "human") -> dict[str, Any]:
        """Run a registered viewer command by id (requires an attached UI)."""
        if self.ui is None or not hasattr(self.ui, "run_command"):
            raise SessionError("No viewer is attached")
        commands = getattr(self.ui, "command_ids", lambda: [])()
        if command_id not in commands:
            raise SessionError(f"Unknown command '{command_id}'. Commands: {', '.join(commands)}")
        if is_agent(author) and command_id in getattr(self.ui, "HUMAN_ONLY", ()):
            raise PolicyError(f"Only the person can run '{command_id}'")
        if is_agent(author) and command_id in getattr(self.ui, "DATA_COMMANDS", ()):
            self._require(author, "auto")
        self.ui.run_command(command_id)
        self.emit("command", command=command_id, author=author)
        return {"ok": True, "command": command_id}

    def start_demo(
        self, mode: str = "continuous", speed: float = 1.0, *, author: str = "human"
    ) -> dict[str, Any]:
        if mode not in ("continuous", "step"):
            raise SessionError("mode must be 'continuous' or 'step'")
        self.demo = {"mode": mode, "speed": max(0.25, min(float(speed), 4.0)), "author": author}
        self.control = "shared"
        self.emit("demo", demo=self.demo)
        return self.status()

    def end_demo(self, *, author: str = "human") -> dict[str, Any]:
        self.demo = None
        self.narration = ""
        self.emit("demo", demo=None)
        return self.status()

    def set_speed(self, speed: float) -> None:
        if self.demo is not None:
            self.demo["speed"] = max(0.25, min(speed, 4.0))
            self.emit("demo", demo=self.demo)

    def take_control(self) -> None:
        """The person pressed a key mid-demo: agents pause until `resume()`."""
        if self.demo is not None and self.control != "human":
            self.control = "human"
            self.workspace.audit.append("take_control", author="human")
            self.emit("control", control="human")
            self._advance.set()  # Wake any waiting agent call so it can see the change

    def resume(self) -> None:
        """Hand control back to agents (explicit, by the person)."""
        if self.control == "human":
            self.control = "shared"
            self.workspace.audit.append("resume", author="human")
            self.emit("control", control="shared")

    def advance(self) -> None:
        """Step-by-step demos: let the agent's next action through."""
        self._advance.set()

    async def gate(self, author: str) -> None:
        """Pace an agent's visible action according to the demo state.

        Raises:
            ControlError: If the person has taken control.
        """
        if not is_agent(author):
            return
        if self.control == "human":
            raise ControlError(
                "The person took control. Wait for them to resume (status shows control='shared')."
            )
        if self.demo is None:
            return
        if self.demo["mode"] == "step":
            self._advance.clear()
            await self._advance.wait()
        else:
            delay = 1.2 / self.demo["speed"]
            self._advance.clear()
            try:
                await asyncio.wait_for(self._advance.wait(), timeout=delay)
            except asyncio.TimeoutError:
                pass
        if self.control == "human":
            raise ControlError("The person took control. Wait for them to resume.")


def _markdown(frame: pl.DataFrame, max_chars: int) -> tuple[str, int]:
    """Render `frame` as a Markdown table within ~max_chars; returns (text, rows shown)."""
    cols = [c for c in frame.columns if not c.startswith("__sweet_") or c == ROW_ID]
    names = ["#" if c == ROW_ID else c for c in cols]
    lines = ["| " + " | ".join(names) + " |", "|" + "---|" * len(cols)]
    size = sum(len(line) + 1 for line in lines)
    shown = 0
    for row in frame.select(cols).iter_rows():
        cells = []
        for value in row:
            text = "∅" if value is None else str(value).replace("|", "\\|").replace("\n", " ")
            cells.append(text if len(text) <= 40 else text[:39] + "…")
        line = "| " + " | ".join(cells) + " |"
        if size + len(line) + 1 > max_chars and shown:
            lines.append(f"… ({frame.height - shown} more rows not shown; use offset)")
            break
        lines.append(line)
        size += len(line) + 1
        shown += 1
    return "\n".join(lines), shown


__all__ = [
    "MODES",
    "ControlError",
    "Highlight",
    "PolicyError",
    "Selection",
    "Session",
    "SessionError",
]
