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
    ) -> "Workspace":
        """Load data from a file into a new sheet.

        Args:
            source: Path to the data file.
            name: Name for the sheet. Defaults to the filename stem.
            format: File format ("csv", "parquet", "json"). Auto-detected if None.

        Returns:
            self (for method chaining).

        Raises:
            FileNotFoundError: If the source file does not exist.
            ValueError: If format is unsupported.
        """
        source = Path(source)

        if not source.exists():
            raise FileNotFoundError(f"File not found: {source}")

        if name is None:
            name = source.stem

        if format is None:
            format = self._detect_format(source)

        sheet = self._workbook.load_sheet_from_file(name, source, format)

        self._record_operation(
            kind=OperationKind.LOAD,
            sheet=name,
            metadata={"source": str(source), "format": format},
            output_hash=compute_dataframe_hash(sheet.df) if sheet.df is not None else "",
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

    def export(self, dest: str | Path, *, format: str | None = None) -> "Workspace":
        """Export the active sheet to a file.

        Args:
            dest: Destination file path.
            format: File format. Auto-detected from extension if None.

        Returns:
            self (for method chaining).

        Raises:
            ValueError: If no data to export or unsupported format.
        """
        sheet = self._require_active_sheet()

        if sheet.df is None:
            raise ValueError("No data to export")

        dest = Path(dest)

        if format is None:
            format = self._detect_format(dest)

        sheet.save_to_file(dest, format)

        self._record_operation(
            kind=OperationKind.EXPORT,
            sheet=sheet.name,
            metadata={"dest": str(dest), "format": format},
        )

        return self

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
