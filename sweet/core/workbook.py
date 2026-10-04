from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .transforms import (
    TransformStep,
    apply_expr,
    compute_dataframe_hash,
    generate_polars_code,
)

try:
    import polars as pl
except ImportError:
    pl = None


class Sheet:
    """Represents a data stage in a workbook.

    A sheet holds either an in-memory DataFrame or a lazy query plan
    (`LazyFrame`) over its source. Lazy sheets only read what they need; reading
    `df` materializes (and caches) the full result.

    Attributes:
        name: Name of the sheet
        df: The data as a Polars DataFrame (materialized on access for lazy sheets)
        lf: The data as a LazyFrame (the plan for lazy sheets; `df.lazy()` otherwise)
        transform_steps: List of transformations applied to this sheet
        extra_cells: Additional computed cells (e.g., {"profit": "revenue - cost"})
        branches: Dictionary of branched sheets
        parent: Reference to parent sheet (if this is a branch)
    """

    def __init__(
        self,
        name: str,
        df: "pl.DataFrame | None" = None,
        transform_steps: list[TransformStep] | None = None,
        extra_cells: dict[str, str] | None = None,
        branches: "dict[str, Sheet] | None" = None,
        parent: "Sheet | None" = None,
        *,
        lf: "pl.LazyFrame | None" = None,
        base: "pl.DataFrame | pl.LazyFrame | None" = None,
    ) -> None:
        if pl is None and (df is not None or lf is not None):
            raise ImportError("Polars is required but not installed")
        self.name = name
        self._df = df
        self._lf = lf if df is None else None
        self.transform_steps = transform_steps if transform_steps is not None else []
        self.extra_cells = extra_cells if extra_cells is not None else {}
        self.branches = branches if branches is not None else {}
        self.parent = parent
        # The data before any steps: steps can be edited and replayed from it.
        # None means the sheet has changes that aren't steps, so it can't be rebuilt.
        if base is None and not self.transform_steps:
            base = self._lf if self._lf is not None else self._df
        self.base = base

    def __repr__(self) -> str:
        mode = "lazy" if self.is_lazy else "eager"
        return f"Sheet(name={self.name!r}, {mode}, steps={len(self.transform_steps)})"

    @property
    def is_lazy(self) -> bool:
        """Whether the sheet's data is a lazy plan rather than an in-memory frame."""
        return self._lf is not None

    @property
    def df(self) -> "pl.DataFrame | None":
        if self._df is None and self._lf is not None:
            self._df = self._lf.collect(engine="streaming")
        return self._df

    @df.setter
    def df(self, value: "pl.DataFrame | None") -> None:
        self._df = value
        self._lf = None

    @property
    def lf(self) -> "pl.LazyFrame | None":
        if self._lf is not None:
            return self._lf
        return self._df.lazy() if self._df is not None else None

    @lf.setter
    def lf(self, value: "pl.LazyFrame | None") -> None:
        self._lf = value
        self._df = None  # Drop any cached materialization

    @property
    def frame(self) -> "pl.DataFrame | pl.LazyFrame | None":
        """The sheet's data in its native form (LazyFrame if lazy, else DataFrame)."""
        return self._lf if self._lf is not None else self._df

    @frame.setter
    def frame(self, value: "pl.DataFrame | pl.LazyFrame | None") -> None:
        if isinstance(value, pl.LazyFrame):
            self.lf = value
        else:
            self.df = value

    def has_data(self) -> bool:
        return self._df is not None or self._lf is not None

    def materialize(self) -> "pl.DataFrame | None":
        """Collect a lazy sheet into memory (it stays eager afterwards)."""
        df = self.df
        self._lf = None
        return df

    @classmethod
    def load_from_file(cls, name: str, file_path: str | Path, format: str = "csv") -> "Sheet":
        """Load a sheet from a file.

        Args:
            name: Name for the sheet
            file_path: Path to the data file
            format: Registered reader name ("csv", "parquet", "json", "ndjson",
                "ipc", "excel", ...)

        Returns:
            New Sheet instance

        Raises:
            ImportError: If polars is not available
            ValueError: If file format is not supported
        """
        if pl is None:
            raise ImportError("Polars is required but not installed")

        from .io import read_file

        return cls(name=name, df=read_file(file_path, format))

    def apply_expr(self, expr: str, description: str = "") -> None:
        """Apply a transformation expression to this sheet.

        Args:
            expr: Python expression to apply
            description: Optional description of the transformation
        """
        if self.df is None:
            raise ValueError("No data loaded in sheet")

        # Compute input hash
        input_hash = compute_dataframe_hash(self.df)

        # Apply the expression
        new_df = apply_expr(self.df, expr, self.extra_cells)

        # Create transform step
        step = TransformStep(
            expr=expr,
            input_hash=input_hash,
            output_schema={col: str(dtype) for col, dtype in new_df.schema.items()},
            metadata={"description": description} if description else {},
        )

        # Update sheet
        self.df = new_df
        self.transform_steps.append(step)

    def fork(self, name: str) -> "Sheet":
        """Create a new branch from this sheet.

        Args:
            name: Name for the new branch

        Returns:
            New Sheet instance as a branch

        Raises:
            ValueError: If branch name already exists
        """
        if name in self.branches:
            raise ValueError(f"Branch '{name}' already exists")

        if not self.has_data():
            raise ValueError("Cannot fork sheet with no data")

        # Create new sheet (lazy plans are immutable, so they're shared as-is)
        new_sheet = Sheet(
            name=name,
            df=self._df.clone() if self._lf is None else None,
            lf=self._lf,
            transform_steps=self.transform_steps.copy(),
            extra_cells=self.extra_cells.copy(),
            parent=self,
            base=self.base,
        )

        # Add to branches
        self.branches[name] = new_sheet
        return new_sheet

    def get_schema(self) -> dict[str, str]:
        """Get the current schema of the sheet.

        Returns:
            Dictionary mapping column names to data types
        """
        if not self.has_data():
            return {}
        schema = self._lf.collect_schema() if self._lf is not None else self._df.schema
        return {col: str(dtype) for col, dtype in schema.items()}

    def export_polars_code(self) -> str:
        """Export the transformation steps as Polars code.

        Returns:
            Generated Python code string
        """
        return generate_polars_code(self.transform_steps)

    def save_to_file(self, file_path: str | Path, format: str = "parquet") -> None:
        """Save the sheet data to a file.

        Args:
            file_path: Path where to save the file
            format: File format ("csv", "parquet", "json")

        Raises:
            ValueError: If no data to save or unsupported format
            ImportError: If polars is not available
        """
        if self.df is None:
            raise ValueError("No data to save")

        if pl is None:
            raise ImportError("Polars is required but not installed")

        from .io import write_file

        write_file(self.df, file_path, format)


@dataclass
class Workbook:
    """Top-level container for sheets and database connections.

    Attributes:
        sheets: Dictionary of sheets by name
        connections: Dictionary of database connections (placeholder for now)
        current_sheet_name: Name of the currently active sheet
    """

    sheets: dict[str, Sheet] = field(default_factory=dict)
    connections: dict[str, Any] = field(default_factory=dict)  # Placeholder for DB connectors
    current_sheet_name: str | None = None

    @property
    def current_sheet(self) -> Sheet | None:
        """Get the currently active sheet."""
        if self.current_sheet_name is None:
            return None
        return self.sheets.get(self.current_sheet_name)

    def add_sheet(self, name: str, df: "pl.DataFrame | None" = None) -> Sheet:
        """Add a new sheet to the workbook.

        Args:
            name: Name for the sheet
            df: Optional DataFrame to initialize the sheet with

        Returns:
            New Sheet instance

        Raises:
            ValueError: If sheet name already exists
        """
        if name in self.sheets:
            raise ValueError(f"Sheet '{name}' already exists")

        sheet = Sheet(name=name, df=df)
        self.sheets[name] = sheet

        # Set as current if it's the first sheet
        if self.current_sheet_name is None:
            self.current_sheet_name = name

        return sheet

    def load_sheet_from_file(self, name: str, file_path: str | Path, format: str = "csv") -> Sheet:
        """Load a sheet from a file and add it to the workbook.

        Args:
            name: Name for the sheet
            file_path: Path to the data file
            format: File format

        Returns:
            New Sheet instance
        """
        if name in self.sheets:
            raise ValueError(f"Sheet '{name}' already exists")

        sheet = Sheet.load_from_file(name, file_path, format)

        self.sheets[name] = sheet

        # Set as current if it's the first sheet
        if self.current_sheet_name is None:
            self.current_sheet_name = name

        return sheet

    def branch_sheet(self, new_name: str, from_sheet: str | None = None) -> Sheet:
        """Create a branch from an existing sheet.

        Args:
            new_name: Name for the new branch
            from_sheet: Name of sheet to branch from (uses current if None)

        Returns:
            New Sheet instance

        Raises:
            ValueError: If source sheet doesn't exist or branch name conflicts
        """
        if from_sheet is None:
            if self.current_sheet_name is None:
                raise ValueError("No current sheet to branch from")
            from_sheet = self.current_sheet_name

        if from_sheet not in self.sheets:
            raise ValueError(f"Sheet '{from_sheet}' not found")

        if new_name in self.sheets:
            raise ValueError(f"Sheet '{new_name}' already exists")

        # Create branch
        source_sheet = self.sheets[from_sheet]
        branch = source_sheet.fork(new_name)

        # Add to workbook
        self.sheets[new_name] = branch

        return branch

    def set_current_sheet(self, name: str) -> None:
        """Set the current active sheet.

        Args:
            name: Name of the sheet to make current

        Raises:
            ValueError: If sheet doesn't exist
        """
        if name not in self.sheets:
            raise ValueError(f"Sheet '{name}' not found")
        self.current_sheet_name = name

    def remove_sheet(self, name: str) -> None:
        """Remove a sheet from the workbook.

        Args:
            name: Name of the sheet to remove

        Raises:
            ValueError: If sheet doesn't exist
        """
        if name not in self.sheets:
            raise ValueError(f"Sheet '{name}' not found")

        # Remove from parent's branches if it's a branch
        sheet = self.sheets[name]
        if sheet.parent and name in sheet.parent.branches:
            del sheet.parent.branches[name]

        # Remove sheet
        del self.sheets[name]

        # Update current sheet if necessary
        if self.current_sheet_name == name:
            self.current_sheet_name = next(iter(self.sheets.keys())) if self.sheets else None

    def export_polars(self) -> str:
        """Export all transformation steps as Polars code.

        Returns:
            Generated Python code string for all sheets
        """
        if not self.sheets:
            return "# No sheets in workbook"

        code_parts = ["# Sweet Workbook Export", "import polars as pl", ""]

        for sheet_name, sheet in self.sheets.items():
            code_parts.append(f"# Sheet: {sheet_name}")
            if sheet.transform_steps:
                code_parts.append(sheet.export_polars_code())
            else:
                code_parts.append("# No transformations")
            code_parts.append("")

        return "\n".join(code_parts)

    def get_sheet_names(self) -> list[str]:
        """Get list of all sheet names.

        Returns:
            List of sheet names
        """
        return list(self.sheets.keys())
