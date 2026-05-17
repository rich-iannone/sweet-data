"""Tests for sweet.core.nl_translate — Natural language to Polars translation."""

from __future__ import annotations

import polars as pl
import pytest

from sweet.core.nl_translate import TranslationResult, translate, translate_multi


# ---------------------------------------------------------------------------
# Filter translations
# ---------------------------------------------------------------------------


class TestFilterTranslations:
    def test_filter_greater_than(self):
        r = translate("filter rows where price greater than 100")
        assert r is not None
        assert r.operation == "filter"
        assert "pl.col('price') > 100" in r.expression

    def test_filter_less_than(self):
        r = translate("filter price less than 50")
        assert r is not None
        assert "pl.col('price') < 50" in r.expression

    def test_filter_equal_to(self):
        r = translate("keep rows where status equal to active")
        assert r is not None
        assert "pl.col('status') ==" in r.expression
        assert '"active"' in r.expression

    def test_filter_not_equal(self):
        r = translate("filter status not equal to inactive")
        assert r is not None
        assert "!=" in r.expression

    def test_filter_at_least(self):
        r = translate("filter score at least 80")
        assert r is not None
        assert ">=" in r.expression

    def test_filter_at_most(self):
        r = translate("filter age at most 65")
        assert r is not None
        assert "<=" in r.expression

    def test_filter_contains(self):
        r = translate("filter name contains Alice")
        assert r is not None
        assert "str.contains" in r.expression
        assert "'Alice'" in r.expression

    def test_filter_starts_with(self):
        r = translate("filter email starts with admin")
        assert r is not None
        assert "str.starts_with" in r.expression

    def test_filter_ends_with(self):
        r = translate("filter filename ends with .csv")
        assert r is not None
        assert "str.ends_with" in r.expression

    def test_filter_is_null(self):
        r = translate("filter value is null")
        assert r is not None
        assert "is_null()" in r.expression

    def test_filter_is_not_null(self):
        r = translate("filter email is not null")
        assert r is not None
        assert "is_not_null()" in r.expression

    def test_filter_in_list(self):
        r = translate("filter country in [US, UK, CA]")
        assert r is not None
        assert "is_in" in r.expression

    def test_filter_between(self):
        r = translate("filter price between 10 and 100")
        assert r is not None
        assert ">= 10" in r.expression
        assert "<= 100" in r.expression

    def test_filter_numeric_value(self):
        r = translate("filter revenue above 1000.5")
        assert r is not None
        assert "1000.5" in r.expression

    def test_filter_show_variant(self):
        r = translate("show rows where age > 18")
        assert r is not None
        assert "filter" in r.expression.lower() or "pl.col" in r.expression

    def test_filter_only_variant(self):
        r = translate("only keep rows where active is true")
        assert r is not None


# ---------------------------------------------------------------------------
# Sort translations
# ---------------------------------------------------------------------------


class TestSortTranslations:
    def test_sort_ascending(self):
        r = translate("sort by name")
        assert r is not None
        assert r.operation == "sort"
        assert "df.sort('name')" == r.expression

    def test_sort_descending(self):
        r = translate("sort by price descending")
        assert r is not None
        assert "descending=True" in r.expression

    def test_sort_desc_keyword(self):
        r = translate("order by revenue desc")
        assert r is not None
        assert "descending=True" in r.expression

    def test_sort_reverse(self):
        r = translate("sort by score reverse")
        assert r is not None
        assert "descending=True" in r.expression


# ---------------------------------------------------------------------------
# Select / drop translations
# ---------------------------------------------------------------------------


class TestSelectDropTranslations:
    def test_select_columns(self):
        r = translate("select name, email, age")
        assert r is not None
        assert r.operation == "select"
        assert "'name'" in r.expression
        assert "'email'" in r.expression

    def test_select_with_and(self):
        r = translate("keep columns name and email")
        assert r is not None
        assert "'name'" in r.expression
        assert "'email'" in r.expression

    def test_drop_columns(self):
        r = translate("drop columns temp, debug")
        assert r is not None
        assert r.operation == "drop"
        assert "'temp'" in r.expression
        assert "'debug'" in r.expression

    def test_remove_variant(self):
        r = translate("remove the column password")
        assert r is not None
        assert r.operation == "drop"


# ---------------------------------------------------------------------------
# Rename translations
# ---------------------------------------------------------------------------


class TestRenameTranslations:
    def test_rename_to(self):
        r = translate("rename old_col to new_col")
        assert r is not None
        assert r.operation == "rename"
        assert "'old_col': 'new_col'" in r.expression

    def test_rename_as(self):
        r = translate("rename column first_name as given_name")
        assert r is not None
        assert "'first_name': 'given_name'" in r.expression


# ---------------------------------------------------------------------------
# Group-by translations
# ---------------------------------------------------------------------------


class TestGroupByTranslations:
    def test_group_by_sum(self):
        r = translate("group by category and compute sum of revenue")
        assert r is not None
        assert r.operation == "group_by"
        assert "group_by('category')" in r.expression
        assert ".sum()" in r.expression

    def test_group_by_mean(self):
        r = translate("group by country and get mean of price")
        assert r is not None
        assert ".mean()" in r.expression

    def test_group_by_count(self):
        r = translate("group by status and count of id")
        assert r is not None
        assert ".count()" in r.expression

    def test_count_by(self):
        r = translate("count rows by category")
        assert r is not None
        assert "group_by('category')" in r.expression
        assert ".len()" in r.expression

    def test_tally_by(self):
        r = translate("tally by status")
        assert r is not None
        assert r.operation == "group_by"


# ---------------------------------------------------------------------------
# Limit / sample translations
# ---------------------------------------------------------------------------


class TestLimitTranslations:
    def test_first_n(self):
        r = translate("first 10 rows")
        assert r is not None
        assert r.operation == "limit"
        assert "head(10)" in r.expression

    def test_top_n(self):
        r = translate("get the top 5")
        assert r is not None
        assert "head(5)" in r.expression

    def test_last_n(self):
        r = translate("last 20 rows")
        assert r is not None
        assert "tail(20)" in r.expression

    def test_sample(self):
        r = translate("sample 50 rows")
        assert r is not None
        assert r.operation == "sample"
        assert "sample(50)" in r.expression


# ---------------------------------------------------------------------------
# Distinct translations
# ---------------------------------------------------------------------------


class TestDistinctTranslations:
    def test_deduplicate(self):
        r = translate("deduplicate")
        assert r is not None
        assert r.operation == "distinct"
        assert "unique()" in r.expression

    def test_unique_on_columns(self):
        r = translate("deduplicate on email")
        assert r is not None
        assert "unique(subset=" in r.expression
        assert "'email'" in r.expression

    def test_remove_duplicates(self):
        r = translate("remove duplicates")
        assert r is not None
        assert "unique()" in r.expression


# ---------------------------------------------------------------------------
# Cast translations
# ---------------------------------------------------------------------------


class TestCastTranslations:
    def test_cast_to_int(self):
        r = translate("cast price to integer")
        assert r is not None
        assert r.operation == "cast"
        assert "pl.Int64" in r.expression
        assert "'price'" in r.expression

    def test_cast_to_string(self):
        r = translate("convert id to string")
        assert r is not None
        assert "pl.Utf8" in r.expression

    def test_cast_to_date(self):
        r = translate("cast created_at to date")
        assert r is not None
        assert "pl.Date" in r.expression


# ---------------------------------------------------------------------------
# Fill null translations
# ---------------------------------------------------------------------------


class TestFillNullTranslations:
    def test_fill_with_value(self):
        r = translate("fill null values in price with 0")
        assert r is not None
        assert r.operation == "fill_null"
        assert "fill_null(0)" in r.expression

    def test_fill_missing_with_string(self):
        r = translate("fill missing in status with unknown")
        assert r is not None
        assert 'fill_null("unknown")' in r.expression


# ---------------------------------------------------------------------------
# Derive column translations
# ---------------------------------------------------------------------------


class TestDeriveTranslations:
    def test_arithmetic_columns(self):
        r = translate("add column total as price * quantity")
        assert r is not None
        assert r.operation == "derive"
        assert "'total'" in r.expression
        assert "pl.col('price')" in r.expression
        assert "pl.col('quantity')" in r.expression
        assert "*" in r.expression

    def test_arithmetic_with_literal(self):
        r = translate("create column tax as price * 0.1")
        assert r is not None
        assert "'tax'" in r.expression
        assert "0.1" in r.expression


# ---------------------------------------------------------------------------
# Multi-step pipelines
# ---------------------------------------------------------------------------


class TestTranslateMulti:
    def test_semicolon_separator(self):
        results = translate_multi("filter price > 10; sort by name")
        assert len(results) == 2
        assert results[0].operation == "filter"
        assert results[1].operation == "sort"

    def test_then_separator(self):
        results = translate_multi("filter age > 18 then sort by name descending")
        assert len(results) == 2

    def test_single_step(self):
        results = translate_multi("filter x > 5")
        assert len(results) == 1

    def test_empty_returns_empty(self):
        results = translate_multi("")
        assert results == []

    def test_untranslatable_skipped(self):
        results = translate_multi("do something weird; sort by name")
        assert len(results) == 1
        assert results[0].operation == "sort"


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


class TestEdgeCases:
    def test_empty_string(self):
        assert translate("") is None

    def test_whitespace_only(self):
        assert translate("   ") is None

    def test_nonsense(self):
        assert translate("xyzzy foobar baz quux") is None

    def test_confidence_range(self):
        r = translate("sort by name")
        assert r is not None
        assert 0.0 < r.confidence <= 1.0

    def test_result_has_all_fields(self):
        r = translate("filter price > 10")
        assert r is not None
        assert r.expression
        assert r.description
        assert r.confidence > 0
        assert r.operation


# ---------------------------------------------------------------------------
# Workspace integration
# ---------------------------------------------------------------------------


class TestWorkspaceNL:
    @pytest.fixture
    def ws(self):
        from sweet.core.workspace import Workspace

        ws = Workspace()
        ws.load_df(
            pl.DataFrame({
                "name": ["Alice", "Bob", "Carol", "Dave"],
                "price": [10.0, 25.0, 5.0, 50.0],
                "category": ["A", "B", "A", "C"],
            }),
            name="data",
        )
        return ws

    def test_nl_transform_filter(self, ws):
        ws.nl_transform("filter price greater than 20")
        assert ws.df.shape[0] == 2  # Bob(25) and Dave(50)

    def test_nl_transform_sort(self, ws):
        ws.nl_transform("sort by price descending")
        assert ws.df["price"][0] == 50.0

    def test_nl_transform_error(self, ws):
        with pytest.raises(ValueError, match="Could not translate"):
            ws.nl_transform("do something impossible")

    def test_nl_translate_preview(self, ws):
        result = ws.nl_translate("filter price > 10")
        assert result is not None
        assert "expression" in result
        assert result["confidence"] > 0

    def test_nl_translate_none(self, ws):
        result = ws.nl_translate("xyzzy impossible")
        assert result is None

    def test_nl_pipeline(self, ws):
        ws.nl_pipeline("filter price > 5; sort by name")
        assert ws.df.shape[0] == 3  # Alice(10), Bob(25), Dave(50) pass; Carol(5) doesn't
        assert ws.df["name"][0] == "Alice"  # sorted alphabetically

    def test_nl_pipeline_error(self, ws):
        with pytest.raises(ValueError, match="Could not translate"):
            ws.nl_pipeline("impossible nonsense")
