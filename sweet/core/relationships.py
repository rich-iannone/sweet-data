"""Cross-dataset intelligence: relationship discovery and auto-join.

Discovers join keys, foreign-key relationships, and enrichment opportunities
across multiple sheets in a workspace.
"""

from __future__ import annotations

from dataclasses import dataclass

import polars as pl

# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------


@dataclass
class Relationship:
    """A discovered relationship between two columns in different sheets."""

    left_sheet: str
    left_column: str
    right_sheet: str
    right_column: str
    kind: str  # "exact_match", "subset", "partial_match"
    match_rate: float  # 0.0–1.0: fraction of left values found in right
    confidence: float  # overall confidence in the relationship
    description: str

    def to_dict(self) -> dict:
        return {
            "left_sheet": self.left_sheet,
            "left_column": self.left_column,
            "right_sheet": self.right_sheet,
            "right_column": self.right_column,
            "kind": self.kind,
            "match_rate": round(self.match_rate, 4),
            "confidence": round(self.confidence, 4),
            "description": self.description,
        }


@dataclass
class JoinSuggestion:
    """A suggested join operation between two sheets."""

    left_sheet: str
    right_sheet: str
    join_keys: list[tuple[str, str]]  # (left_col, right_col) pairs
    join_type: str  # "inner", "left", "cross"
    confidence: float
    description: str

    def to_dict(self) -> dict:
        return {
            "left_sheet": self.left_sheet,
            "right_sheet": self.right_sheet,
            "join_keys": self.join_keys,
            "join_type": self.join_type,
            "confidence": round(self.confidence, 4),
            "description": self.description,
        }


# ---------------------------------------------------------------------------
# Relationship discovery
# ---------------------------------------------------------------------------


def discover_relationships(
    sheets: dict[str, pl.DataFrame],
    *,
    min_match_rate: float = 0.5,
    sample_size: int = 1000,
) -> list[Relationship]:
    """Discover relationships between columns across sheets.

    Parameters
    ----------
    sheets
        Dict mapping sheet names to DataFrames.
    min_match_rate
        Minimum fraction of left column values that must appear in the right
        column to consider it a relationship.
    sample_size
        Number of values to sample for comparison (for performance on large datasets).

    Returns
    -------
    list[Relationship]
        Discovered relationships sorted by confidence (descending).
    """
    if len(sheets) < 2:
        return []

    relationships: list[Relationship] = []
    sheet_names = list(sheets.keys())

    for i, left_name in enumerate(sheet_names):
        left_df = sheets[left_name]
        for right_name in sheet_names[i + 1 :]:
            right_df = sheets[right_name]
            rels = _compare_sheets(
                left_name,
                left_df,
                right_name,
                right_df,
                min_match_rate=min_match_rate,
                sample_size=sample_size,
            )
            relationships.extend(rels)

    # Sort by confidence descending
    relationships.sort(key=lambda r: r.confidence, reverse=True)
    return relationships


def _compare_sheets(
    left_name: str,
    left_df: pl.DataFrame,
    right_name: str,
    right_df: pl.DataFrame,
    *,
    min_match_rate: float,
    sample_size: int,
) -> list[Relationship]:
    """Compare columns between two sheets for relationships."""
    relationships: list[Relationship] = []

    for left_col in left_df.columns:
        left_dtype = left_df[left_col].dtype
        left_series = left_df[left_col].drop_nulls()

        if len(left_series) == 0:
            continue

        for right_col in right_df.columns:
            right_dtype = right_df[right_col].dtype
            right_series = right_df[right_col].drop_nulls()

            if len(right_series) == 0:
                continue

            # Only compare compatible types
            if not _types_compatible(left_dtype, right_dtype):
                continue

            rel = _check_relationship(
                left_name,
                left_col,
                left_series,
                right_name,
                right_col,
                right_series,
                min_match_rate=min_match_rate,
                sample_size=sample_size,
            )
            if rel is not None:
                relationships.append(rel)

    return relationships


def _types_compatible(left: pl.DataType, right: pl.DataType) -> bool:
    """Check if two Polars types are compatible for relationship discovery."""
    # Exact same type
    if left == right:
        return True
    # Both numeric
    if left.is_numeric() and right.is_numeric():
        return True
    # Both string-like
    if left in (pl.Utf8, pl.Categorical) and right in (pl.Utf8, pl.Categorical):
        return True
    # One numeric, one string — allow for ID columns
    if (left.is_numeric() and right == pl.Utf8) or (left == pl.Utf8 and right.is_numeric()):
        return True
    return False


def _check_relationship(
    left_name: str,
    left_col: str,
    left_series: pl.Series,
    right_name: str,
    right_col: str,
    right_series: pl.Series,
    *,
    min_match_rate: float,
    sample_size: int,
) -> Relationship | None:
    """Check if there's a relationship between two columns."""
    # Cast to strings for comparison if types differ
    left_vals = _to_string_set(left_series, sample_size)
    right_vals = _to_string_set(right_series, sample_size)

    if not left_vals or not right_vals:
        return None

    # Calculate match rate: fraction of left values found in right
    intersection = left_vals & right_vals
    left_match_rate = len(intersection) / len(left_vals)
    right_match_rate = len(intersection) / len(right_vals)

    # Use the higher match rate (check both directions)
    if left_match_rate >= min_match_rate:
        match_rate = left_match_rate
        direction = "left_to_right"
    elif right_match_rate >= min_match_rate:
        match_rate = right_match_rate
        direction = "right_to_left"
        # Swap so the "subset" side is always left
        left_name, right_name = right_name, left_name
        left_col, right_col = right_col, left_col
        left_match_rate, right_match_rate = right_match_rate, left_match_rate
    else:
        return None

    # Determine relationship kind
    if left_match_rate >= 0.95 and right_match_rate >= 0.95:
        kind = "exact_match"
        confidence = min(left_match_rate, right_match_rate)
    elif left_match_rate >= 0.95:
        kind = "subset"  # left is subset of right (FK-like)
        confidence = left_match_rate * 0.9
    else:
        kind = "partial_match"
        confidence = left_match_rate * 0.7

    # Boost confidence for column name similarity
    name_boost = _name_similarity_boost(left_col, right_col)
    confidence = min(1.0, confidence + name_boost)

    # Penalize very high cardinality matches that might be coincidental
    left_unique = len(left_vals)
    right_unique = len(right_vals)
    if left_unique < 5 and right_unique < 5:
        # Very low cardinality — could be coincidental (e.g., both have 1-4 values)
        confidence *= 0.5

    description = _describe_relationship(
        left_name, left_col, right_name, right_col, kind, left_match_rate
    )

    return Relationship(
        left_sheet=left_name,
        left_column=left_col,
        right_sheet=right_name,
        right_column=right_col,
        kind=kind,
        match_rate=left_match_rate,
        confidence=confidence,
        description=description,
    )


def _to_string_set(series: pl.Series, max_size: int) -> set[str]:
    """Convert a series to a set of string values for comparison."""
    if len(series) > max_size:
        series = series.sample(max_size, seed=42)
    return {str(v) for v in series.to_list() if v is not None}


def _name_similarity_boost(left: str, right: str) -> float:
    """Boost confidence if column names are similar."""
    left_norm = left.lower().replace("_", "").replace("-", "")
    right_norm = right.lower().replace("_", "").replace("-", "")

    # Exact name match
    if left_norm == right_norm:
        return 0.2

    # One contains the other
    if left_norm in right_norm or right_norm in left_norm:
        return 0.1

    # Common ID patterns
    id_suffixes = ("id", "key", "ref", "code", "num", "number")
    left_base = left_norm.rstrip("s")  # depluralize
    right_base = right_norm.rstrip("s")

    for suffix in id_suffixes:
        if left_base.endswith(suffix) and right_base.endswith(suffix):
            return 0.1

    return 0.0


def _describe_relationship(
    left_name: str,
    left_col: str,
    right_name: str,
    right_col: str,
    kind: str,
    match_rate: float,
) -> str:
    """Generate a human-readable description of a relationship."""
    if kind == "exact_match":
        return (
            f"{left_name}.{left_col} ↔ {right_name}.{right_col}: "
            f"Exact match ({match_rate:.0%} overlap) — likely the same entity key."
        )
    if kind == "subset":
        return (
            f"{left_name}.{left_col} → {right_name}.{right_col}: "
            f"Foreign key relationship ({match_rate:.0%} of values found in target). "
            f"Join to enrich {left_name} with data from {right_name}."
        )
    return (
        f"{left_name}.{left_col} ~ {right_name}.{right_col}: "
        f"Partial match ({match_rate:.0%} overlap) — possible enrichment opportunity."
    )


# ---------------------------------------------------------------------------
# Join suggestions
# ---------------------------------------------------------------------------


def suggest_joins(
    sheets: dict[str, pl.DataFrame],
    *,
    min_match_rate: float = 0.5,
) -> list[JoinSuggestion]:
    """Suggest join operations based on discovered relationships.

    Parameters
    ----------
    sheets
        Dict mapping sheet names to DataFrames.
    min_match_rate
        Minimum match rate to consider for join suggestions.

    Returns
    -------
    list[JoinSuggestion]
        Suggested joins sorted by confidence (descending).
    """
    relationships = discover_relationships(sheets, min_match_rate=min_match_rate)
    if not relationships:
        return []

    # Group relationships by sheet pairs
    pair_rels: dict[tuple[str, str], list[Relationship]] = {}
    for rel in relationships:
        key = (rel.left_sheet, rel.right_sheet)
        pair_rels.setdefault(key, []).append(rel)

    suggestions: list[JoinSuggestion] = []
    for (left_sheet, right_sheet), rels in pair_rels.items():
        # Pick the best relationship(s) as join keys
        best = sorted(rels, key=lambda r: r.confidence, reverse=True)
        join_keys = [(r.left_column, r.right_column) for r in best[:3]]

        # Determine join type based on match kind
        top_rel = best[0]
        if top_rel.kind == "exact_match":
            join_type = "inner"
        else:
            join_type = "left"

        # Overall confidence from the best key
        confidence = top_rel.confidence

        description = (
            f"Join {left_sheet} with {right_sheet} on "
            + ", ".join(f"{lk}={rk}" for lk, rk in join_keys[:2])
            + f" ({join_type} join, confidence: {confidence:.0%})"
        )

        suggestions.append(
            JoinSuggestion(
                left_sheet=left_sheet,
                right_sheet=right_sheet,
                join_keys=join_keys,
                join_type=join_type,
                confidence=confidence,
                description=description,
            )
        )

    suggestions.sort(key=lambda s: s.confidence, reverse=True)
    return suggestions


# ---------------------------------------------------------------------------
# Auto-join execution
# ---------------------------------------------------------------------------


def auto_join(
    left_df: pl.DataFrame,
    right_df: pl.DataFrame,
    left_name: str = "left",
    right_name: str = "right",
    *,
    min_match_rate: float = 0.5,
    join_type: str | None = None,
) -> tuple[pl.DataFrame, JoinSuggestion | None]:
    """Automatically join two DataFrames by discovering the best join key.

    Parameters
    ----------
    left_df
        Left DataFrame.
    right_df
        Right DataFrame.
    left_name
        Name for the left sheet (for reporting).
    right_name
        Name for the right sheet (for reporting).
    min_match_rate
        Minimum match rate for key discovery.
    join_type
        Override the auto-detected join type ("inner", "left", "cross").

    Returns
    -------
    tuple[pl.DataFrame, JoinSuggestion | None]
        The joined DataFrame and the suggestion used (or None if no key found).

    Raises
    ------
    ValueError
        If no suitable join key could be discovered.
    """
    sheets = {left_name: left_df, right_name: right_df}
    suggestions = suggest_joins(sheets, min_match_rate=min_match_rate)

    if not suggestions:
        raise ValueError(
            f"Could not discover a join key between '{left_name}' and '{right_name}'. "
            f"No columns have sufficient value overlap (min_match_rate={min_match_rate})."
        )

    suggestion = suggestions[0]
    left_col, right_col = suggestion.join_keys[0]
    effective_join_type = join_type or suggestion.join_type

    # Handle type mismatches for join
    left_join = left_df.with_columns(pl.col(left_col).cast(pl.Utf8).alias("__join_key__"))
    right_join = right_df.with_columns(pl.col(right_col).cast(pl.Utf8).alias("__join_key__"))

    # Avoid column name collisions (except join key)
    right_cols_to_keep = [c for c in right_df.columns if c != right_col]
    suffix = f"_{right_name}"

    result = left_join.join(
        right_join.select(["__join_key__"] + right_cols_to_keep),
        on="__join_key__",
        how=effective_join_type,
        suffix=suffix,
    ).drop("__join_key__")

    return result, suggestion
