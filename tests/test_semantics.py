"""Tests for sweet.core.semantics — Semantic column understanding."""

from __future__ import annotations

import polars as pl
import pytest

from sweet.core.semantics import (
    ColumnSemantic,
    JoinSuggestion,
    SemanticType,
    _compute_overlap,
    _infer_from_content,
    _infer_from_name,
    discover_joins,
    infer_semantic_types,
)


# ---------------------------------------------------------------------------
# Name-based inference
# ---------------------------------------------------------------------------


class TestInferFromName:
    def test_id_column(self):
        result = _infer_from_name("id")
        assert result is not None
        assert result[0] == SemanticType.IDENTIFIER

    def test_customer_id(self):
        result = _infer_from_name("customer_id")
        assert result is not None
        assert result[0] == SemanticType.IDENTIFIER

    def test_email(self):
        result = _infer_from_name("email")
        assert result is not None
        assert result[0] == SemanticType.EMAIL

    def test_email_address(self):
        result = _infer_from_name("email_address")
        assert result is not None
        assert result[0] == SemanticType.EMAIL

    def test_phone(self):
        result = _infer_from_name("phone_number")
        assert result is not None
        assert result[0] == SemanticType.PHONE

    def test_url(self):
        result = _infer_from_name("website_url")
        assert result is not None
        assert result[0] == SemanticType.URL

    def test_created_at(self):
        result = _infer_from_name("created_at")
        assert result is not None
        assert result[0] == SemanticType.DATETIME

    def test_date(self):
        result = _infer_from_name("date")
        assert result is not None
        assert result[0] == SemanticType.DATE

    def test_price(self):
        result = _infer_from_name("price")
        assert result is not None
        assert result[0] == SemanticType.CURRENCY

    def test_total_amount(self):
        result = _infer_from_name("total_amount")
        assert result is not None
        assert result[0] == SemanticType.CURRENCY

    def test_is_active(self):
        result = _infer_from_name("is_active")
        assert result is not None
        assert result[0] == SemanticType.BOOLEAN

    def test_has_shipped(self):
        result = _infer_from_name("has_shipped")
        assert result is not None
        assert result[0] == SemanticType.BOOLEAN

    def test_country(self):
        result = _infer_from_name("country")
        assert result is not None
        assert result[0] == SemanticType.COUNTRY

    def test_zip_code(self):
        result = _infer_from_name("zip_code")
        assert result is not None
        assert result[0] == SemanticType.ZIP_CODE

    def test_latitude(self):
        result = _infer_from_name("latitude")
        assert result is not None
        assert result[0] == SemanticType.GEO_COORDINATE

    def test_ip_address(self):
        result = _infer_from_name("ip_address")
        assert result is not None
        assert result[0] == SemanticType.IP_ADDRESS

    def test_first_name(self):
        result = _infer_from_name("first_name")
        assert result is not None
        assert result[0] == SemanticType.NAME

    def test_uuid(self):
        result = _infer_from_name("uuid")
        assert result is not None
        assert result[0] == SemanticType.IDENTIFIER

    def test_percentage(self):
        result = _infer_from_name("tax_pct")
        assert result is not None
        assert result[0] == SemanticType.PERCENTAGE

    def test_count(self):
        result = _infer_from_name("n_orders")
        assert result is not None
        assert result[0] == SemanticType.QUANTITY

    def test_unknown_name(self):
        result = _infer_from_name("xyzzy_foobar")
        assert result is None


# ---------------------------------------------------------------------------
# Content-based inference
# ---------------------------------------------------------------------------


class TestInferFromContent:
    def test_email_content(self):
        s = pl.Series(["alice@example.com", "bob@test.org", "carol@foo.io"])
        result = _infer_from_content(s)
        assert result is not None
        assert result[0] == SemanticType.EMAIL

    def test_url_content(self):
        s = pl.Series(["https://example.com", "http://test.org/path", "https://foo.io"])
        result = _infer_from_content(s)
        assert result is not None
        assert result[0] == SemanticType.URL

    def test_ip_content(self):
        s = pl.Series(["192.168.1.1", "10.0.0.1", "172.16.0.1"])
        result = _infer_from_content(s)
        assert result is not None
        assert result[0] == SemanticType.IP_ADDRESS

    def test_uuid_content(self):
        s = pl.Series([
            "550e8400-e29b-41d4-a716-446655440000",
            "6ba7b810-9dad-11d1-80b4-00c04fd430c8",
            "f47ac10b-58cc-4372-a567-0e02b2c3d479",
        ])
        result = _infer_from_content(s)
        assert result is not None
        assert result[0] == SemanticType.IDENTIFIER

    def test_zip_content(self):
        s = pl.Series(["90210", "10001", "60601", "94105", "02134"])
        result = _infer_from_content(s)
        assert result is not None
        assert result[0] == SemanticType.ZIP_CODE

    def test_boolean_dtype(self):
        s = pl.Series([True, False, True, False])
        result = _infer_from_content(s)
        assert result is not None
        assert result[0] == SemanticType.BOOLEAN

    def test_date_dtype(self):
        from datetime import date

        s = pl.Series([date(2024, 1, 1), date(2024, 2, 1)])
        result = _infer_from_content(s)
        assert result is not None
        assert result[0] == SemanticType.DATE

    def test_boolean_int(self):
        s = pl.Series([0, 1, 1, 0, 1, 0, 1, 1, 0, 0, 1])
        result = _infer_from_content(s)
        assert result is not None
        assert result[0] == SemanticType.BOOLEAN

    def test_category_low_cardinality(self):
        s = pl.Series(["red", "blue", "green"] * 20)
        result = _infer_from_content(s)
        assert result is not None
        assert result[0] == SemanticType.CATEGORY

    def test_empty_series(self):
        s = pl.Series("x", [], dtype=pl.Utf8)
        result = _infer_from_content(s)
        assert result is None

    def test_all_null(self):
        s = pl.Series("x", [None, None, None], dtype=pl.Utf8)
        result = _infer_from_content(s)
        assert result is None


# ---------------------------------------------------------------------------
# Full inference
# ---------------------------------------------------------------------------


class TestInferSemanticTypes:
    def test_basic_dataframe(self):
        df = pl.DataFrame({
            "customer_id": [1, 2, 3],
            "email": ["a@b.com", "c@d.org", "e@f.io"],
            "price": [10.5, 20.0, 30.5],
        })
        results = infer_semantic_types(df)
        assert len(results) == 3

        by_col = {r.column: r for r in results}
        assert by_col["customer_id"].semantic_type == SemanticType.IDENTIFIER
        assert by_col["email"].semantic_type == SemanticType.EMAIL
        assert by_col["price"].semantic_type == SemanticType.CURRENCY

    def test_content_overrides_name_when_confident(self):
        # Column named "data" (no name match) but contains emails
        df = pl.DataFrame({
            "data": ["alice@test.com", "bob@example.org", "carol@foo.io"],
        })
        results = infer_semantic_types(df)
        assert results[0].semantic_type == SemanticType.EMAIL

    def test_name_wins_on_tie(self):
        # Column named "email" with content also confirming email
        df = pl.DataFrame({
            "email": ["a@b.com", "c@d.org", "e@f.io"],
        })
        results = infer_semantic_types(df)
        assert results[0].semantic_type == SemanticType.EMAIL
        assert "name pattern" in results[0].reasoning

    def test_fallback_numeric(self):
        df = pl.DataFrame({"value": [1.1, 2.2, 3.3, 4.4, 5.5]})
        results = infer_semantic_types(df)
        assert results[0].semantic_type == SemanticType.NUMERIC

    def test_fallback_text(self):
        df = pl.DataFrame({
            "notes": ["hello world", "foo bar baz", "testing one two three"],
        })
        results = infer_semantic_types(df)
        # Should be TEXT since no specific pattern matches
        assert results[0].semantic_type == SemanticType.TEXT

    def test_min_confidence_filter(self):
        df = pl.DataFrame({
            "customer_id": [1, 2, 3],
            "random_col": [1.1, 2.2, 3.3],
        })
        # random_col gets low confidence numeric
        all_results = infer_semantic_types(df)
        high_conf = [r for r in all_results if r.confidence >= 0.7]
        low_conf = [r for r in all_results if r.confidence < 0.7]
        assert len(high_conf) >= 1  # customer_id
        assert len(low_conf) >= 1  # random_col


# ---------------------------------------------------------------------------
# Overlap computation
# ---------------------------------------------------------------------------


class TestComputeOverlap:
    def test_full_overlap(self):
        left = pl.Series([1, 2, 3])
        right = pl.Series([1, 2, 3])
        assert _compute_overlap(left, right) == 1.0

    def test_no_overlap(self):
        left = pl.Series([1, 2, 3])
        right = pl.Series([4, 5, 6])
        assert _compute_overlap(left, right) == 0.0

    def test_partial_overlap(self):
        left = pl.Series([1, 2, 3, 4])
        right = pl.Series([3, 4, 5, 6])
        # intersection={3,4}, union={1,2,3,4,5,6} → 2/6
        overlap = _compute_overlap(left, right)
        assert abs(overlap - 2 / 6) < 0.01

    def test_empty_series(self):
        left = pl.Series("a", [], dtype=pl.Int64)
        right = pl.Series("b", [1, 2, 3])
        assert _compute_overlap(left, right) == 0.0

    def test_handles_nulls(self):
        left = pl.Series([1, 2, None, 3])
        right = pl.Series([2, 3, None, 4])
        # nulls dropped: left={1,2,3}, right={2,3,4} → intersection={2,3}, union={1,2,3,4} → 2/4
        overlap = _compute_overlap(left, right)
        assert abs(overlap - 0.5) < 0.01


# ---------------------------------------------------------------------------
# Join discovery
# ---------------------------------------------------------------------------


class TestDiscoverJoins:
    def test_finds_matching_ids(self):
        orders = pl.DataFrame({
            "order_id": [1, 2, 3, 4, 5],
            "customer_id": [101, 102, 103, 101, 102],
            "amount": [50.0, 75.0, 100.0, 25.0, 60.0],
        })
        customers = pl.DataFrame({
            "customer_id": [101, 102, 103, 104],
            "name": ["Alice", "Bob", "Carol", "Dave"],
            "email": ["a@b.com", "c@d.org", "e@f.io", "g@h.net"],
        })
        results = discover_joins({"orders": orders, "customers": customers})
        # Should find customer_id match
        assert len(results) > 0
        id_joins = [r for r in results if "customer_id" in r.left_column or "customer_id" in r.right_column]
        assert len(id_joins) > 0

    def test_no_joins_unrelated(self):
        df1 = pl.DataFrame({"x": [1, 2, 3], "y": [4.0, 5.0, 6.0]})
        df2 = pl.DataFrame({"a": ["hello", "world", "test"], "b": [True, False, True]})
        results = discover_joins({"sheet1": df1, "sheet2": df2})
        assert len(results) == 0

    def test_single_sheet_returns_empty(self):
        df = pl.DataFrame({"id": [1, 2, 3]})
        results = discover_joins({"only": df})
        assert results == []

    def test_min_overlap_filtering(self):
        # Shared column name but no value overlap
        df1 = pl.DataFrame({"customer_id": [1, 2, 3]})
        df2 = pl.DataFrame({"customer_id": [100, 200, 300]})
        results = discover_joins({"a": df1, "b": df2}, min_overlap=0.3)
        assert len(results) == 0

    def test_respects_min_confidence(self):
        orders = pl.DataFrame({
            "customer_id": [101, 102, 103],
            "amount": [50.0, 75.0, 100.0],
        })
        customers = pl.DataFrame({
            "customer_id": [101, 102, 103],
            "name": ["Alice", "Bob", "Carol"],
        })
        # Very high confidence threshold should filter some results
        high = discover_joins({"orders": orders, "customers": customers}, min_confidence=0.95)
        low = discover_joins({"orders": orders, "customers": customers}, min_confidence=0.5)
        assert len(low) >= len(high)

    def test_multi_sheet_discovery(self):
        orders = pl.DataFrame({
            "order_id": [1, 2, 3],
            "customer_id": [101, 102, 103],
            "product_id": ["SKU-A", "SKU-B", "SKU-C"],
        })
        customers = pl.DataFrame({
            "customer_id": [101, 102, 103, 104],
            "email": ["a@b.com", "c@d.org", "e@f.io", "g@h.net"],
        })
        products = pl.DataFrame({
            "product_id": ["SKU-A", "SKU-B", "SKU-C", "SKU-D"],
            "name": ["Widget", "Gadget", "Doohickey", "Thingamajig"],
            "price": [9.99, 19.99, 29.99, 39.99],
        })
        results = discover_joins({
            "orders": orders,
            "customers": customers,
            "products": products,
        })
        # Should find both customer_id and product_id joins
        assert len(results) >= 2


# ---------------------------------------------------------------------------
# Workspace integration
# ---------------------------------------------------------------------------


class TestWorkspaceSemantics:
    def test_semantic_types(self):
        from sweet.core.workspace import Workspace

        ws = Workspace()
        ws.load_df(
            pl.DataFrame({
                "user_id": [1, 2, 3],
                "email": ["a@b.com", "c@d.org", "e@f.io"],
                "price": [10.5, 20.0, 30.5],
            }),
            name="data",
        )
        results = ws.semantic_types()
        assert len(results) == 3
        by_col = {r["column"]: r for r in results}
        assert by_col["user_id"]["semantic_type"] == "identifier"
        assert by_col["email"]["semantic_type"] == "email"
        assert by_col["price"]["semantic_type"] == "currency"

    def test_semantic_types_min_confidence(self):
        from sweet.core.workspace import Workspace

        ws = Workspace()
        ws.load_df(
            pl.DataFrame({
                "customer_id": [1, 2, 3],
                "random_col": [1.5, 2.5, 3.5],
            }),
            name="data",
        )
        high = ws.semantic_types(min_confidence=0.8)
        low = ws.semantic_types(min_confidence=0.0)
        assert len(low) >= len(high)

    def test_discover_joins(self):
        from sweet.core.workspace import Workspace

        ws = Workspace()
        ws.load_df(
            pl.DataFrame({
                "order_id": [1, 2, 3],
                "customer_id": [101, 102, 103],
            }),
            name="orders",
        )
        ws.load_df(
            pl.DataFrame({
                "customer_id": [101, 102, 103, 104],
                "name": ["Alice", "Bob", "Carol", "Dave"],
            }),
            name="customers",
        )
        results = ws.discover_joins()
        assert len(results) > 0
        assert any("customer_id" in r["left_column"] or "customer_id" in r["right_column"] for r in results)

    def test_discover_joins_single_sheet(self):
        from sweet.core.workspace import Workspace

        ws = Workspace()
        ws.load_df(pl.DataFrame({"id": [1, 2, 3]}), name="only")
        results = ws.discover_joins()
        assert results == []
