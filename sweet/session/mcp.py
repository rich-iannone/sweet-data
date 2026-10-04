"""`sweet mcp`: a small MCP tool surface for working in a Sweet session.

The server either attaches to a running viewer (the person sees everything the
agent does, and agent changes arrive as proposals) or runs a headless session
of its own. Tools return compact text to keep agent context small.

    sweet mcp                 # attach to the most recent session, else headless
    sweet mcp --attach demo   # attach to the session named "demo"
    sweet mcp --headless      # never attach
    sweet mcp --launch data.csv   # open a viewer in a new tmux pane, then attach
    sweet mcp --labs          # also expose the older, larger tool set (headless only)
"""

from __future__ import annotations

import asyncio
import json
import os
import shlex
import shutil
import subprocess
import time
from typing import Any

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import TextContent, Tool

from ..core.policy import PolicyError
from ..core.session import ControlError, Session, SessionError
from .client import RemoteError, SessionClient
from .registry import find_session

STEP_HELP = (
    "A step is {kind, params}. Kinds: filter {sql|expr}, mutate {column, sql|expr}, "
    "cast {columns: {col: type}}, rename {mapping}, select {columns}, drop {columns}, "
    "sort {columns, descending}, edit_cell {row, column, value}, delete_rows {rows}, "
    "insert_row {values, index}, sql {query} (table name: df), polars {code}. Prefer `sql` "
    'expressions (e.g. {"kind": "filter", "params": {"sql": "amount > 0"}}): '
    "they export to both Polars and SQL."
)


def _obj(properties: dict[str, Any], required: list[str] | None = None) -> dict[str, Any]:
    return {"type": "object", "properties": properties, "required": required or []}


S, INT, B = {"type": "string"}, {"type": "integer"}, {"type": "boolean"}
SHEET = {"type": "string", "description": "Sheet name (default: the current sheet)"}

TOOLS: list[Tool] = [
    Tool(
        name="status",
        description="Session status: agent mode (read-only/propose/auto), who has control, demo state, open sheets, and masks. Check this first.",
        inputSchema=_obj({}),
    ),
    Tool(
        name="open",
        description="Open a file, glob, directory, or URL as a new sheet (Parquet, CSV, JSON, Excel, s3://, hf://, ...).",
        inputSchema=_obj({"target": S, "name": S}, ["target"]),
    ),
    Tool(
        name="sheets",
        description="List open sheets with row counts, column types, masked columns, detected PII, and step counts.",
        inputSchema=_obj({}),
    ),
    Tool(
        name="view",
        description="Read a window of rows as a Markdown table. Use where (SQL) and sort (column names, '-col' for descending) to look around without changing data.",
        inputSchema=_obj(
            {
                "sheet": SHEET,
                "offset": INT,
                "limit": INT,
                "columns": {"type": "array", "items": S},
                "where": S,
                "sort": {"type": "array", "items": S},
            }
        ),
    ),
    Tool(
        name="profile",
        description="Column statistics: type, nulls, distinct values, min/max/mean/quantiles, top values. Masked columns show counts only.",
        inputSchema=_obj({"sheet": SHEET, "columns": {"type": "array", "items": S}}),
    ),
    Tool(
        name="query",
        description="Read-only SQL over open sheets (each table is named after its sheet). Results are capped; nothing changes.",
        inputSchema=_obj({"sql": S, "limit": INT}, ["sql"]),
    ),
    Tool(
        name="propose_step",
        description="Change data by adding a step. In 'propose' mode the person sees a diff and accepts or rejects it; in 'auto' mode it applies. Returns a preview of the effect. "
        + STEP_HELP,
        inputSchema=_obj({"step": {"type": "object"}, "sheet": SHEET}, ["step"]),
    ),
    Tool(
        name="steps",
        description="List a sheet's steps and pending proposals. With action (toggle/remove/move/replace) and step_id, change an existing step ('auto' mode only).",
        inputSchema=_obj(
            {"sheet": SHEET, "action": S, "step_id": S, "index": INT, "step": {"type": "object"}}
        ),
    ),
    Tool(name="undo", description="Undo the last change ('auto' mode only).", inputSchema=_obj({})),
    Tool(
        name="redo",
        description="Redo the last undone change ('auto' mode only).",
        inputSchema=_obj({}),
    ),
    Tool(
        name="diff",
        description="Compare two sheets: schema changes, added/removed/changed rows (by key columns), changed cells per column.",
        inputSchema=_obj(
            {"before": S, "after": S, "key": {"type": "array", "items": S}}, ["before", "after"]
        ),
    ),
    Tool(
        name="get_selection",
        description="What the person has selected in the viewer (rows and columns, with values).",
        inputSchema=_obj({}),
    ),
    Tool(
        name="highlight",
        description="Point at a row (row id from view's # column), a column, or a cell in the viewer, with an optional note. clear=true removes your highlights.",
        inputSchema=_obj(
            {"sheet": SHEET, "row": INT, "column": S, "note": S, "color": S, "clear": B}
        ),
    ),
    Tool(
        name="export",
        description="Write the pipeline (what: pipeline, polars, sql, dbt, marimo) or the data (what: data; blocked while masks are active).",
        inputSchema=_obj({"path": S, "what": S, "sheet": SHEET}, ["path"]),
    ),
    Tool(
        name="screen",
        description="What's on the person's screen: sheet, visible rows, cursor, sort, open panels, preview, steps.",
        inputSchema=_obj({}),
    ),
    Tool(
        name="command",
        description="Run a viewer command by id (e.g. view.sort_ascending, panel.inspector, panel.steps, panel.overview, filter.equal). screen shows the cursor it acts on.",
        inputSchema=_obj({"id": S}, ["id"]),
    ),
    Tool(
        name="set_policy",
        description="Tighten governance: mask a column (method: redact/hash/partial/null), turn on mask_pii, or lower the agent mode. Agents can't loosen policy.",
        inputSchema=_obj({"mask": S, "method": S, "mask_pii": B, "mode": S}),
    ),
    Tool(
        name="demo",
        description="Demonstrate in the viewer: action start (mode continuous|step, speed 0.25-4), narrate (text shown to the person), or end.",
        inputSchema=_obj(
            {"action": S, "mode": S, "speed": {"type": "number"}, "text": S}, ["action"]
        ),
    ),
    Tool(
        name="launch_session",
        description="Open a viewer in a new terminal pane (tmux) on the given targets and attach to it, so the person can watch.",
        inputSchema=_obj({"targets": {"type": "array", "items": S}}),
    ),
]

TOOL_METHODS = {
    "status": "status",
    "open": "open",
    "sheets": "sheets",
    "view": "view",
    "profile": "profile",
    "query": "query",
    "propose_step": "propose_step",
    "undo": "undo",
    "redo": "redo",
    "diff": "diff",
    "get_selection": "get_selection",
    "export": "export",
    "screen": "screen",
    "command": "command",
    "set_policy": "set_policy",
}


class LocalBackend:
    """A headless session in this process."""

    def __init__(self, session: Session, author: str) -> None:
        self.session = session
        self.author = author
        self.attached = False
        session.connect(author, {"client": author.removeprefix("agent:"), "headless": True})

    async def call(self, method: str, **params: Any) -> Any:
        if method in (
            "open",
            "propose_step",
            "highlight",
            "narrate",
            "command",
            "undo",
            "redo",
            "step_action",
        ):
            await self.session.gate(self.author)
        return getattr(self.session, method)(**params, author=self.author)


class RemoteBackend:
    """A running viewer's session, over its socket."""

    def __init__(self, client: SessionClient) -> None:
        self.client = client
        self.attached = True

    async def call(self, method: str, **params: Any) -> Any:
        return await self.client.call(method, **params)


class SweetMCP:
    def __init__(self, backend: Any, *, labs: bool = False, author: str = "agent:mcp") -> None:
        self.backend = backend
        self.labs = labs and isinstance(backend, LocalBackend)
        self.author = author
        self.server = Server("sweet")
        self.server.list_tools()(self.list_tools)
        self.server.call_tool()(self.call_tool)
        if self.labs:
            from .. import mcp as legacy

            legacy._workspace = backend.session.workspace
            self._legacy = legacy

    async def list_tools(self) -> list[Tool]:
        tools = list(TOOLS)
        if self.labs:
            tools += await self._legacy.list_tools()
        return tools

    async def call_tool(self, name: str, arguments: dict[str, Any] | None) -> list[TextContent]:
        args = dict(arguments or {})
        if self.labs and name.startswith("sweet_"):
            return await self._legacy.call_tool(name, args)
        try:
            text = await self._run(name, args)
        except (
            PolicyError,
            ControlError,
            SessionError,
            RemoteError,
            ConnectionError,
            ValueError,
        ) as e:
            raise RuntimeError(str(e)) from e
        return [TextContent(type="text", text=text)]

    async def _run(self, name: str, args: dict[str, Any]) -> str:
        call = self.backend.call
        if name == "launch_session":
            return await self._launch(args.get("targets") or [])
        if name == "steps":
            action = args.pop("action", None)
            if action:
                result = await call("step_action", action=action, **args)
            else:
                result = await call("steps", sheet=args.get("sheet"))
            return _format_steps(result)
        if name == "highlight":
            if args.pop("clear", False):
                return _json(await call("clear_highlights"))
            return _json(await call("highlight", **args))
        if name == "demo":
            action = args.get("action")
            if action == "start":
                return _json(
                    await call(
                        "start_demo",
                        mode=args.get("mode", "continuous"),
                        speed=args.get("speed", 1.0),
                    )
                )
            if action == "narrate":
                return _json(await call("narrate", text=args.get("text", "")))
            if action == "end":
                return _json(await call("end_demo"))
            raise ValueError("demo action must be start, narrate, or end")
        if name == "command":
            return _json(await call("command", command_id=args["id"]))
        if name not in TOOL_METHODS:
            raise ValueError(f"Unknown tool '{name}'")
        result = await call(TOOL_METHODS[name], **args)
        if name == "view":
            return _format_view(result)
        if name == "query":
            return result["markdown"]
        if name == "profile":
            return _format_profile(result)
        if name == "propose_step":
            return _format_proposal(result)
        if name == "get_selection":
            return result.get("markdown") or "Nothing is selected."
        if name == "screen":
            markdown = result.pop("visible_markdown", None)
            return _json(result) + (f"\n\nVisible rows:\n{markdown}" if markdown else "")
        return _json(result)

    async def _launch(self, targets: list[str]) -> str:
        name = f"agent-{int(time.time()) % 100000}"
        command = shlex.join(["sweet", "--session", name, *targets])
        if os.environ.get("TMUX") and shutil.which("tmux"):
            subprocess.run(["tmux", "split-window", "-h", command], check=True)
        else:
            raise ValueError(
                "Can't open a terminal pane automatically (not inside tmux). Ask the person to run: "
                + command
            )
        for _ in range(100):
            if find_session(name) is not None:
                client = await SessionClient.connect(
                    name, client=self.author.removeprefix("agent:")
                )
                self.backend = RemoteBackend(client)
                return f"Launched and attached to session '{name}'. The person can watch and approve changes there."
            await asyncio.sleep(0.1)
        raise ValueError(f"The viewer didn't start in time; ask the person to run: {command}")


def _json(obj: Any) -> str:
    return json.dumps(obj, default=str, separators=(",", ":"))


def _format_view(r: dict[str, Any]) -> str:
    end = r["offset"] + r["returned"]
    masked = f" · masked: {', '.join(r['masked'])}" if r.get("masked") else ""
    return f"{r['sheet']}: rows {r['offset']}–{end} of {r['total_rows']:,}{masked}\n{r['markdown']}"


def _format_profile(r: dict[str, Any]) -> str:
    lines = [f"{r['sheet']}:"]
    for name, st in r["columns"].items():
        parts = [f"{st['dtype']}", f"nulls {st['null_fraction']:.1%}"]
        if st.get("n_unique") is not None:
            parts.append(f"~{st['n_unique']:,} distinct")
        if st.get("masked"):
            parts.append(f"MASKED ({st['masked']})")
        else:
            if st.get("min") is not None:
                parts.append(f"range {st['min']} … {st['max']}")
            if st.get("mean") is not None:
                parts.append(f"mean {st['mean']:.6g}")
            if st.get("quantiles"):
                parts.append("q25/50/75 " + "/".join(str(v) for v in st["quantiles"].values()))
            if st.get("top_values"):
                parts.append("top " + ", ".join(f"{v}({c})" for v, c in st["top_values"][:5]))
        lines.append(f"- {name}: " + "; ".join(parts))
    return "\n".join(lines)


def _format_steps(r: dict[str, Any]) -> str:
    lines = [f"{r['sheet']} steps:"]
    for n, s in enumerate(r["steps"], 1):
        mark = "" if s.get("enabled", True) else " (disabled)"
        lines.append(f"{n}. [{s['id']}] {s['label']}{mark} — {s['author']}")
    for p in r.get("proposals", []):
        lines.append(f"proposed [{p['id']}] {p['label']} — {p['author']} (waiting for the person)")
    return "\n".join(lines) if len(lines) > 1 else f"{r['sheet']}: no steps yet"


def _format_proposal(r: dict[str, Any]) -> str:
    preview = r.get("preview", {})
    head = (
        f"Proposed (id {r['proposal']}); the person will accept or reject it."
        if r["status"] == "proposed"
        else f"Applied (step {r['step']})."
    )
    text = f"{head}\nEffect: {preview.get('summary', '')}"
    if preview.get("samples"):
        text += "\nExamples: " + _json(preview["samples"])
    return text


async def serve(
    *,
    attach: str | None = None,
    headless: bool = False,
    launch: list[str] | None = None,
    labs: bool = False,
    client_name: str = "mcp",
) -> None:
    """Run the MCP server over stdio."""
    author = f"agent:{client_name}"
    backend: Any
    info = None if headless else find_session(attach)
    if attach and info is None:
        raise SystemExit(f"No running Sweet session named '{attach}'")
    if info is not None:
        backend = RemoteBackend(await SessionClient.connect(info=info, client=client_name))
    else:
        backend = LocalBackend(Session(), author)
    app = SweetMCP(backend, labs=labs, author=author)
    if launch is not None:
        await app._launch(launch)
    async with stdio_server() as (read_stream, write_stream):
        await app.server.run(read_stream, write_stream, app.server.create_initialization_options())
