"""Tests for sweet.core.suggestions — Transform suggestion engine."""

from __future__ import annotations

import polars as pl
import pytest

from sweet.core.suggestions import (
    Suggestion,
    SuggestionKind,
    _to_snake_case,
    suggest_transforms,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def make_currency_df():
    return pl.DataFrame(
        {
            "product": ["Widget", "Gadget", "Doohickey"],
            "price": ["$1,234.56", "$99.99", "$42,000.00"],
            "quantity": [10, 25, 3],
        }
    )


def make_whitespace_df():
    return pl.DataFrame(
        {
            "name": ["  Alice  ", "Bob  ", "  Carol"],
            "city": ["  NYC", "LA  ", "  Chicago  "],
            "age": [30, 25, 35],
        }
    )


def make_name_merge_df():
    return pl.DataFrame(
        {
            "first_name": ["Alice", "Bob", "Carol"],
            "last_name": ["Smith", "Jones", "Williams"],
            "email": ["a@b.com", "b@c.com", "c@d.com"],
        }
    )


def make_date_df():
    return pl.DataFrame(
        {
            "event": ["Launch", "Review", "Deploy"],
            "date": ["2024-01-15", "2024-02-20", "2024-03-10"],
            "count": [5, 3, 8],
        }
    )


def make_boolean_df():
    return pl.DataFrame(
        {
            "name": ["Alice", "Bob", "Carol", "Dave"],
            "active": ["yes", "no", "yes", "no"],
            "verified": ["true", "false", "true", "true"],
        }
    )


def make_percent_df():
    return pl.DataFrame(
        {
            "item": ["A", "B", "C"],
            "completion": ["85.5%", "92%", "100%"],
            "score": [1, 2, 3],
        }
    )


def make_constant_df():
    return pl.DataFrame(
        {
            "id": [1, 2, 3],
            "status": ["active", "active", "active"],
            "version": ["1.0", "1.0", "1.0"],
        }
    )


def make_camel_case_df():
    return pl.DataFrame(
        {
            "firstName": ["Alice", "Bob"],
            "lastName": ["Smith", "Jones"],
            "emailAddress": ["a@b.com", "c@d.com"],
        }
    )


def make_empty_col_df():
    return pl.DataFrame(
        {
            "id": [1, 2, 3],
            "name": ["A", "B", "C"],
            "notes": [None, None, None],
        }
    )


def make_split_df():
    return pl.DataFrame(
        {
            "location": ["New York, NY", "Los Angeles, CA", "Chicago, IL", "Houston, TX", "Phoenix, AZ"],
            "count": [1, 2, 3, 4, 5],
        }
    )


# ---------------------------------------------------------------------------
# Currency detection
# ---------------------------------------------------------------------------


class TestCurrencyDetection:
    def test_detects_dollar_prefix(self):
        df = make_currency_df()
        suggestions = suggest_transforms(df)
        currency = [s for s in suggestions if s.kind == SuggestionKind.EXTRACT_NUMERIC]
        assert len(currency) == 1
        assert currency[0].columns == ["price"]
        assert "$" in currency[0].metadata["symbol"]

    def test_confidence_based_on_ratio(self):
        df = make_currency_df()
        suggestions = suggest_transforms(df)
        currency = [s for s in suggestions if s.kind == SuggestionKind.EXTRACT_NUMERIC]
        assert currency[0].confidence >= 0.7

    def test_no_false_positive_on_numeric_col(self):
        df = pl.DataFrame({"value": ["hello", "world", "test"]})
        suggestions = suggest_transforms(df)
        currency = [s for s in suggestions if s.kind == SuggestionKind.EXTRACT_NUMERIC]
        assert len(currency) == 0


# ---------------------------------------------------------------------------
# Percentage detection
# ---------------------------------------------------------------------------


class TestPercentDetection:
    def test_detects_percent_strings(self):
        df = make_percent_df()
        suggestions = suggest_transforms(df)
        pct = [s for s in suggestions if s.kind == SuggestionKind.EXTRACT_PERCENT]
        assert len(pct) == 1
        assert pct[0].columns == ["completion"]

    def test_expression_removes_percent(self):
        df = make_percent_df()
        suggestions = suggest_transforms(df)
        pct = [s for s in suggestions if s.kind == SuggestionKind.EXTRACT_PERCENT]
        assert "replace" in pct[0].expression
        assert "Float64" in pct[0].expression


# ---------------------------------------------------------------------------
# Whitespace detection
# ---------------------------------------------------------------------------


class TestWhitespaceDetection:
    def test_detects_whitespace_columns(self):
        df = make_whitespace_df()
        suggestions = suggest_transforms(df)
        ws = [s for s in suggestions if s.kind == SuggestionKind.TRIM_WHITESPACE]
        assert len(ws) == 1
        assert set(ws[0].columns) == {"name", "city"}

    def test_no_false_positive_clean_strings(self):
        df = pl.DataFrame({"x": ["hello", "world", "test"]})
        suggestions = suggest_transforms(df)
        ws = [s for s in suggestions if s.kind == SuggestionKind.TRIM_WHITESPACE]
        assert len(ws) == 0


# ---------------------------------------------------------------------------
# Name merge detection
# ---------------------------------------------------------------------------


class TestNameMergeDetection:
    def test_detects_first_last_name(self):
        df = make_name_merge_df()
        suggestions = suggest_transforms(df)
        merge = [s for s in suggestions if s.kind == SuggestionKind.MERGE_COLUMNS]
        assert len(merge) == 1
        assert "first_name" in merge[0].columns
        assert "last_name" in merge[0].columns
        assert "full_name" in merge[0].expression

    def test_no_false_positive_unrelated_cols(self):
        df = pl.DataFrame({"x": ["a", "b"], "y": ["c", "d"]})
        suggestions = suggest_transforms(df)
        merge = [s for s in suggestions if s.kind == SuggestionKind.MERGE_COLUMNS]
        assert len(merge) == 0


# ---------------------------------------------------------------------------
# Column name normalization
# ---------------------------------------------------------------------------


class TestColumnNameNormalization:
    def test_detects_camel_case(self):
        df = make_camel_case_df()
        suggestions = suggest_transforms(df)
        norm = [s for s in suggestions if s.kind == SuggestionKind.NORMALIZE_NAMES]
        assert len(norm) == 1
        assert "first_name" in norm[0].metadata["renames"].values()
        assert "last_name" in norm[0].metadata["renames"].values()

    def test_no_suggestion_for_already_snake(self):
        df = pl.DataFrame({"first_name": ["a"], "last_name": ["b"], "email": ["c"]})
        suggestions = suggest_transforms(df)
        norm = [s for s in suggestions if s.kind == SuggestionKind.NORMALIZE_NAMES]
        assert len(norm) == 0


# ---------------------------------------------------------------------------
# Constant column detection
# ---------------------------------------------------------------------------


class TestConstantDetection:
    def test_detects_constant_columns(self):
        df = make_constant_df()
        suggestions = suggest_transforms(df)
        const = [s for s in suggestions if s.kind == SuggestionKind.DROP_CONSTANT]
        assert len(const) == 2  # status and version
        cols = {s.columns[0] for s in const}
        assert "status" in cols
        assert "version" in cols

    def test_no_constant_in_varied_data(self):
        df = pl.DataFrame({"x": [1, 2, 3], "y": ["a", "b", "c"]})
        suggestions = suggest_transforms(df)
        const = [s for s in suggestions if s.kind == SuggestionKind.DROP_CONSTANT]
        assert len(const) == 0


# ---------------------------------------------------------------------------
# Empty column detection
# ---------------------------------------------------------------------------


class TestEmptyColumnDetection:
    def test_detects_all_null_column(self):
        df = make_empty_col_df()
        suggestions = suggest_transforms(df)
        empty = [s for s in suggestions if s.kind == SuggestionKind.DROP_EMPTY]
        assert len(empty) == 1
        assert empty[0].columns == ["notes"]


# ---------------------------------------------------------------------------
# Date string detection
# ---------------------------------------------------------------------------


class TestDateDetection:
    def test_detects_iso_dates(self):
        df = make_date_df()
        suggestions = suggest_transforms(df)
        dates = [s for s in suggestions if s.kind == SuggestionKind.PARSE_DATE]
        assert len(dates) == 1
        assert dates[0].columns == ["date"]
        assert "%Y-%m-%d" in dates[0].metadata["format"]

    def test_detects_us_dates(self):
        df = pl.DataFrame({"d": ["01/15/2024", "02/20/2024", "12/31/2024"]})
        suggestions = suggest_transforms(df)
        dates = [s for s in suggestions if s.kind == SuggestionKind.PARSE_DATE]
        assert len(dates) == 1
        assert "%m/%d/%Y" in dates[0].metadata["format"]


# ---------------------------------------------------------------------------
# Boolean string detection
# ---------------------------------------------------------------------------


class TestBooleanDetection:
    def test_detects_yes_no(self):
        df = make_boolean_df()
        suggestions = suggest_transforms(df)
        bools = [s for s in suggestions if s.kind == SuggestionKind.NORMALIZE_BOOLEAN]
        assert len(bools) == 2
        cols = {s.columns[0] for s in bools}
        assert "active" in cols
        assert "verified" in cols

    def test_no_false_positive_on_regular_strings(self):
        df = pl.DataFrame({"x": ["hello", "world", "test"]})
        suggestions = suggest_transforms(df)
        bools = [s for s in suggestions if s.kind == SuggestionKind.NORMALIZE_BOOLEAN]
        assert len(bools) == 0


# ---------------------------------------------------------------------------
# Splittable column detection
# ---------------------------------------------------------------------------


class TestSplitDetection:
    def test_detects_comma_separator(self):
        df = make_split_df()
        suggestions = suggest_transforms(df)
        splits = [s for s in suggestions if s.kind == SuggestionKind.SPLIT_COLUMN]
        assert len(splits) == 1
        assert splits[0].columns == ["location"]
        assert "comma" in splits[0].metadata["separator"] or "," in splits[0].metadata["separator"]


# ---------------------------------------------------------------------------
# Helper: _to_snake_case
# ---------------------------------------------------------------------------


class TestToSnakeCase:
    def test_camel_case(self):
        assert _to_snake_case("firstName") == "first_name"

    def test_pascal_case(self):
        assert _to_snake_case("FirstName") == "first_name"

    def test_already_snake(self):
        assert _to_snake_case("first_name") == "first_name"

    def test_with_spaces(self):
        assert _to_snake_case("First Name") == "first_name"

    def test_with_dashes(self):
        assert _to_snake_case("first-name") == "first_name"

    def test_acronym(self):
        assert _to_snake_case("HTMLParser") == "html_parser"

    def test_mixed(self):
        assert _to_snake_case("getHTTPResponse") == "get_http_response"


# ---------------------------------------------------------------------------
# Suggestion dataclass
# ---------------------------------------------------------------------------


class TestSuggestion:
    def test_to_dict(self):
        s = Suggestion(
            kind=SuggestionKind.TRIM_WHITESPACE,
            description="test",
            columns=["a"],
            expression="df.with_columns(...)",
            confidence=0.9,
            priority=5,
        )
        d = s.to_dict()
        assert d["kind"] == "trim_whitespace"
        assert d["confidence"] == 0.9
        assert d["columns"] == ["a"]


# ---------------------------------------------------------------------------
# Workspace integration
# ---------------------------------------------------------------------------


class TestWorkspaceSuggest:
    def test_suggest_from_workspace(self):
        from sweet.core.workspace import Workspace

        ws = Workspace()
        ws.load_df(make_currency_df(), name="sales")
        suggestions = ws.suggest()
        assert len(suggestions) > 0
        kinds = {s["kind"] for s in suggestions}
        assert "extract_numeric" in kinds

    def test_suggest_empty_for_clean_data(self):
        from sweet.core.workspace import Workspace

        ws = Workspace()
        ws.load_df(pl.DataFrame({"x": [1, 2, 3], "y": [4, 5, 6]}), name="clean")
        suggestions = ws.suggest()
        assert len(suggestions) == 0

    def test_suggest_max_limit(self):
        from sweet.core.workspace import Workspace

        ws = Workspace()
        ws.load_df(make_boolean_df(), name="data")
        suggestions = ws.suggest(max_suggestions=1)
        assert len(suggestions) <= 1

    def test_suggest_no_data_raises(self):
        from sweet.core.workspace import Workspace

        ws = Workspace()
        with pytest.raises(ValueError):
            ws.suggest()


# ---------------------------------------------------------------------------
# Priority ordering
# ---------------------------------------------------------------------------


class TestPriorityOrdering:
    def test_higher_priority_first(self):
        """Whitespace (priority 9) should come before name normalize (priority 4)."""
        df = pl.DataFrame(
            {
                "firstName": ["  Alice  ", "  Bob  "],
                "lastName": ["  Smith  ", "  Jones  "],
                "email": ["a@b.com", "c@d.com"],
            }
        )
        suggestions = suggest_transforms(df)
        assert len(suggestions) >= 2
        # Whitespace should be first (priority 9)
        assert suggestions[0].kind == SuggestionKind.TRIM_WHITESPACE
