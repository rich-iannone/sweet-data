"""Workbook and Sheet models for Sweet."""

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


@dataclass
class Sheet:
    """Represents a data stage in a workbook.

    Attributes:
        name: Name of the sheet
        df: Polars DataFrame containing the data
        transform_steps: List of transformations applied to this sheet
        extra_cells: Additional computed cells (e.g., {"profit": "revenue - cost"})
        branches: Dictionary of branched sheets
        parent: Reference to parent sheet (if this is a branch)
    """

    name: str
    df: "pl.DataFrame | None" = None
    transform_steps: list[TransformStep] = field(default_factory=list)
    extra_cells: dict[str, str] = field(default_factory=dict)
    branches: dict[str, "Sheet"] = field(default_factory=dict)
    parent: "Sheet | None" = None

    def __post_init__(self) -> None:
        """Initialize sheet after creation."""
        if pl is None and self.df is not None:
            raise ImportError("Polars is required but not installed")

    @classmethod
    def load_from_file(
        cls, name: str, file_path: str | Path, format: str = "csv"
    ) -> "Sheet":
        """Load a sheet from a file.

        Args:
            name: Name for the sheet
            file_path: Path to the data file
            format: File format ("csv", "parquet", "json", etc.)

        Returns:
            New Sheet instance

        Raises:
            ImportError: If polars is not available
            ValueError: If file format is not supported
        """
        if pl is None:
            raise ImportError("Polars is required but not installed")

        file_path = Path(file_path)
        if not file_path.exists():
            raise FileNotFoundError(f"File not found: {file_path}")

        # Load data based on format
        if format.lower() == "csv":
            df = pl.read_csv(file_path)
        elif format.lower() == "parquet":
            df = pl.read_parquet(file_path)
        elif format.lower() == "json":
            df = pl.read_json(file_path)
        else:
            raise ValueError(f"Unsupported file format: {format}")

        return cls(name=name, df=df)

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

        if self.df is None:
            raise ValueError("Cannot fork sheet with no data")

        # Create new sheet
        new_sheet = Sheet(
            name=name,
            df=self.df.clone(),
            transform_steps=self.transform_steps.copy(),
            extra_cells=self.extra_cells.copy(),
            parent=self,
        )

        # Add to branches
        self.branches[name] = new_sheet
        return new_sheet

    def get_schema(self) -> dict[str, str]:
        """Get the current schema of the sheet.

        Returns:
            Dictionary mapping column names to data types
        """
        if self.df is None:
            return {}
        return {col: str(dtype) for col, dtype in self.df.schema.items()}

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

        file_path = Path(file_path)

        # Save data based on format
        if format.lower() == "csv":
            self.df.write_csv(file_path)
        elif format.lower() == "parquet":
            self.df.write_parquet(file_path)
        elif format.lower() == "json":
            self.df.write_json(file_path)
        else:
            raise ValueError(f"Unsupported file format: {format}")


@dataclass
class Workbook:
    """Top-level container for sheets and database connections.

    Attributes:
        sheets: Dictionary of sheets by name
        connections: Dictionary of database connections (placeholder for now)
        current_sheet_name: Name of the currently active sheet
    """

    sheets: dict[str, Sheet] = field(default_factory=dict)
    connections: dict[str, Any] = field(
        default_factory=dict
    )  # Placeholder for DB connectors
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

    def load_sheet_from_file(
        self, name: str, file_path: str | Path, format: str = "csv"
    ) -> Sheet:
        """Load a sheet from a file and add it to the workbook.

        Args:
            name: Name for the sheet
            file_path: Path to the data file
            format: File format

        Returns:
            New Sheet instance
        """
        sheet = Sheet.load_from_file(name, file_path, format)

        if name in self.sheets:
            raise ValueError(f"Sheet '{name}' already exists")

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
            self.current_sheet_name = (
                next(iter(self.sheets.keys())) if self.sheets else None
            )

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
