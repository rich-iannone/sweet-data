"""Semantic column understanding for Sweet.

Infers semantic types from column names and content, enabling
auto-join discovery and intelligent column matching across sheets.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum

import polars as pl

# ---------------------------------------------------------------------------
# Semantic type definitions
# ---------------------------------------------------------------------------


class SemanticType(str, Enum):
    """High-level semantic categories for columns."""

    IDENTIFIER = "identifier"
    NAME = "name"
    EMAIL = "email"
    PHONE = "phone"
    URL = "url"
    DATE = "date"
    DATETIME = "datetime"
    TIMESTAMP = "timestamp"
    CURRENCY = "currency"
    PERCENTAGE = "percentage"
    QUANTITY = "quantity"
    BOOLEAN = "boolean"
    CATEGORY = "category"
    ADDRESS = "address"
    COUNTRY = "country"
    ZIP_CODE = "zip_code"
    GEO_COORDINATE = "geo_coordinate"
    IP_ADDRESS = "ip_address"
    JSON = "json"
    TEXT = "text"
    NUMERIC = "numeric"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class ColumnSemantic:
    """Semantic type assignment for a single column."""

    column: str
    semantic_type: SemanticType
    confidence: float  # 0.0 – 1.0
    reasoning: str = ""


@dataclass
class JoinSuggestion:
    """A suggested join between two sheets based on semantic matching."""

    left_sheet: str
    left_column: str
    right_sheet: str
    right_column: str
    semantic_type: SemanticType
    confidence: float
    overlap_ratio: float = 0.0  # Fraction of values in common
    description: str = ""


# ---------------------------------------------------------------------------
# Name-based heuristics
# ---------------------------------------------------------------------------

# Patterns: (regex, semantic_type, confidence)
_NAME_PATTERNS: list[tuple[str, SemanticType, float]] = [
    # --- High-specificity patterns first ---
    # Zip/postal (before identifier patterns that match _code)
    (r"(^|_)(zip|zipcode|zip_code|postal|postal_code|postcode)$", SemanticType.ZIP_CODE, 0.9),
    # IP (before address pattern)
    (r"(^|_)(ip|ip_address|ipv4|ipv6|source_ip|dest_ip)$", SemanticType.IP_ADDRESS, 0.9),
    # Geo
    (r"(^|_)(lat|latitude|lng|longitude|lon|geo_lat|geo_lon)$", SemanticType.GEO_COORDINATE, 0.9),
    # Country
    (r"(^|_)(country|country_code|nation)$", SemanticType.COUNTRY, 0.9),
    # Email
    (r"(^|_)e?mail(_address)?$", SemanticType.EMAIL, 0.95),
    # Phone
    (r"(^|_)(phone|tel|telephone|mobile|cell|fax)(_number)?$", SemanticType.PHONE, 0.9),
    # URL
    (r"(^|_)(url|uri|link|href|website|homepage)$", SemanticType.URL, 0.9),
    (r"_url$", SemanticType.URL, 0.85),
    # --- Identifiers ---
    (r"^(id|pk|key)$", SemanticType.IDENTIFIER, 0.9),
    (r"(^|_)(id|pk|key)$", SemanticType.IDENTIFIER, 0.85),
    (r"^(uuid|guid|ref|code|sku|ean|upc|isbn|asin)$", SemanticType.IDENTIFIER, 0.85),
    (r"_(uuid|guid|ref|code|sku|ean|upc|isbn|asin)$", SemanticType.IDENTIFIER, 0.8),
    (r"_id$", SemanticType.IDENTIFIER, 0.85),
    (r"^fk_", SemanticType.IDENTIFIER, 0.85),
    # Names
    (r"(^|_)(first|last|middle|full)_?name", SemanticType.NAME, 0.9),
    (r"^(name|display_name|username|user_name|account_name)$", SemanticType.NAME, 0.85),
    (r"(^|_)(author|owner|creator|assignee|contact)(_name)?$", SemanticType.NAME, 0.75),
    # Date/time
    (r"(^|_)(date|dt)$", SemanticType.DATE, 0.8),
    (r"_(date|dt)$", SemanticType.DATE, 0.8),
    (r"(^|_)(created|updated|modified|deleted|started|ended)(_at|_on|_date)?$", SemanticType.DATETIME, 0.85),
    (r"(^|_)(timestamp|ts)$", SemanticType.TIMESTAMP, 0.9),
    (r"_ts$", SemanticType.TIMESTAMP, 0.8),
    # Currency
    (r"(^|_)(price|cost|amount|revenue|salary|fee|total|subtotal|balance|payment)$", SemanticType.CURRENCY, 0.8),
    (r"_(price|cost|amount|revenue|salary|fee|total)$", SemanticType.CURRENCY, 0.75),
    # Percentage
    (r"(^|_)(pct|percent|percentage|rate|ratio)$", SemanticType.PERCENTAGE, 0.8),
    (r"_(pct|percent|rate)$", SemanticType.PERCENTAGE, 0.8),
    # Quantity
    (r"(^|_)(count|qty|quantity|num|number|n_|total_)$", SemanticType.QUANTITY, 0.7),
    (r"^(n|cnt|num)_", SemanticType.QUANTITY, 0.7),
    # Boolean
    (r"^(is|has|was|can|should|allow|enable|active|flag)_", SemanticType.BOOLEAN, 0.85),
    (r"_(flag|yn|bool|enabled|active)$", SemanticType.BOOLEAN, 0.8),
    # Address (generic — specific patterns above take priority)
    (r"(^|_)(address|street|city|state|province|region)$", SemanticType.ADDRESS, 0.8),
]

# Compile patterns once
_COMPILED_NAME_PATTERNS: list[tuple[re.Pattern[str], SemanticType, float]] = [
    (re.compile(pat, re.IGNORECASE), stype, conf)
    for pat, stype, conf in _NAME_PATTERNS
]


def _infer_from_name(col: str) -> tuple[SemanticType, float] | None:
    """Infer semantic type from column name alone."""
    normalized = col.lower().strip()
    for pattern, stype, conf in _COMPILED_NAME_PATTERNS:
        if pattern.search(normalized):
            return stype, conf
    return None


# ---------------------------------------------------------------------------
# Content-based heuristics
# ---------------------------------------------------------------------------

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_URL_RE = re.compile(r"^https?://", re.IGNORECASE)
_PHONE_RE = re.compile(r"^[\+\d\s\-\(\)\.]{7,20}$")
_IP_RE = re.compile(r"^\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}$")
_UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.IGNORECASE)
_JSON_RE = re.compile(r"^\s*[\{\[]")
_ZIP_RE = re.compile(r"^\d{5}(-\d{4})?$")


def _sample_values(series: pl.Series, n: int = 100) -> list[str]:
    """Get up to n non-null string values from a series."""
    s = series.drop_nulls()
    if s.dtype != pl.Utf8:
        s = s.cast(pl.Utf8)
    if len(s) > n:
        s = s.sample(n, seed=42)
    return s.to_list()


def _match_ratio(values: list[str], pattern: re.Pattern[str]) -> float:
    """Fraction of values matching a regex pattern."""
    if not values:
        return 0.0
    matches = sum(1 for v in values if pattern.match(v))
    return matches / len(values)


def _infer_from_content(series: pl.Series) -> tuple[SemanticType, float] | None:
    """Infer semantic type from column content."""
    if series.is_empty() or series.null_count() == len(series):
        return None

    dtype = series.dtype

    # Polars temporal types → direct mapping
    if dtype in (pl.Date,):
        return SemanticType.DATE, 0.95
    if dtype in (pl.Datetime,):
        return SemanticType.DATETIME, 0.95
    if dtype == pl.Boolean:
        return SemanticType.BOOLEAN, 0.95

    # For string columns, sample and check patterns
    if dtype == pl.Utf8:
        values = _sample_values(series)
        if not values:
            return None

        # Check in order of specificity
        ratio = _match_ratio(values, _UUID_RE)
        if ratio > 0.8:
            return SemanticType.IDENTIFIER, min(ratio, 0.95)

        ratio = _match_ratio(values, _EMAIL_RE)
        if ratio > 0.7:
            return SemanticType.EMAIL, min(ratio, 0.95)

        ratio = _match_ratio(values, _URL_RE)
        if ratio > 0.7:
            return SemanticType.URL, min(ratio, 0.95)

        ratio = _match_ratio(values, _IP_RE)
        if ratio > 0.7:
            return SemanticType.IP_ADDRESS, min(ratio, 0.95)

        ratio = _match_ratio(values, _ZIP_RE)
        if ratio > 0.6:
            return SemanticType.ZIP_CODE, min(ratio, 0.9)

        ratio = _match_ratio(values, _PHONE_RE)
        if ratio > 0.6:
            return SemanticType.PHONE, min(ratio, 0.85)

        ratio = _match_ratio(values, _JSON_RE)
        if ratio > 0.5:
            return SemanticType.JSON, min(ratio, 0.9)

        # Low cardinality string → category
        n_unique = series.n_unique()
        n_total = len(series) - series.null_count()
        if n_total > 10 and n_unique <= min(50, n_total * 0.1):
            return SemanticType.CATEGORY, 0.7

    # Numeric columns: check if looks like boolean (0/1)
    if dtype in (pl.Int8, pl.Int16, pl.Int32, pl.Int64, pl.UInt8, pl.UInt16, pl.UInt32, pl.UInt64):
        non_null = series.drop_nulls()
        unique_vals = non_null.unique().to_list()
        if set(unique_vals) <= {0, 1}:
            return SemanticType.BOOLEAN, 0.8

        # High cardinality integer → likely identifier
        n_unique = non_null.n_unique()
        n_total = len(non_null)
        if n_total > 10 and n_unique / n_total > 0.95:
            return SemanticType.IDENTIFIER, 0.5  # Low confidence, name-based should override

    return None


# ---------------------------------------------------------------------------
# Main inference function
# ---------------------------------------------------------------------------


def infer_semantic_types(df: pl.DataFrame) -> list[ColumnSemantic]:
    """Infer semantic types for all columns in a DataFrame.

    Combines name-based heuristics with content analysis.
    Name-based inference takes priority when confident; content
    analysis refines or overrides when it has stronger signal.
    """
    results: list[ColumnSemantic] = []

    for col in df.columns:
        name_result = _infer_from_name(col)
        content_result = _infer_from_content(df[col])

        if name_result and content_result:
            # Both have opinions — take higher confidence, prefer name on tie
            n_type, n_conf = name_result
            c_type, c_conf = content_result
            if c_conf > n_conf + 0.1:
                # Content is significantly more confident
                results.append(ColumnSemantic(
                    column=col,
                    semantic_type=c_type,
                    confidence=c_conf,
                    reasoning=f"content analysis ({c_conf:.0%}), name suggested {n_type.value}",
                ))
            else:
                results.append(ColumnSemantic(
                    column=col,
                    semantic_type=n_type,
                    confidence=n_conf,
                    reasoning=f"name pattern ({n_conf:.0%})",
                ))
        elif name_result:
            n_type, n_conf = name_result
            results.append(ColumnSemantic(
                column=col,
                semantic_type=n_type,
                confidence=n_conf,
                reasoning=f"name pattern ({n_conf:.0%})",
            ))
        elif content_result:
            c_type, c_conf = content_result
            results.append(ColumnSemantic(
                column=col,
                semantic_type=c_type,
                confidence=c_conf,
                reasoning=f"content analysis ({c_conf:.0%})",
            ))
        else:
            # Fallback: use dtype to assign generic type
            dtype = df[col].dtype
            if dtype in (pl.Float32, pl.Float64, pl.Int8, pl.Int16, pl.Int32, pl.Int64,
                         pl.UInt8, pl.UInt16, pl.UInt32, pl.UInt64):
                results.append(ColumnSemantic(
                    column=col,
                    semantic_type=SemanticType.NUMERIC,
                    confidence=0.4,
                    reasoning="numeric dtype, no specific pattern",
                ))
            elif dtype == pl.Utf8:
                results.append(ColumnSemantic(
                    column=col,
                    semantic_type=SemanticType.TEXT,
                    confidence=0.3,
                    reasoning="string dtype, no specific pattern",
                ))
            else:
                results.append(ColumnSemantic(
                    column=col,
                    semantic_type=SemanticType.UNKNOWN,
                    confidence=0.0,
                    reasoning="no pattern matched",
                ))

    return results


# ---------------------------------------------------------------------------
# Auto-join discovery
# ---------------------------------------------------------------------------


def discover_joins(
    sheets: dict[str, pl.DataFrame],
    *,
    min_confidence: float = 0.6,
    min_overlap: float = 0.3,
) -> list[JoinSuggestion]:
    """Discover potential join relationships across sheets.

    Compares columns with compatible semantic types across sheets and
    measures value overlap to suggest joins.

    Parameters
    ----------
    sheets
        Mapping of sheet name → DataFrame.
    min_confidence
        Minimum semantic confidence for a column to be considered.
    min_overlap
        Minimum fraction of overlapping values to suggest a join.

    Returns
    -------
    list[JoinSuggestion]
        Suggested joins sorted by confidence (highest first).
    """
    if len(sheets) < 2:
        return []

    # Compute semantics for all sheets
    sheet_semantics: dict[str, list[ColumnSemantic]] = {}
    for name, df in sheets.items():
        sheet_semantics[name] = infer_semantic_types(df)

    # Find matching semantic types across sheets
    suggestions: list[JoinSuggestion] = []
    sheet_names = list(sheets.keys())

    # Joinable types (identifiers, categories that match)
    joinable_types = {
        SemanticType.IDENTIFIER,
        SemanticType.EMAIL,
        SemanticType.PHONE,
        SemanticType.ZIP_CODE,
        SemanticType.COUNTRY,
        SemanticType.IP_ADDRESS,
        SemanticType.CATEGORY,
    }

    for i, left_name in enumerate(sheet_names):
        for right_name in sheet_names[i + 1:]:
            left_cols = [
                s for s in sheet_semantics[left_name]
                if s.semantic_type in joinable_types and s.confidence >= min_confidence
            ]
            right_cols = [
                s for s in sheet_semantics[right_name]
                if s.semantic_type in joinable_types and s.confidence >= min_confidence
            ]

            for lc in left_cols:
                for rc in right_cols:
                    if lc.semantic_type != rc.semantic_type:
                        continue

                    # Compute value overlap
                    overlap = _compute_overlap(
                        sheets[left_name][lc.column],
                        sheets[right_name][rc.column],
                    )
                    if overlap < min_overlap:
                        continue

                    # Confidence = min of semantic confidences × overlap
                    conf = min(lc.confidence, rc.confidence) * overlap

                    # Build a natural description
                    desc = (
                        f"{left_name}.{lc.column} → {right_name}.{rc.column} "
                        f"({lc.semantic_type.value}, {overlap:.0%} overlap)"
                    )

                    suggestions.append(JoinSuggestion(
                        left_sheet=left_name,
                        left_column=lc.column,
                        right_sheet=right_name,
                        right_column=rc.column,
                        semantic_type=lc.semantic_type,
                        confidence=conf,
                        overlap_ratio=overlap,
                        description=desc,
                    ))

    # Sort by confidence descending
    suggestions.sort(key=lambda s: s.confidence, reverse=True)
    return suggestions


def _compute_overlap(left: pl.Series, right: pl.Series) -> float:
    """Compute fraction of left values that appear in right (Jaccard-like)."""
    left_set = set(left.drop_nulls().cast(pl.Utf8).to_list())
    right_set = set(right.drop_nulls().cast(pl.Utf8).to_list())

    if not left_set or not right_set:
        return 0.0

    intersection = left_set & right_set
    union = left_set | right_set
    return len(intersection) / len(union) if union else 0.0
