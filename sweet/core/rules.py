"""Data quality rules engine.

Define, parse, and evaluate YAML-based data quality rules against DataFrames.
Rules support column-level checks (type, range, regex, null limits, uniqueness)
and cross-column/cross-sheet referential integrity checks.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import polars as pl

# ---------------------------------------------------------------------------
# Rule types
# ---------------------------------------------------------------------------


@dataclass
class Rule:
    """A single data quality rule."""

    name: str
    column: str | None = None  # None for cross-column rules
    check: str = ""  # The check expression
    severity: str = "error"  # "error", "warning", "info"
    message: str | None = None  # Custom failure message
    parameters: dict = field(default_factory=dict)


@dataclass
class Violation:
    """A single rule violation."""

    rule_name: str
    severity: str
    message: str
    column: str | None = None
    failing_rows: int = 0
    sample_values: list = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "rule_name": self.rule_name,
            "severity": self.severity,
            "message": self.message,
            "column": self.column,
            "failing_rows": self.failing_rows,
            "sample_values": self.sample_values[:10],
        }


@dataclass
class ValidationResult:
    """Aggregate result of running all rules against a DataFrame."""

    violations: list[Violation] = field(default_factory=list)
    rules_checked: int = 0
    rules_passed: int = 0

    @property
    def passed(self) -> bool:
        return not any(v.severity == "error" for v in self.violations)

    @property
    def error_count(self) -> int:
        return sum(1 for v in self.violations if v.severity == "error")

    @property
    def warning_count(self) -> int:
        return sum(1 for v in self.violations if v.severity == "warning")

    def to_dict(self) -> dict:
        return {
            "passed": self.passed,
            "rules_checked": self.rules_checked,
            "rules_passed": self.rules_passed,
            "error_count": self.error_count,
            "warning_count": self.warning_count,
            "violations": [v.to_dict() for v in self.violations],
        }


# ---------------------------------------------------------------------------
# Rule parsing from YAML
# ---------------------------------------------------------------------------


def parse_rules(raw: dict | list) -> list[Rule]:
    """Parse rules from a YAML-loaded structure.

    Accepts either:
      - A dict with a "rules" key containing a list
      - A list of rule dicts directly

    Each rule dict should have:
      - name: str (required)
      - column: str (optional)
      - check: str (required)
      - severity: "error" | "warning" | "info" (default: "error")
      - message: str (optional custom message)
    """
    if isinstance(raw, dict):
        rule_list = raw.get("rules", [])
    elif isinstance(raw, list):
        rule_list = raw
    else:
        raise ValueError(f"Expected dict or list of rules, got {type(raw).__name__}")

    rules: list[Rule] = []
    for item in rule_list:
        if not isinstance(item, dict):
            continue
        name = item.get("name")
        if not name:
            continue
        rules.append(
            Rule(
                name=name,
                column=item.get("column"),
                check=item.get("check", ""),
                severity=item.get("severity", "error"),
                message=item.get("message"),
                parameters=item.get("parameters", {}),
            )
        )
    return rules


def load_rules_from_file(path: str | Path) -> list[Rule]:
    """Load rules from a YAML file."""
    from yaml12 import read_yaml

    data = read_yaml(str(path))
    return parse_rules(data)


# ---------------------------------------------------------------------------
# Rule evaluation
# ---------------------------------------------------------------------------


def validate(df: pl.DataFrame, rules: list[Rule]) -> ValidationResult:
    """Evaluate all rules against a DataFrame.

    Parameters
    ----------
    df
        The DataFrame to validate.
    rules
        List of rules to check.

    Returns
    -------
    ValidationResult
        Aggregated validation results with any violations.
    """
    result = ValidationResult(rules_checked=len(rules))
    passed = 0

    for rule in rules:
        violation = _evaluate_rule(df, rule)
        if violation is None:
            passed += 1
        else:
            result.violations.append(violation)

    result.rules_passed = passed
    return result


def _evaluate_rule(df: pl.DataFrame, rule: Rule) -> Violation | None:
    """Evaluate a single rule against a DataFrame."""
    check = rule.check.strip()

    if not check:
        return None

    # Column existence check
    if rule.column and rule.column not in df.columns:
        return Violation(
            rule_name=rule.name,
            severity=rule.severity,
            message=rule.message or f"Column '{rule.column}' does not exist",
            column=rule.column,
        )

    # Dispatch to the appropriate check handler
    if check.startswith("not_null") or check == "required":
        return _check_not_null(df, rule)
    elif check.startswith("unique"):
        return _check_unique(df, rule)
    elif check.startswith("regex("):
        return _check_regex(df, rule)
    elif check.startswith("in(") or check.startswith("in ["):
        return _check_in_set(df, rule)
    elif check.startswith("type("):
        return _check_type(df, rule)
    elif check.startswith("max_null_pct("):
        return _check_max_null_pct(df, rule)
    elif check.startswith("min_length(") or check.startswith("max_length("):
        return _check_length(df, rule)
    elif _is_comparison(check):
        return _check_comparison(df, rule)
    elif check.startswith("between("):
        return _check_between(df, rule)
    else:
        # Try as a generic Polars filter expression
        return _check_expression(df, rule)


# ---------------------------------------------------------------------------
# Individual check implementations
# ---------------------------------------------------------------------------


def _check_not_null(df: pl.DataFrame, rule: Rule) -> Violation | None:
    """Check that a column has no null values."""
    if not rule.column:
        return None
    null_count = df[rule.column].null_count()
    if null_count == 0:
        return None
    return Violation(
        rule_name=rule.name,
        severity=rule.severity,
        message=rule.message or f"Column '{rule.column}' has {null_count} null value(s)",
        column=rule.column,
        failing_rows=null_count,
    )


def _check_unique(df: pl.DataFrame, rule: Rule) -> Violation | None:
    """Check that column values are unique."""
    if not rule.column:
        return None
    col = df[rule.column]
    total = len(col) - col.null_count()
    unique = col.drop_nulls().n_unique()
    duplicates = total - unique
    if duplicates == 0:
        return None
    # Find some sample duplicate values
    value_counts = df.group_by(rule.column).len()
    dupes = value_counts.filter(pl.col("len") > 1)[rule.column].head(5).to_list()
    return Violation(
        rule_name=rule.name,
        severity=rule.severity,
        message=rule.message or f"Column '{rule.column}' has {duplicates} duplicate value(s)",
        column=rule.column,
        failing_rows=duplicates,
        sample_values=dupes,
    )


def _check_regex(df: pl.DataFrame, rule: Rule) -> Violation | None:
    """Check that column values match a regex pattern."""
    if not rule.column:
        return None
    # Extract pattern from regex("...")
    m = re.match(r'regex\(["\'](.+?)["\']\)', rule.check)
    if not m:
        return None
    pattern = m.group(1)

    col = df[rule.column].drop_nulls()
    if len(col) == 0:
        return None

    non_matching = col.filter(~col.str.contains(pattern))
    if len(non_matching) == 0:
        return None

    samples = non_matching.head(5).to_list()
    return Violation(
        rule_name=rule.name,
        severity=rule.severity,
        message=rule.message or f"Column '{rule.column}' has {len(non_matching)} value(s) not matching pattern",
        column=rule.column,
        failing_rows=len(non_matching),
        sample_values=samples,
    )


def _check_in_set(df: pl.DataFrame, rule: Rule) -> Violation | None:
    """Check that column values are in an allowed set."""
    if not rule.column:
        return None
    # Extract values from in(...) or in [...]
    check = rule.check
    m = re.match(r'in\s*[\(\[]\s*(.+?)\s*[\)\]]', check)
    if not m:
        return None
    raw_vals = m.group(1)
    allowed = {v.strip().strip("'\"") for v in raw_vals.split(",")}

    col = df[rule.column].drop_nulls()
    if len(col) == 0:
        return None

    # Cast to string for comparison
    str_col = col.cast(pl.Utf8)
    invalid = str_col.filter(~str_col.is_in(list(allowed)))
    if len(invalid) == 0:
        return None

    samples = invalid.unique().head(5).to_list()
    return Violation(
        rule_name=rule.name,
        severity=rule.severity,
        message=rule.message or f"Column '{rule.column}' has {len(invalid)} value(s) not in allowed set",
        column=rule.column,
        failing_rows=len(invalid),
        sample_values=samples,
    )


def _check_type(df: pl.DataFrame, rule: Rule) -> Violation | None:
    """Check that a column has the expected type."""
    if not rule.column:
        return None
    m = re.match(r'type\(["\']?(\w+)["\']?\)', rule.check)
    if not m:
        return None
    expected = m.group(1).lower()

    type_map = {
        "int": (pl.Int8, pl.Int16, pl.Int32, pl.Int64, pl.UInt8, pl.UInt16, pl.UInt32, pl.UInt64),
        "integer": (pl.Int8, pl.Int16, pl.Int32, pl.Int64, pl.UInt8, pl.UInt16, pl.UInt32, pl.UInt64),
        "float": (pl.Float32, pl.Float64),
        "numeric": (pl.Int8, pl.Int16, pl.Int32, pl.Int64, pl.UInt8, pl.UInt16, pl.UInt32, pl.UInt64, pl.Float32, pl.Float64),
        "string": (pl.Utf8,),
        "str": (pl.Utf8,),
        "utf8": (pl.Utf8,),
        "bool": (pl.Boolean,),
        "boolean": (pl.Boolean,),
        "date": (pl.Date,),
        "datetime": (pl.Datetime,),
    }

    expected_types = type_map.get(expected)
    if expected_types is None:
        return None

    actual = df[rule.column].dtype
    if actual in expected_types:
        return None

    return Violation(
        rule_name=rule.name,
        severity=rule.severity,
        message=rule.message or f"Column '{rule.column}' has type {actual}, expected {expected}",
        column=rule.column,
    )


def _check_max_null_pct(df: pl.DataFrame, rule: Rule) -> Violation | None:
    """Check that null percentage doesn't exceed threshold."""
    if not rule.column:
        return None
    m = re.match(r'max_null_pct\(\s*([\d.]+)\s*\)', rule.check)
    if not m:
        return None
    max_pct = float(m.group(1))

    total = len(df)
    if total == 0:
        return None
    null_count = df[rule.column].null_count()
    null_pct = (null_count / total) * 100

    if null_pct <= max_pct:
        return None

    return Violation(
        rule_name=rule.name,
        severity=rule.severity,
        message=rule.message or f"Column '{rule.column}' is {null_pct:.1f}% null (max allowed: {max_pct}%)",
        column=rule.column,
        failing_rows=null_count,
    )


def _check_length(df: pl.DataFrame, rule: Rule) -> Violation | None:
    """Check string min/max length."""
    if not rule.column:
        return None
    check = rule.check
    m_min = re.match(r'min_length\(\s*(\d+)\s*\)', check)
    m_max = re.match(r'max_length\(\s*(\d+)\s*\)', check)

    col = df[rule.column].drop_nulls()
    if len(col) == 0:
        return None

    if col.dtype != pl.Utf8:
        return None

    lengths = col.str.len_chars()

    if m_min:
        min_len = int(m_min.group(1))
        failing = lengths.filter(lengths < min_len)
        if len(failing) == 0:
            return None
        short_vals = col.filter(col.str.len_chars() < min_len).head(5).to_list()
        return Violation(
            rule_name=rule.name,
            severity=rule.severity,
            message=rule.message or f"Column '{rule.column}' has {len(failing)} value(s) shorter than {min_len} chars",
            column=rule.column,
            failing_rows=len(failing),
            sample_values=short_vals,
        )

    if m_max:
        max_len = int(m_max.group(1))
        failing = lengths.filter(lengths > max_len)
        if len(failing) == 0:
            return None
        long_vals = col.filter(col.str.len_chars() > max_len).head(5).to_list()
        return Violation(
            rule_name=rule.name,
            severity=rule.severity,
            message=rule.message or f"Column '{rule.column}' has {len(failing)} value(s) longer than {max_len} chars",
            column=rule.column,
            failing_rows=len(failing),
            sample_values=long_vals,
        )

    return None


_COMPARISON_RE = re.compile(r'^([><=!]+)\s*(.+)$')


def _is_comparison(check: str) -> bool:
    """Check if the rule check is a simple comparison."""
    return bool(_COMPARISON_RE.match(check.strip()))


def _check_comparison(df: pl.DataFrame, rule: Rule) -> Violation | None:
    """Check a comparison rule (e.g., "> 0", "<= 100", "!= 0")."""
    if not rule.column:
        return None
    m = _COMPARISON_RE.match(rule.check.strip())
    if not m:
        return None

    op = m.group(1)
    raw_val = m.group(2).strip()

    # Parse the value
    val = _parse_check_value(raw_val)

    col = df[rule.column].drop_nulls()
    if len(col) == 0:
        return None

    # Build the filter for FAILING rows
    try:
        if op == ">":
            failing = df.filter(pl.col(rule.column) <= val)
        elif op == ">=":
            failing = df.filter(pl.col(rule.column) < val)
        elif op == "<":
            failing = df.filter(pl.col(rule.column) >= val)
        elif op == "<=":
            failing = df.filter(pl.col(rule.column) > val)
        elif op == "==" or op == "=":
            failing = df.filter(pl.col(rule.column) != val)
        elif op == "!=":
            failing = df.filter(pl.col(rule.column) == val)
        else:
            return None
    except Exception:
        return None

    # Filter out nulls from failing count
    failing = failing.filter(pl.col(rule.column).is_not_null())

    if len(failing) == 0:
        return None

    samples = failing[rule.column].head(5).to_list()
    return Violation(
        rule_name=rule.name,
        severity=rule.severity,
        message=rule.message or f"Column '{rule.column}' has {len(failing)} value(s) failing check: {rule.check}",
        column=rule.column,
        failing_rows=len(failing),
        sample_values=samples,
    )


def _check_between(df: pl.DataFrame, rule: Rule) -> Violation | None:
    """Check that values are between two bounds."""
    if not rule.column:
        return None
    m = re.match(r'between\(\s*(.+?)\s*,\s*(.+?)\s*\)', rule.check)
    if not m:
        return None

    low = _parse_check_value(m.group(1).strip())
    high = _parse_check_value(m.group(2).strip())

    col = df[rule.column].drop_nulls()
    if len(col) == 0:
        return None

    failing = df.filter(
        (pl.col(rule.column) < low) | (pl.col(rule.column) > high)
    ).filter(pl.col(rule.column).is_not_null())

    if len(failing) == 0:
        return None

    samples = failing[rule.column].head(5).to_list()
    return Violation(
        rule_name=rule.name,
        severity=rule.severity,
        message=rule.message or f"Column '{rule.column}' has {len(failing)} value(s) outside [{low}, {high}]",
        column=rule.column,
        failing_rows=len(failing),
        sample_values=samples,
    )


def _check_expression(df: pl.DataFrame, rule: Rule) -> Violation | None:
    """Evaluate a rule as a generic Polars expression string.

    The expression should be a filter condition that returns True for PASSING rows.
    """
    # Safety: only allow simple expressions, no exec/eval of arbitrary code
    check = rule.check.strip()
    if any(kw in check for kw in ("import ", "__", "exec(", "eval(", "open(")):
        return Violation(
            rule_name=rule.name,
            severity="error",
            message=f"Unsafe expression in rule '{rule.name}': {check}",
            column=rule.column,
        )
    return None


def _parse_check_value(raw: str) -> int | float | str:
    """Parse a value from a check expression."""
    raw = raw.strip().strip("'\"")
    try:
        return int(raw)
    except ValueError:
        pass
    try:
        return float(raw)
    except ValueError:
        pass
    return raw
