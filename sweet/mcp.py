"""Sweet MCP Server — Exposes workspace operations as MCP tools.

This allows AI agents (Claude Desktop, VS Code Copilot, Cursor, etc.) to drive
Sweet data operations through the Model Context Protocol.

Usage:
    sweet serve --mcp          # Via CLI
    python -m sweet.mcp        # Directly
"""

from __future__ import annotations

import json

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import TextContent, Tool

from .core.workspace import Workspace

# Global workspace instance for the MCP session
_workspace: Workspace | None = None


def _get_workspace() -> Workspace:
    """Get or create the global workspace."""
    global _workspace
    if _workspace is None:
        _workspace = Workspace()
    return _workspace


# Create the MCP server
server = Server("sweet")


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------


@server.list_tools()
async def list_tools() -> list[Tool]:
    """List all available Sweet tools."""
    return [
        Tool(
            name="sweet_load",
            description="Load data from a file into the workspace. Supports CSV, Parquet, and JSON.",
            inputSchema={
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Path to the data file to load.",
                    },
                    "name": {
                        "type": "string",
                        "description": "Optional name for the sheet. Defaults to filename.",
                    },
                    "format": {
                        "type": "string",
                        "enum": ["csv", "parquet", "json"],
                        "description": "File format. Auto-detected from extension if omitted.",
                    },
                },
                "required": ["path"],
            },
        ),
        Tool(
            name="sweet_inspect",
            description="Inspect the active sheet: get schema, shape, sample rows, and null counts.",
            inputSchema={
                "type": "object",
                "properties": {
                    "n_rows": {
                        "type": "integer",
                        "description": "Number of sample rows to return. Default: 5.",
                        "default": 5,
                    },
                },
            },
        ),
        Tool(
            name="sweet_transform",
            description=(
                "Apply a Polars expression to the active sheet. "
                "The expression receives `df` (current DataFrame) and `pl` (polars module). "
                "Example: df.filter(pl.col('age') > 30)"
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "expr": {
                        "type": "string",
                        "description": "Polars expression to apply.",
                    },
                    "description": {
                        "type": "string",
                        "description": "Human-readable description of the transform.",
                    },
                },
                "required": ["expr"],
            },
        ),
        Tool(
            name="sweet_query",
            description=(
                "Run a SQL query against the active sheet's data via DuckDB. "
                "The sheet is available as a table with its sheet name."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "sql": {
                        "type": "string",
                        "description": "SQL query to execute.",
                    },
                },
                "required": ["sql"],
            },
        ),
        Tool(
            name="sweet_filter",
            description="Filter rows by a Polars condition. Example: pl.col('age') > 30",
            inputSchema={
                "type": "object",
                "properties": {
                    "condition": {
                        "type": "string",
                        "description": "Polars filter condition expression.",
                    },
                },
                "required": ["condition"],
            },
        ),
        Tool(
            name="sweet_sort",
            description="Sort the active sheet by one or more columns.",
            inputSchema={
                "type": "object",
                "properties": {
                    "columns": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Column names to sort by.",
                    },
                    "descending": {
                        "type": "boolean",
                        "description": "Sort descending. Default: false.",
                        "default": False,
                    },
                },
                "required": ["columns"],
            },
        ),
        Tool(
            name="sweet_select",
            description="Select specific columns from the active sheet.",
            inputSchema={
                "type": "object",
                "properties": {
                    "columns": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Column names to keep.",
                    },
                },
                "required": ["columns"],
            },
        ),
        Tool(
            name="sweet_branch",
            description=(
                "Create a named branch (copy) of the active sheet for exploratory work. "
                "Switches to the new branch automatically."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "Name for the new branch.",
                    },
                },
                "required": ["name"],
            },
        ),
        Tool(
            name="sweet_switch",
            description="Switch to a different sheet by name.",
            inputSchema={
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "Name of the sheet to switch to.",
                    },
                },
                "required": ["name"],
            },
        ),
        Tool(
            name="sweet_export",
            description="Export the active sheet to a file. Supports CSV, Parquet, and JSON.",
            inputSchema={
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Destination file path.",
                    },
                    "format": {
                        "type": "string",
                        "enum": ["csv", "parquet", "json"],
                        "description": "File format. Auto-detected from extension if omitted.",
                    },
                },
                "required": ["path"],
            },
        ),
        Tool(
            name="sweet_undo",
            description="Undo the last transform operation.",
            inputSchema={
                "type": "object",
                "properties": {},
            },
        ),
        Tool(
            name="sweet_redo",
            description="Redo the last undone operation.",
            inputSchema={
                "type": "object",
                "properties": {},
            },
        ),
        Tool(
            name="sweet_history",
            description="Get the operation history as a list of steps performed.",
            inputSchema={
                "type": "object",
                "properties": {},
            },
        ),
        Tool(
            name="sweet_generate_code",
            description="Generate reproducible Polars Python code from the transformation history.",
            inputSchema={
                "type": "object",
                "properties": {},
            },
        ),
        Tool(
            name="sweet_sheets",
            description="List all sheets in the workspace with their shapes.",
            inputSchema={
                "type": "object",
                "properties": {},
            },
        ),
        Tool(
            name="sweet_sample",
            description="Get a random sample of rows from the active sheet.",
            inputSchema={
                "type": "object",
                "properties": {
                    "n": {
                        "type": "integer",
                        "description": "Number of rows to sample. Default: 10.",
                        "default": 10,
                    },
                },
            },
        ),
        Tool(
            name="sweet_scan",
            description=(
                "Deep statistical profile of the active sheet via Pointblank. "
                "Returns per-column statistics: type, missingness, uniqueness, "
                "mean, median, std, quartiles, min/max, and sample values."
            ),
            inputSchema={
                "type": "object",
                "properties": {},
            },
        ),
        Tool(
            name="sweet_validate",
            description=(
                "Run data quality validation checks on the active sheet via Pointblank. "
                "Provide a list of checks or a path to a YAML validation file. "
                "Without checks, validates all columns for non-null values plus "
                "rows_distinct and rows_complete. Supports thresholds for graduated "
                "severity (warning/error/critical) and optional data extracts (failing rows)."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "checks": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "type": {
                                    "type": "string",
                                    "description": (
                                        "Validation method: col_vals_gt, col_vals_ge, "
                                        "col_vals_lt, col_vals_le, col_vals_eq, col_vals_ne, "
                                        "col_vals_between, col_vals_outside, col_vals_in_set, "
                                        "col_vals_not_in_set, col_vals_not_null, col_vals_null, "
                                        "col_vals_regex, rows_distinct, rows_complete, "
                                        "col_schema_match."
                                    ),
                                },
                                "column": {
                                    "type": "string",
                                    "description": "Column name to validate (not needed for row-level checks).",
                                },
                            },
                            "required": ["type"],
                        },
                        "description": "List of validation check definitions.",
                    },
                    "yaml_path": {
                        "type": "string",
                        "description": "Path to a Pointblank YAML validation file.",
                    },
                    "thresholds": {
                        "type": "object",
                        "properties": {
                            "warning": {
                                "type": "number",
                                "description": "Warning threshold (fraction 0-1 or count > 1).",
                            },
                            "error": {"type": "number", "description": "Error threshold."},
                            "critical": {"type": "number", "description": "Critical threshold."},
                        },
                        "description": "Graduated severity thresholds for validation steps.",
                    },
                    "get_extracts": {
                        "type": "boolean",
                        "description": "If true, include failing rows for each step (up to 50 rows).",
                        "default": False,
                    },
                },
            },
        ),
        Tool(
            name="sweet_sundered",
            description=(
                "Split the active sheet into passing and failing rows based on "
                "non-null validation. Returns row counts and a sample of each split."
            ),
            inputSchema={"type": "object", "properties": {}},
        ),
        Tool(
            name="sweet_schema",
            description=(
                "Get detailed schema information for the active sheet via Pointblank. "
                "Returns column names, data types, and structural metadata."
            ),
            inputSchema={
                "type": "object",
                "properties": {},
            },
        ),
        Tool(
            name="sweet_detect_types",
            description=(
                "Detect semantic types in string columns (dates, emails, URLs, "
                "integers, booleans, etc.) and suggest casts. Also flags potential PII columns."
            ),
            inputSchema={
                "type": "object",
                "properties": {},
            },
        ),
        Tool(
            name="sweet_detect_outliers",
            description=(
                "Detect statistical outliers in numeric columns. Returns outlier counts, "
                "bounds, and row indices for each column."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "method": {
                        "type": "string",
                        "enum": ["iqr", "zscore"],
                        "description": "Detection method: 'iqr' (interquartile range) or 'zscore'. Default: iqr.",
                        "default": "iqr",
                    },
                    "threshold": {
                        "type": "number",
                        "description": "IQR multiplier (default 1.5) or z-score threshold (default 3.0).",
                    },
                },
            },
        ),
        Tool(
            name="sweet_describe",
            description=(
                "Generate a plain-English description of the active sheet's data. "
                "Summarizes shape, types, completeness, numeric ranges, cardinality, and duplicates."
            ),
            inputSchema={
                "type": "object",
                "properties": {},
            },
        ),
    ]


@server.call_tool()
async def call_tool(name: str, arguments: dict) -> list[TextContent]:
    """Handle tool calls from the agent."""
    ws = _get_workspace()

    try:
        if name == "sweet_load":
            ws.load(
                arguments["path"],
                name=arguments.get("name"),
                format=arguments.get("format"),
            )
            info = ws.inspect()
            return [
                TextContent(
                    type="text",
                    text=f"Loaded '{info['name']}': {info['shape'][0]} rows × {info['shape'][1]} columns\n"
                    f"Schema: {json.dumps(info['schema'], indent=2)}",
                )
            ]

        elif name == "sweet_inspect":
            n_rows = arguments.get("n_rows", 5)
            info = ws.inspect(n_rows=n_rows)
            return [
                TextContent(
                    type="text",
                    text=json.dumps(info, indent=2, default=str),
                )
            ]

        elif name == "sweet_transform":
            ws.transform(
                arguments["expr"],
                description=arguments.get("description", ""),
            )
            return [
                TextContent(
                    type="text",
                    text=f"Transform applied. Shape: {ws.shape[0]} rows × {ws.shape[1]} columns",
                )
            ]

        elif name == "sweet_query":
            ws.query(arguments["sql"])
            return [
                TextContent(
                    type="text",
                    text=f"Query executed. Shape: {ws.shape[0]} rows × {ws.shape[1]} columns\n"
                    f"Columns: {list(ws.df.columns)}",
                )
            ]

        elif name == "sweet_filter":
            ws.filter(arguments["condition"])
            return [
                TextContent(
                    type="text",
                    text=f"Filtered. Shape: {ws.shape[0]} rows × {ws.shape[1]} columns",
                )
            ]

        elif name == "sweet_sort":
            ws.sort(*arguments["columns"], descending=arguments.get("descending", False))
            return [
                TextContent(
                    type="text",
                    text=f"Sorted by: {arguments['columns']}",
                )
            ]

        elif name == "sweet_select":
            ws.select(*arguments["columns"])
            return [
                TextContent(
                    type="text",
                    text=f"Selected {len(arguments['columns'])} columns. Shape: {ws.shape[0]} rows × {ws.shape[1]} columns",
                )
            ]

        elif name == "sweet_branch":
            ws.branch(arguments["name"])
            return [
                TextContent(
                    type="text",
                    text=f"Branch '{arguments['name']}' created and activated.",
                )
            ]

        elif name == "sweet_switch":
            ws.switch(arguments["name"])
            info = ws.inspect(n_rows=0)
            return [
                TextContent(
                    type="text",
                    text=f"Switched to '{arguments['name']}'. Shape: {info['shape'][0]} rows × {info['shape'][1]} columns",
                )
            ]

        elif name == "sweet_export":
            ws.export(arguments["path"], format=arguments.get("format"))
            return [
                TextContent(
                    type="text",
                    text=f"Exported to: {arguments['path']}",
                )
            ]

        elif name == "sweet_undo":
            ws.undo()
            return [
                TextContent(
                    type="text",
                    text=f"Undone. Shape: {ws.shape[0]} rows × {ws.shape[1]} columns",
                )
            ]

        elif name == "sweet_redo":
            ws.redo()
            return [
                TextContent(
                    type="text",
                    text=f"Redone. Shape: {ws.shape[0]} rows × {ws.shape[1]} columns",
                )
            ]

        elif name == "sweet_history":
            summary = ws.history_summary()
            return [
                TextContent(
                    type="text",
                    text=json.dumps(summary, indent=2),
                )
            ]

        elif name == "sweet_generate_code":
            code = ws.generate_code()
            return [
                TextContent(
                    type="text",
                    text=code,
                )
            ]

        elif name == "sweet_sheets":
            sheets_info = []
            for sheet_name in ws.sheet_names:
                ws.switch(sheet_name)
                sheets_info.append(
                    {
                        "name": sheet_name,
                        "shape": ws.shape,
                        "columns": list(ws.df.columns) if ws.df is not None else [],
                    }
                )
            return [
                TextContent(
                    type="text",
                    text=json.dumps(sheets_info, indent=2),
                )
            ]

        elif name == "sweet_sample":
            n = arguments.get("n", 10)
            sample = ws.sample(n)
            if sample is None:
                return [TextContent(type="text", text="No data in active sheet.")]
            return [
                TextContent(
                    type="text",
                    text=json.dumps(sample.to_dicts(), indent=2, default=str),
                )
            ]

        elif name == "sweet_scan":
            scan_result = ws.scan()
            return [
                TextContent(
                    type="text",
                    text=json.dumps(scan_result, indent=2, default=str),
                )
            ]

        elif name == "sweet_validate":
            checks = arguments.get("checks")
            yaml_path = arguments.get("yaml_path")
            thresholds = arguments.get("thresholds")
            get_extracts = arguments.get("get_extracts", False)
            result = ws.validate(
                checks=checks,
                yaml_path=yaml_path,
                thresholds=thresholds,
                get_extracts=get_extracts,
            )
            return [
                TextContent(
                    type="text",
                    text=json.dumps(result, indent=2, default=str),
                )
            ]

        elif name == "sweet_sundered":
            sundered = ws.get_sundered_data()
            summary = {
                "pass_rows": sundered["pass"].shape[0],
                "fail_rows": sundered["fail"].shape[0],
                "pass_sample": sundered["pass"].head(5).to_dicts(),
                "fail_sample": sundered["fail"].head(5).to_dicts(),
            }
            return [
                TextContent(
                    type="text",
                    text=json.dumps(summary, indent=2, default=str),
                )
            ]

        elif name == "sweet_schema":
            schema_result = ws.schema_info()
            return [
                TextContent(
                    type="text",
                    text=json.dumps(schema_result, indent=2, default=str),
                )
            ]

        elif name == "sweet_detect_types":
            types_result = ws.detect_types()
            return [
                TextContent(
                    type="text",
                    text=json.dumps(types_result, indent=2, default=str),
                )
            ]

        elif name == "sweet_detect_outliers":
            method = arguments.get("method", "iqr")
            threshold = arguments.get("threshold", 1.5)
            outlier_result = ws.detect_outliers(method=method, threshold=threshold)
            return [
                TextContent(
                    type="text",
                    text=json.dumps(outlier_result, indent=2, default=str),
                )
            ]

        elif name == "sweet_describe":
            description = ws.describe()
            return [
                TextContent(
                    type="text",
                    text=description,
                )
            ]

        else:
            return [TextContent(type="text", text=f"Unknown tool: {name}")]

    except Exception as e:
        return [TextContent(type="text", text=f"Error: {type(e).__name__}: {e}")]


async def run_mcp_server():
    """Run the MCP server over stdio."""
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())
