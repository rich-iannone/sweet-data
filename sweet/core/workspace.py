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
