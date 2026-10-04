"""ViewerApp: the Sweet data viewer.

Opens files, globs, directories, remote URLs, or stdin as sheets of a
`Workspace` and shows them in a virtualized `DataView`. Every change is a
journaled step; sorting is a view (it becomes a step when data is saved).
"""

from __future__ import annotations

import io
from pathlib import Path
from typing import Any

import polars as pl
from textual import work
from textual.app import App, ComposeResult
from textual.containers import Horizontal
from textual.widgets import Footer, Static, Tab, Tabs

from ...core.diff import STATUS
from ...core.io import write_file
from ...core.pipeline import PIPELINE_SUFFIX
from ...core.steps import Step, StepError, value_filter_step
from ...core.view import ROW_ID, TableView
from ...core.workspace import Workspace
from .commands import COMMANDS_BY_ID, SweetCommands, bindings
from .data_view import DataView, format_value, short_type
from .panels import Inspector, OverviewScreen, PromptScreen
from .steps_panel import ComposerScreen, StepsPanel


class ViewerApp(App):
    """Open anything; look, understand, and reshape it."""

    TITLE = "sweet"
    COMMANDS = App.COMMANDS | {SweetCommands}
    BINDINGS = bindings()

    CSS = """
    Screen { layout: vertical; }
    #topbar { height: 1; background: $panel; color: $text; padding: 0 1; }
    #topbar .muted { color: $text-muted; }
    Tabs { height: 2; }
    Tabs.hidden { display: none; }
    #main { height: 1fr; }
    #grid { width: 1fr; height: 1fr; }
    #status { height: 1; background: $panel; color: $text-muted; padding: 0 1; }
    #empty {
        width: 1fr; height: 1fr; content-align: center middle; color: $text-muted;
    }
    """

    def __init__(
        self,
        targets: list[str] | None = None,
        *,
        stdin_data: bytes | None = None,
        lazy: bool | None = None,
    ) -> None:
        super().__init__()
        self.targets = list(targets or [])
        self.stdin_data = stdin_data
        self.lazy = lazy
        self.workspace = Workspace()
        self.views: dict[str, TableView] = {}
        self.sheet: str | None = None
        self._tab_sheets: dict[str, str] = {}  # tab id -> sheet name
        # A pending change shown as a diff preview: (kind, payload, title), where
        # kind is "add" (a Step), "replace" ((step id, Step)), or "proposal" (id)
        self._pending: tuple[str, Any, str] | None = None

    # -- layout -------------------------------------------------------------------

    def compose(self) -> ComposeResult:
        yield Static("sweet", id="topbar")
        yield Tabs(id="sheets", classes="hidden")
        with Horizontal(id="main"):
            yield StepsPanel(id="steps", classes="hidden")
            yield DataView(id="grid")
            yield Static("Open a file with [b]Ctrl+O[/b], or run [b]sweet <path>[/b]", id="empty")
            yield Inspector(id="inspector", classes="hidden")
        yield Static("", id="status")
        yield Footer()

    @property
    def grid(self) -> DataView:
        return self.query_one("#grid", DataView)

    @property
    def inspector(self) -> Inspector:
        return self.query_one("#inspector", Inspector)

    @property
    def steps_panel(self) -> StepsPanel:
        return self.query_one("#steps", StepsPanel)

    @property
    def view(self) -> TableView | None:
        return self.views.get(self.sheet) if self.sheet else None

    def on_mount(self) -> None:
        for target in self.targets:
            self.open_target(target)
        self._update_empty_state()
        self.grid.focus()

    def _update_empty_state(self) -> None:
        has_data = bool(self.views)
        self.grid.display = has_data
        self.query_one("#empty").display = not has_data

    # -- sheets -------------------------------------------------------------------

    def open_target(self, target: str) -> bool:
        """Open `target` as a new sheet and show it. Returns False on failure."""
        stdin = io.BytesIO(self.stdin_data) if target == "-" and self.stdin_data else None
        try:
            self.workspace.read(target, lazy=self.lazy, stdin=stdin)
        except Exception as e:
            self.notify(f"Couldn't open {target}: {e}", severity="error", timeout=8)
            return False
        self._add_sheet_view(self.workspace.current_sheet_name)
        return True

    def _add_sheet_view(self, name: str) -> None:
        self.views[name] = TableView(self.workspace, name)
        tabs = self.query_one("#sheets", Tabs)
        tab_id = f"sheet-{len(self.views)}"
        self._tab_sheets[tab_id] = name
        tabs.add_tab(Tab(name, id=tab_id))
        tabs.set_class(len(self.views) < 2, "hidden")
        self.show_sheet(name)
        self._update_empty_state()

    def show_sheet(self, name: str) -> None:
        """Show sheet `name` and select its tab."""
        self._activate_sheet(name)
        # A just-added tab isn't mounted yet, so select it after the next refresh
        self.call_after_refresh(self._sync_tab, name)

    def _activate_sheet(self, name: str) -> None:
        self.sheet = name
        self.workspace._workbook.set_current_sheet(name)
        self.grid.set_view(self.views[name])
        self._data_changed(reset_cursor=False)

    def _sync_tab(self, name: str) -> None:
        tabs = self.query_one("#sheets", Tabs)
        tab_id = next((t for t, n in self._tab_sheets.items() if n == name), None)
        if tab_id is not None and tabs.active != tab_id:
            tabs.active = tab_id

    def on_tabs_tab_activated(self, event: Tabs.TabActivated) -> None:
        # Programmatic tab changes post events that can arrive late; only act on
        # the tab that is still active.
        if not self.is_running or event.tab is None or event.tab.id != event.tabs.active:
            return
        name = self._tab_sheets.get(event.tab.id)
        if name in self.views and name != self.sheet:
            self._activate_sheet(name)
            self.grid.focus()

    def action_sheet(self, delta: int) -> None:
        names = list(self.views)
        if len(names) > 1 and self.sheet in names:
            self.show_sheet(names[(names.index(self.sheet) + delta) % len(names)])

    # -- refresh after changes ----------------------------------------------------------

    def _data_changed(self, *, reset_cursor: bool = False) -> None:
        """Re-read the view after a data change and refresh counts and stats."""
        view = self.view
        if view is None:
            return
        self.grid.reload()
        if reset_cursor:
            self.grid.move_cursor(row=0)
        self._update_topbar()
        self._update_status()
        self._update_steps_panel()
        self._count_rows(view)
        self._compute_stats(view)

    @work(thread=True, exclusive=True, group="count", exit_on_error=False)
    def _count_rows(self, view: TableView) -> None:
        view.row_count()
        self.call_from_thread(self._after_background, view)

    @work(thread=True, exclusive=True, group="stats", exit_on_error=False)
    def _compute_stats(self, view: TableView) -> None:
        view.stats()
        self.call_from_thread(self._after_background, view)

    def _after_background(self, view: TableView) -> None:
        if view is not self.view:
            return
        self.grid._update_virtual_size()
        self.grid.refresh()
        self._update_topbar()
        self._update_status()
        self._update_inspector()

    def _update_topbar(self) -> None:
        view = self.view
        if view is None:
            return
        source = self.workspace._sources.get(view.sheet, {})
        where = source.get("path") or source.get("uri") or view.sheet
        rows = view.known_row_count
        shape = f"{rows:,}" if rows is not None else "counting…"
        mode = "lazy" if view.is_lazy else "in memory"
        steps = len(self.workspace._workbook.sheets[view.sheet].transform_steps)
        step_text = f" · {steps} step{'s' if steps != 1 else ''}" if steps else ""
        topbar = self.query_one("#topbar", Static)
        if self._pending is not None and view.diff is not None:
            topbar.update(
                f"[b reverse] PREVIEW [/] [b]{self._pending[2]}[/b]  {view.diff.summary()}"
                "   [b]a[/b] accept · [b]r[/b] reject"
            )
            return
        if view.at_step is not None:
            total = len(self.workspace.steps(view.sheet))
            topbar.update(
                f"[b reverse] AS OF STEP {view.at_step}/{total} [/] [b]{view.sheet}[/b]  "
                f"{shape} rows · read-only   [b]Esc[/b] back to live data"
            )
            return
        topbar.update(
            f"[b]{view.sheet}[/b]  {shape} rows × {len(self.grid.columns)} cols"
            f"  [dim]{mode}{step_text}   {_short_path(where)}[/dim]"
        )

    def _update_status(self) -> None:
        grid = self.grid
        column = grid.current_column
        if self.view is None or column is None:
            self.query_one("#status", Static).update("")
            return
        row = grid.current_row()
        value = "…" if row is None else format_value(row.get(column))
        if len(value) > 80:
            value = value[:79] + "…"
        dtype = short_type(self.view.schema[column])
        change = grid.cell_change()
        note = ""
        if change is not None:
            kind, old = change
            note = {
                "changed": f"   [b $warning]was[/] {format_value(old)}",
                "removed": "   [b $error]row removed[/]",
                "added": "   [b $success]row added[/]",
            }[kind]
        self.query_one("#status", Static).update(
            f"row {grid.cursor_row + 1:,} · [b]{column}[/b] [dim]{dtype}[/dim] = {value}{note}"
            "   [dim]Ctrl+P: commands[/dim]"
        )

    def _update_inspector(self) -> None:
        inspector = self.inspector
        if inspector.has_class("hidden") or self.view is None:
            return
        column = self.grid.current_column
        stats = self.view.cached_stats(column) if column else None
        dtype = str(self.view.schema[column]) if column else ""
        inspector.show(column, stats, dtype)

    def on_data_view_cursor_moved(self, event: DataView.CursorMoved) -> None:
        self._update_status()
        self._update_inspector()

    def on_data_view_cell_activated(self, event: DataView.CellActivated) -> None:
        self.action_edit_cell()

    # -- applying changes -------------------------------------------------------------

    def _require_live(self) -> bool:
        """True if the live data is shown; otherwise explain how to get back to it."""
        view = self.view
        if view is None:
            return False
        if self._pending is not None:
            self.notify("Accept (a) or reject (r) the preview first", severity="warning")
            return False
        if view.at_step is not None:
            self.notify("You're viewing an earlier step; press Esc to return", severity="warning")
            return False
        return True

    def apply_step(self, step: Step) -> bool:
        """Apply a step to the current sheet. Returns False (and notifies) on failure."""
        if not self._require_live():
            return False
        try:
            self.workspace.apply_step(step)
        except (StepError, ValueError) as e:
            self.notify(str(e), severity="error", timeout=8)
            return False
        self.view.invalidate()
        self._data_changed()
        return True

    def run_command(self, command_id: str) -> None:
        """Run a registered command by id (see `commands.COMMANDS`)."""
        self.call_later(self.run_action, COMMANDS_BY_ID[command_id].action)

    def goto_column(self, column: str) -> None:
        if column in self.grid.columns:
            self.grid.move_cursor(column=column)
            self.grid.focus()

    # -- actions ------------------------------------------------------------------

    def action_sort(self, descending: bool) -> None:
        column = self.grid.current_column
        if column is None or not self._require_live():
            return
        self.view.toggle_sort(column, descending)
        self.grid.reload()
        self.grid.move_cursor(row=0)

    def action_clear_sort(self) -> None:
        if self.view is not None and self.view.sort:
            self.view.set_sort([])
            self.grid.reload()

    def action_filter_value(self, exclude: bool) -> None:
        column = self.grid.current_column
        row = self.grid.current_row()
        if self.view is None or column is None or row is None:
            return
        self._filter_on(column, row.get(column), exclude=exclude)

    def _filter_on(self, column: str, value: Any, *, exclude: bool = False) -> None:
        try:
            step = value_filter_step(column, value, self.view.schema[column], exclude=exclude)
        except StepError as e:
            self.notify(str(e), severity="warning")
            return
        if self.apply_step(step):
            self.grid.move_cursor(row=0)

    def on_inspector_filter_requested(self, event: Inspector.FilterRequested) -> None:
        self._filter_on(event.column, event.value)

    def action_drop_column(self) -> None:
        column = self.grid.current_column
        if column is not None:
            self.apply_step(Step("drop", {"columns": [column]}))

    def action_edit_cell(self) -> None:
        column = self.grid.current_column
        row = self.grid.current_row()
        if column is None or row is None or not self._require_live():
            return
        row_id = row[ROW_ID]
        dtype = self.view.schema[column]
        current = row.get(column)

        def done(text: str | None) -> None:
            if text is None:
                return
            value: Any = text
            if text == "∅" or (text == "" and dtype.base_type().__name__ != "String"):
                value = None
            self.apply_step(
                Step(
                    "edit_cell",
                    {"row": int(row_id), "column": column, "value": value, "dtype": str(dtype)},
                )
            )

        shown = "" if current is None else str(current)
        self.push_screen(
            PromptScreen(
                f"Edit [b]{column}[/b] (row {self.grid.cursor_row + 1:,}) · ∅ for null", shown
            ),
            done,
        )

    def action_toggle_inspector(self) -> None:
        inspector = self.inspector
        inspector.toggle_class("hidden")
        self._update_inspector()

    def action_overview(self) -> None:
        view = self.view
        if view is None:
            return
        stats = {c: s for c in view.columns if (s := view.cached_stats(c)) is not None}
        dtypes = [short_type(view.schema[c]) for c in view.columns]
        rows = view.known_row_count
        title = f"{view.sheet} · {rows:,} rows" if rows is not None else view.sheet
        self.push_screen(
            OverviewScreen(f"{title} · {len(view.columns)} columns", view.columns, dtypes, stats),
            lambda column: column and self.goto_column(column),
        )

    def action_undo(self) -> None:
        self._history("undo")

    def action_redo(self) -> None:
        self._history("redo")

    def _history(self, which: str) -> None:
        if not self._require_live():
            return
        try:
            getattr(self.workspace, which)()
        except ValueError as e:
            self.notify(str(e), severity="warning")
            return
        sheet = self.workspace.history()[-1].sheet if which == "redo" else None
        for view in self.views.values():
            view.invalidate()
        if sheet and sheet in self.views and sheet != self.sheet:
            self.show_sheet(sheet)
        else:
            self._data_changed()

    def action_open_file(self) -> None:
        from ..file_browser import FileBrowserModal

        self.push_screen(FileBrowserModal(), lambda path: path and self.open_target(path))

    def action_save_data(self) -> None:
        view = self.view
        if not self._require_live():
            return
        source = self.workspace._sources.get(view.sheet, {}).get("path", "")
        suffix = Path(source).suffix if source and "*" not in source else ".parquet"
        default = f"{view.sheet}_sweet{suffix or '.parquet'}"
        self.push_screen(PromptScreen("Save data as", default), self._save_data_to)

    def _save_data_to(self, path: str | None) -> None:
        if not path or self.view is None:
            return
        view = self.view
        if view.sort:
            # Saved files keep the sort you see; record it as a step so the
            # pipeline reproduces the file.
            cols = [c for c, _ in view.sort]
            desc = [d for _, d in view.sort]
            self.workspace.apply_step(
                Step("sort", {"columns": cols, "descending": desc, "nulls_last": True})
            )
            view.set_sort([])
            view.invalidate()
            self._data_changed()
        try:
            write_file(self.workspace.df, path)
        except Exception as e:
            self.notify(f"Couldn't save {path}: {e}", severity="error", timeout=8)
            return
        self.notify(f"Saved {path}")

    def action_save_pipeline(self) -> None:
        view = self.view
        if view is None:
            return
        source = self.workspace._sources.get(view.sheet, {}).get("path")
        stem = Path(source).with_suffix("") if source and "*" not in source else Path(view.sheet)
        self.push_screen(
            PromptScreen("Save pipeline as", f"{stem}{PIPELINE_SUFFIX}"), self._save_pipeline_to
        )

    def _save_pipeline_to(self, path: str | None) -> None:
        if not path or self.view is None:
            return
        pipeline = self.workspace.pipeline(self.view.sheet)
        try:
            pipeline.save(path)
        except Exception as e:
            self.notify(f"Couldn't save pipeline: {e}", severity="error", timeout=8)
            return
        manual = sum(1 for s in pipeline.steps if s.kind == "manual")
        note = f" ({manual} manual edit(s) can't be replayed)" if manual else ""
        self.notify(f"Saved {len(pipeline.steps)} step(s) to {path}{note}")

    # -- steps, previews, and time travel ----------------------------------------------

    def _update_steps_panel(self) -> None:
        panel = self.steps_panel
        view = self.view
        if panel.has_class("hidden") or view is None:
            return
        source = self.workspace._sources.get(view.sheet, {})
        where = _short_path(source.get("path") or source.get("uri") or view.sheet, 34)
        proposals = [
            (p.id, p.step, p.author) for p in self.workspace.proposals if p.sheet == view.sheet
        ]
        panel.show(where, self.workspace.steps(view.sheet), proposals, at_step=view.at_step)

    def action_toggle_steps(self) -> None:
        panel = self.steps_panel
        panel.toggle_class("hidden")
        if panel.has_class("hidden"):
            self.grid.focus()
        else:
            self._update_steps_panel()
            panel.option_list.focus()

    def on_steps_panel_selected(self, event: StepsPanel.Selected) -> None:
        view = self.view
        if view is None or self._pending is not None:
            return
        total = len(self.workspace.steps(view.sheet))
        try:
            view.set_mode(at_step=None if event.index >= total else event.index)
        except (StepError, ValueError) as e:
            self.notify(str(e), severity="error", timeout=8)
            return
        self._data_changed()

    def on_steps_panel_action(self, event: StepsPanel.Action) -> None:
        view = self.view
        if view is None:
            return
        if event.kind == "refresh":
            self._update_steps_panel()
            return
        if event.kind == "preview_proposal":
            self._preview_proposal(str(event.target))
            return
        if event.kind == "branch":
            self._branch(int(event.target))
            return
        if self._pending is not None:
            self.notify("Accept (a) or reject (r) the preview first", severity="warning")
            return
        if event.kind == "edit":
            self._edit_step(str(event.target))
            return
        ws, step_id = self.workspace, str(event.target)
        try:
            if event.kind == "toggle":
                ws.toggle_step(step_id, sheet_name=view.sheet)
            elif event.kind == "remove":
                ws.remove_step(step_id, sheet_name=view.sheet)
            elif event.kind == "move":
                index = [s.id for s in ws.steps(view.sheet)].index(step_id)
                ws.move_step(step_id, index + int(event.value), sheet_name=view.sheet)
        except (StepError, ValueError) as e:
            self.notify(str(e), severity="error", timeout=8)
            return
        view.set_mode()
        self._data_changed()

    def action_new_step(self) -> None:
        if not self._require_live():
            return
        self.push_screen(ComposerScreen(self.grid.columns), self._preview_new_step)

    def _preview_new_step(self, step: Step | None) -> None:
        if step is None or self.view is None:
            return
        try:
            diff = self.workspace.preview_step(step, sheet_name=self.view.sheet)
        except (StepError, ValueError) as e:
            self.notify(str(e), severity="error", timeout=8)
            return
        self._start_preview(("add", step, step.label), diff)

    def _edit_step(self, step_id: str) -> None:
        view = self.view
        steps = self.workspace.steps(view.sheet)
        step = next((s for s in steps if s.id == step_id), None)
        if step is None:
            return

        def done(new_step: Step | None) -> None:
            if new_step is None:
                return
            edited = [new_step if s.id == step_id else s for s in self.workspace.steps(view.sheet)]
            try:
                diff = self.workspace.preview_steps(edited, sheet_name=view.sheet)
            except (StepError, ValueError) as e:
                self.notify(str(e), severity="error", timeout=8)
                return
            self._start_preview(("replace", (step_id, new_step), f"Edit: {new_step.label}"), diff)

        self.push_screen(ComposerScreen(self.grid.columns, step), done)

    def _preview_proposal(self, proposal_id: str) -> None:
        proposal = next((p for p in self.workspace.proposals if p.id == proposal_id), None)
        if proposal is None or self.view is None:
            return
        try:
            diff = self.workspace.preview_step(proposal.step, sheet_name=proposal.sheet)
        except (StepError, ValueError) as e:
            self.notify(str(e), severity="error", timeout=8)
            return
        title = f"{proposal.step.label} ({proposal.author})"
        self._start_preview(("proposal", proposal_id, title), diff)

    def _start_preview(self, pending: tuple[str, Any, str], diff) -> None:
        view = self.view
        self._pending = pending
        view.set_mode(diff=diff)
        self._data_changed(reset_cursor=True)

    def _end_preview(self) -> None:
        self._pending = None
        if self.view is not None:
            self.view.set_mode()
        self._data_changed()

    def action_accept(self) -> None:
        if self._pending is None:
            return
        kind, payload, _ = self._pending
        sheet = self.view.sheet
        try:
            if kind == "add":
                self.workspace.apply_step(payload)
            elif kind == "replace":
                step_id, step = payload
                self.workspace.replace_step(step_id, step, sheet_name=sheet)
            elif kind == "proposal":
                self.workspace.accept(payload)
        except (StepError, ValueError) as e:
            self.notify(str(e), severity="error", timeout=8)
            return
        self._end_preview()

    def action_next_change(self) -> None:
        """In a diff preview, jump to the next added, removed, or changed row."""
        view = self.view
        if view is None or not view.diff_rows:
            return
        positions = (
            view.frame()
            .with_row_index("__sweet_pos")
            .filter(pl.col(STATUS) != "=")
            .select("__sweet_pos")
        )
        current = self.grid.cursor_row
        after = positions.filter(pl.col("__sweet_pos") > current).head(1).collect()
        if after.height == 0:
            after = positions.head(1).collect()  # Wrap around
        if after.height == 0:
            self.notify("No changed rows", severity="information")
            return
        self.grid.move_cursor(row=int(after.item()))

    def action_reject(self) -> None:
        if self._pending is None:
            return
        if self._pending[0] == "proposal":
            self.workspace.reject(self._pending[1])
        self._end_preview()

    def action_escape(self) -> None:
        """Leave a preview (rejecting it) or time travel; otherwise focus the grid."""
        if self._pending is not None:
            self.action_reject()
        elif self.view is not None and self.view.at_step is not None:
            self.view.set_mode()
            self._data_changed()
        self.grid.focus()

    def _branch(self, index: int) -> None:
        view = self.view
        if view is None:
            return

        def done(name: str | None) -> None:
            if not name:
                return
            try:
                self.workspace.branch_at(name, index, sheet_name=view.sheet)
            except (StepError, ValueError) as e:
                self.notify(str(e), severity="error", timeout=8)
                return
            self._add_sheet_view(name)
            self.notify(f"Branched '{name}' after step {index}")

        self.push_screen(
            PromptScreen(f"Name for a branch after step {index}", f"{view.sheet}_branch"), done
        )

    # -- agent/test hooks -----------------------------------------------------------

    def screen_state(self) -> dict[str, Any]:
        """What's on screen, as data (the start of the agent-facing Screen IR)."""
        state = self.grid.state() if self.view is not None else {}
        state.update(
            {
                "sheet": self.sheet,
                "sheets": list(self.views),
                "inspector_open": not self.inspector.has_class("hidden"),
                "steps_panel_open": not self.steps_panel.has_class("hidden"),
                "mode": "preview"
                if self._pending is not None
                else (
                    "history" if self.view is not None and self.view.at_step is not None else "live"
                ),
                "preview": self.view.diff.to_dict()
                if self.view is not None and self.view.diff
                else None,
                "steps": [s.label for s in self.workspace.pipeline(self.sheet).steps]
                if self.sheet
                else [],
            }
        )
        return state


def _short_path(path: str, limit: int = 60) -> str:
    """A display form of a path: relative to cwd or ~ where possible, middle-truncated."""
    if "://" not in path:
        try:
            p = Path(path).expanduser().resolve()
            try:
                path = str(p.relative_to(Path.cwd()))
            except ValueError:
                home = Path.home()
                path = "~/" + str(p.relative_to(home)) if p.is_relative_to(home) else str(p)
        except (OSError, ValueError):
            pass
    if len(path) <= limit:
        return path
    keep = (limit - 1) // 2
    return path[:keep] + "…" + path[-(limit - keep - 1) :]


def run_viewer(
    targets: list[str], *, stdin_data: bytes | None = None, lazy: bool | None = None
) -> None:
    ViewerApp(targets, stdin_data=stdin_data, lazy=lazy).run()


__all__ = ["ViewerApp", "run_viewer"]
