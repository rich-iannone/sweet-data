"""Team conventions for Sweet workspaces.

Defines and validates conventions from a `.sweet/conventions.yaml` file,
enforcing naming rules, type preferences, and data quality thresholds.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import polars as pl

# ---------------------------------------------------------------------------
# Convention data model
# ---------------------------------------------------------------------------


@dataclass
class NamingConventions:
    """Column and sheet naming rules."""

    columns: str = ""  # e.g., "snake_case", "camelCase", "kebab-case"
    sheets: str = ""


@dataclass
class TypeConventions:
    """Preferred Polars dtypes for semantic categories."""

    dates: str = ""  # e.g., "pl.Date"
    money: str = ""  # e.g., "pl.Int64"
    ids: str = ""  # e.g., "pl.Utf8"


@dataclass
class QualityConventions:
    """Data quality thresholds."""

    max_null_pct: float = 100.0  # Maximum null percentage per column
    require_unique: list[str] = field(default_factory=list)
    banned_values: list[str] = field(default_factory=list)


@dataclass
class Conventions:
    """Full team conventions configuration."""

    naming: NamingConventions = field(default_factory=NamingConventions)
    types: TypeConventions = field(default_factory=TypeConventions)
    quality: QualityConventions = field(default_factory=QualityConventions)


@dataclass
class Violation:
    """A single convention violation."""

    rule: str  # e.g., "naming.columns"
    message: str
    column: str = ""
    sheet: str = ""
    severity: str = "warning"  # "warning" or "error"


# ---------------------------------------------------------------------------
# Naming validation helpers
# ---------------------------------------------------------------------------

_SNAKE_CASE_RE = re.compile(r"^[a-z][a-z0-9]*(_[a-z0-9]+)*$")
_CAMEL_CASE_RE = re.compile(r"^[a-z][a-zA-Z0-9]*$")
_PASCAL_CASE_RE = re.compile(r"^[A-Z][a-zA-Z0-9]*$")
_KEBAB_CASE_RE = re.compile(r"^[a-z][a-z0-9]*(-[a-z0-9]+)*$")


def _check_naming(name: str, convention: str) -> bool:
    """Check if a name follows the specified convention."""
    if not convention:
        return True
    if convention == "snake_case":
        return bool(_SNAKE_CASE_RE.match(name))
    elif convention == "camelCase":
        return bool(_CAMEL_CASE_RE.match(name))
    elif convention == "PascalCase":
        return bool(_PASCAL_CASE_RE.match(name))
    elif convention == "kebab-case":
        return bool(_KEBAB_CASE_RE.match(name))
    return True  # Unknown convention → pass


# ---------------------------------------------------------------------------
# Load conventions from YAML
# ---------------------------------------------------------------------------


def load_conventions(path: str | Path) -> Conventions:
    """Load conventions from a YAML file.

    Parameters
    ----------
    path
        Path to the conventions YAML file.

    Returns
    -------
    Conventions
        Parsed conventions object.

    Raises
    ------
    FileNotFoundError
        If the file does not exist.
    """
    from yaml12 import read_yaml

    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"Conventions file not found: {p}")

    data = read_yaml(p)
    if not isinstance(data, dict):
        return Conventions()

    naming_data = data.get("naming", {}) or {}
    types_data = data.get("types", {}) or {}
    quality_data = data.get("quality", {}) or {}

    naming = NamingConventions(
        columns=str(naming_data.get("columns", "")),
        sheets=str(naming_data.get("sheets", "")),
    )

    types = TypeConventions(
        dates=str(types_data.get("dates", "")),
        money=str(types_data.get("money", "")),
        ids=str(types_data.get("ids", "")),
    )

    quality = QualityConventions(
        max_null_pct=float(quality_data.get("max_null_pct", 100.0)),
        require_unique=list(quality_data.get("require_unique", [])),
        banned_values=[str(v) for v in quality_data.get("banned_values", [])],
    )

    return Conventions(naming=naming, types=types, quality=quality)


def find_conventions_file(start: str | Path | None = None) -> Path | None:
    """Search for a .sweet/conventions.yaml file.

    Walks up from *start* (defaults to cwd) looking for
    ``.sweet/conventions.yaml``.

    Returns
    -------
    Path | None
        Path to conventions file, or None if not found.
    """
    search = Path(start) if start else Path.cwd()
    for directory in [search, *search.parents]:
        candidate = directory / ".sweet" / "conventions.yaml"
        if candidate.exists():
            return candidate
    return None


# ---------------------------------------------------------------------------
# Validate against conventions
# ---------------------------------------------------------------------------


def validate(
    df: pl.DataFrame,
    conventions: Conventions,
    *,
    sheet_name: str = "",
) -> list[Violation]:
    """Validate a DataFrame against team conventions.

    Parameters
    ----------
    df
        DataFrame to validate.
    conventions
        The conventions to check against.
    sheet_name
        Name of the sheet (for sheet naming validation).

    Returns
    -------
    list[Violation]
        List of violations found. Empty if fully compliant.
    """
    violations: list[Violation] = []

    # --- Naming: sheet ---
    if sheet_name and conventions.naming.sheets:
        if not _check_naming(sheet_name, conventions.naming.sheets):
            violations.append(Violation(
                rule="naming.sheets",
                message=f"Sheet name '{sheet_name}' does not follow {conventions.naming.sheets}",
                sheet=sheet_name,
            ))

    # --- Naming: columns ---
    if conventions.naming.columns:
        for col in df.columns:
            if not _check_naming(col, conventions.naming.columns):
                violations.append(Violation(
                    rule="naming.columns",
                    message=f"Column '{col}' does not follow {conventions.naming.columns}",
                    column=col,
                    sheet=sheet_name,
                ))

    # --- Quality: max_null_pct ---
    if conventions.quality.max_null_pct < 100.0:
        n_rows = len(df)
        if n_rows > 0:
            for col in df.columns:
                null_pct = (df[col].null_count() / n_rows) * 100
                if null_pct > conventions.quality.max_null_pct:
                    violations.append(Violation(
                        rule="quality.max_null_pct",
                        message=(
                            f"Column '{col}' has {null_pct:.1f}% nulls "
                            f"(max: {conventions.quality.max_null_pct}%)"
                        ),
                        column=col,
                        sheet=sheet_name,
                        severity="error",
                    ))

    # --- Quality: require_unique ---
    for col in conventions.quality.require_unique:
        if col in df.columns:
            n_unique = df[col].n_unique()
            n_non_null = len(df) - df[col].null_count()
            if n_unique < n_non_null:
                n_dupes = n_non_null - n_unique
                violations.append(Violation(
                    rule="quality.require_unique",
                    message=f"Column '{col}' has {n_dupes} duplicate value(s)",
                    column=col,
                    sheet=sheet_name,
                    severity="error",
                ))

    # --- Quality: banned_values ---
    if conventions.quality.banned_values:
        banned = set(conventions.quality.banned_values)
        for col in df.columns:
            if df[col].dtype == pl.Utf8:
                values = set(df[col].drop_nulls().to_list())
                found = values & banned
                if found:
                    violations.append(Violation(
                        rule="quality.banned_values",
                        message=(
                            f"Column '{col}' contains banned value(s): "
                            f"{sorted(found)}"
                        ),
                        column=col,
                        sheet=sheet_name,
                    ))

    return violations


def generate_default_yaml() -> str:
    """Generate a default conventions.yaml template.

    Returns
    -------
    str
        YAML content for a starter conventions file.
    """
    return """\
# Sweet Team Conventions
# Place this file at .sweet/conventions.yaml in your project root.
# Sweet will validate data against these rules.

naming:
  columns: snake_case
  sheets: snake_case

types:
  dates: pl.Date
  money: pl.Int64
  ids: pl.Utf8

quality:
  max_null_pct: 5.0
  require_unique: []
  banned_values: ["N/A", "null", "NULL", ""]
"""
