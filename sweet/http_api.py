"""HTTP REST API for Sweet — exposes the Workspace engine over HTTP.

Provides the same operations as the MCP server but via standard REST endpoints,
enabling non-MCP clients (web UIs, curl, webhooks, other languages) to drive Sweet.

Usage:
    sweet serve --http --port 8421
"""

from __future__ import annotations

import json

from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.middleware.cors import CORSMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from .core.workspace import Workspace

# ---------------------------------------------------------------------------
# Workspace singleton
# ---------------------------------------------------------------------------

_workspace: Workspace | None = None


def _get_workspace() -> Workspace:
    global _workspace
    if _workspace is None:
        _workspace = Workspace()
    return _workspace


def reset_workspace() -> None:
    """Reset the workspace (for testing)."""
    global _workspace
    _workspace = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _error(message: str, status: int = 400) -> JSONResponse:
    return JSONResponse({"error": message}, status_code=status)


async def _get_body(request: Request) -> dict:
    """Parse JSON body, returning empty dict if no body."""
    body = await request.body()
    if not body:
        return {}
    return json.loads(body)


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


async def health(request: Request) -> JSONResponse:
    """Health check."""
    ws = _get_workspace()
    return JSONResponse({
        "status": "ok",
        "active_sheet": ws._workbook.current_sheet_name,
        "sheets": ws.sheet_names,
    })


async def load(request: Request) -> JSONResponse:
    """Load a file into the workspace.

    Body: {"path": "file.csv", "name": "optional_sheet_name"}
    """
    ws = _get_workspace()
    data = await _get_body(request)

    path = data.get("path")
    if not path:
        return _error("'path' is required")

    try:
        ws.load(path, name=data.get("name"))
        info = ws.inspect()
        return JSONResponse({
            "loaded": path,
            "shape": info["shape"],
            "columns": list(info["schema"].keys()),
        })
    except Exception as e:
        return _error(f"{type(e).__name__}: {e}")


async def inspect(request: Request) -> JSONResponse:
    """Inspect the active sheet — schema, shape, sample rows."""
    ws = _get_workspace()
    try:
        info = ws.inspect()
        return JSONResponse(info)
    except Exception as e:
        return _error(f"{type(e).__name__}: {e}")


async def transform(request: Request) -> JSONResponse:
    """Apply a Polars expression.

    Body: {"expr": "df.filter(pl.col('x') > 5)"}
    """
    ws = _get_workspace()
    data = await _get_body(request)

    expr = data.get("expr")
    if not expr:
        return _error("'expr' is required")

    try:
        ws.transform(expr)
        info = ws.inspect()
        return JSONResponse({"transformed": True, "shape": info["shape"]})
    except Exception as e:
        return _error(f"{type(e).__name__}: {e}")


async def query(request: Request) -> JSONResponse:
    """Run SQL via DuckDB.

    Body: {"sql": "SELECT * FROM active LIMIT 10"}
    """
    ws = _get_workspace()
    data = await _get_body(request)

    sql = data.get("sql")
    if not sql:
        return _error("'sql' is required")

    try:
        ws.query(sql)
        info = ws.inspect()
        return JSONResponse({"queried": True, "shape": info["shape"]})
    except Exception as e:
        return _error(f"{type(e).__name__}: {e}")


async def filter_rows(request: Request) -> JSONResponse:
    """Filter rows by condition.

    Body: {"condition": "pl.col('age') > 30"}
    """
    ws = _get_workspace()
    data = await _get_body(request)

    condition = data.get("condition")
    if not condition:
        return _error("'condition' is required")

    try:
        ws.filter(condition)
        info = ws.inspect()
        return JSONResponse({"filtered": True, "shape": info["shape"]})
    except Exception as e:
        return _error(f"{type(e).__name__}: {e}")


async def sort_rows(request: Request) -> JSONResponse:
    """Sort by column(s).

    Body: {"by": "column_name", "descending": false}
    """
    ws = _get_workspace()
    data = await _get_body(request)

    by = data.get("by")
    if not by:
        return _error("'by' is required")

    try:
        ws.sort(by, descending=data.get("descending", False))
        info = ws.inspect()
        return JSONResponse({"sorted": True, "shape": info["shape"]})
    except Exception as e:
        return _error(f"{type(e).__name__}: {e}")


async def select_columns(request: Request) -> JSONResponse:
    """Select specific columns.

    Body: {"columns": ["col1", "col2"]}
    """
    ws = _get_workspace()
    data = await _get_body(request)

    columns = data.get("columns")
    if not columns:
        return _error("'columns' is required")

    try:
        ws.select(*columns)
        info = ws.inspect()
        return JSONResponse({"selected": True, "shape": info["shape"], "columns": list(info["schema"].keys())})
    except Exception as e:
        return _error(f"{type(e).__name__}: {e}")


async def sheets(request: Request) -> JSONResponse:
    """List all sheets in the workspace."""
    ws = _get_workspace()
    current = ws._workbook.current_sheet_name
    if not ws.sheet_names:
        return JSONResponse({"sheets": [], "active": None})
    try:
        sheets_info = []
        for name in ws.sheet_names:
            ws.switch(name)
            sheets_info.append({
                "name": name,
                "shape": ws.shape,
                "columns": list(ws.df.columns) if ws.df is not None else [],
            })
        if current:
            ws.switch(current)
        return JSONResponse({"sheets": sheets_info, "active": current})
    except Exception as e:
        return _error(f"{type(e).__name__}: {e}")


async def sample(request: Request) -> JSONResponse:
    """Get a sample of rows.

    Query params: ?n=10
    """
    ws = _get_workspace()
    n = int(request.query_params.get("n", "10"))

    try:
        df = ws.sample(n)
        if df is None:
            return _error("No data loaded", status=404)
        return JSONResponse({"rows": df.to_dicts(), "n": len(df)})
    except Exception as e:
        return _error(f"{type(e).__name__}: {e}")


async def export_data(request: Request) -> JSONResponse:
    """Export data to a file.

    Body: {"path": "output.csv", "format": "csv"}
    """
    ws = _get_workspace()
    data = await _get_body(request)

    path = data.get("path")
    if not path:
        return _error("'path' is required")

    try:
        ws.export(path, format=data.get("format"))
        return JSONResponse({"exported": path})
    except Exception as e:
        return _error(f"{type(e).__name__}: {e}")


async def undo(request: Request) -> JSONResponse:
    """Undo the last operation."""
    ws = _get_workspace()
    try:
        ws.undo()
        info = ws.inspect()
        return JSONResponse({"undone": True, "shape": info["shape"]})
    except Exception as e:
        return _error(f"{type(e).__name__}: {e}")


async def redo(request: Request) -> JSONResponse:
    """Redo the last undone operation."""
    ws = _get_workspace()
    try:
        ws.redo()
        info = ws.inspect()
        return JSONResponse({"redone": True, "shape": info["shape"]})
    except Exception as e:
        return _error(f"{type(e).__name__}: {e}")


async def history(request: Request) -> JSONResponse:
    """Get operation history."""
    ws = _get_workspace()
    try:
        result = ws.history_summary()
        return JSONResponse({"operations": result})
    except Exception as e:
        return _error(f"{type(e).__name__}: {e}")


async def branch(request: Request) -> JSONResponse:
    """Create a named branch.

    Body: {"name": "experiment-1"}
    """
    ws = _get_workspace()
    data = await _get_body(request)

    name = data.get("name")
    if not name:
        return _error("'name' is required")

    try:
        ws.branch(name)
        return JSONResponse({"branched": name})
    except Exception as e:
        return _error(f"{type(e).__name__}: {e}")


async def switch(request: Request) -> JSONResponse:
    """Switch to a different sheet.

    Body: {"name": "sheet_name"}
    """
    ws = _get_workspace()
    data = await _get_body(request)

    name = data.get("name")
    if not name:
        return _error("'name' is required")

    try:
        ws.switch(name)
        info = ws.inspect()
        return JSONResponse({"switched": name, "shape": info["shape"]})
    except Exception as e:
        return _error(f"{type(e).__name__}: {e}")


async def profile(request: Request) -> JSONResponse:
    """Profile the active sheet (deep statistical scan)."""
    ws = _get_workspace()
    try:
        result = ws.scan()
        return JSONResponse(result)
    except Exception as e:
        return _error(f"{type(e).__name__}: {e}")


async def describe(request: Request) -> JSONResponse:
    """Get a natural-language description of the data."""
    ws = _get_workspace()
    try:
        result = ws.describe()
        return JSONResponse({"description": result})
    except Exception as e:
        return _error(f"{type(e).__name__}: {e}")


async def recipe_run(request: Request) -> JSONResponse:
    """Run a recipe on the active sheet.

    Body: {"recipe": "clean-csv", "params": {"key": "value"}, "stop_on_error": true}
    """
    ws = _get_workspace()
    data = await _get_body(request)

    recipe_name = data.get("recipe")
    if not recipe_name:
        return _error("'recipe' is required")

    try:
        result = ws.run_recipe(
            recipe_name,
            params=data.get("params"),
            stop_on_error=data.get("stop_on_error", True),
        )
        return JSONResponse(result)
    except Exception as e:
        return _error(f"{type(e).__name__}: {e}")


async def recipe_list(request: Request) -> JSONResponse:
    """List available recipes.

    Query params: ?recipe_dir=/path/to/recipes
    """
    ws = _get_workspace()
    recipe_dir = request.query_params.get("recipe_dir")
    try:
        result = ws.list_recipes(recipe_dir=recipe_dir)
        return JSONResponse(result)
    except Exception as e:
        return _error(f"{type(e).__name__}: {e}")


async def generate_code(request: Request) -> JSONResponse:
    """Generate reproducible code from the operation history.

    Query params: ?format=polars
    """
    ws = _get_workspace()
    fmt = request.query_params.get("format", "polars")
    try:
        if fmt == "polars":
            code = ws.generate_code()
        else:
            code = ws.generate_pipeline(format=fmt)
        return JSONResponse({"code": code, "format": fmt})
    except Exception as e:
        return _error(f"{type(e).__name__}: {e}")


async def validate_rules(request: Request) -> JSONResponse:
    """Validate data against rules.

    Body: {"rules": [{"name": "...", "check": "...", "severity": "error"}]}
    """
    ws = _get_workspace()
    data = await _get_body(request)

    rules = data.get("rules")
    if not rules:
        return _error("'rules' is required")

    try:
        result = ws.validate_rules(rules)
        return JSONResponse(result)
    except Exception as e:
        return _error(f"{type(e).__name__}: {e}")


async def schema(request: Request) -> JSONResponse:
    """Get the schema of the active sheet."""
    ws = _get_workspace()
    try:
        self_schema = ws.schema
        return JSONResponse(self_schema)
    except Exception as e:
        return _error(f"{type(e).__name__}: {e}")


# ---------------------------------------------------------------------------
# App factory
# ---------------------------------------------------------------------------


def create_app() -> Starlette:
    """Create the Starlette application with all routes."""
    routes = [
        Route("/api/health", health, methods=["GET"]),
        Route("/api/load", load, methods=["POST"]),
        Route("/api/inspect", inspect, methods=["GET"]),
        Route("/api/transform", transform, methods=["POST"]),
        Route("/api/query", query, methods=["POST"]),
        Route("/api/filter", filter_rows, methods=["POST"]),
        Route("/api/sort", sort_rows, methods=["POST"]),
        Route("/api/select", select_columns, methods=["POST"]),
        Route("/api/sheets", sheets, methods=["GET"]),
        Route("/api/sample", sample, methods=["GET"]),
        Route("/api/export", export_data, methods=["POST"]),
        Route("/api/undo", undo, methods=["POST"]),
        Route("/api/redo", redo, methods=["POST"]),
        Route("/api/history", history, methods=["GET"]),
        Route("/api/branch", branch, methods=["POST"]),
        Route("/api/switch", switch, methods=["POST"]),
        Route("/api/profile", profile, methods=["GET"]),
        Route("/api/describe", describe, methods=["GET"]),
        Route("/api/schema", schema, methods=["GET"]),
        Route("/api/recipe/run", recipe_run, methods=["POST"]),
        Route("/api/recipe/list", recipe_list, methods=["GET"]),
        Route("/api/generate-code", generate_code, methods=["GET"]),
        Route("/api/validate", validate_rules, methods=["POST"]),
    ]

    middleware = [
        Middleware(
            CORSMiddleware,
            allow_origins=["*"],
            allow_methods=["*"],
            allow_headers=["*"],
        ),
    ]

    return Starlette(routes=routes, middleware=middleware)


def run_http_server(host: str = "127.0.0.1", port: int = 8421) -> None:
    """Run the HTTP API server."""
    import uvicorn

    app = create_app()
    uvicorn.run(app, host=host, port=port)
