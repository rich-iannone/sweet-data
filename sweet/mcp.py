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
            description=(
                "Export the active sheet to a file, database, or cloud storage. "
                "Supports local files (CSV, Parquet, JSON, TSV, NDJSON, IPC), "
                "cloud (s3://, gs://, az://), and databases "
                "(postgresql://, mysql://, sqlite://, duckdb://)."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "dest": {
                        "type": "string",
                        "description": (
                            "Destination — file path, cloud URL (s3://...), "
                            "or database connection string."
                        ),
                    },
                    "format": {
                        "type": "string",
                        "description": "File format. Auto-detected from extension if omitted.",
                    },
                    "table": {
                        "type": "string",
                        "description": "Table name (for database destinations).",
                    },
                    "mode": {
                        "type": "string",
                        "enum": ["replace", "append", "fail"],
                        "description": "Write mode for databases. Default: 'replace'.",
                    },
                },
                "required": ["dest"],
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
        Tool(
            name="sweet_detect_pii",
            description=(
                "Detect columns likely containing Personally Identifiable Information "
                "(emails, phone numbers, SSNs, credit cards, IP addresses). "
                "Uses pattern matching on column names and sampled values."
            ),
            inputSchema={"type": "object", "properties": {}},
        ),
        Tool(
            name="sweet_relationships",
            description=(
                "Detect potential join keys and relationships across sheets. "
                "Analyzes column names, types, cardinality, and value overlap. "
                "Requires at least 2 sheets loaded."
            ),
            inputSchema={"type": "object", "properties": {}},
        ),
        Tool(
            name="sweet_infer_contract",
            description=(
                "Infer a schema contract for the active sheet. "
                "Captures column types, nullability, uniqueness, value ranges, "
                "and allowed values for categorical columns."
            ),
            inputSchema={"type": "object", "properties": {}},
        ),
        Tool(
            name="sweet_enforce_contract",
            description=(
                "Enforce a previously inferred schema contract against the active sheet. "
                "Reports violations: missing columns, dtype mismatches, unexpected nulls, "
                "uniqueness violations, out-of-range values, unexpected categorical values."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "contract": {
                        "type": "object",
                        "description": "A contract object as returned by sweet_infer_contract.",
                    },
                },
                "required": ["contract"],
            },
        ),
        Tool(
            name="sweet_suggest_casts",
            description=(
                "Suggest type casts for string columns that contain typed data "
                "(dates, integers, floats, booleans). Returns the Polars expression "
                "needed to apply each cast."
            ),
            inputSchema={"type": "object", "properties": {}},
        ),
        Tool(
            name="sweet_apply_casts",
            description=(
                "Apply all high-confidence (>=90%) suggested type casts to the active sheet. "
                "Converts string columns to their detected types (dates, ints, floats, booleans)."
            ),
            inputSchema={"type": "object", "properties": {}},
        ),
        Tool(
            name="sweet_suggest",
            description=(
                "Analyze the active sheet and suggest transforms based on data patterns. "
                "Detects currency extraction, whitespace trimming, date parsing, column merging, "
                "naming normalization, constant/empty columns, boolean strings, and more. "
                "Returns suggestions with Polars expressions ready to apply."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "max_suggestions": {
                        "type": "integer",
                        "description": "Maximum suggestions to return. Default: 20.",
                        "default": 20,
                    },
                },
            },
        ),
        Tool(
            name="sweet_learned_suggestions",
            description=(
                "Get transform suggestions based on learned usage patterns. "
                "Returns recommendations from frequently observed behaviors — "
                "things the user has done repeatedly on similar data."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "min_count": {
                        "type": "integer",
                        "description": "Minimum observation count. Default: 3.",
                    },
                },
            },
        ),
        Tool(
            name="sweet_patterns_summary",
            description=(
                "Get a summary of learned usage patterns — how many patterns, "
                "which kinds, and the most frequent behaviors."
            ),
            inputSchema={"type": "object", "properties": {}},
        ),
        Tool(
            name="sweet_correlations",
            description=(
                "Compute pairwise correlations between numeric columns. "
                "Returns pairs sorted by absolute correlation strength."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "method": {
                        "type": "string",
                        "enum": ["pearson", "spearman"],
                        "description": "Correlation method (default: pearson).",
                    },
                    "min_abs": {
                        "type": "number",
                        "description": "Only include pairs with |correlation| >= this value (default: 0.0).",
                    },
                },
            },
        ),
        Tool(
            name="sweet_run_recipe",
            description=(
                "Run a named recipe (multi-step workflow) on the active sheet. "
                "Built-in recipes: 'clean-csv', 'quality-check', 'prepare-export'. "
                "Each recipe executes a sequence of steps with validation and rollback."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "recipe": {
                        "type": "string",
                        "description": "Recipe name (e.g., 'clean-csv', 'quality-check', 'prepare-export').",
                    },
                },
                "required": ["recipe"],
            },
        ),
        Tool(
            name="sweet_run_steps",
            description=(
                "Run a custom sequence of agent steps on the active sheet. "
                "Available steps: detect_and_cast_types, remove_duplicates, "
                "standardize_nulls, trim_whitespace, drop_all_null_columns, "
                "drop_all_null_rows, detect_outliers, validate, generate_report."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "steps": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Ordered list of step names to execute.",
                    },
                },
                "required": ["steps"],
            },
        ),
        Tool(
            name="sweet_list_recipes",
            description="List all available recipes with their descriptions and steps.",
            inputSchema={"type": "object", "properties": {}},
        ),
        Tool(
            name="sweet_memory_summary",
            description="Get a summary of the agent's persistent memory (preferences, domain rules, run history).",
            inputSchema={"type": "object", "properties": {}},
        ),
        Tool(
            name="sweet_memory_get_preferences",
            description="Get all stored user preferences.",
            inputSchema={"type": "object", "properties": {}},
        ),
        Tool(
            name="sweet_memory_set_preference",
            description="Set a user preference that persists across sessions.",
            inputSchema={
                "type": "object",
                "properties": {
                    "key": {
                        "type": "string",
                        "description": "Preference key (e.g., 'date_format', 'null_handling', 'naming_convention').",
                    },
                    "value": {
                        "description": "Preference value (string, number, boolean, or object).",
                    },
                },
                "required": ["key", "value"],
            },
        ),
        Tool(
            name="sweet_memory_add_rule",
            description="Add a domain rule to memory (business rules, valid value ranges, constraints).",
            inputSchema={
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "Rule name (e.g., 'revenue_positive', 'valid_countries').",
                    },
                    "rule": {
                        "type": "object",
                        "description": "Rule definition with fields like column, check, severity, description.",
                    },
                },
                "required": ["name", "rule"],
            },
        ),
        Tool(
            name="sweet_memory_list_rules",
            description="List all domain rules stored in memory.",
            inputSchema={"type": "object", "properties": {}},
        ),
        Tool(
            name="sweet_memory_suggest_recipe",
            description="Suggest a recipe based on memory of what worked on similar datasets before.",
            inputSchema={"type": "object", "properties": {}},
        ),
        Tool(
            name="sweet_memory_find_similar_runs",
            description="Find past agent runs on datasets similar to the currently loaded data.",
            inputSchema={
                "type": "object",
                "properties": {
                    "threshold": {
                        "type": "number",
                        "description": "Minimum similarity score (0.0–1.0). Default: 0.5.",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Max results to return. Default: 5.",
                    },
                },
            },
        ),
        Tool(
            name="sweet_run_pipeline",
            description=(
                "Run a multi-agent pipeline on the loaded data. "
                "Available pipelines: 'standard' (ingest → quality → transform → export), "
                "or specify custom stages."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "pipeline": {
                        "type": "string",
                        "description": "Pipeline name. Currently: 'standard'.",
                    },
                    "stages": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": (
                            "Custom stage list (agent domains): 'ingestion', 'quality', "
                            "'transform', 'export'. Overrides the named pipeline."
                        ),
                    },
                },
            },
        ),
        Tool(
            name="sweet_generate_pipeline",
            description=(
                "Generate production-ready pipeline code from the workspace's "
                "transform history. Formats: 'polars' (Python script), 'sql' (DuckDB), "
                "'dbt' (dbt model), 'script' (minimal Python)."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "format": {
                        "type": "string",
                        "enum": ["polars", "sql", "dbt", "script"],
                        "description": "Output code format. Default: 'polars'.",
                    },
                    "source": {
                        "type": "string",
                        "description": "Source file path (for loader line in generated code).",
                    },
                    "output": {
                        "type": "string",
                        "description": "Output file path (for export line in generated code).",
                    },
                    "name": {
                        "type": "string",
                        "description": "Pipeline/function/model name.",
                    },
                },
            },
        ),
        Tool(
            name="sweet_load_url",
            description=(
                "Load data from a URL into the workspace. Supports direct file downloads "
                "(CSV, Parquet, JSON, etc.), cloud storage (s3://, gs://), and web pages "
                "(extracts HTML tables)."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "url": {
                        "type": "string",
                        "description": "URL to load data from.",
                    },
                    "name": {
                        "type": "string",
                        "description": "Sheet name. Auto-derived if not provided.",
                    },
                    "format": {
                        "type": "string",
                        "description": "Force format (csv, parquet, json). Auto-detected if omitted.",
                    },
                    "selector": {
                        "type": "integer",
                        "description": "Table index for web pages with multiple tables. Default: 0.",
                    },
                },
                "required": ["url"],
            },
        ),
        Tool(
            name="sweet_load_database",
            description=(
                "Load data from a database into the workspace. Supports PostgreSQL, MySQL, "
                "SQLite, and DuckDB via connection strings."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "connection": {
                        "type": "string",
                        "description": (
                            "Database connection string "
                            "(e.g., 'sqlite:///path.db', 'postgresql://user:pass@host/db')."
                        ),
                    },
                    "table": {
                        "type": "string",
                        "description": "Table name to load.",
                    },
                    "query": {
                        "type": "string",
                        "description": "Custom SQL query (overrides table).",
                    },
                    "name": {
                        "type": "string",
                        "description": "Sheet name for the loaded data.",
                    },
                },
                "required": ["connection"],
            },
        ),
        Tool(
            name="sweet_list_tables",
            description="List available tables in a database source.",
            inputSchema={
                "type": "object",
                "properties": {
                    "connection": {
                        "type": "string",
                        "description": "Database connection string.",
                    },
                },
                "required": ["connection"],
            },
        ),
        Tool(
            name="sweet_to_great_table",
            description=(
                "Export the active sheet as a publication-quality HTML table using "
                "Great Tables. Save to a file or return raw HTML."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "dest": {
                        "type": "string",
                        "description": "Output file path (.html). If omitted, returns raw HTML.",
                    },
                    "title": {
                        "type": "string",
                        "description": "Table title.",
                    },
                    "subtitle": {
                        "type": "string",
                        "description": "Table subtitle.",
                    },
                    "rowname_col": {
                        "type": "string",
                        "description": "Column to use as row names.",
                    },
                    "groupname_col": {
                        "type": "string",
                        "description": "Column to group rows by.",
                    },
                    "fmt_currency": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Columns to format as currency.",
                    },
                    "fmt_number": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Columns to format as numbers.",
                    },
                    "fmt_percent": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Columns to format as percentages.",
                    },
                    "striping": {
                        "type": "boolean",
                        "description": "Enable row striping.",
                    },
                    "stylize": {
                        "type": "integer",
                        "description": "Style preset (1-6).",
                    },
                },
            },
        ),
        Tool(
            name="sweet_commit",
            description="Create a versioned snapshot (commit) of the current sheet's data.",
            inputSchema={
                "type": "object",
                "properties": {
                    "message": {
                        "type": "string",
                        "description": "Commit message describing this state.",
                    },
                },
                "required": ["message"],
            },
        ),
        Tool(
            name="sweet_version_log",
            description="Get commit history for the workspace (most recent first).",
            inputSchema={
                "type": "object",
                "properties": {
                    "sheet": {
                        "type": "string",
                        "description": "Filter to a specific sheet. Omit for current sheet.",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Maximum number of commits to return.",
                    },
                },
            },
        ),
        Tool(
            name="sweet_checkout",
            description="Restore the active sheet's data to a previous commit.",
            inputSchema={
                "type": "object",
                "properties": {
                    "commit_id": {
                        "type": "string",
                        "description": "The commit ID (or unique prefix) to restore.",
                    },
                },
                "required": ["commit_id"],
            },
        ),
        Tool(
            name="sweet_diff",
            description=(
                "Diff the current sheet against a commit, another sheet, or its last commit. "
                "Provides column-aware comparison with row-level change detection."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "target": {
                        "type": "string",
                        "description": (
                            "Commit ID or sheet name to diff against. "
                            "Omit to diff against the most recent commit."
                        ),
                    },
                    "key_columns": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Key column(s) for row matching. Omit for positional diff.",
                    },
                },
            },
        ),
        Tool(
            name="sweet_save_bundle",
            description=(
                "Save the workspace as a shareable .sweet bundle file containing "
                "all sheets, transforms, and history."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Output file path (.sweet extension added if missing).",
                    },
                    "description": {
                        "type": "string",
                        "description": "Description for the bundle.",
                    },
                },
                "required": ["path"],
            },
        ),
        Tool(
            name="sweet_open_bundle",
            description=(
                "Restore a workspace from a .sweet bundle file. "
                "Replaces the current workspace state."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Path to the .sweet bundle file.",
                    },
                },
                "required": ["path"],
            },
        ),
        Tool(
            name="sweet_inspect_bundle",
            description="Inspect a .sweet bundle file without loading it. Shows metadata and sheet info.",
            inputSchema={
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Path to the .sweet bundle file.",
                    },
                },
                "required": ["path"],
            },
        ),
        Tool(
            name="sweet_semantic_types",
            description=(
                "Infer semantic types for columns in the active sheet. "
                "Identifies identifiers, emails, dates, currency, etc. from "
                "column names and content patterns."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "min_confidence": {
                        "type": "number",
                        "description": "Minimum confidence threshold (0-1). Default 0.4.",
                    },
                },
            },
        ),
        Tool(
            name="sweet_discover_joins",
            description=(
                "Discover potential join relationships across all loaded sheets. "
                "Finds columns with matching semantic types and overlapping values."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "min_confidence": {
                        "type": "number",
                        "description": "Minimum semantic confidence (0-1). Default 0.6.",
                    },
                    "min_overlap": {
                        "type": "number",
                        "description": "Minimum value overlap ratio (0-1). Default 0.3.",
                    },
                },
            },
        ),
        Tool(
            name="sweet_synthesize",
            description=(
                "Generate synthetic data matching the active sheet's schema and "
                "statistical profile. Creates a new sheet with realistic fake data."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "rows": {
                        "type": "integer",
                        "description": "Number of rows to generate. Default 1000.",
                    },
                    "seed": {
                        "type": "integer",
                        "description": "Random seed for reproducibility.",
                    },
                },
            },
        ),
        Tool(
            name="sweet_impute",
            description=(
                "Fill null values in a column using a specified strategy "
                "(mean, median, mode, forward, backward, zero, interpolate)."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "column": {
                        "type": "string",
                        "description": "Column name to impute.",
                    },
                    "method": {
                        "type": "string",
                        "description": "Imputation method. Default 'median'.",
                        "enum": ["mean", "median", "mode", "forward", "backward", "zero", "interpolate"],
                    },
                },
                "required": ["column"],
            },
        ),
        Tool(
            name="sweet_augment",
            description=(
                "Add a derived column to the active sheet: "
                "fill_rate (per-row completeness), row_hash (dedup hash), "
                "or row_number (sequential index)."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "kind": {
                        "type": "string",
                        "description": "Augmentation type.",
                        "enum": ["fill_rate", "row_hash", "row_number"],
                    },
                },
                "required": ["kind"],
            },
        ),
        Tool(
            name="sweet_load_conventions",
            description=(
                "Load team conventions from a .sweet/conventions.yaml file. "
                "If no path given, auto-discovers by walking up from cwd."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Path to conventions.yaml. Omit to auto-discover.",
                    },
                },
            },
        ),
        Tool(
            name="sweet_check_conventions",
            description=(
                "Validate the active sheet against loaded team conventions. "
                "Returns a list of violations (naming, quality, etc.)."
            ),
            inputSchema={
                "type": "object",
                "properties": {},
            },
        ),
        Tool(
            name="sweet_nl_transform",
            description=(
                "Apply a transformation described in natural language. "
                "Translates English to a Polars expression and executes it. "
                "Examples: 'filter rows where price > 100', 'sort by name descending'."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "text": {
                        "type": "string",
                        "description": "Natural language description of the operation.",
                    },
                },
                "required": ["text"],
            },
        ),
        Tool(
            name="sweet_nl_translate",
            description=(
                "Translate natural language to a Polars expression without executing it. "
                "Returns the expression, confidence, and operation type."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "text": {
                        "type": "string",
                        "description": "Natural language description of the operation.",
                    },
                },
                "required": ["text"],
            },
        ),
        Tool(
            name="sweet_nl_pipeline",
            description=(
                "Apply multiple natural language transforms separated by 'then' or ';'. "
                "Example: 'filter price > 10 then sort by name descending'."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "text": {
                        "type": "string",
                        "description": "Multi-step natural language pipeline.",
                    },
                },
                "required": ["text"],
            },
        ),
        Tool(
            name="sweet_explain_anomalies",
            description=(
                "Detect and explain anomalies (outliers, spikes, null clusters, "
                "pattern breaks) in the active sheet. Returns structured findings "
                "with severity, affected rows, and human-readable explanations."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "z_threshold": {
                        "type": "number",
                        "description": "Z-score threshold for outlier detection (default 3.0).",
                    },
                },
            },
        ),
        Tool(
            name="sweet_discover_relationships",
            description=(
                "Discover relationships (join keys, foreign keys, enrichment "
                "opportunities) between columns across all loaded sheets."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "min_match_rate": {
                        "type": "number",
                        "description": "Minimum value overlap fraction (default 0.5).",
                    },
                },
            },
        ),
        Tool(
            name="sweet_auto_join",
            description=(
                "Automatically join two loaded sheets by discovering the best join key. "
                "Creates a new sheet with the joined result."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "left_sheet": {
                        "type": "string",
                        "description": "Name of the left sheet.",
                    },
                    "right_sheet": {
                        "type": "string",
                        "description": "Name of the right sheet.",
                    },
                    "join_type": {
                        "type": "string",
                        "description": "Override join type: 'inner' or 'left'.",
                    },
                },
                "required": ["left_sheet", "right_sheet"],
            },
        ),
        Tool(
            name="sweet_validate_rules",
            description=(
                "Validate the active sheet against data quality rules. "
                "Rules can include: not_null, unique, regex, comparison (> 0), "
                "between, in(set), type checks, max_null_pct, min/max_length."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "rules": {
                        "type": "array",
                        "description": "Array of rule objects with name, column, check, severity.",
                        "items": {
                            "type": "object",
                            "properties": {
                                "name": {"type": "string"},
                                "column": {"type": "string"},
                                "check": {"type": "string"},
                                "severity": {"type": "string"},
                            },
                            "required": ["name", "check"],
                        },
                    },
                },
                "required": ["rules"],
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
            dest = arguments.get("dest") or arguments.get("path", "")
            ws.export(
                dest,
                format=arguments.get("format"),
                table=arguments.get("table"),
                mode=arguments.get("mode", "replace"),
            )
            return [
                TextContent(
                    type="text",
                    text=f"Exported {ws.shape[0]} rows × {ws.shape[1]} cols to: {dest}",
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

        elif name == "sweet_detect_pii":
            pii_result = ws.detect_pii()
            return [
                TextContent(
                    type="text",
                    text=json.dumps(pii_result, indent=2, default=str),
                )
            ]

        elif name == "sweet_relationships":
            rels = ws.detect_relationships()
            return [
                TextContent(
                    type="text",
                    text=json.dumps(rels, indent=2, default=str),
                )
            ]

        elif name == "sweet_infer_contract":
            contract = ws.infer_contract()
            return [
                TextContent(
                    type="text",
                    text=json.dumps(contract, indent=2, default=str),
                )
            ]

        elif name == "sweet_enforce_contract":
            contract = arguments["contract"]
            result = ws.enforce_contract(contract)
            return [
                TextContent(
                    type="text",
                    text=json.dumps(result, indent=2, default=str),
                )
            ]

        elif name == "sweet_suggest_casts":
            suggestions = ws.suggest_casts()
            return [
                TextContent(
                    type="text",
                    text=json.dumps(suggestions, indent=2, default=str),
                )
            ]

        elif name == "sweet_apply_casts":
            ws.apply_casts()
            return [
                TextContent(
                    type="text",
                    text="Applied high-confidence type casts.",
                )
            ]

        elif name == "sweet_suggest":
            max_s = arguments.get("max_suggestions", 20)
            suggestions = ws.suggest(max_suggestions=max_s)
            return [TextContent(type="text", text=json.dumps(suggestions, default=str))]

        elif name == "sweet_learned_suggestions":
            min_count = arguments.get("min_count")
            learned = ws.learned_suggestions(min_count=min_count)
            return [TextContent(type="text", text=json.dumps(learned, default=str))]

        elif name == "sweet_patterns_summary":
            summary = ws.patterns_summary()
            return [TextContent(type="text", text=json.dumps(summary, default=str))]

        elif name == "sweet_correlations":
            method = arguments.get("method", "pearson")
            min_abs = arguments.get("min_abs", 0.0)
            corr_result = ws.correlations(method=method, min_abs=min_abs)
            return [
                TextContent(
                    type="text",
                    text=json.dumps(corr_result, indent=2, default=str),
                )
            ]

        elif name == "sweet_run_recipe":
            from .agents import DataAgent, RecipeRegistry

            recipe_name = arguments["recipe"]
            registry = RecipeRegistry()
            recipe = registry.get(recipe_name)
            if recipe is None:
                available = [r["key"] for r in registry.list()]
                return [
                    TextContent(
                        type="text",
                        text=f"Unknown recipe: '{recipe_name}'. Available: {available}",
                    )
                ]
            agent = DataAgent(workspace=ws)
            result = agent.run_recipe(recipe)
            return [
                TextContent(
                    type="text",
                    text=json.dumps(result.to_dict(), indent=2, default=str),
                )
            ]

        elif name == "sweet_run_steps":
            from .agents import DataAgent

            steps = arguments["steps"]
            agent = DataAgent(workspace=ws)
            result = agent.run_steps(steps)
            return [
                TextContent(
                    type="text",
                    text=json.dumps(result.to_dict(), indent=2, default=str),
                )
            ]

        elif name == "sweet_list_recipes":
            from .agents import RecipeRegistry

            registry = RecipeRegistry()
            recipes = registry.list()
            return [
                TextContent(
                    type="text",
                    text=json.dumps(recipes, indent=2, default=str),
                )
            ]

        elif name == "sweet_memory_summary":
            from .agents import AgentMemory

            memory = AgentMemory.load()
            return [
                TextContent(
                    type="text",
                    text=json.dumps(memory.summary(), indent=2, default=str),
                )
            ]

        elif name == "sweet_memory_get_preferences":
            from .agents import AgentMemory

            memory = AgentMemory.load()
            return [
                TextContent(
                    type="text",
                    text=json.dumps(memory.preferences, indent=2, default=str),
                )
            ]

        elif name == "sweet_memory_set_preference":
            from .agents import AgentMemory

            memory = AgentMemory.load()
            memory.set_preference(arguments["key"], arguments["value"])
            memory.save()
            return [
                TextContent(
                    type="text",
                    text=f"Preference '{arguments['key']}' set to: {arguments['value']}",
                )
            ]

        elif name == "sweet_memory_add_rule":
            from .agents import AgentMemory

            memory = AgentMemory.load()
            memory.add_rule(arguments["name"], arguments["rule"])
            memory.save()
            return [
                TextContent(
                    type="text",
                    text=f"Domain rule '{arguments['name']}' added.",
                )
            ]

        elif name == "sweet_memory_list_rules":
            from .agents import AgentMemory

            memory = AgentMemory.load()
            return [
                TextContent(
                    type="text",
                    text=json.dumps(memory.list_rules(), indent=2, default=str),
                )
            ]

        elif name == "sweet_memory_suggest_recipe":
            from .agents import AgentMemory

            memory = AgentMemory.load()
            fingerprint = AgentMemory.fingerprint_workspace(ws)
            suggestion = memory.suggest_recipe(fingerprint)
            if suggestion:
                return [
                    TextContent(
                        type="text",
                        text=f"Suggested recipe: {suggestion} (based on past success with similar data)",
                    )
                ]
            return [
                TextContent(
                    type="text",
                    text="No recipe suggestion available — no similar past runs found.",
                )
            ]

        elif name == "sweet_memory_find_similar_runs":
            from .agents import AgentMemory

            memory = AgentMemory.load()
            fingerprint = AgentMemory.fingerprint_workspace(ws)
            threshold = arguments.get("threshold", 0.5)
            limit = arguments.get("limit", 5)
            similar = memory.find_similar_runs(fingerprint, threshold=threshold, limit=limit)
            return [
                TextContent(
                    type="text",
                    text=json.dumps([r.to_dict() for r in similar], indent=2, default=str),
                )
            ]

        elif name == "sweet_run_pipeline":
            from .agents import (
                ExportAgent,
                IngestionAgent,
                Pipeline,
                QualityAgent,
                TransformAgent,
            )

            stages = arguments.get("stages")
            if stages:
                # Custom stage list
                agent_map = {
                    "ingestion": IngestionAgent,
                    "quality": QualityAgent,
                    "transform": TransformAgent,
                    "export": ExportAgent,
                }
                pipeline = Pipeline(workspace=ws)
                for stage_name in stages:
                    agent_cls = agent_map.get(stage_name)
                    if agent_cls is None:
                        return [
                            TextContent(
                                type="text",
                                text=f"Unknown stage: '{stage_name}'. "
                                f"Available: {', '.join(agent_map.keys())}",
                            )
                        ]
                    pipeline.add_stage(stage_name, agent_cls(ws))
            else:
                pipeline = Pipeline.standard(ws)

            result = pipeline.run()
            return [
                TextContent(
                    type="text",
                    text=json.dumps(result.to_dict(), indent=2, default=str),
                )
            ]

        elif name == "sweet_load_url":
            url = arguments["url"]
            ws.load(
                url,
                name=arguments.get("name"),
                format=arguments.get("format"),
                selector=arguments.get("selector", 0),
            )
            info = ws.inspect()
            return [
                TextContent(
                    type="text",
                    text=f"Loaded from URL: {info['name']} "
                    f"({info['n_rows']} rows × {info['n_cols']} cols)",
                )
            ]

        elif name == "sweet_load_database":
            connection = arguments["connection"]
            ws.load(
                connection,
                name=arguments.get("name"),
                query=arguments.get("query"),
                table=arguments.get("table"),
            )
            info = ws.inspect()
            return [
                TextContent(
                    type="text",
                    text=f"Loaded from database: {info['name']} "
                    f"({info['n_rows']} rows × {info['n_cols']} cols)",
                )
            ]

        elif name == "sweet_list_tables":
            from .core.connectors import list_database_tables

            connection = arguments["connection"]
            tables = list_database_tables(connection)
            return [
                TextContent(
                    type="text",
                    text=json.dumps({"tables": tables}, indent=2),
                )
            ]

        elif name == "sweet_to_great_table":
            from .core.gt_export import save_great_table, to_great_table

            dest = arguments.get("dest")
            kwargs = {
                "title": arguments.get("title"),
                "subtitle": arguments.get("subtitle"),
                "rowname_col": arguments.get("rowname_col"),
                "groupname_col": arguments.get("groupname_col"),
                "fmt_currency": arguments.get("fmt_currency"),
                "fmt_number": arguments.get("fmt_number"),
                "fmt_percent": arguments.get("fmt_percent"),
                "striping": arguments.get("striping", False),
                "stylize": arguments.get("stylize"),
            }

            if dest:
                save_great_table(ws.df, dest, **kwargs)
                return [
                    TextContent(type="text", text=f"Great Tables export saved to: {dest}")
                ]
            else:
                gt_obj = to_great_table(ws.df, **kwargs)
                html = gt_obj.as_raw_html()
                return [TextContent(type="text", text=html)]

        elif name == "sweet_generate_pipeline":
            fmt = arguments.get("format", "polars")
            code = ws.generate_pipeline(
                format=fmt,
                source=arguments.get("source"),
                output=arguments.get("output"),
                name=arguments.get("name"),
            )
            return [TextContent(type="text", text=code)]

        elif name == "sweet_commit":
            result = ws.commit(arguments["message"])
            return [TextContent(type="text", text=json.dumps(result, default=str))]

        elif name == "sweet_version_log":
            entries = ws.version_log(
                sheet=arguments.get("sheet"),
                limit=arguments.get("limit"),
            )
            return [TextContent(type="text", text=json.dumps(entries, default=str))]

        elif name == "sweet_checkout":
            ws.checkout(arguments["commit_id"])
            return [
                TextContent(type="text", text=f"Checked out commit: {arguments['commit_id']}")
            ]

        elif name == "sweet_diff":
            target = arguments.get("target")
            key_columns = arguments.get("key_columns")
            result = ws.diff(target, key_columns=key_columns)
            return [TextContent(type="text", text=json.dumps(result, default=str))]

        elif name == "sweet_save_bundle":
            result_path = ws.save(
                arguments["path"],
                description=arguments.get("description", ""),
            )
            return [TextContent(type="text", text=f"Bundle saved: {result_path}")]

        elif name == "sweet_open_bundle":
            global _workspace
            from .core.workspace import Workspace as WS

            _workspace = WS.open(arguments["path"])
            info = _workspace.inspect()
            return [
                TextContent(
                    type="text",
                    text=f"Restored workspace from bundle. "
                    f"Sheets: {', '.join(_workspace.sheet_names)}. "
                    f"Active: {info['name']} ({info['shape'][0]}×{info['shape'][1]})",
                )
            ]

        elif name == "sweet_inspect_bundle":
            from .core.workspace import Workspace as WS

            info = WS.inspect_bundle(arguments["path"])
            return [TextContent(type="text", text=json.dumps(info, default=str))]

        elif name == "sweet_semantic_types":
            min_conf = arguments.get("min_confidence", 0.4)
            results = ws.semantic_types(min_confidence=min_conf)
            return [TextContent(type="text", text=json.dumps(results, default=str))]

        elif name == "sweet_discover_joins":
            min_conf = arguments.get("min_confidence", 0.6)
            min_overlap = arguments.get("min_overlap", 0.3)
            results = ws.discover_joins(min_confidence=min_conf, min_overlap=min_overlap)
            return [TextContent(type="text", text=json.dumps(results, default=str))]

        elif name == "sweet_synthesize":
            rows = arguments.get("rows", 1000)
            seed = arguments.get("seed")
            ws.synthesize(rows=rows, seed=seed)
            info = ws.inspect()
            return [
                TextContent(
                    type="text",
                    text=f"Generated {rows} synthetic rows → sheet '{info['name']}' "
                    f"({info['shape'][0]}×{info['shape'][1]})",
                )
            ]

        elif name == "sweet_impute":
            column = arguments["column"]
            method = arguments.get("method", "median")
            before = ws.df[column].null_count()
            ws.impute(column, method=method)
            after = ws.df[column].null_count()
            return [
                TextContent(
                    type="text",
                    text=f"Imputed '{column}' ({method}): {before} → {after} nulls",
                )
            ]

        elif name == "sweet_augment":
            kind = arguments["kind"]
            ws.augment(kind)
            return [
                TextContent(
                    type="text",
                    text=f"Added '_{kind}' column. Shape: {ws.df.shape[0]}×{ws.df.shape[1]}",
                )
            ]

        elif name == "sweet_load_conventions":
            path = arguments.get("path")
            ws.load_conventions(path)
            return [TextContent(type="text", text="Conventions loaded successfully.")]

        elif name == "sweet_check_conventions":
            violations = ws.check_conventions()
            if not violations:
                return [TextContent(type="text", text="All conventions pass. No violations.")]
            return [TextContent(type="text", text=json.dumps(violations, indent=2))]

        elif name == "sweet_nl_transform":
            text = arguments["text"]
            ws.nl_transform(text)
            info = ws.inspect()
            return [
                TextContent(
                    type="text",
                    text=f"Applied NL transform: {text}\n"
                    f"Result: {info['shape'][0]}×{info['shape'][1]}",
                )
            ]

        elif name == "sweet_nl_translate":
            text = arguments["text"]
            result = ws.nl_translate(text)
            if result is None:
                return [TextContent(type="text", text="Could not translate to a Polars expression.")]
            return [TextContent(type="text", text=json.dumps(result))]

        elif name == "sweet_nl_pipeline":
            text = arguments["text"]
            ws.nl_pipeline(text)
            info = ws.inspect()
            return [
                TextContent(
                    type="text",
                    text=f"Pipeline applied. Result: {info['shape'][0]}×{info['shape'][1]}",
                )
            ]

        elif name == "sweet_explain_anomalies":
            z_threshold = arguments.get("z_threshold", 3.0)
            results = ws.explain_anomalies(z_threshold=z_threshold)
            if not results:
                return [TextContent(type="text", text="No anomalies detected.")]
            return [TextContent(type="text", text=json.dumps(results, indent=2))]

        elif name == "sweet_discover_relationships":
            min_match_rate = arguments.get("min_match_rate", 0.5)
            results = ws.discover_relationships(min_match_rate=min_match_rate)
            if not results:
                return [TextContent(type="text", text="No relationships discovered.")]
            return [TextContent(type="text", text=json.dumps(results, indent=2))]

        elif name == "sweet_auto_join":
            left_sheet = arguments["left_sheet"]
            right_sheet = arguments["right_sheet"]
            join_type = arguments.get("join_type")
            ws.auto_join(left_sheet, right_sheet, join_type=join_type)
            info = ws.inspect()
            return [
                TextContent(
                    type="text",
                    text=f"Auto-joined {left_sheet} + {right_sheet}. "
                    f"Result: {info['shape'][0]}×{info['shape'][1]}",
                )
            ]

        elif name == "sweet_validate_rules":
            rules_data = arguments["rules"]
            result = ws.validate_rules(rules_data)
            return [TextContent(type="text", text=json.dumps(result, indent=2))]

        else:
            return [TextContent(type="text", text=f"Unknown tool: {name}")]

    except Exception as e:
        return [TextContent(type="text", text=f"Error: {type(e).__name__}: {e}")]


async def run_mcp_server():
    """Run the MCP server over stdio."""
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())
