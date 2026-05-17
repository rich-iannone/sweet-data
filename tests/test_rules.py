"""Tests for sweet.core.rules — Data quality rules engine."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import polars as pl
import pytest

from sweet.core.rules import (
    Rule,
    ValidationResult,
    Violation,
    load_rules_from_file,
    parse_rules,
    validate,
)


# ---------------------------------------------------------------------------
# Rule parsing
# ---------------------------------------------------------------------------


class TestParseRules:
    def test_parse_from_dict_with_rules_key(self):
        raw = {
            "rules": [
                {"name": "r1", "column": "x", "check": "> 0"},
                {"name": "r2", "column": "y", "check": "not_null"},
            ]
        }
        rules = parse_rules(raw)
        assert len(rules) == 2
        assert rules[0].name == "r1"
        assert rules[1].check == "not_null"

    def test_parse_from_list(self):
        raw = [
            {"name": "r1", "column": "x", "check": "> 0", "severity": "warning"},
        ]
        rules = parse_rules(raw)
        assert len(rules) == 1
        assert rules[0].severity == "warning"

    def test_parse_skips_invalid_entries(self):
        raw = [
            {"name": "good", "check": "> 0"},
            "not a dict",
            {"no_name": True, "check": "x"},
        ]
        rules = parse_rules(raw)
        assert len(rules) == 1
        assert rules[0].name == "good"

    def test_parse_with_defaults(self):
        raw = [{"name": "r1", "column": "x", "check": "> 0"}]
        rules = parse_rules(raw)
        assert rules[0].severity == "error"
        assert rules[0].message is None

    def test_parse_with_custom_message(self):
        raw = [{"name": "r1", "column": "x", "check": "> 0", "message": "Must be positive"}]
        rules = parse_rules(raw)
        assert rules[0].message == "Must be positive"

    def test_parse_invalid_type_raises(self):
        with pytest.raises(ValueError, match="Expected dict or list"):
            parse_rules("not valid")


class TestLoadRulesFromFile:
    def test_load_yaml_file(self, tmp_path):
        rules_file = tmp_path / "rules.yaml"
        rules_file.write_text(
            "rules:\n"
            "  - name: positive_price\n"
            "    column: price\n"
            "    check: '> 0'\n"
            "    severity: error\n"
        )
        rules = load_rules_from_file(str(rules_file))
        assert len(rules) == 1
        assert rules[0].name == "positive_price"
        assert rules[0].column == "price"


# ---------------------------------------------------------------------------
# Not null checks
# ---------------------------------------------------------------------------


class TestNotNullCheck:
    def test_passes_when_no_nulls(self):
        df = pl.DataFrame({"x": [1, 2, 3]})
        rules = [Rule(name="no_nulls", column="x", check="not_null")]
        result = validate(df, rules)
        assert result.passed
        assert result.rules_passed == 1

    def test_fails_with_nulls(self):
        df = pl.DataFrame({"x": [1, None, 3]})
        rules = [Rule(name="no_nulls", column="x", check="not_null")]
        result = validate(df, rules)
        assert not result.passed
        assert result.violations[0].failing_rows == 1

    def test_required_alias(self):
        df = pl.DataFrame({"x": [None, None]})
        rules = [Rule(name="req", column="x", check="required")]
        result = validate(df, rules)
        assert not result.passed
        assert result.violations[0].failing_rows == 2


# ---------------------------------------------------------------------------
# Unique checks
# ---------------------------------------------------------------------------


class TestUniqueCheck:
    def test_passes_when_unique(self):
        df = pl.DataFrame({"id": [1, 2, 3, 4, 5]})
        rules = [Rule(name="unique_id", column="id", check="unique")]
        result = validate(df, rules)
        assert result.passed

    def test_fails_with_duplicates(self):
        df = pl.DataFrame({"id": [1, 2, 2, 3, 3]})
        rules = [Rule(name="unique_id", column="id", check="unique")]
        result = validate(df, rules)
        assert not result.passed
        assert result.violations[0].failing_rows > 0
        assert result.violations[0].sample_values


# ---------------------------------------------------------------------------
# Regex checks
# ---------------------------------------------------------------------------


class TestRegexCheck:
    def test_passes_when_all_match(self):
        df = pl.DataFrame({"email": ["a@b.com", "c@d.org"]})
        rules = [Rule(name="valid_email", column="email", check='regex("^[^@]+@[^@]+\\.[^@]+$")')]
        result = validate(df, rules)
        assert result.passed

    def test_fails_when_some_dont_match(self):
        df = pl.DataFrame({"email": ["valid@test.com", "invalid", "also-bad"]})
        rules = [Rule(name="valid_email", column="email", check='regex("^[^@]+@[^@]+\\.[^@]+$")')]
        result = validate(df, rules)
        assert not result.passed
        assert result.violations[0].failing_rows == 2

    def test_regex_with_samples(self):
        df = pl.DataFrame({"code": ["AB12", "CD34", "bad!", "XX"]})
        rules = [Rule(name="code_format", column="code", check='regex("^[A-Z]{2}\\d{2}$")')]
        result = validate(df, rules)
        assert result.violations[0].sample_values


# ---------------------------------------------------------------------------
# In-set checks
# ---------------------------------------------------------------------------


class TestInSetCheck:
    def test_passes_when_all_in_set(self):
        df = pl.DataFrame({"status": ["active", "inactive", "active"]})
        rules = [Rule(name="valid_status", column="status", check="in(active, inactive, pending)")]
        result = validate(df, rules)
        assert result.passed

    def test_fails_with_invalid_values(self):
        df = pl.DataFrame({"status": ["active", "unknown", "bad"]})
        rules = [Rule(name="valid_status", column="status", check="in(active, inactive)")]
        result = validate(df, rules)
        assert not result.passed
        assert result.violations[0].failing_rows == 2

    def test_in_bracket_syntax(self):
        df = pl.DataFrame({"color": ["red", "blue", "green"]})
        rules = [Rule(name="valid_color", column="color", check="in [red, blue, green]")]
        result = validate(df, rules)
        assert result.passed


# ---------------------------------------------------------------------------
# Type checks
# ---------------------------------------------------------------------------


class TestTypeCheck:
    def test_passes_correct_type(self):
        df = pl.DataFrame({"price": [1.0, 2.5, 3.7]})
        rules = [Rule(name="price_float", column="price", check="type(float)")]
        result = validate(df, rules)
        assert result.passed

    def test_fails_wrong_type(self):
        df = pl.DataFrame({"price": ["1.0", "2.5"]})
        rules = [Rule(name="price_float", column="price", check="type(float)")]
        result = validate(df, rules)
        assert not result.passed

    def test_numeric_type_accepts_int_and_float(self):
        df_int = pl.DataFrame({"x": [1, 2, 3]})
        df_float = pl.DataFrame({"x": [1.0, 2.0]})
        rules = [Rule(name="is_numeric", column="x", check="type(numeric)")]
        assert validate(df_int, rules).passed
        assert validate(df_float, rules).passed

    def test_string_type(self):
        df = pl.DataFrame({"name": ["Alice", "Bob"]})
        rules = [Rule(name="is_str", column="name", check="type(string)")]
        assert validate(df, rules).passed


# ---------------------------------------------------------------------------
# Max null percentage checks
# ---------------------------------------------------------------------------


class TestMaxNullPct:
    def test_passes_below_threshold(self):
        df = pl.DataFrame({"x": [1, 2, None, 4, 5, 6, 7, 8, 9, 10]})
        rules = [Rule(name="low_nulls", column="x", check="max_null_pct(15)")]
        result = validate(df, rules)
        assert result.passed

    def test_fails_above_threshold(self):
        df = pl.DataFrame({"x": [None, None, None, 4, 5]})
        rules = [Rule(name="low_nulls", column="x", check="max_null_pct(10)")]
        result = validate(df, rules)
        assert not result.passed
        assert "60.0%" in result.violations[0].message


# ---------------------------------------------------------------------------
# Length checks
# ---------------------------------------------------------------------------


class TestLengthChecks:
    def test_min_length_passes(self):
        df = pl.DataFrame({"code": ["ABCD", "EFGH", "IJKL"]})
        rules = [Rule(name="min_len", column="code", check="min_length(3)")]
        assert validate(df, rules).passed

    def test_min_length_fails(self):
        df = pl.DataFrame({"code": ["AB", "CDEF", "G"]})
        rules = [Rule(name="min_len", column="code", check="min_length(3)")]
        result = validate(df, rules)
        assert not result.passed
        assert result.violations[0].failing_rows == 2

    def test_max_length_passes(self):
        df = pl.DataFrame({"name": ["Al", "Bo", "Jo"]})
        rules = [Rule(name="max_len", column="name", check="max_length(5)")]
        assert validate(df, rules).passed

    def test_max_length_fails(self):
        df = pl.DataFrame({"name": ["Short", "This is way too long"]})
        rules = [Rule(name="max_len", column="name", check="max_length(10)")]
        result = validate(df, rules)
        assert not result.passed
        assert result.violations[0].failing_rows == 1


# ---------------------------------------------------------------------------
# Comparison checks
# ---------------------------------------------------------------------------


class TestComparisonChecks:
    def test_greater_than_passes(self):
        df = pl.DataFrame({"price": [10, 20, 30]})
        rules = [Rule(name="positive", column="price", check="> 0")]
        assert validate(df, rules).passed

    def test_greater_than_fails(self):
        df = pl.DataFrame({"price": [10, -5, 30]})
        rules = [Rule(name="positive", column="price", check="> 0")]
        result = validate(df, rules)
        assert not result.passed
        assert -5 in result.violations[0].sample_values

    def test_less_than_or_equal(self):
        df = pl.DataFrame({"score": [50, 80, 100, 110]})
        rules = [Rule(name="max_100", column="score", check="<= 100")]
        result = validate(df, rules)
        assert not result.passed
        assert result.violations[0].failing_rows == 1

    def test_not_equal(self):
        df = pl.DataFrame({"status": [0, 1, 1, 0]})
        rules = [Rule(name="not_zero", column="status", check="!= 0")]
        result = validate(df, rules)
        assert not result.passed
        assert result.violations[0].failing_rows == 2

    def test_with_nulls_ignored(self):
        df = pl.DataFrame({"x": [10, None, 20]})
        rules = [Rule(name="pos", column="x", check="> 0")]
        result = validate(df, rules)
        assert result.passed  # nulls are not counted as failures


# ---------------------------------------------------------------------------
# Between checks
# ---------------------------------------------------------------------------


class TestBetweenCheck:
    def test_passes_in_range(self):
        df = pl.DataFrame({"age": [18, 25, 65]})
        rules = [Rule(name="valid_age", column="age", check="between(0, 120)")]
        assert validate(df, rules).passed

    def test_fails_outside_range(self):
        df = pl.DataFrame({"age": [18, 150, -5, 30]})
        rules = [Rule(name="valid_age", column="age", check="between(0, 120)")]
        result = validate(df, rules)
        assert not result.passed
        assert result.violations[0].failing_rows == 2


# ---------------------------------------------------------------------------
# Column existence
# ---------------------------------------------------------------------------


class TestColumnExistence:
    def test_missing_column_fails(self):
        df = pl.DataFrame({"x": [1, 2, 3]})
        rules = [Rule(name="has_y", column="y", check="> 0")]
        result = validate(df, rules)
        assert not result.passed
        assert "does not exist" in result.violations[0].message


# ---------------------------------------------------------------------------
# Multiple rules
# ---------------------------------------------------------------------------


class TestMultipleRules:
    def test_all_pass(self):
        df = pl.DataFrame({
            "id": [1, 2, 3],
            "name": ["Alice", "Bob", "Carol"],
            "score": [85, 92, 78],
        })
        rules = [
            Rule(name="id_unique", column="id", check="unique"),
            Rule(name="name_required", column="name", check="not_null"),
            Rule(name="score_range", column="score", check="between(0, 100)"),
        ]
        result = validate(df, rules)
        assert result.passed
        assert result.rules_checked == 3
        assert result.rules_passed == 3

    def test_mixed_pass_and_fail(self):
        df = pl.DataFrame({
            "id": [1, 1, 2],
            "score": [85, -5, 78],
        })
        rules = [
            Rule(name="id_unique", column="id", check="unique"),
            Rule(name="score_pos", column="score", check="> 0"),
            Rule(name="score_range", column="score", check="between(0, 100)"),
        ]
        result = validate(df, rules)
        assert not result.passed
        assert result.rules_checked == 3
        assert result.error_count == 3

    def test_warning_severity(self):
        df = pl.DataFrame({"x": [1, 2, None]})
        rules = [
            Rule(name="no_nulls_warn", column="x", check="not_null", severity="warning"),
        ]
        result = validate(df, rules)
        # passed is True because no "error"-level violations
        assert result.passed
        assert result.warning_count == 1


# ---------------------------------------------------------------------------
# ValidationResult serialization
# ---------------------------------------------------------------------------


class TestValidationResult:
    def test_to_dict(self):
        result = ValidationResult(
            violations=[
                Violation(
                    rule_name="test",
                    severity="error",
                    message="failed",
                    column="x",
                    failing_rows=5,
                    sample_values=[1, 2, 3],
                )
            ],
            rules_checked=3,
            rules_passed=2,
        )
        d = result.to_dict()
        assert d["passed"] is False
        assert d["rules_checked"] == 3
        assert d["error_count"] == 1
        assert len(d["violations"]) == 1
        # Should be JSON serializable
        json.dumps(d)

    def test_empty_result_passes(self):
        result = ValidationResult(rules_checked=5, rules_passed=5)
        assert result.passed
        assert result.error_count == 0


# ---------------------------------------------------------------------------
# Expression safety
# ---------------------------------------------------------------------------


class TestExpressionSafety:
    def test_rejects_unsafe_expression(self):
        df = pl.DataFrame({"x": [1, 2, 3]})
        rules = [Rule(name="bad", column="x", check="import os")]
        result = validate(df, rules)
        assert not result.passed
        assert "Unsafe" in result.violations[0].message

    def test_rejects_eval(self):
        df = pl.DataFrame({"x": [1]})
        rules = [Rule(name="bad", column="x", check="eval('1+1')")]
        result = validate(df, rules)
        assert not result.passed


# ---------------------------------------------------------------------------
# Workspace integration
# ---------------------------------------------------------------------------


class TestWorkspaceValidation:
    @pytest.fixture
    def ws(self):
        from sweet.core.workspace import Workspace

        ws = Workspace()
        ws.load_df(
            pl.DataFrame({
                "id": [1, 2, 3, 4, 5],
                "name": ["Alice", "Bob", None, "Dave", "Eve"],
                "price": [10.0, -5.0, 30.0, 0.0, 50.0],
                "status": ["active", "inactive", "active", "unknown", "active"],
            }),
            name="data",
        )
        return ws

    def test_validate_with_dict(self, ws):
        rules = {
            "rules": [
                {"name": "name_required", "column": "name", "check": "not_null"},
                {"name": "price_positive", "column": "price", "check": "> 0"},
            ]
        }
        result = ws.validate_rules(rules)
        assert result["rules_checked"] == 2
        assert result["error_count"] == 2
        assert not result["passed"]

    def test_validate_with_list(self, ws):
        rules = [
            {"name": "id_unique", "column": "id", "check": "unique"},
        ]
        result = ws.validate_rules(rules)
        assert result["passed"]

    def test_validate_with_file(self, ws, tmp_path):
        rules_file = tmp_path / "rules.yaml"
        rules_file.write_text(
            "rules:\n"
            "  - name: status_valid\n"
            "    column: status\n"
            "    check: 'in(active, inactive, pending)'\n"
            "    severity: warning\n"
        )
        result = ws.validate_rules(str(rules_file))
        assert result["warning_count"] == 1
        # "unknown" is not in the allowed set
        assert result["violations"][0]["rule_name"] == "status_valid"

    def test_validate_all_pass(self, ws):
        rules = [{"name": "has_id", "column": "id", "check": "unique"}]
        result = ws.validate_rules(rules)
        assert result["passed"]
        assert result["rules_passed"] == 1
