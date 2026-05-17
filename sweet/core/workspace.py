"""Workspace: High-level programmatic API for Sweet data operations.

This module provides the `Workspace` class — a standalone engine that can be used
headlessly (without the TUI), by AI agents via MCP, or programmatically in scripts.
It wraps the existing Workbook/Sheet/Transform core with a fluent API and
operation journaling for undo/redo.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any

import polars as pl

from .transforms import TransformStep, compute_dataframe_hash, generate_polars_code
from .workbook import Sheet, Workbook


class OperationKind(str, Enum):
    """Types of operations that can be journaled."""

    LOAD = "load"
    TRANSFORM = "transform"
    SCHEMA_CHANGE = "schema_change"
    BRANCH = "branch"
    SWITCH_SHEET = "switch_sheet"
    ADD_SHEET = "add_sheet"
    REMOVE_SHEET = "remove_sheet"
    EXPORT = "export"
    SYNTHESIZE = "synthesize"
    IMPUTE = "impute"
    AUGMENT = "augment"


@dataclass
class Operation:
    """A single journaled operation with undo support.

    Attributes:
        id: Unique identifier for this operation.
        timestamp: When the operation was performed.
        kind: The type of operation.
        sheet: Name of the sheet this operation targeted.
        expr: Expression string (for transforms).
        metadata: Additional context about the operation.
        input_hash: Hash of the data before the operation.
        output_hash: Hash of the data after the operation.
        snapshot: DataFrame snapshot before the operation (for undo).
    """

    id: str
    timestamp: datetime
    kind: OperationKind
    sheet: str
    expr: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    input_hash: str = ""
    output_hash: str = ""
    snapshot: pl.DataFrame | None = field(default=None, repr=False)


class Workspace:
    """High-level programmatic API for Sweet data operations.

    The Workspace is the primary interface for both human-driven (TUI/CLI) and
    agent-driven (MCP/SDK) data work. It manages sheets, tracks operations
    with full undo/redo, and produces reproducible transformation code.

    Examples:
        >>> ws = Workspace()
        >>> ws.load("sales.csv")
        >>> ws.transform("df.filter(pl.col('revenue') > 1000)")
        >>> ws.branch("high-revenue")
        >>> ws.export("filtered.parquet")
        >>> print(ws.history())
    """

    def __init__(self) -> None:
        self._workbook = Workbook()
        self._journal: list[Operation] = []
        self._redo_stack: list[Operation] = []
        self._version_store: Any = None  # Lazy-loaded VersionStore
        self._pattern_store: Any = None  # Lazy-loaded PatternStore
        self._learning_enabled: bool = True  # Track usage patterns

    # -------------------------------------------------------------------------
    # Properties
    # -------------------------------------------------------------------------

    @property
    def current_sheet_name(self) -> str | None:
        """Name of the active sheet."""
        return self._workbook.current_sheet_name

    @property
    def current_sheet(self) -> Sheet | None:
        """The active Sheet object."""
        return self._workbook.current_sheet

    @property
    def sheet_names(self) -> list[str]:
        """List of all sheet names in the workspace."""
        return self._workbook.get_sheet_names()

    @property
    def df(self) -> pl.DataFrame | None:
        """The active sheet's DataFrame (convenience accessor)."""
        sheet = self.current_sheet
        return sheet.df if sheet else None

    @property
    def shape(self) -> tuple[int, int] | None:
        """Shape (rows, cols) of the active sheet's data."""
        if self.df is not None:
            return self.df.shape
        return None

    @property
    def schema(self) -> dict[str, str]:
        """Schema of the active sheet as {column: dtype}."""
        sheet = self.current_sheet
        return sheet.get_schema() if sheet else {}

    # -------------------------------------------------------------------------
    # Data Loading
    # -------------------------------------------------------------------------

    def load(
        self,
        source: str | Path,
        *,
        name: str | None = None,
        format: str | None = None,
        query: str | None = None,
        table: str | None = None,
        selector: int = 0,
    ) -> "Workspace":
        """Load data from a file, URL, database, or web page into a new sheet.

        Args:
            source: Data source — file path, URL, or database connection string.
            name: Name for the sheet. Auto-derived from source if None.
            format: File format ("csv", "parquet", "json"). Auto-detected if None.
            query: SQL query (for database sources).
            table: Table name (for database sources).
            selector: Table index when source contains multiple tables (web pages).

        Returns:
            self (for method chaining).

        Raises:
            FileNotFoundError: If a local file source does not exist.
            ValueError: If format is unsupported or source type unknown.
            ConnectionError: If a remote source is unreachable.
        """
        from .connectors import detect_source_type, load_source

        source_str = str(source)
        source_type = detect_source_type(source_str)

        if source_type == "file":
            # Preserve original file-loading path for local files
            path = Path(source_str)
            if not path.exists():
                raise FileNotFoundError(f"File not found: {path}")

            if name is None:
                name = path.stem
            if format is None:
                format = self._detect_format(path)

            sheet = self._workbook.load_sheet_from_file(name, path, format)
            self._source_file = str(path)

            self._record_operation(
                kind=OperationKind.LOAD,
                sheet=name,
                metadata={"source": str(path), "format": format},
                output_hash=compute_dataframe_hash(sheet.df) if sheet.df is not None else "",
            )
        else:
            # URL, database, or web table — use connectors module
            df, meta = load_source(
                source_str, name=name, format=format,
                query=query, table=table, selector=selector,
            )

            if name is None:
                # Derive name from source
                if source_type == "url":
                    from urllib.parse import urlparse
                    path_part = urlparse(source_str).path
                    name = Path(path_part).stem or "data"
                elif source_type == "database":
                    name = table or "query_result"
                elif source_type == "web_table":
                    name = f"table_{selector}"
                else:
                    name = "data"

            sheet = self._workbook.add_sheet(name, df)
            self._source_file = source_str

            self._record_operation(
                kind=OperationKind.LOAD,
                sheet=name,
                metadata=meta,
                output_hash=compute_dataframe_hash(df),
            )

        return self

    def load_df(self, df: pl.DataFrame, *, name: str = "data") -> "Workspace":
        """Load a Polars DataFrame directly.

        Args:
            df: Polars DataFrame to load.
            name: Name for the sheet.

        Returns:
            self (for method chaining).
        """
        sheet = self._workbook.add_sheet(name, df)

        self._record_operation(
            kind=OperationKind.LOAD,
            sheet=name,
            metadata={"source": "dataframe", "shape": df.shape},
            output_hash=compute_dataframe_hash(df),
        )

        return self

    # -------------------------------------------------------------------------
    # Transformations
    # -------------------------------------------------------------------------

    def transform(self, expr: str, *, description: str = "") -> "Workspace":
        """Apply a Polars expression to the active sheet.

        The expression receives `df` (the current DataFrame) and `pl` (polars module)
        in its evaluation context.

        Args:
            expr: Python expression that transforms the DataFrame.
                  Example: "df.filter(pl.col('age') > 30)"
            description: Human-readable description of what this transform does.

        Returns:
            self (for method chaining).

        Raises:
            ValueError: If no sheet is active, no data is loaded, or expression is invalid.
        """
        sheet = self._require_active_sheet()

        if sheet.df is None:
            raise ValueError("No data loaded in active sheet")

        input_hash = compute_dataframe_hash(sheet.df)
        snapshot = sheet.df.clone()

        # Apply the expression via the existing Sheet method
        sheet.apply_expr(expr, description)

        output_hash = compute_dataframe_hash(sheet.df) if sheet.df is not None else ""

        self._record_operation(
            kind=OperationKind.TRANSFORM,
            sheet=sheet.name,
            expr=expr,
            metadata={"description": description},
            input_hash=input_hash,
            output_hash=output_hash,
            snapshot=snapshot,
        )

        # Observe pattern for learning
        if self._learning_enabled:
            try:
                schema = {col: str(dtype) for col, dtype in snapshot.schema.items()}
                from .patterns import observe_transform

                observe_transform(
                    self._get_pattern_store(), expr, schema, description=description
                )
            except Exception:
                pass  # Learning is best-effort, never block transforms

        return self

    def query(self, sql: str) -> "Workspace":
        """Run a SQL query against the active sheet's data via DuckDB.

        The active sheet's DataFrame is registered as a table named after the sheet.

        Args:
            sql: SQL query string.

        Returns:
            self (for method chaining).

        Raises:
            ValueError: If no sheet is active or no data is loaded.
            ImportError: If duckdb is not available.
        """
        import duckdb

        sheet = self._require_active_sheet()

        if sheet.df is None:
            raise ValueError("No data loaded in active sheet")

        input_hash = compute_dataframe_hash(sheet.df)
        snapshot = sheet.df.clone()

        # Register the DataFrame and run the query via DuckDB's native Arrow support
        conn = duckdb.connect()
        conn.register(sheet.name, sheet.df.to_arrow())
        result_arrow = conn.execute(sql).fetch_arrow_table()
        conn.close()

        sheet.df = pl.from_arrow(result_arrow)

        # Record as a transform step on the sheet
        step = TransformStep(
            expr=f"-- SQL: {sql}",
            input_hash=input_hash,
            output_schema={col: str(dtype) for col, dtype in sheet.df.schema.items()},
            metadata={"description": f"SQL query: {sql}", "type": "sql"},
        )
        sheet.transform_steps.append(step)

        output_hash = compute_dataframe_hash(sheet.df)

        self._record_operation(
            kind=OperationKind.TRANSFORM,
            sheet=sheet.name,
            expr=sql,
            metadata={"description": f"SQL: {sql}", "type": "sql"},
            input_hash=input_hash,
            output_hash=output_hash,
            snapshot=snapshot,
        )

        return self

    def filter(self, condition: str) -> "Workspace":
        """Filter rows by a condition expression.

        Args:
            condition: Polars filter expression (e.g., "pl.col('age') > 30").

        Returns:
            self (for method chaining).
        """
        return self.transform(
            f"df.filter({condition})",
            description=f"Filter: {condition}",
        )

    def select(self, *columns: str) -> "Workspace":
        """Select specific columns.

        Args:
            columns: Column names to keep.

        Returns:
            self (for method chaining).
        """
        cols_expr = ", ".join(f'"{c}"' for c in columns)
        return self.transform(
            f"df.select([{cols_expr}])",
            description=f"Select columns: {', '.join(columns)}",
        )

    def sort(self, *columns: str, descending: bool = False) -> "Workspace":
        """Sort by one or more columns.

        Args:
            columns: Column names to sort by.
            descending: Sort in descending order.

        Returns:
            self (for method chaining).
        """
        cols_expr = ", ".join(f'"{c}"' for c in columns)
        return self.transform(
            f"df.sort([{cols_expr}], descending={descending})",
            description=f"Sort by: {', '.join(columns)} ({'desc' if descending else 'asc'})",
        )

    # -------------------------------------------------------------------------
    # Branching & Sheets
    # -------------------------------------------------------------------------

    def branch(self, name: str) -> "Workspace":
        """Create a new branch from the active sheet.

        A branch is a copy of the current sheet that can be transformed independently.
        Use this for exploratory analysis without affecting the original data.

        Args:
            name: Name for the new branch/sheet.

        Returns:
            self (for method chaining).

        Raises:
            ValueError: If name already exists or no active sheet.
        """
        self._require_active_sheet()

        self._workbook.branch_sheet(name)
        self._workbook.set_current_sheet(name)

        self._record_operation(
            kind=OperationKind.BRANCH,
            sheet=name,
            metadata={"from_sheet": self._workbook.current_sheet_name},
        )

        return self

    def switch(self, name: str) -> "Workspace":
        """Switch to a different sheet.

        Args:
            name: Name of the sheet to switch to.

        Returns:
            self (for method chaining).

        Raises:
            ValueError: If sheet does not exist.
        """
        self._workbook.set_current_sheet(name)

        self._record_operation(
            kind=OperationKind.SWITCH_SHEET,
            sheet=name,
        )

        return self

    # -------------------------------------------------------------------------
    # Inspection
    # -------------------------------------------------------------------------

    def inspect(self, n_rows: int = 5) -> dict[str, Any]:
        """Inspect the active sheet: schema, shape, and sample rows.

        Args:
            n_rows: Number of sample rows to include.

        Returns:
            Dictionary with keys: name, shape, schema, sample, null_counts.
        """
        sheet = self._require_active_sheet()

        if sheet.df is None:
            return {
                "name": sheet.name,
                "shape": (0, 0),
                "schema": {},
                "sample": [],
                "null_counts": {},
            }

        sample = sheet.df.head(n_rows)

        return {
            "name": sheet.name,
            "shape": sheet.df.shape,
            "schema": {col: str(dtype) for col, dtype in sheet.df.schema.items()},
            "sample": sample.to_dicts(),
            "null_counts": {col: sheet.df[col].null_count() for col in sheet.df.columns},
        }

    def scan(self) -> dict[str, Any]:
        """Deep statistical profile of the active sheet via Pointblank DataScan.

        Returns a per-column summary including types, missingness, uniqueness,
        descriptive statistics (mean, median, std, quartiles, min, max),
        and sample values.

        Returns:
            Dictionary with keys: name, shape, columns (list of per-column dicts).

        Raises:
            ValueError: If no sheet is active or no data loaded.
        """
        import json as json_mod

        from pointblank import DataScan

        sheet = self._require_active_sheet()

        if sheet.df is None:
            raise ValueError("No data loaded in active sheet")

        ds = DataScan(sheet.df, tbl_name=sheet.name)
        raw = json_mod.loads(ds.to_json())

        # Reshape from column-oriented dict to per-column list of dicts
        n_cols = len(raw.get("colname", []))
        columns = []
        for i in range(n_cols):
            col_info: dict[str, Any] = {}
            for key, values in raw.items():
                if key == "icon":
                    continue  # Skip SVG icons
                if isinstance(values, list) and len(values) > i:
                    col_info[key] = values[i]
            columns.append(col_info)

        return {
            "name": sheet.name,
            "shape": sheet.df.shape,
            "columns": columns,
        }

    def validate(
        self,
        *,
        checks: list[dict[str, Any]] | None = None,
        yaml_path: str | None = None,
        thresholds: dict[str, float] | None = None,
        get_extracts: bool = False,
    ) -> dict[str, Any]:
        """Run data validation on the active sheet via Pointblank.

        Accepts either a list of check dictionaries or a path to a YAML
        validation file. If neither is provided, runs a default set of checks
        (non-null for all columns + rows_distinct + rows_complete).

        Args:
            checks: List of validation check dicts. Each dict has:
                - "type": Validation method name (e.g., "col_vals_gt",
                  "col_vals_not_null", "col_vals_between", "col_vals_in_set",
                  "col_vals_regex", "rows_distinct", "rows_complete",
                  "col_schema_match")
                - "column": Column name(s) to check (not required for row-level checks)
                - Additional keys passed as kwargs to the method
            yaml_path: Path to a Pointblank YAML validation file.
            thresholds: Threshold levels as {"warning": float, "error": float,
                "critical": float}. Fractions (0-1) represent percentage of failures.
                Integers > 1 represent absolute failure counts.
            get_extracts: If True, include the failing rows for each step in the
                results (under "extracts" key in each step dict).

        Returns:
            Dictionary with keys: all_passed, n_steps, steps (list of step results).
            When get_extracts=True, each step also has "extracts" (list of row dicts).

        Raises:
            ValueError: If no sheet is active or no data loaded.
        """
        from pointblank import Validate

        sheet = self._require_active_sheet()

        if sheet.df is None:
            raise ValueError("No data loaded in active sheet")

        if yaml_path is not None:
            from pointblank import yaml_interrogate

            v = yaml_interrogate(yaml_path, set_tbl=sheet.df)
        else:
            # Build Validate object with optional thresholds
            validate_kwargs: dict[str, Any] = {}
            if thresholds:
                from pointblank import Thresholds

                validate_kwargs["thresholds"] = Thresholds(
                    warning=thresholds.get("warning"),
                    error=thresholds.get("error"),
                    critical=thresholds.get("critical"),
                )

            v = Validate(sheet.df, **validate_kwargs)

            if checks is None:
                # Default: comprehensive check
                for col_name in sheet.df.columns:
                    v = v.col_vals_not_null(col_name)
                v = v.rows_distinct()
                v = v.rows_complete()
            else:
                for check in checks:
                    method_name = check["type"]
                    column = check.get("column")
                    kwargs = {k: v_val for k, v_val in check.items() if k not in ("type", "column")}

                    # Handle schema check specially
                    if method_name == "col_schema_match" and "schema" in kwargs:
                        from pointblank.schema import Schema as PBSchema

                        schema_def = kwargs.pop("schema")
                        pb_schema = PBSchema(columns=[(c["name"], c["dtype"]) for c in schema_def])
                        v = v.col_schema_match(pb_schema, **kwargs)
                        continue

                    method = getattr(v, method_name, None)
                    if method is None:
                        raise ValueError(f"Unknown validation method: {method_name}")
                    if column is not None:
                        v = method(column, **kwargs)
                    else:
                        v = method(**kwargs)

            v = v.interrogate()

        # Extract results
        steps = []
        for i, step_info in enumerate(v.validation_info, 1):
            step_dict: dict[str, Any] = {
                "step": i,
                "type": step_info.assertion_type,
                "column": step_info.column,
                "n": step_info.n,
                "n_passed": step_info.n_passed,
                "n_failed": step_info.n_failed,
                "f_passed": round(step_info.n_passed / step_info.n, 4) if step_info.n else 1.0,
                "f_failed": round(step_info.n_failed / step_info.n, 4) if step_info.n else 0.0,
                "all_passed": step_info.all_passed,
                "warning": step_info.warning,
                "error": step_info.error,
                "critical": step_info.critical,
            }

            if get_extracts and step_info.n_failed > 0:
                try:
                    extract_df = v.get_data_extracts(i=i, frame=True)
                    # Drop internal row number column and convert to dicts
                    if "_row_num_" in extract_df.columns:
                        extract_df = extract_df.drop("_row_num_")
                    step_dict["extracts"] = extract_df.head(50).to_dicts()
                except Exception:
                    step_dict["extracts"] = []

            steps.append(step_dict)

        return {
            "all_passed": v.all_passed(),
            "n_steps": len(steps),
            "steps": steps,
        }

    def get_sundered_data(self) -> dict[str, pl.DataFrame]:
        """Split the active sheet into passing and failing rows.

        Runs a default validation (non-null + rows_complete) and splits the data
        into rows that pass all checks vs. rows that fail at least one check.
        This is useful for separating clean data from dirty data.

        Returns:
            Dictionary with keys "pass" and "fail", each a Polars DataFrame.

        Raises:
            ValueError: If no sheet is active or no data loaded.
        """
        from pointblank import Validate

        sheet = self._require_active_sheet()

        if sheet.df is None:
            raise ValueError("No data loaded in active sheet")

        v = Validate(sheet.df)
        for col_name in sheet.df.columns:
            v = v.col_vals_not_null(col_name)
        v = v.interrogate()

        pass_df = v.get_sundered_data(type="pass")
        fail_df = v.get_sundered_data(type="fail")

        return {"pass": pass_df, "fail": fail_df}

    # -------------------------------------------------------------------------
    # PII Detection
    # -------------------------------------------------------------------------

    def detect_pii(self) -> dict[str, Any]:
        """Detect columns likely containing Personally Identifiable Information.

        Uses pattern matching on column names and sampled values to flag columns
        that may contain PII (emails, phone numbers, SSNs, credit cards, IP
        addresses, etc.).

        Returns:
            Dictionary with keys: name, pii_columns (list of dicts with column,
            pii_type, confidence, sample_matches).

        Raises:
            ValueError: If no sheet is active or no data loaded.
        """
        import re

        sheet = self._require_active_sheet()

        if sheet.df is None:
            raise ValueError("No data loaded in active sheet")

        # Name-based indicators (high confidence from name alone)
        name_indicators: dict[str, re.Pattern[str]] = {
            "ssn": re.compile(r"(ssn|social.?security)", re.IGNORECASE),
            "credit_card": re.compile(r"(credit.?card|card.?num|cc.?num)", re.IGNORECASE),
            "phone": re.compile(r"(phone|mobile|cell|fax|tel)", re.IGNORECASE),
            "email": re.compile(r"(e.?mail)", re.IGNORECASE),
            "address": re.compile(r"(address|street|city|zip|postal)", re.IGNORECASE),
            "name": re.compile(
                r"(first.?name|last.?name|full.?name|surname|given.?name)", re.IGNORECASE
            ),
            "date_of_birth": re.compile(r"(birth.?date|dob|date.?of.?birth)", re.IGNORECASE),
            "passport": re.compile(r"(passport)", re.IGNORECASE),
            "ip_address": re.compile(r"(ip.?addr|ip$)", re.IGNORECASE),
            "salary": re.compile(r"(salary|income|wage|compensation)", re.IGNORECASE),
        }

        # Value-based patterns (ordered: more specific first)
        value_patterns: dict[str, re.Pattern[str]] = {
            "ssn": re.compile(r"^\d{3}-\d{2}-\d{4}$"),
            "credit_card": re.compile(r"^\d{4}[\s-]?\d{4}[\s-]?\d{4}[\s-]?\d{4}$"),
            "email": re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$"),
            "ip_address": re.compile(r"^\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}$"),
            "phone": re.compile(r"^[\+]?[(]?\d{1,4}[)]?[\s-]?\d{2,4}[\s-]?\d{3,4}[\s-]?\d{0,4}$"),
        }

        pii_columns: list[dict[str, Any]] = []

        for col_name in sheet.df.columns:
            # Check column name
            name_match = None
            for pii_type, pattern in name_indicators.items():
                if pattern.search(col_name):
                    name_match = pii_type
                    break

            # Check values for string columns
            value_match = None
            value_confidence = 0.0
            dtype = sheet.df[col_name].dtype
            if dtype in (pl.Utf8, pl.String):
                non_null = sheet.df[col_name].drop_nulls()
                if non_null.len() > 0:
                    sample_size = min(100, non_null.len())
                    sample_vals = non_null.head(sample_size).to_list()

                    # Patterns are ordered most-specific-first; take first high match
                    for pii_type, pattern in value_patterns.items():
                        matches = sum(1 for v in sample_vals if pattern.match(str(v)))
                        confidence = matches / len(sample_vals)
                        if confidence >= 0.7:
                            value_match = pii_type
                            value_confidence = confidence
                            break

            # Report if either name or value triggered
            if name_match or value_match:
                pii_type = value_match or name_match
                confidence = value_confidence if value_match else 0.8
                pii_columns.append(
                    {
                        "column": col_name,
                        "pii_type": pii_type,
                        "confidence": round(confidence, 3),
                        "detected_by": "value_pattern" if value_match else "column_name",
                    }
                )

        return {
            "name": sheet.name,
            "pii_columns": pii_columns,
            "has_pii": len(pii_columns) > 0,
        }

    # -------------------------------------------------------------------------
    # Relationship / Join-Key Detection
    # -------------------------------------------------------------------------

    def detect_relationships(self) -> dict[str, Any]:
        """Detect potential join keys across sheets in the workspace.

        Analyzes column names, types, and cardinality to find columns that
        likely represent foreign-key relationships between sheets.

        Returns:
            Dictionary with keys: relationships (list of dicts with
            sheet_a, column_a, sheet_b, column_b, relationship_type, confidence).

        Raises:
            ValueError: If fewer than 2 sheets are loaded.
        """
        sheets = self._workbook.get_sheet_names()

        if len(sheets) < 2:
            raise ValueError("Need at least 2 sheets to detect relationships")

        relationships: list[dict[str, Any]] = []

        # Collect column metadata per sheet
        sheet_cols: dict[str, dict[str, dict[str, Any]]] = {}
        for sheet_name in sheets:
            sheet = self._workbook.sheets[sheet_name]
            if sheet.df is None or sheet.df.height == 0:
                continue
            cols_info: dict[str, dict[str, Any]] = {}
            for col_name in sheet.df.columns:
                col = sheet.df[col_name]
                cols_info[col_name] = {
                    "dtype": col.dtype,
                    "n_unique": col.n_unique(),
                    "null_pct": col.null_count() / sheet.df.height,
                    "is_unique": col.n_unique() == sheet.df.height,
                }
            sheet_cols[sheet_name] = cols_info

        sheet_names_list = list(sheet_cols.keys())

        # Compare pairs of sheets
        for i, sheet_a in enumerate(sheet_names_list):
            for sheet_b in sheet_names_list[i + 1 :]:
                cols_a = sheet_cols[sheet_a]
                cols_b = sheet_cols[sheet_b]

                for col_a_name, col_a_info in cols_a.items():
                    for col_b_name, col_b_info in cols_b.items():
                        # Must be same base dtype
                        if col_a_info["dtype"] != col_b_info["dtype"]:
                            continue

                        confidence = 0.0

                        # Name similarity (exact match or common FK patterns)
                        if col_a_name == col_b_name:
                            confidence += 0.4
                        elif col_a_name.endswith("_id") and col_a_name[:-3] in sheet_b.lower():
                            confidence += 0.5
                        elif col_b_name.endswith("_id") and col_b_name[:-3] in sheet_a.lower():
                            confidence += 0.5
                        elif col_a_name in col_b_name or col_b_name in col_a_name:
                            confidence += 0.2

                        if confidence == 0.0:
                            continue

                        # One side is unique (potential PK)
                        if col_a_info["is_unique"] or col_b_info["is_unique"]:
                            confidence += 0.3

                        # Low null percentage
                        if col_a_info["null_pct"] < 0.05 and col_b_info["null_pct"] < 0.05:
                            confidence += 0.1

                        # Value overlap check (sample-based)
                        if confidence >= 0.4:
                            df_a = self._workbook.sheets[sheet_a].df
                            df_b = self._workbook.sheets[sheet_b].df
                            if df_a is not None and df_b is not None:
                                vals_a = set(df_a[col_a_name].drop_nulls().head(200).to_list())
                                vals_b = set(df_b[col_b_name].drop_nulls().head(200).to_list())
                                if vals_a and vals_b:
                                    overlap = len(vals_a & vals_b) / min(len(vals_a), len(vals_b))
                                    confidence += overlap * 0.3

                        if confidence >= 0.5:
                            # Determine relationship type
                            if col_a_info["is_unique"] and col_b_info["is_unique"]:
                                rel_type = "one-to-one"
                            elif col_a_info["is_unique"]:
                                rel_type = "one-to-many"
                            elif col_b_info["is_unique"]:
                                rel_type = "many-to-one"
                            else:
                                rel_type = "many-to-many"

                            relationships.append(
                                {
                                    "sheet_a": sheet_a,
                                    "column_a": col_a_name,
                                    "sheet_b": sheet_b,
                                    "column_b": col_b_name,
                                    "relationship_type": rel_type,
                                    "confidence": round(min(confidence, 1.0), 3),
                                }
                            )

        # Sort by confidence descending
        relationships.sort(key=lambda r: r["confidence"], reverse=True)

        return {"relationships": relationships}

    # -------------------------------------------------------------------------
    # Schema Contracts
    # -------------------------------------------------------------------------

    def infer_contract(self) -> dict[str, Any]:
        """Infer a schema contract for the active sheet.

        Analyzes column types, nullability, uniqueness, and value ranges to
        produce a contract that can be enforced on future data.

        Returns:
            Dictionary describing the inferred contract with keys: name,
            columns (list of column contracts).

        Raises:
            ValueError: If no sheet is active or no data loaded.
        """
        sheet = self._require_active_sheet()

        if sheet.df is None:
            raise ValueError("No data loaded in active sheet")

        df = sheet.df
        columns: list[dict[str, Any]] = []

        for col_name in df.columns:
            col = df[col_name]
            dtype = col.dtype

            contract: dict[str, Any] = {
                "column": col_name,
                "dtype": str(dtype),
                "nullable": col.null_count() > 0,
                "unique": col.n_unique() == df.height,
            }

            # For numeric columns, include range
            if dtype.is_numeric():
                non_null = col.drop_nulls()
                if non_null.len() > 0:
                    contract["min"] = non_null.min()
                    contract["max"] = non_null.max()

            # For string columns, include cardinality info
            if dtype in (pl.Utf8, pl.String):
                n_unique = col.n_unique()
                contract["n_unique"] = n_unique
                contract["is_categorical"] = n_unique <= 20

                # Store allowed values for low-cardinality columns
                if n_unique <= 20:
                    contract["allowed_values"] = sorted(col.drop_nulls().unique().to_list())

            columns.append(contract)

        return {
            "name": sheet.name,
            "n_rows": df.height,
            "columns": columns,
        }

    def enforce_contract(self, contract: dict[str, Any]) -> dict[str, Any]:
        """Enforce a schema contract against the active sheet.

        Checks the current data against a previously inferred (or manually
        defined) contract. Reports violations.

        Args:
            contract: A contract dict (as returned by infer_contract()).

        Returns:
            Dictionary with keys: passed, violations (list of violation dicts).

        Raises:
            ValueError: If no sheet is active or no data loaded.
        """
        sheet = self._require_active_sheet()

        if sheet.df is None:
            raise ValueError("No data loaded in active sheet")

        df = sheet.df
        violations: list[dict[str, Any]] = []

        for col_contract in contract.get("columns", []):
            col_name = col_contract["column"]

            # Check column existence
            if col_name not in df.columns:
                violations.append(
                    {
                        "column": col_name,
                        "violation": "missing_column",
                        "message": f"Column '{col_name}' not found in data",
                    }
                )
                continue

            col = df[col_name]

            # Check dtype
            if str(col.dtype) != col_contract.get("dtype"):
                violations.append(
                    {
                        "column": col_name,
                        "violation": "dtype_mismatch",
                        "expected": col_contract["dtype"],
                        "actual": str(col.dtype),
                    }
                )

            # Check nullability
            if not col_contract.get("nullable", True) and col.null_count() > 0:
                violations.append(
                    {
                        "column": col_name,
                        "violation": "unexpected_nulls",
                        "null_count": col.null_count(),
                    }
                )

            # Check uniqueness
            if col_contract.get("unique") and col.n_unique() != df.height:
                violations.append(
                    {
                        "column": col_name,
                        "violation": "uniqueness_violated",
                        "n_unique": col.n_unique(),
                        "n_rows": df.height,
                    }
                )

            # Check numeric range
            if "min" in col_contract and col.dtype.is_numeric():
                non_null = col.drop_nulls()
                if non_null.len() > 0:
                    actual_min = non_null.min()
                    actual_max = non_null.max()
                    if actual_min < col_contract["min"]:
                        violations.append(
                            {
                                "column": col_name,
                                "violation": "below_minimum",
                                "expected_min": col_contract["min"],
                                "actual_min": actual_min,
                            }
                        )
                    if "max" in col_contract and actual_max > col_contract["max"]:
                        violations.append(
                            {
                                "column": col_name,
                                "violation": "above_maximum",
                                "expected_max": col_contract["max"],
                                "actual_max": actual_max,
                            }
                        )

            # Check allowed values for categorical columns
            if "allowed_values" in col_contract:
                allowed = set(col_contract["allowed_values"])
                actual_vals = set(col.drop_nulls().unique().to_list())
                unexpected = actual_vals - allowed
                if unexpected:
                    violations.append(
                        {
                            "column": col_name,
                            "violation": "unexpected_values",
                            "unexpected": sorted(unexpected)[:20],
                        }
                    )

        # Check for extra columns not in contract
        contract_cols = {c["column"] for c in contract.get("columns", [])}
        extra_cols = set(df.columns) - contract_cols
        if extra_cols:
            violations.append(
                {
                    "column": None,
                    "violation": "extra_columns",
                    "columns": sorted(extra_cols),
                }
            )

        return {
            "passed": len(violations) == 0,
            "n_violations": len(violations),
            "violations": violations,
        }

    # -------------------------------------------------------------------------
    # Suggested Casts
    # -------------------------------------------------------------------------

    def suggest_casts(self) -> list[dict[str, Any]]:
        """Suggest type casts for string columns that contain typed data.

        Wraps detect_types() and returns only actionable cast suggestions
        with the Polars expression needed to apply them.

        Returns:
            List of dicts with keys: column, from_type, to_type, expression,
            confidence.

        Raises:
            ValueError: If no sheet is active or no data loaded.
        """
        type_info = self.detect_types()

        cast_suggestions: list[dict[str, Any]] = []

        for col_info in type_info["suggestions"]:
            if col_info.get("suggestion") is None:
                continue

            detected = col_info["detected_type"]
            col_name = col_info["column"]

            # Build the Polars expression for the cast
            expr_map = {
                "iso_date": f"pl.col('{col_name}').str.to_date('%Y-%m-%d')",
                "iso_datetime": f"pl.col('{col_name}').str.to_datetime()",
                "us_date": f"pl.col('{col_name}').str.to_date('%m/%d/%Y')",
                "integer": f"pl.col('{col_name}').cast(pl.Int64)",
                "float": f"pl.col('{col_name}').cast(pl.Float64)",
                "boolean": (
                    f"pl.col('{col_name}').str.to_lowercase()"
                    f".is_in(['true', 'yes', '1']).alias('{col_name}')"
                ),
            }

            expression = expr_map.get(detected)
            if expression:
                cast_suggestions.append(
                    {
                        "column": col_name,
                        "from_type": col_info["current_type"],
                        "to_type": col_info["suggestion"],
                        "expression": expression,
                        "confidence": col_info["confidence"],
                    }
                )

        return cast_suggestions

    def apply_casts(self) -> "Workspace":
        """Apply all high-confidence suggested casts to the active sheet.

        Only applies casts with confidence >= 0.9. Records as a transform
        for undo support.

        Returns:
            self (for method chaining).
        """
        suggestions = self.suggest_casts()

        high_confidence = [s for s in suggestions if s["confidence"] >= 0.9]
        if not high_confidence:
            return self

        # Build a single with_columns expression
        exprs = [s["expression"] for s in high_confidence]
        combined = f"df.with_columns([{', '.join(exprs)}])"

        cols = ", ".join(s["column"] for s in high_confidence)
        return self.transform(combined, description=f"Auto-cast columns: {cols}")

    def suggest(self, *, max_suggestions: int = 20) -> list[dict[str, Any]]:
        """Suggest transforms based on detected data patterns.

        Analyzes the active sheet for common patterns (currency extraction,
        whitespace trimming, date parsing, column merging, etc.) and returns
        actionable suggestions with Polars expressions.

        Args:
            max_suggestions: Maximum number of suggestions to return.

        Returns:
            List of suggestion dicts with keys: kind, description, columns,
            expression, confidence, priority, metadata.

        Raises:
            ValueError: If no active sheet or no data loaded.
        """
        sheet = self._require_active_sheet()
        if sheet.df is None:
            raise ValueError("No data loaded in the active sheet.")

        from .suggestions import suggest_transforms

        suggestions = suggest_transforms(sheet.df, max_suggestions=max_suggestions)
        return [s.to_dict() for s in suggestions]

    def learned_suggestions(self, *, min_count: int | None = None) -> list[dict[str, Any]]:
        """Get suggestions based on learned usage patterns.

        Returns recommendations from patterns that have been observed
        multiple times across previous transforms.

        Args:
            min_count: Minimum observation count to include. Defaults to 3.

        Returns:
            List of suggestion dicts with: kind, trigger, action, count, confidence, source.

        Raises:
            ValueError: If no active sheet or no data loaded.
        """
        sheet = self._require_active_sheet()
        if sheet.df is None:
            raise ValueError("No data loaded in the active sheet.")

        columns = {col: str(dtype) for col, dtype in sheet.df.schema.items()}
        store = self._get_pattern_store()
        return store.suggestions_for(columns, min_count=min_count)

    def patterns_summary(self) -> dict[str, Any]:
        """Get a summary of learned usage patterns.

        Returns:
            Dict with total_patterns, actionable_patterns, kinds breakdown,
            and top patterns.
        """
        store = self._get_pattern_store()
        return store.summary()

    def forget_patterns(self, *, kind: str | None = None, trigger: str | None = None) -> int:
        """Remove learned patterns.

        Args:
            kind: Remove only this kind. None = all.
            trigger: Remove only this trigger. None = all.

        Returns:
            Number of patterns removed.
        """
        store = self._get_pattern_store()
        return store.forget(kind=kind, trigger=trigger)

    # -------------------------------------------------------------------------
    # Bundle (Shareable Workspaces)
    # -------------------------------------------------------------------------

    def save(
        self,
        path: str | Path,
        *,
        description: str = "",
        include_journal: bool = True,
    ) -> Path:
        """Save the workspace as a .sweet bundle file.

        Creates a portable archive containing all sheets, transforms,
        and operation history that can be shared and restored.

        Args:
            path: Output file path (.sweet extension added if missing).
            description: Optional description for the bundle.
            include_journal: Whether to include operation history.

        Returns:
            Path to the created bundle file.

        Raises:
            ValueError: If no sheets or no data loaded.
        """
        from .bundle import save_bundle

        return save_bundle(
            self, path, description=description, include_journal=include_journal
        )

    @classmethod
    def open(cls, path: str | Path) -> "Workspace":
        """Restore a workspace from a .sweet bundle file.

        Args:
            path: Path to the .sweet bundle file.

        Returns:
            A new Workspace instance with restored state.

        Raises:
            ValueError: If the file is not a valid bundle.
            FileNotFoundError: If the file doesn't exist.
        """
        from .bundle import load_bundle
        from .transforms import TransformStep

        bundle = load_bundle(path)
        ws = cls()

        # Restore sheets
        for name, df in bundle["sheets"].items():
            ws.load_df(df, name=name)

            # Restore transform steps
            if name in bundle["transforms"]:
                sheet = ws._workbook.sheets[name]
                for step_data in bundle["transforms"][name]:
                    step = TransformStep(
                        expr=step_data["expr"],
                        input_hash=step_data["input_hash"],
                        output_schema=step_data["output_schema"],
                        metadata=step_data.get("metadata"),
                    )
                    sheet.transform_steps.append(step)

        # Restore current sheet
        current = bundle["manifest"].get("current_sheet")
        if current and current in ws.sheet_names:
            ws._workbook.set_current_sheet(current)

        # Restore source file reference
        source = bundle["manifest"].get("source_file")
        if source:
            ws._source_file = source

        return ws

    @staticmethod
    def inspect_bundle(path: str | Path) -> dict[str, Any]:
        """Inspect a .sweet bundle without fully loading it.

        Args:
            path: Path to the .sweet bundle file.

        Returns:
            Dict with manifest, file size, and data sizes per sheet.
        """
        from .bundle import inspect_bundle

        return inspect_bundle(path)

    # -------------------------------------------------------------------------
    # Semantic Column Understanding
    # -------------------------------------------------------------------------

    def semantic_types(self, *, min_confidence: float = 0.0) -> list[dict[str, Any]]:
        """Infer semantic types for all columns in the active sheet.

        Uses column name patterns and content analysis to determine what
        each column represents (identifier, email, currency, etc.).

        Args:
            min_confidence: Only include results with confidence >= this value.

        Returns:
            List of dicts with keys: column, semantic_type, confidence, reasoning.
        """
        self._require_active_sheet()
        from .semantics import infer_semantic_types

        results = infer_semantic_types(self.df)
        out = [
            {
                "column": r.column,
                "semantic_type": r.semantic_type.value,
                "confidence": r.confidence,
                "reasoning": r.reasoning,
            }
            for r in results
            if r.confidence >= min_confidence
        ]
        return out

    def discover_joins(
        self, *, min_confidence: float = 0.6, min_overlap: float = 0.3
    ) -> list[dict[str, Any]]:
        """Discover potential join relationships across loaded sheets.

        Compares columns with matching semantic types and measures value
        overlap to suggest joins.

        Args:
            min_confidence: Minimum semantic confidence for columns to consider.
            min_overlap: Minimum Jaccard overlap to suggest a join.

        Returns:
            List of dicts with keys: left_sheet, left_column, right_sheet,
            right_column, semantic_type, confidence, overlap_ratio, description.
        """
        from .semantics import discover_joins

        sheets = {
            name: sheet.df
            for name, sheet in self._workbook.sheets.items()
            if sheet.df is not None
        }
        if len(sheets) < 2:
            return []

        results = discover_joins(
            sheets, min_confidence=min_confidence, min_overlap=min_overlap
        )
        return [
            {
                "left_sheet": r.left_sheet,
                "left_column": r.left_column,
                "right_sheet": r.right_sheet,
                "right_column": r.right_column,
                "semantic_type": r.semantic_type.value,
                "confidence": round(r.confidence, 3),
                "overlap_ratio": round(r.overlap_ratio, 3),
                "description": r.description,
            }
            for r in results
        ]

    # -------------------------------------------------------------------------
    # Data Synthesis & Augmentation
    # -------------------------------------------------------------------------

    def synthesize(self, rows: int = 1000, *, seed: int | None = None) -> "Workspace":
        """Generate synthetic data matching the active sheet's schema and profile.

        Creates a new sheet named ``<current>_synthetic`` with realistic
        fake data that mirrors the distributions of the original.

        Args:
            rows: Number of rows to generate.
            seed: Random seed for reproducibility.

        Returns:
            Self (with the new synthetic sheet active).
        """
        self._require_active_sheet()
        from .synthesis import synthesize

        synthetic_df = synthesize(self.df, rows=rows, seed=seed)
        source_name = self.current_sheet_name
        new_name = f"{source_name}_synthetic"
        self.load_df(synthetic_df, name=new_name)
        self.switch(new_name)
        self._record_operation(
            OperationKind.SYNTHESIZE, new_name,
            metadata={"rows": rows, "seed": seed, "source": source_name},
        )
        return self

    def impute(self, column: str, *, method: str = "median") -> "Workspace":
        """Fill null values in a column using the specified strategy.

        Args:
            column: Column name to impute.
            method: One of "mean", "median", "mode", "forward",
                "backward", "zero", "interpolate".

        Returns:
            Self (with imputed data in the active sheet).
        """
        self._require_active_sheet()
        from .synthesis import impute

        new_df = impute(self.df, column, method=method)
        self.current_sheet.df = new_df
        self._record_operation(
            OperationKind.IMPUTE, self.current_sheet_name,
            metadata={"column": column, "method": method},
        )
        return self

    def augment(self, kind: str) -> "Workspace":
        """Add a derived column to the active sheet.

        Args:
            kind: Augmentation type — "fill_rate", "row_hash", or
                "row_number".

        Returns:
            Self (with the new column added).
        """
        self._require_active_sheet()
        from .synthesis import augment_fill_rate, augment_row_hash, augment_row_number

        if kind == "fill_rate":
            self.current_sheet.df = augment_fill_rate(self.df)
        elif kind == "row_hash":
            self.current_sheet.df = augment_row_hash(self.df)
        elif kind == "row_number":
            self.current_sheet.df = augment_row_number(self.df)
        else:
            raise ValueError(f"Unknown augmentation kind '{kind}'. Valid: fill_rate, row_hash, row_number")
        self._record_operation(
            OperationKind.AUGMENT, self.current_sheet_name,
            metadata={"kind": kind},
        )
        return self

    # -------------------------------------------------------------------------
    # Correlation Analysis
    # -------------------------------------------------------------------------

    def correlations(self, *, method: str = "pearson", min_abs: float = 0.0) -> dict[str, Any]:
        """Compute pairwise correlations between numeric columns.

        Args:
            method: Correlation method — "pearson" or "spearman".
            min_abs: Only include pairs with |correlation| >= this value.

        Returns:
            Dictionary with keys: method, pairs (list of dicts with
            column_a, column_b, correlation).

        Raises:
            ValueError: If no sheet is active, no data loaded, fewer than
                2 numeric columns, or invalid method.
        """
        sheet = self._require_active_sheet()

        if sheet.df is None:
            raise ValueError("No data loaded in active sheet")

        if method not in ("pearson", "spearman"):
            raise ValueError(f"Unknown correlation method: {method}. Use 'pearson' or 'spearman'.")

        numeric_cols = [col for col in sheet.df.columns if sheet.df[col].dtype.is_numeric()]

        if len(numeric_cols) < 2:
            raise ValueError("Need at least 2 numeric columns for correlation analysis")

        pairs: list[dict[str, Any]] = []

        for i, col_a in enumerate(numeric_cols):
            for col_b in numeric_cols[i + 1 :]:
                # Drop rows where either column is null
                subset = sheet.df.select([col_a, col_b]).drop_nulls()
                if subset.height < 3:
                    continue

                corr = subset.select(pl.corr(col_a, col_b, method=method)).item()

                if corr is not None and abs(corr) >= min_abs:
                    pairs.append(
                        {
                            "column_a": col_a,
                            "column_b": col_b,
                            "correlation": round(corr, 4),
                        }
                    )

        # Sort by absolute correlation descending
        pairs.sort(key=lambda p: abs(p["correlation"]), reverse=True)

        return {
            "method": method,
            "n_numeric_columns": len(numeric_cols),
            "pairs": pairs,
        }

    def schema_info(self) -> dict[str, Any]:
        """Get schema information via Pointblank Schema inference.

        Returns column names, types, and structural metadata about the
        active sheet's data.

        Returns:
            Dictionary with keys: name, columns (list of {name, dtype}).

        Raises:
            ValueError: If no sheet is active or no data loaded.
        """
        from pointblank.schema import Schema as PBSchema

        sheet = self._require_active_sheet()

        if sheet.df is None:
            raise ValueError("No data loaded in active sheet")

        pb_schema = PBSchema(tbl=sheet.df)
        columns = [
            {"name": col_name, "dtype": col_type} for col_name, col_type in pb_schema.columns
        ]

        return {
            "name": sheet.name,
            "n_rows": sheet.df.height,
            "n_cols": sheet.df.width,
            "columns": columns,
        }

    def detect_types(self) -> dict[str, Any]:
        """Detect semantic types and suggest casts for the active sheet.

        Analyzes string columns for patterns that suggest a more specific type
        (dates, emails, URLs, integers, floats, booleans). Also flags potential
        PII columns.

        Returns:
            Dictionary with keys: name, suggestions (list of per-column dicts with
            column, current_type, detected_type, suggestion, confidence, pii).

        Raises:
            ValueError: If no sheet is active or no data loaded.
        """
        import re

        sheet = self._require_active_sheet()

        if sheet.df is None:
            raise ValueError("No data loaded in active sheet")

        suggestions: list[dict[str, Any]] = []

        # Patterns for semantic type detection
        patterns = {
            "email": re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$"),
            "url": re.compile(r"^https?://\S+$"),
            "ipv4": re.compile(r"^\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}$"),
            "iso_date": re.compile(r"^\d{4}-\d{2}-\d{2}$"),
            "iso_datetime": re.compile(r"^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}"),
            "us_date": re.compile(r"^\d{1,2}/\d{1,2}/\d{4}$"),
            "integer": re.compile(r"^-?\d+$"),
            "float": re.compile(r"^-?\d+\.\d+$"),
            "boolean": re.compile(r"^(true|false|yes|no|1|0)$", re.IGNORECASE),
            "phone": re.compile(r"^[\+]?[\d\s\-\(\)]{7,15}$"),
            "uuid": re.compile(
                r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.IGNORECASE
            ),
        }

        # PII-indicating patterns
        pii_patterns = {"email", "phone", "ipv4"}
        pii_column_names = re.compile(
            r"(ssn|social.?security|passport|credit.?card|phone|email|address|"
            r"zip.?code|postal|birth.?date|dob|salary|income)",
            re.IGNORECASE,
        )

        for col_name in sheet.df.columns:
            dtype = sheet.df[col_name].dtype
            col_suggestion: dict[str, Any] = {
                "column": col_name,
                "current_type": str(dtype),
                "detected_type": None,
                "suggestion": None,
                "confidence": 0.0,
                "pii": False,
            }

            # Check column name for PII indicators
            if pii_column_names.search(col_name):
                col_suggestion["pii"] = True

            # Only analyze string columns for type suggestions
            if dtype == pl.Utf8 or dtype == pl.String:
                # Sample non-null values for pattern matching
                non_null = sheet.df[col_name].drop_nulls()
                if non_null.len() == 0:
                    suggestions.append(col_suggestion)
                    continue

                sample_size = min(100, non_null.len())
                sample_vals = non_null.head(sample_size).to_list()

                # Test each pattern
                best_match = None
                best_confidence = 0.0

                for pattern_name, pattern in patterns.items():
                    matches = sum(1 for v in sample_vals if pattern.match(str(v)))
                    confidence = matches / len(sample_vals)

                    if confidence > best_confidence and confidence >= 0.8:
                        best_match = pattern_name
                        best_confidence = confidence

                if best_match:
                    col_suggestion["detected_type"] = best_match
                    col_suggestion["confidence"] = round(best_confidence, 3)

                    # Map detected type to Polars cast suggestion
                    cast_map = {
                        "iso_date": "Cast to pl.Date (format: '%Y-%m-%d')",
                        "iso_datetime": "Cast to pl.Datetime",
                        "us_date": "Cast to pl.Date (format: '%m/%d/%Y')",
                        "integer": "Cast to pl.Int64",
                        "float": "Cast to pl.Float64",
                        "boolean": "Cast to pl.Boolean",
                    }
                    col_suggestion["suggestion"] = cast_map.get(best_match)

                    # Check for PII in detected patterns
                    if best_match in pii_patterns:
                        col_suggestion["pii"] = True

            suggestions.append(col_suggestion)

        return {
            "name": sheet.name,
            "suggestions": suggestions,
        }

    def detect_outliers(self, *, method: str = "iqr", threshold: float = 1.5) -> dict[str, Any]:
        """Detect outliers in numeric columns of the active sheet.

        Args:
            method: Detection method. "iqr" (interquartile range) or "zscore".
            threshold: For IQR method, the multiplier (default 1.5).
                      For zscore method, the number of standard deviations (default 3.0).

        Returns:
            Dictionary with keys: name, method, threshold, columns (list of
            per-column dicts with column, n_outliers, outlier_bounds, outlier_indices).

        Raises:
            ValueError: If no sheet is active, no data loaded, or invalid method.
        """
        sheet = self._require_active_sheet()

        if sheet.df is None:
            raise ValueError("No data loaded in active sheet")

        if method not in ("iqr", "zscore"):
            raise ValueError(f"Unknown outlier method: {method}. Use 'iqr' or 'zscore'.")

        if method == "zscore" and threshold == 1.5:
            threshold = 3.0  # sensible default for zscore

        results: list[dict[str, Any]] = []

        for col_name in sheet.df.columns:
            dtype = sheet.df[col_name].dtype
            if not dtype.is_numeric():
                continue

            col_data = sheet.df[col_name].drop_nulls()
            if col_data.len() < 4:
                continue

            if method == "iqr":
                q1 = col_data.quantile(0.25)
                q3 = col_data.quantile(0.75)
                iqr = q3 - q1
                lower = q1 - threshold * iqr
                upper = q3 + threshold * iqr
            else:  # zscore
                mean = col_data.mean()
                std = col_data.std()
                if std == 0:
                    continue
                lower = mean - threshold * std
                upper = mean + threshold * std

            # Find outlier indices
            outlier_mask = (sheet.df[col_name] < lower) | (sheet.df[col_name] > upper)
            outlier_indices = [
                i for i, is_outlier in enumerate(outlier_mask.to_list()) if is_outlier
            ]

            results.append(
                {
                    "column": col_name,
                    "n_outliers": len(outlier_indices),
                    "lower_bound": round(lower, 6),
                    "upper_bound": round(upper, 6),
                    "outlier_indices": outlier_indices[:50],  # Cap at 50 indices
                }
            )

        return {
            "name": sheet.name,
            "method": method,
            "threshold": threshold,
            "columns": results,
        }

    def describe(self) -> str:
        """Generate a natural language description of the active sheet's data.

        Uses the scan results and schema to produce a plain-English summary
        suitable for both humans and LLM context. Does not call an external LLM —
        produces a deterministic description from statistics.

        Returns:
            A plain-English description of the dataset.

        Raises:
            ValueError: If no sheet is active or no data loaded.
        """
        sheet = self._require_active_sheet()

        if sheet.df is None:
            raise ValueError("No data loaded in active sheet")

        df = sheet.df
        n_rows, n_cols = df.shape
        parts: list[str] = []

        # Opening summary
        parts.append(f"Dataset '{sheet.name}': {n_rows:,} rows × {n_cols} columns.")

        # Column type breakdown
        type_counts: dict[str, int] = {}
        for dtype in df.dtypes:
            category = (
                "numeric"
                if dtype.is_numeric()
                else ("temporal" if dtype.is_temporal() else "string/other")
            )
            type_counts[category] = type_counts.get(category, 0) + 1

        type_parts = [f"{count} {cat}" for cat, count in type_counts.items()]
        parts.append(f"Column types: {', '.join(type_parts)}.")

        # Completeness
        total_cells = n_rows * n_cols
        total_nulls = sum(df[col].null_count() for col in df.columns)
        if total_nulls == 0:
            parts.append("Data is fully complete (no missing values).")
        else:
            pct_missing = (total_nulls / total_cells) * 100
            null_cols = [col for col in df.columns if df[col].null_count() > 0]
            parts.append(
                f"Missing values: {total_nulls:,} ({pct_missing:.1f}% of all cells) "
                f"across {len(null_cols)} column(s): {', '.join(null_cols[:5])}"
                f"{'...' if len(null_cols) > 5 else ''}."
            )

        # Numeric column summaries
        numeric_cols = [col for col in df.columns if df[col].dtype.is_numeric()]
        if numeric_cols:
            summaries = []
            for col in numeric_cols[:5]:  # Cap at 5
                col_data = df[col].drop_nulls()
                if col_data.len() > 0:
                    min_v = col_data.min()
                    max_v = col_data.max()
                    mean_v = col_data.mean()
                    summaries.append(f"'{col}' ranges {min_v}–{max_v} (mean: {mean_v:.4g})")
            if summaries:
                parts.append("Numeric highlights: " + "; ".join(summaries) + ".")

        # String column cardinality
        str_cols = [col for col in df.columns if df[col].dtype in (pl.Utf8, pl.String)]
        if str_cols:
            low_card = []
            for col in str_cols:
                n_unique = df[col].n_unique()
                if n_unique <= 10:
                    low_card.append(f"'{col}' ({n_unique} unique values)")
            if low_card:
                parts.append(f"Categorical columns: {', '.join(low_card[:5])}.")

        # Duplicate info
        n_dupes = n_rows - df.unique().height
        if n_dupes > 0:
            parts.append(f"Contains {n_dupes:,} duplicate row(s).")

        return " ".join(parts)

    def sample(self, n: int = 10) -> pl.DataFrame | None:
        """Get a random sample of rows from the active sheet.

        Args:
            n: Number of rows to sample.

        Returns:
            Sampled DataFrame, or None if no data.
        """
        if self.df is None:
            return None
        n = min(n, self.df.height)
        return self.df.sample(n) if n > 0 else self.df.head(0)

    # -------------------------------------------------------------------------
    # Export
    # -------------------------------------------------------------------------

    def export(
        self,
        dest: str | Path,
        *,
        format: str | None = None,
        table: str | None = None,
        mode: str = "replace",
    ) -> "Workspace":
        """Export the active sheet to a file, database, or cloud storage.

        Args:
            dest: Destination — file path, cloud URL (s3://, gs://), or
                database connection string (postgresql://, sqlite://, etc.).
            format: File format. Auto-detected from extension if None.
            table: Table name for database destinations.
            mode: Write mode for databases — 'replace', 'append', or 'fail'.

        Returns:
            self (for method chaining).

        Raises:
            ValueError: If no data to export or unsupported format/destination.
        """
        from .exporters import export_to

        sheet = self._require_active_sheet()

        if sheet.df is None:
            raise ValueError("No data to export")

        meta = export_to(
            sheet.df, str(dest), format=format, table=table, mode=mode
        )

        self._record_operation(
            kind=OperationKind.EXPORT,
            sheet=sheet.name,
            metadata=meta,
        )

        return self

    def to_great_table(
        self,
        *,
        title: str | None = None,
        subtitle: str | None = None,
        rowname_col: str | None = None,
        groupname_col: str | None = None,
        fmt_number: list[str] | None = None,
        fmt_currency: list[str] | None = None,
        fmt_percent: list[str] | None = None,
        fmt_integer: list[str] | None = None,
        locale: str | None = None,
        source_note: str | None = None,
        striping: bool = False,
        stylize: int | None = None,
    ) -> "Any":
        """Create a great_tables GT object from the active sheet.

        Args:
            title: Table title.
            subtitle: Table subtitle.
            rowname_col: Column to use as row names.
            groupname_col: Column to use for row grouping.
            fmt_number: Columns to format as numbers.
            fmt_currency: Columns to format as currency.
            fmt_percent: Columns to format as percentages.
            fmt_integer: Columns to format as integers.
            locale: Locale for formatting.
            source_note: Source note at table footer.
            striping: Enable row striping.
            stylize: Built-in style preset (1-6).

        Returns:
            A great_tables GT object.

        Raises:
            ImportError: If great_tables is not installed.
            ValueError: If no active sheet or invalid columns.
        """
        from .gt_export import to_great_table

        sheet = self._require_active_sheet()
        if sheet.df is None:
            raise ValueError("No data to export")

        return to_great_table(
            sheet.df,
            title=title,
            subtitle=subtitle,
            rowname_col=rowname_col,
            groupname_col=groupname_col,
            fmt_number=fmt_number,
            fmt_currency=fmt_currency,
            fmt_percent=fmt_percent,
            fmt_integer=fmt_integer,
            locale=locale,
            source_note=source_note,
            striping=striping,
            stylize=stylize,
        )

    # -------------------------------------------------------------------------
    # History & Code Generation
    # -------------------------------------------------------------------------

    def history(self) -> list[Operation]:
        """Get the full operation journal.

        Returns:
            List of all operations performed in this workspace session.
        """
        return list(self._journal)

    def history_summary(self) -> list[dict[str, Any]]:
        """Get a concise summary of the operation journal (no snapshots).

        Returns:
            List of dicts with operation metadata.
        """
        return [
            {
                "id": op.id,
                "timestamp": op.timestamp.isoformat(),
                "kind": op.kind.value,
                "sheet": op.sheet,
                "expr": op.expr,
                "description": op.metadata.get("description", ""),
            }
            for op in self._journal
        ]

    def generate_code(self) -> str:
        """Generate reproducible Polars code from the active sheet's transforms.

        Returns:
            Python code string that reproduces the transformations.
        """
        sheet = self._require_active_sheet()
        return generate_polars_code(sheet.transform_steps)

    def generate_pipeline(
        self,
        *,
        format: str = "polars",
        source: str | None = None,
        output: str | None = None,
        name: str | None = None,
        description: str | None = None,
    ) -> str:
        """Generate production-ready pipeline code from transform history.

        Args:
            format: Output format — 'polars', 'sql', 'dbt', or 'script'.
            source: Source file path (for loader/comments). Uses loaded file if None.
            output: Output file path (for export line).
            name: Pipeline/model name.
            description: Description for the generated code.

        Returns:
            Generated code string.

        Raises:
            ValueError: If no active sheet or unknown format.
        """
        from .codegen import generate_pipeline

        sheet = self._require_active_sheet()
        # Use the loaded file path as source if not provided
        if source is None:
            source = getattr(self, "_source_file", None)

        return generate_pipeline(
            sheet.transform_steps,
            format=format,
            source=source,
            output=output,
            name=name,
            description=description,
            schema=dict(sheet.df.schema) if sheet.df is not None else None,
        )

    # -------------------------------------------------------------------------
    # Undo / Redo
    # -------------------------------------------------------------------------

    def undo(self) -> "Workspace":
        """Undo the last transform operation.

        Only transform operations with snapshots can be undone.
        Non-transform operations (load, export, switch) are skipped.

        Returns:
            self (for method chaining).

        Raises:
            ValueError: If there is nothing to undo.
        """
        # Find the last undoable operation
        for i in range(len(self._journal) - 1, -1, -1):
            op = self._journal[i]
            if op.kind == OperationKind.TRANSFORM and op.snapshot is not None:
                # Restore the snapshot
                sheet = self._workbook.sheets.get(op.sheet)
                if sheet is not None:
                    sheet.df = op.snapshot
                    # Remove the last transform step
                    if sheet.transform_steps:
                        sheet.transform_steps.pop()

                # Move to redo stack
                self._journal.pop(i)
                self._redo_stack.append(op)
                return self

        raise ValueError("Nothing to undo")

    def redo(self) -> "Workspace":
        """Redo the last undone operation.

        Returns:
            self (for method chaining).

        Raises:
            ValueError: If there is nothing to redo.
        """
        if not self._redo_stack:
            raise ValueError("Nothing to redo")

        op = self._redo_stack.pop()

        # Re-apply the expression
        sheet = self._workbook.sheets.get(op.sheet)
        if sheet is None:
            raise ValueError(f"Sheet '{op.sheet}' no longer exists")

        if sheet.df is None:
            raise ValueError(f"Sheet '{op.sheet}' has no data")

        if op.expr is not None:
            # Re-apply via the sheet method
            description = op.metadata.get("description", "")
            sheet.apply_expr(op.expr, description)

        # Re-add to journal (with updated timestamp)
        op.timestamp = datetime.now(timezone.utc)
        self._journal.append(op)

        return self

    @property
    def can_undo(self) -> bool:
        """Whether there are operations that can be undone."""
        return any(
            op.kind == OperationKind.TRANSFORM and op.snapshot is not None for op in self._journal
        )

    @property
    def can_redo(self) -> bool:
        """Whether there are operations that can be redone."""
        return len(self._redo_stack) > 0

    # -------------------------------------------------------------------------
    # Version Control
    # -------------------------------------------------------------------------

    def _get_version_store(self) -> Any:
        """Lazy-load the VersionStore."""
        if self._version_store is None:
            from .versioning import VersionStore

            self._version_store = VersionStore()
        return self._version_store

    def _get_pattern_store(self) -> Any:
        """Lazy-load the PatternStore."""
        if self._pattern_store is None:
            from .patterns import PatternStore

            self._pattern_store = PatternStore()
        return self._pattern_store

    def commit(self, message: str) -> dict[str, Any]:
        """Create a versioned snapshot of the current sheet's data.

        Args:
            message: Commit message describing this state.

        Returns:
            Dict with commit info (id, message, timestamp, shape).

        Raises:
            ValueError: If no active sheet or no data.
        """
        sheet = self._require_active_sheet()
        if sheet.df is None:
            raise ValueError("No data to commit.")

        store = self._get_version_store()
        c = store.commit(sheet.df, sheet.name, message, metadata={"ops": len(self._journal)})

        return {
            "id": c.id,
            "message": c.message,
            "timestamp": c.timestamp.isoformat(),
            "sheet": c.sheet_name,
            "shape": c.shape,
        }

    def version_log(
        self, *, sheet: str | None = None, limit: int | None = None
    ) -> list[dict[str, Any]]:
        """Get commit history for the workspace.

        Args:
            sheet: Filter to a specific sheet. None = all commits.
            limit: Maximum number of commits to return.

        Returns:
            List of commit summaries (most recent first).
        """
        store = self._get_version_store()
        # Default to current sheet if one exists
        if sheet is None and self.current_sheet_name:
            sheet = self.current_sheet_name

        commits = store.log(sheet, limit=limit)
        return [
            {
                "id": c.id,
                "message": c.message,
                "timestamp": c.timestamp.isoformat(),
                "sheet": c.sheet_name,
                "shape": c.shape,
                "parent_id": c.parent_id,
            }
            for c in commits
        ]

    def checkout(self, commit_id: str) -> "Workspace":
        """Restore the active sheet's data to a previous commit.

        Args:
            commit_id: The commit ID (or unique prefix) to restore.

        Returns:
            self (for method chaining).

        Raises:
            ValueError: If commit not found or no active sheet.
        """
        store = self._get_version_store()
        commit = store.get_commit(commit_id)
        df = commit.snapshot.clone()

        sheet = self._require_active_sheet()

        # Record current state for undo
        self._record_operation(
            kind=OperationKind.TRANSFORM,
            sheet=sheet.name,
            expr=f"checkout('{commit_id}')",
            metadata={"description": f"Checkout commit {commit.id}: {commit.message}"},
            snapshot=sheet.df.clone() if sheet.df is not None else None,
        )

        sheet.df = df
        return self

    def diff(
        self,
        target: str | pl.DataFrame | None = None,
        *,
        key_columns: list[str] | None = None,
    ) -> dict[str, Any]:
        """Diff the current sheet against a commit, another sheet, or a DataFrame.

        Args:
            target: A commit ID, sheet name, or DataFrame to compare against.
                If None, diffs against the most recent commit.
            key_columns: Columns to use as row identity for matching.

        Returns:
            Dict with diff summary and details.

        Raises:
            ValueError: If no active sheet or target not found.
        """
        from .versioning import diff as compute_diff

        sheet = self._require_active_sheet()
        if sheet.df is None:
            raise ValueError("No data in active sheet.")

        current_df = sheet.df

        # Resolve target
        if target is None:
            # Diff against most recent commit for this sheet
            store = self._get_version_store()
            commits = store.log(sheet.name, limit=1)
            if not commits:
                raise ValueError("No commits to diff against. Commit first.")
            target_df = commits[0].snapshot
        elif isinstance(target, pl.DataFrame):
            target_df = target
        else:
            # Try as commit ID first, then as sheet name
            store = self._get_version_store()
            try:
                commit = store.get_commit(target)
                target_df = commit.snapshot
            except ValueError:
                # Try as sheet name
                other_sheet = self._workbook.sheets.get(target)
                if other_sheet is None or other_sheet.df is None:
                    raise ValueError(
                        f"'{target}' is not a valid commit ID or sheet name."
                    )
                target_df = other_sheet.df

        result = compute_diff(target_df, current_df, key_columns=key_columns)

        return {
            "has_changes": result.has_changes,
            "summary": result.summary(),
            "rows_added": result.rows_added,
            "rows_removed": result.rows_removed,
            "rows_modified": result.rows_modified,
            "columns_added": result.columns_added,
            "columns_removed": result.columns_removed,
            "schema_changes": result.schema_changes,
            "shape_left": result.shape_left,
            "shape_right": result.shape_right,
            "sample_changes": result.sample_changes,
        }

    # -------------------------------------------------------------------------
    # Team Conventions
    # -------------------------------------------------------------------------

    def load_conventions(self, path: str | Path | None = None) -> "Workspace":
        """Load team conventions from a YAML file.

        If no path is given, searches for ``.sweet/conventions.yaml``
        walking up from the current directory.

        Args:
            path: Explicit path to conventions YAML, or None to auto-discover.

        Returns:
            Self (conventions are stored for subsequent validation).

        Raises:
            FileNotFoundError: If no conventions file is found.
        """
        from .conventions import find_conventions_file, load_conventions

        if path is None:
            found = find_conventions_file()
            if found is None:
                raise FileNotFoundError(
                    "No .sweet/conventions.yaml found. "
                    "Use 'sweet conventions init' to create one."
                )
            path = found

        self._conventions = load_conventions(path)
        return self

    def check_conventions(self, *, sheet_name: str | None = None) -> list[dict[str, Any]]:
        """Validate the active sheet against loaded conventions.

        Args:
            sheet_name: Override sheet name for validation (defaults to current).

        Returns:
            List of violation dicts with keys: rule, message, column, sheet, severity.

        Raises:
            ValueError: If no conventions have been loaded.
        """
        self._require_active_sheet()

        conventions = getattr(self, "_conventions", None)
        if conventions is None:
            raise ValueError(
                "No conventions loaded. Call load_conventions() first."
            )

        from .conventions import validate

        name = sheet_name or self.current_sheet_name or ""
        violations = validate(self.df, conventions, sheet_name=name)
        return [
            {
                "rule": v.rule,
                "message": v.message,
                "column": v.column,
                "sheet": v.sheet,
                "severity": v.severity,
            }
            for v in violations
        ]

    # -------------------------------------------------------------------------
    # Natural Language Transforms
    # -------------------------------------------------------------------------

    def nl_transform(self, text: str) -> "Workspace":
        """Apply a transformation described in natural language.

        Translates the text into a Polars expression and applies it
        to the active sheet. Raises ValueError if the text cannot be
        translated.

        Args:
            text: Natural language description of the operation.

        Returns:
            Self (with the transform applied).

        Raises:
            ValueError: If the text cannot be translated to an expression.
        """
        self._require_active_sheet()
        from .nl_translate import translate

        result = translate(text)
        if result is None:
            raise ValueError(
                f"Could not translate to a Polars expression: {text!r}"
            )
        self.transform(result.expression, description=text)
        return self

    def nl_translate(self, text: str) -> dict[str, Any] | None:
        """Translate natural language to a Polars expression without applying it.

        Useful for previewing what would be executed.

        Args:
            text: Natural language description of the operation.

        Returns:
            Dict with keys: expression, description, confidence, operation.
            None if no translation is possible.
        """
        from .nl_translate import translate

        result = translate(text)
        if result is None:
            return None
        return {
            "expression": result.expression,
            "description": result.description,
            "confidence": result.confidence,
            "operation": result.operation,
        }

    def nl_pipeline(self, text: str) -> "Workspace":
        """Apply multiple transformations described in natural language.

        Splits on 'then', ';', or numbered steps and applies each in order.

        Args:
            text: Natural language with one or more operations.

        Returns:
            Self (with all translatable transforms applied).

        Raises:
            ValueError: If no operations could be translated.
        """
        self._require_active_sheet()
        from .nl_translate import translate_multi

        results = translate_multi(text)
        if not results:
            raise ValueError(
                f"Could not translate any operations from: {text!r}"
            )
        for r in results:
            self.transform(r.expression, description=r.description)
        return self

    # -------------------------------------------------------------------------
    # Anomaly Explanation
    # -------------------------------------------------------------------------

    def explain_anomalies(
        self,
        *,
        z_threshold: float = 3.0,
        iqr_factor: float = 1.5,
        null_cluster_threshold: float = 0.10,
    ) -> list[dict[str, Any]]:
        """Detect and explain anomalies in the active sheet.

        Returns a list of anomaly dicts with keys: column, kind, severity,
        description, rows, values, stats, explanation.
        """
        self._require_active_sheet()
        from .anomalies import explain_anomalies

        results = explain_anomalies(
            self.df,
            z_threshold=z_threshold,
            iqr_factor=iqr_factor,
            null_cluster_threshold=null_cluster_threshold,
        )
        return [a.to_dict() for a in results]

    # -------------------------------------------------------------------------
    # Cross-Dataset Intelligence
    # -------------------------------------------------------------------------

    def discover_relationships(
        self,
        *,
        min_match_rate: float = 0.5,
    ) -> list[dict[str, Any]]:
        """Discover relationships between columns across all loaded sheets.

        Returns a list of relationship dicts sorted by confidence.
        """
        from .relationships import discover_relationships

        sheets = {name: sheet.df for name, sheet in self._workbook.sheets.items()}
        results = discover_relationships(sheets, min_match_rate=min_match_rate)
        return [r.to_dict() for r in results]

    def suggest_joins(self, *, min_match_rate: float = 0.5) -> list[dict[str, Any]]:
        """Suggest join operations based on discovered relationships.

        Returns a list of join suggestion dicts sorted by confidence.
        """
        from .relationships import suggest_joins

        sheets = {name: sheet.df for name, sheet in self._workbook.sheets.items()}
        results = suggest_joins(sheets, min_match_rate=min_match_rate)
        return [s.to_dict() for s in results]

    def auto_join(
        self,
        left_sheet: str,
        right_sheet: str,
        *,
        min_match_rate: float = 0.5,
        join_type: str | None = None,
        target_name: str | None = None,
    ) -> "Workspace":
        """Automatically join two sheets by discovering the best join key.

        Creates a new sheet with the join result.

        Args:
            left_sheet: Name of the left sheet.
            right_sheet: Name of the right sheet.
            min_match_rate: Minimum overlap fraction for key discovery.
            join_type: Override join type ("inner", "left").
            target_name: Name for the resulting sheet (default: left_right_joined).

        Returns:
            Self (with new joined sheet as active).

        Raises:
            ValueError: If sheets not found or no join key discovered.
        """
        if left_sheet not in self._workbook.sheets:
            raise ValueError(f"Sheet not found: {left_sheet!r}")
        if right_sheet not in self._workbook.sheets:
            raise ValueError(f"Sheet not found: {right_sheet!r}")

        from .relationships import auto_join

        left_df = self._workbook.sheets[left_sheet].df
        right_df = self._workbook.sheets[right_sheet].df

        result_df, suggestion = auto_join(
            left_df,
            right_df,
            left_name=left_sheet,
            right_name=right_sheet,
            min_match_rate=min_match_rate,
            join_type=join_type,
        )

        name = target_name or f"{left_sheet}_{right_sheet}_joined"
        self.load_df(result_df, name=name)
        self.switch(name)
        self._record_operation(
            OperationKind.TRANSFORM,
            name,
            metadata={
                "action": "auto_join",
                "left": left_sheet,
                "right": right_sheet,
                "join_keys": suggestion.join_keys if suggestion else [],
                "join_type": suggestion.join_type if suggestion else "unknown",
            },
        )
        return self

    # -------------------------------------------------------------------------
    # Private Helpers
    # -------------------------------------------------------------------------

    def _require_active_sheet(self) -> Sheet:
        """Get the active sheet or raise ValueError."""
        sheet = self.current_sheet
        if sheet is None:
            raise ValueError("No active sheet. Load data first.")
        return sheet

    def _record_operation(
        self,
        kind: OperationKind,
        sheet: str,
        expr: str | None = None,
        metadata: dict[str, Any] | None = None,
        input_hash: str = "",
        output_hash: str = "",
        snapshot: pl.DataFrame | None = None,
    ) -> Operation:
        """Record an operation in the journal."""
        op = Operation(
            id=str(uuid.uuid4()),
            timestamp=datetime.now(timezone.utc),
            kind=kind,
            sheet=sheet,
            expr=expr,
            metadata=metadata or {},
            input_hash=input_hash,
            output_hash=output_hash,
            snapshot=snapshot,
        )
        self._journal.append(op)
        # Clear redo stack on new operation (standard undo/redo behavior)
        if kind == OperationKind.TRANSFORM:
            self._redo_stack.clear()
        return op

    @staticmethod
    def _detect_format(path: Path) -> str:
        """Detect file format from extension."""
        suffix = path.suffix.lower()
        format_map = {
            ".csv": "csv",
            ".tsv": "csv",
            ".parquet": "parquet",
            ".pq": "parquet",
            ".json": "json",
            ".jsonl": "json",
            ".ndjson": "json",
        }
        format = format_map.get(suffix)
        if format is None:
            raise ValueError(
                f"Cannot detect format from extension '{suffix}'. "
                f"Supported: {', '.join(format_map.keys())}"
            )
        return format
