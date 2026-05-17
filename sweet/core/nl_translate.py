"""Natural language to Polars expression translation.

Translates plain English descriptions of data operations into
executable Polars expressions. Uses pattern-based parsing for
common operations without requiring an external LLM.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# ---------------------------------------------------------------------------
# Result type
# ---------------------------------------------------------------------------


@dataclass
class TranslationResult:
    """Result of translating natural language to a Polars expression."""

    expression: str
    description: str
    confidence: float  # 0.0 – 1.0
    operation: str  # e.g., "filter", "select", "sort", "rename", etc.


# ---------------------------------------------------------------------------
# Pattern registry
# ---------------------------------------------------------------------------

# Each pattern: (compiled_regex, operation, builder_function)
# Builder receives the match object and returns (expression, confidence)

_PATTERNS: list[tuple[re.Pattern[str], str, object]] = []


def _register(pattern: str, operation: str):
    """Decorator to register a translation pattern."""

    def decorator(func):
        _PATTERNS.append((re.compile(pattern, re.IGNORECASE), operation, func))
        return func

    return decorator


# ---------------------------------------------------------------------------
# Comparison helpers
# ---------------------------------------------------------------------------

_COMPARATORS = {
    "greater than": ">",
    "more than": ">",
    "above": ">",
    ">": ">",
    ">=": ">=",
    "greater than or equal to": ">=",
    "at least": ">=",
    "less than": "<",
    "below": "<",
    "under": "<",
    "<": "<",
    "<=": "<=",
    "less than or equal to": "<=",
    "at most": "<=",
    "equal to": "==",
    "equals": "==",
    "is": "==",
    "==": "==",
    "!=": "!=",
    "not equal to": "!=",
    "is not": "!=",
    "not": "!=",
}

_COMP_PATTERN = "|".join(re.escape(k) for k in sorted(_COMPARATORS.keys(), key=len, reverse=True))


def _parse_value(val: str) -> str:
    """Parse a value string into a Polars literal."""
    val = val.strip().strip("'\"")
    # Check if numeric
    try:
        int(val)
        return val
    except ValueError:
        pass
    try:
        float(val)
        return val
    except ValueError:
        pass
    # Check for special keywords
    if val.lower() in ("true", "false"):
        return val.capitalize()
    if val.lower() in ("null", "none"):
        return "None"
    # String literal
    return f'"{val}"'


# ---------------------------------------------------------------------------
# Filter patterns
# ---------------------------------------------------------------------------


@_register(
    r"(?:filter|keep|show|get|where|only)\s+(?:rows?\s+)?(?:where\s+)?(?:the\s+)?['\"]?(\w+)['\"]?"
    r"\s+is\s+not\s+null$",
    "filter",
)
def _filter_not_null(m: re.Match) -> tuple[str, float]:
    col = m.group(1)
    return f"df.filter(pl.col('{col}').is_not_null())", 0.9


@_register(
    r"(?:filter|keep|show|get|where|only)\s+(?:rows?\s+)?(?:where\s+)?(?:the\s+)?['\"]?(\w+)['\"]?"
    r"\s+is\s+null$",
    "filter",
)
def _filter_null(m: re.Match) -> tuple[str, float]:
    col = m.group(1)
    return f"df.filter(pl.col('{col}').is_null())", 0.9


@_register(
    r"(?:filter|keep|show|get|where|only)\s+(?:rows?\s+)?(?:where\s+)?(?:the\s+)?['\"]?(\w+)['\"]?"
    r"\s+(?:column\s+)?(" + _COMP_PATTERN + r")\s+(.+?)$",
    "filter",
)
def _filter_comparison(m: re.Match) -> tuple[str, float]:
    col = m.group(1)
    comp = _COMPARATORS.get(m.group(2).lower(), "==")
    val = _parse_value(m.group(3))
    return f"df.filter(pl.col('{col}') {comp} {val})", 0.9


@_register(
    r"(?:filter|keep|show|get|where|only)\s+(?:rows?\s+)?(?:where\s+)?(?:the\s+)?['\"]?(\w+)['\"]?"
    r"\s+(?:contains?|has|includes?)\s+['\"]?(.+?)['\"]?$",
    "filter",
)
def _filter_contains(m: re.Match) -> tuple[str, float]:
    col = m.group(1)
    val = m.group(2).strip().strip("'\"")
    return f"df.filter(pl.col('{col}').str.contains('{val}'))", 0.85


@_register(
    r"(?:filter|keep|show|get|where|only)\s+(?:rows?\s+)?(?:where\s+)?(?:the\s+)?['\"]?(\w+)['\"]?"
    r"\s+(?:starts?\s+with|begins?\s+with)\s+['\"]?(.+?)['\"]?$",
    "filter",
)
def _filter_starts_with(m: re.Match) -> tuple[str, float]:
    col = m.group(1)
    val = m.group(2).strip().strip("'\"")
    return f"df.filter(pl.col('{col}').str.starts_with('{val}'))", 0.85


@_register(
    r"(?:filter|keep|show|get|where|only)\s+(?:rows?\s+)?(?:where\s+)?(?:the\s+)?['\"]?(\w+)['\"]?"
    r"\s+(?:ends?\s+with)\s+['\"]?(.+?)['\"]?$",
    "filter",
)
def _filter_ends_with(m: re.Match) -> tuple[str, float]:
    col = m.group(1)
    val = m.group(2).strip().strip("'\"")
    return f"df.filter(pl.col('{col}').str.ends_with('{val}'))", 0.85


@_register(
    r"(?:filter|keep|show|get|where|only)\s+(?:rows?\s+)?(?:where\s+)?(?:the\s+)?['\"]?(\w+)['\"]?"
    r"\s+(?:is\s+)?in\s+\[(.+?)\]$",
    "filter",
)
def _filter_in_list(m: re.Match) -> tuple[str, float]:
    col = m.group(1)
    raw_vals = m.group(2).split(",")
    vals = [_parse_value(v) for v in raw_vals]
    val_list = ", ".join(vals)
    return f"df.filter(pl.col('{col}').is_in([{val_list}]))", 0.85


@_register(
    r"(?:filter|keep|show|get|where|only)\s+(?:rows?\s+)?(?:where\s+)?(?:the\s+)?['\"]?(\w+)['\"]?"
    r"\s+between\s+(.+?)\s+and\s+(.+?)$",
    "filter",
)
def _filter_between(m: re.Match) -> tuple[str, float]:
    col = m.group(1)
    low = _parse_value(m.group(2))
    high = _parse_value(m.group(3))
    return (
        f"df.filter((pl.col('{col}') >= {low}) & (pl.col('{col}') <= {high}))",
        0.85,
    )


# ---------------------------------------------------------------------------
# Sort patterns
# ---------------------------------------------------------------------------


@_register(
    r"(?:sort|order|arrange)\s+(?:by\s+)?(?:the\s+)?['\"]?(\w+)['\"]?"
    r"(?:\s+(?:in\s+)?(?:desc(?:ending)?|reverse|high\s*(?:est)?\s*(?:to\s*low)?))$",
    "sort",
)
def _sort_desc(m: re.Match) -> tuple[str, float]:
    col = m.group(1)
    return f"df.sort('{col}', descending=True)", 0.9


@_register(
    r"(?:sort|order|arrange)\s+(?:by\s+)?(?:the\s+)?['\"]?(\w+)['\"]?"
    r"(?:\s+(?:in\s+)?(?:asc(?:ending)?|low\s*(?:est)?\s*(?:to\s*high)?))?$",
    "sort",
)
def _sort_asc(m: re.Match) -> tuple[str, float]:
    col = m.group(1)
    return f"df.sort('{col}')", 0.9


# ---------------------------------------------------------------------------
# Select / drop patterns
# ---------------------------------------------------------------------------


@_register(
    r"(?:(?:select|pick|choose)\s+(?:only\s+)?(?:columns?\s+)?|keep\s+(?:only\s+)?columns?\s+)(.+?)$",
    "select",
)
def _select_columns(m: re.Match) -> tuple[str, float]:
    raw = m.group(1)
    cols = [c.strip().strip("'\"") for c in re.split(r"[,\s]+and\s+|,\s*", raw)]
    cols = [c for c in cols if c]
    col_list = ", ".join(f"'{c}'" for c in cols)
    return f"df.select({col_list})", 0.85


@_register(
    r"(?:remove|drop)\s+(?:the\s+)?duplicates?$",
    "distinct",
)
def _remove_duplicates(m: re.Match) -> tuple[str, float]:
    return "df.unique()", 0.85


@_register(
    r"(?:drop|remove|delete|exclude)\s+(?:the\s+)?(?:columns?\s+)?(.+?)$",
    "drop",
)
def _drop_columns(m: re.Match) -> tuple[str, float]:
    raw = m.group(1)
    cols = [c.strip().strip("'\"") for c in re.split(r"[,\s]+and\s+|,\s*", raw)]
    cols = [c for c in cols if c]
    col_list = ", ".join(f"'{c}'" for c in cols)
    return f"df.drop({col_list})", 0.85


# ---------------------------------------------------------------------------
# Rename patterns
# ---------------------------------------------------------------------------


@_register(
    r"rename\s+(?:the\s+)?(?:column\s+)?['\"]?(\w+)['\"]?\s+(?:to|as)\s+['\"]?(\w+)['\"]?$",
    "rename",
)
def _rename_column(m: re.Match) -> tuple[str, float]:
    old = m.group(1)
    new = m.group(2)
    return f"df.rename({{'{old}': '{new}'}})", 0.9


# ---------------------------------------------------------------------------
# Group-by / aggregate patterns
# ---------------------------------------------------------------------------


@_register(
    r"(?:group|aggregate|agg)\s+(?:by\s+)?['\"]?(\w+)['\"]?"
    r"\s+(?:and\s+)?(?:compute|calculate|get|find)?\s*(?:the\s+)?"
    r"(sum|mean|average|avg|count|min|max|median)\s+(?:of\s+)?['\"]?(\w+)['\"]?$",
    "group_by",
)
def _group_by_agg(m: re.Match) -> tuple[str, float]:
    group_col = m.group(1)
    agg_func = m.group(2).lower()
    agg_col = m.group(3)

    func_map = {
        "sum": "sum",
        "mean": "mean",
        "average": "mean",
        "avg": "mean",
        "count": "count",
        "min": "min",
        "max": "max",
        "median": "median",
    }
    func = func_map.get(agg_func, "sum")
    return f"df.group_by('{group_col}').agg(pl.col('{agg_col}').{func}())", 0.85


@_register(
    r"(?:count|tally)\s+(?:rows?\s+)?(?:by|per|for\s+each|grouped?\s+by)\s+['\"]?(\w+)['\"]?$",
    "group_by",
)
def _count_by(m: re.Match) -> tuple[str, float]:
    col = m.group(1)
    return f"df.group_by('{col}').len()", 0.85


# ---------------------------------------------------------------------------
# Limit / sample patterns
# ---------------------------------------------------------------------------


@_register(
    r"(?:(?:get|show|take|keep)\s+)?(?:the\s+)?(?:first|top|head)\s+(\d+)\s*(?:rows?)?$",
    "limit",
)
def _head(m: re.Match) -> tuple[str, float]:
    n = m.group(1)
    return f"df.head({n})", 0.95


@_register(
    r"(?:(?:get|show|take|keep)\s+)?(?:the\s+)?(?:last|bottom|tail)\s+(\d+)\s*(?:rows?)?$",
    "limit",
)
def _tail(m: re.Match) -> tuple[str, float]:
    n = m.group(1)
    return f"df.tail({n})", 0.95


@_register(
    r"(?:sample|random)\s+(\d+)\s*(?:rows?)?$",
    "sample",
)
def _sample(m: re.Match) -> tuple[str, float]:
    n = m.group(1)
    return f"df.sample({n})", 0.9


# ---------------------------------------------------------------------------
# Distinct / deduplicate
# ---------------------------------------------------------------------------


@_register(
    r"(?:deduplicate|dedup|unique|distinct|remove\s+duplicates?)(?:\s+(?:on|by)\s+(.+?))?$",
    "distinct",
)
def _distinct(m: re.Match) -> tuple[str, float]:
    cols_raw = m.group(1)
    if cols_raw:
        cols = [c.strip().strip("'\"") for c in re.split(r"[,\s]+and\s+|,\s*", cols_raw)]
        cols = [c for c in cols if c]
        col_list = ", ".join(f"'{c}'" for c in cols)
        return f"df.unique(subset=[{col_list}])", 0.85
    return "df.unique()", 0.85


# ---------------------------------------------------------------------------
# Cast / type conversion
# ---------------------------------------------------------------------------


@_register(
    r"(?:cast|convert|change)\s+(?:the\s+)?(?:column\s+)?['\"]?(\w+)['\"]?"
    r"\s+(?:to|as|into)\s+(int(?:eger)?|float|string|str|date|bool(?:ean)?|utf8)$",
    "cast",
)
def _cast_column(m: re.Match) -> tuple[str, float]:
    col = m.group(1)
    target = m.group(2).lower()
    type_map = {
        "int": "pl.Int64",
        "integer": "pl.Int64",
        "float": "pl.Float64",
        "string": "pl.Utf8",
        "str": "pl.Utf8",
        "utf8": "pl.Utf8",
        "date": "pl.Date",
        "bool": "pl.Boolean",
        "boolean": "pl.Boolean",
    }
    pl_type = type_map.get(target, "pl.Utf8")
    return f"df.with_columns(pl.col('{col}').cast({pl_type}))", 0.85


# ---------------------------------------------------------------------------
# Fill null patterns
# ---------------------------------------------------------------------------


@_register(
    r"(?:fill|replace)\s+(?:the\s+)?(?:null|missing|empty)\s*(?:values?)?\s+(?:in\s+)?['\"]?(\w+)['\"]?"
    r"\s+(?:with|using|by)\s+(.+?)$",
    "fill_null",
)
def _fill_null(m: re.Match) -> tuple[str, float]:
    col = m.group(1)
    val = _parse_value(m.group(2))
    return f"df.with_columns(pl.col('{col}').fill_null({val}))", 0.85


# ---------------------------------------------------------------------------
# Add / derive column patterns
# ---------------------------------------------------------------------------


@_register(
    r"(?:add|create|derive|compute)\s+(?:a\s+)?(?:new\s+)?(?:column\s+)?['\"]?(\w+)['\"]?"
    r"\s+(?:as|=|equal\s+to|from)\s+['\"]?(\w+)['\"]?\s*([+\-*/])\s*['\"]?([\w.]+)['\"]?$",
    "derive",
)
def _derive_arithmetic(m: re.Match) -> tuple[str, float]:
    new_col = m.group(1)
    left = m.group(2)
    op = m.group(3)
    right = m.group(4)

    # Check if right is a column name or literal
    try:
        float(right)
        right_expr = right
    except ValueError:
        right_expr = f"pl.col('{right}')"

    return (
        f"df.with_columns((pl.col('{left}') {op} {right_expr}).alias('{new_col}'))",
        0.8,
    )


# ---------------------------------------------------------------------------
# Main translation function
# ---------------------------------------------------------------------------


def translate(text: str) -> TranslationResult | None:
    """Translate natural language into a Polars expression.

    Parameters
    ----------
    text
        Natural language description of a data operation.

    Returns
    -------
    TranslationResult | None
        The translated expression with confidence, or None if no pattern matched.
    """
    text = text.strip()
    if not text:
        return None

    for pattern, operation, builder in _PATTERNS:
        m = pattern.search(text)
        if m:
            expr, confidence = builder(m)
            return TranslationResult(
                expression=expr,
                description=text,
                confidence=confidence,
                operation=operation,
            )

    return None


def translate_multi(text: str) -> list[TranslationResult]:
    """Translate text that may contain multiple operations (separated by 'then' or ';').

    Parameters
    ----------
    text
        Natural language potentially containing multiple steps.

    Returns
    -------
    list[TranslationResult]
        List of translated expressions in order.
    """
    # Split on "then", ";", "and then", numbered steps
    parts = re.split(r"\s*(?:;\s*|,?\s+then\s+|,?\s+and\s+then\s+|\d+\.\s*)", text)
    results: list[TranslationResult] = []
    for part in parts:
        part = part.strip()
        if not part:
            continue
        result = translate(part)
        if result:
            results.append(result)
    return results
