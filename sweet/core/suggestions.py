"""Transform suggestion engine — detect data patterns and suggest fixes.

Analyzes a DataFrame for common patterns and recommends transformations:
- Currency/numeric extraction (e.g., "$1,234" → 1234.0)
- Column merging opportunities (first_name + last_name → full_name)
- Whitespace trimming
- Column name normalization (camelCase → snake_case)
- Constant/empty column removal
- Date string parsing
- Boolean-like string normalization
- Percentage extraction
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

import polars as pl


class SuggestionKind(str, Enum):
    """Categories of transform suggestions."""

    EXTRACT_NUMERIC = "extract_numeric"
    MERGE_COLUMNS = "merge_columns"
    TRIM_WHITESPACE = "trim_whitespace"
    NORMALIZE_NAMES = "normalize_names"
    DROP_CONSTANT = "drop_constant"
    DROP_EMPTY = "drop_empty"
    PARSE_DATE = "parse_date"
    NORMALIZE_BOOLEAN = "normalize_boolean"
    EXTRACT_PERCENT = "extract_percent"
    STRIP_PREFIX_SUFFIX = "strip_prefix_suffix"
    SPLIT_COLUMN = "split_column"


@dataclass
class Suggestion:
    """A single transform suggestion.

    Attributes:
        kind: Category of the suggestion.
        description: Human-readable explanation.
        columns: Columns involved in this suggestion.
        expression: Polars expression string to apply.
        confidence: How confident we are (0.0–1.0).
        priority: Ordering hint (higher = more important).
        metadata: Additional details about the detected pattern.
    """

    kind: SuggestionKind
    description: str
    columns: list[str]
    expression: str
    confidence: float = 0.8
    priority: int = 5
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Convert to a serializable dict."""
        return {
            "kind": self.kind.value,
            "description": self.description,
            "columns": self.columns,
            "expression": self.expression,
            "confidence": self.confidence,
            "priority": self.priority,
            "metadata": self.metadata,
        }


def suggest_transforms(df: pl.DataFrame, *, max_suggestions: int = 20) -> list[Suggestion]:
    """Analyze a DataFrame and return suggested transforms.

    Runs all pattern detectors and returns suggestions sorted by priority.

    Args:
        df: The DataFrame to analyze.
        max_suggestions: Maximum number of suggestions to return.

    Returns:
        List of Suggestion objects, sorted by priority (highest first).
    """
    suggestions: list[Suggestion] = []

    suggestions.extend(_detect_currency_columns(df))
    suggestions.extend(_detect_percent_columns(df))
    suggestions.extend(_detect_whitespace(df))
    suggestions.extend(_detect_name_merge(df))
    suggestions.extend(_detect_column_name_issues(df))
    suggestions.extend(_detect_constant_columns(df))
    suggestions.extend(_detect_empty_columns(df))
    suggestions.extend(_detect_date_strings(df))
    suggestions.extend(_detect_boolean_strings(df))
    suggestions.extend(_detect_splittable_columns(df))

    # Sort by priority descending, then confidence descending
    suggestions.sort(key=lambda s: (-s.priority, -s.confidence))
    return suggestions[:max_suggestions]


# ---------------------------------------------------------------------------
# Pattern detectors
# ---------------------------------------------------------------------------

_CURRENCY_RE = re.compile(r"^[\$€£¥₹]\s*[\d,]+\.?\d*$|^[\d,]+\.?\d*\s*[\$€£¥₹]$")
_PERCENT_RE = re.compile(r"^[\d.]+\s*%$")
_DATE_PATTERNS = [
    (re.compile(r"^\d{4}-\d{2}-\d{2}$"), "%Y-%m-%d"),
    (re.compile(r"^\d{2}/\d{2}/\d{4}$"), "%m/%d/%Y"),
    (re.compile(r"^\d{2}-\d{2}-\d{4}$"), "%m-%d-%Y"),
    (re.compile(r"^\d{4}/\d{2}/\d{2}$"), "%Y/%m/%d"),
]
_BOOL_TRUE = {"true", "yes", "y", "1", "on", "t", "active", "enabled"}
_BOOL_FALSE = {"false", "no", "n", "0", "off", "f", "inactive", "disabled"}


def _sample_non_null(series: pl.Series, n: int = 100) -> list[str]:
    """Get a sample of non-null string values from a series."""
    if series.dtype != pl.Utf8:
        return []
    non_null = series.drop_nulls()
    if non_null.len() == 0:
        return []
    sample = non_null.head(min(n, non_null.len()))
    return sample.to_list()


def _match_ratio(values: list[str], pattern: re.Pattern) -> float:
    """Fraction of values matching a regex pattern."""
    if not values:
        return 0.0
    matches = sum(1 for v in values if pattern.match(v.strip()))
    return matches / len(values)


def _detect_currency_columns(df: pl.DataFrame) -> list[Suggestion]:
    """Detect columns with currency-formatted strings like '$1,234.56'."""
    suggestions = []
    for col in df.columns:
        if df[col].dtype != pl.Utf8:
            continue
        values = _sample_non_null(df[col])
        ratio = _match_ratio(values, _CURRENCY_RE)
        if ratio >= 0.7:
            # Detect which currency symbol
            symbols = [v.strip()[0] for v in values if v.strip() and v.strip()[0] in "$€£¥₹"]
            symbol = symbols[0] if symbols else "$"
            suggestions.append(
                Suggestion(
                    kind=SuggestionKind.EXTRACT_NUMERIC,
                    description=(
                        f"Column '{col}' contains currency values ({symbol}) → extract numeric"
                    ),
                    columns=[col],
                    expression=(
                        f"df.with_columns("
                        f"pl.col('{col}').str.replace_all(r'[{symbol},\\s]', '')"
                        f".cast(pl.Float64).alias('{col}'))"
                    ),
                    confidence=min(ratio, 0.95),
                    priority=8,
                    metadata={"symbol": symbol, "match_ratio": ratio},
                )
            )
    return suggestions


def _detect_percent_columns(df: pl.DataFrame) -> list[Suggestion]:
    """Detect columns with percentage strings like '85.5%'."""
    suggestions = []
    for col in df.columns:
        if df[col].dtype != pl.Utf8:
            continue
        values = _sample_non_null(df[col])
        ratio = _match_ratio(values, _PERCENT_RE)
        if ratio >= 0.7:
            suggestions.append(
                Suggestion(
                    kind=SuggestionKind.EXTRACT_PERCENT,
                    description=f"Column '{col}' contains percentages → extract as float",
                    columns=[col],
                    expression=(
                        f"df.with_columns("
                        f"pl.col('{col}').str.replace('%', '').str.strip_chars()"
                        f".cast(pl.Float64).alias('{col}'))"
                    ),
                    confidence=min(ratio, 0.95),
                    priority=7,
                    metadata={"match_ratio": ratio},
                )
            )
    return suggestions


def _detect_whitespace(df: pl.DataFrame) -> list[Suggestion]:
    """Detect string columns with leading/trailing whitespace."""
    suggestions = []
    cols_with_ws: list[str] = []

    for col in df.columns:
        if df[col].dtype != pl.Utf8:
            continue
        values = _sample_non_null(df[col])
        if not values:
            continue
        ws_count = sum(1 for v in values if v != v.strip())
        if ws_count / len(values) >= 0.1:
            cols_with_ws.append(col)

    if cols_with_ws:
        cols_expr = ", ".join(f"pl.col('{c}').str.strip_chars()" for c in cols_with_ws)
        suggestions.append(
            Suggestion(
                kind=SuggestionKind.TRIM_WHITESPACE,
                description=(
                    f"{len(cols_with_ws)} column(s) have leading/trailing whitespace → trim"
                ),
                columns=cols_with_ws,
                expression=f"df.with_columns({cols_expr})",
                confidence=0.95,
                priority=9,
                metadata={"n_columns": len(cols_with_ws)},
            )
        )
    return suggestions


def _detect_name_merge(df: pl.DataFrame) -> list[Suggestion]:
    """Detect first/last name columns that could be merged."""
    suggestions = []
    col_lower = {c.lower().replace("_", "").replace("-", ""): c for c in df.columns}

    # Common merge patterns
    merge_patterns = [
        (["firstname", "fname", "first"], ["lastname", "lname", "last"], "full_name", " "),
        (["city", "town"], ["state", "province", "region"], "location", ", "),
    ]

    for first_alts, second_alts, merged_name, sep in merge_patterns:
        first_col = None
        second_col = None
        for alt in first_alts:
            if alt in col_lower:
                first_col = col_lower[alt]
                break
        for alt in second_alts:
            if alt in col_lower:
                second_col = col_lower[alt]
                break

        if first_col and second_col:
            suggestions.append(
                Suggestion(
                    kind=SuggestionKind.MERGE_COLUMNS,
                    description=(
                        f"'{first_col}' + '{second_col}' → create '{merged_name}'"
                    ),
                    columns=[first_col, second_col],
                    expression=(
                        f"df.with_columns("
                        f"(pl.col('{first_col}') + pl.lit('{sep}') + pl.col('{second_col}'))"
                        f".alias('{merged_name}'))"
                    ),
                    confidence=0.85,
                    priority=6,
                    metadata={"separator": sep, "output_name": merged_name},
                )
            )
    return suggestions


def _detect_column_name_issues(df: pl.DataFrame) -> list[Suggestion]:
    """Detect column names that aren't snake_case and suggest normalization."""
    non_snake: list[str] = []
    renames: dict[str, str] = {}

    for col in df.columns:
        snake = _to_snake_case(col)
        if snake != col:
            non_snake.append(col)
            renames[col] = snake

    if non_snake and len(non_snake) >= 2:
        rename_pairs = ", ".join(f"'{old}': '{new}'" for old, new in renames.items())
        suggestions = [
            Suggestion(
                kind=SuggestionKind.NORMALIZE_NAMES,
                description=(
                    f"{len(non_snake)} column(s) not in snake_case → normalize names"
                ),
                columns=non_snake,
                expression=f"df.rename({{{rename_pairs}}})",
                confidence=0.75,
                priority=4,
                metadata={"renames": renames},
            )
        ]
        return suggestions
    return []


def _detect_constant_columns(df: pl.DataFrame) -> list[Suggestion]:
    """Detect columns where all values are the same."""
    suggestions = []
    if df.height < 2:
        return suggestions

    for col in df.columns:
        n_unique = df[col].n_unique()
        # A constant column (1 unique value, or 1 value + null)
        if n_unique <= 1 or (n_unique == 2 and df[col].null_count() > 0):
            non_null = df[col].drop_nulls()
            val = non_null[0] if non_null.len() > 0 else None
            suggestions.append(
                Suggestion(
                    kind=SuggestionKind.DROP_CONSTANT,
                    description=f"Column '{col}' is constant (value: {val!r}) → consider dropping",
                    columns=[col],
                    expression=f"df.drop('{col}')",
                    confidence=0.7,
                    priority=3,
                    metadata={"constant_value": str(val)},
                )
            )
    return suggestions


def _detect_empty_columns(df: pl.DataFrame) -> list[Suggestion]:
    """Detect columns that are entirely null."""
    suggestions = []
    for col in df.columns:
        if df[col].null_count() == df.height:
            suggestions.append(
                Suggestion(
                    kind=SuggestionKind.DROP_EMPTY,
                    description=f"Column '{col}' is entirely null → drop",
                    columns=[col],
                    expression=f"df.drop('{col}')",
                    confidence=0.95,
                    priority=9,
                    metadata={},
                )
            )
    return suggestions


def _detect_date_strings(df: pl.DataFrame) -> list[Suggestion]:
    """Detect string columns that look like dates."""
    suggestions = []
    for col in df.columns:
        if df[col].dtype != pl.Utf8:
            continue
        values = _sample_non_null(df[col])
        if not values:
            continue

        for pattern, fmt in _DATE_PATTERNS:
            ratio = _match_ratio(values, pattern)
            if ratio >= 0.8:
                suggestions.append(
                    Suggestion(
                        kind=SuggestionKind.PARSE_DATE,
                        description=(
                            f"Column '{col}' contains date strings ({fmt}) → parse as Date"
                        ),
                        columns=[col],
                        expression=(
                            f"df.with_columns("
                            f"pl.col('{col}').str.to_date('{fmt}').alias('{col}'))"
                        ),
                        confidence=min(ratio, 0.95),
                        priority=7,
                        metadata={"format": fmt, "match_ratio": ratio},
                    )
                )
                break  # First matching format wins
    return suggestions


def _detect_boolean_strings(df: pl.DataFrame) -> list[Suggestion]:
    """Detect string columns with boolean-like values."""
    suggestions = []
    for col in df.columns:
        if df[col].dtype != pl.Utf8:
            continue
        values = _sample_non_null(df[col])
        if not values:
            continue

        lower_vals = {v.strip().lower() for v in values}
        # Check if all values are boolean-like
        if lower_vals and lower_vals <= (_BOOL_TRUE | _BOOL_FALSE):
            true_vals = lower_vals & _BOOL_TRUE
            suggestions.append(
                Suggestion(
                    kind=SuggestionKind.NORMALIZE_BOOLEAN,
                    description=f"Column '{col}' contains boolean-like values → cast to Boolean",
                    columns=[col],
                    expression=(
                        f"df.with_columns("
                        f"pl.col('{col}').str.to_lowercase().str.strip_chars()"
                        f".is_in({sorted(true_vals)}).alias('{col}'))"
                    ),
                    confidence=0.9,
                    priority=6,
                    metadata={"true_values": sorted(true_vals)},
                )
            )
    return suggestions


def _detect_splittable_columns(df: pl.DataFrame) -> list[Suggestion]:
    """Detect columns that consistently contain a separator (e.g., 'city, state')."""
    suggestions = []
    for col in df.columns:
        if df[col].dtype != pl.Utf8:
            continue
        values = _sample_non_null(df[col], n=50)
        if len(values) < 5:
            continue

        # Check for consistent delimiter patterns
        for sep, sep_name in [(" - ", "dash"), (", ", "comma"), (" | ", "pipe")]:
            ratio = sum(1 for v in values if sep in v) / len(values)
            if ratio >= 0.8:
                suggestions.append(
                    Suggestion(
                        kind=SuggestionKind.SPLIT_COLUMN,
                        description=(
                            f"Column '{col}' consistently contains '{sep_name}' "
                            f"separator → consider splitting"
                        ),
                        columns=[col],
                        expression=(
                            f"df.with_columns("
                            f"pl.col('{col}').str.split_exact('{sep.strip()}', 1)"
                            f".struct.rename_fields(['{col}_1', '{col}_2']).alias('_split')"
                            f").unnest('_split')"
                        ),
                        confidence=min(ratio, 0.85),
                        priority=5,
                        metadata={"separator": sep, "match_ratio": ratio},
                    )
                )
                break  # First matching separator wins
    return suggestions


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _to_snake_case(name: str) -> str:
    """Convert a column name to snake_case."""
    # Handle camelCase / PascalCase
    s = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1_\2", name)
    s = re.sub(r"([a-z\d])([A-Z])", r"\1_\2", s)
    # Replace common separators
    s = re.sub(r"[\s\-\.]+", "_", s)
    # Remove non-alphanumeric (except underscore)
    s = re.sub(r"[^\w]", "", s)
    # Collapse multiple underscores
    s = re.sub(r"_+", "_", s)
    return s.lower().strip("_")
