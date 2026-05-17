"""Tests for sweet.core.relationships — Cross-dataset intelligence."""

from __future__ import annotations

import polars as pl
import pytest

from sweet.core.relationships import (
    JoinSuggestion,
    Relationship,
    auto_join,
    discover_relationships,
    suggest_joins,
)


# ---------------------------------------------------------------------------
# Relationship discovery
# ---------------------------------------------------------------------------


class TestDiscoverRelationships:
    def test_discovers_exact_match(self):
        """Columns with identical values should be found."""
        sheets = {
            "orders": pl.DataFrame({
                "customer_id": [1, 2, 3, 4, 5],
                "amount": [100, 200, 300, 400, 500],
            }),
            "customers": pl.DataFrame({
                "customer_id": [1, 2, 3, 4, 5],
                "name": ["Alice", "Bob", "Carol", "Dave", "Eve"],
            }),
        }
        results = discover_relationships(sheets)
        assert len(results) >= 1
        # Should find the customer_id match
        id_rels = [r for r in results if "customer_id" in r.left_column or "customer_id" in r.right_column]
        assert id_rels
        assert id_rels[0].kind == "exact_match"

    def test_discovers_subset_relationship(self):
        """FK-like relationship where one column is a subset."""
        sheets = {
            "orders": pl.DataFrame({
                "product_id": [1, 2, 3, 1, 2],
                "qty": [10, 20, 30, 40, 50],
            }),
            "products": pl.DataFrame({
                "product_id": [1, 2, 3, 4, 5, 6, 7, 8, 9, 10],
                "name": [f"Product {i}" for i in range(10)],
            }),
        }
        results = discover_relationships(sheets)
        assert len(results) >= 1
        id_rels = [r for r in results if "product_id" in r.left_column or "product_id" in r.right_column]
        assert id_rels

    def test_no_relationship_for_unrelated(self):
        """Completely unrelated columns should not match."""
        sheets = {
            "a": pl.DataFrame({"x": [100, 200, 300, 400, 500]}),
            "b": pl.DataFrame({"y": [1, 2, 3, 4, 5]}),
        }
        results = discover_relationships(sheets, min_match_rate=0.8)
        assert len(results) == 0

    def test_single_sheet_returns_empty(self):
        """Need at least 2 sheets to find relationships."""
        sheets = {"only": pl.DataFrame({"x": [1, 2, 3]})}
        results = discover_relationships(sheets)
        assert results == []

    def test_string_columns(self):
        """String columns should also be compared."""
        sheets = {
            "orders": pl.DataFrame({
                "country": ["US", "UK", "DE", "FR", "US", "UK"],
                "total": [100, 200, 300, 400, 500, 600],
            }),
            "countries": pl.DataFrame({
                "code": ["US", "UK", "DE", "FR", "JP", "CN"],
                "name": ["United States", "United Kingdom", "Germany", "France", "Japan", "China"],
            }),
        }
        results = discover_relationships(sheets)
        rel = [r for r in results if "country" in r.left_column or "code" in r.right_column]
        assert rel

    def test_sorted_by_confidence(self):
        """Results should be sorted by confidence descending."""
        sheets = {
            "a": pl.DataFrame({
                "id": [1, 2, 3, 4, 5],
                "cat": ["A", "B", "C", "D", "E"],
            }),
            "b": pl.DataFrame({
                "id": [1, 2, 3, 4, 5],
                "cat": ["A", "B", "C", "D", "E"],
            }),
        }
        results = discover_relationships(sheets)
        if len(results) >= 2:
            for i in range(len(results) - 1):
                assert results[i].confidence >= results[i + 1].confidence

    def test_min_match_rate_filter(self):
        """Higher min_match_rate should produce fewer results."""
        sheets = {
            "a": pl.DataFrame({"x": [1, 2, 3, 4, 5, 6, 7, 8, 9, 10]}),
            "b": pl.DataFrame({"y": [1, 2, 3, 11, 12, 13, 14, 15, 16, 17]}),
        }
        results_low = discover_relationships(sheets, min_match_rate=0.2)
        results_high = discover_relationships(sheets, min_match_rate=0.8)
        assert len(results_low) >= len(results_high)

    def test_name_similarity_boosts_confidence(self):
        """Columns with similar names should get higher confidence."""
        sheets = {
            "orders": pl.DataFrame({
                "customer_id": [1, 2, 3, 4, 5],
                "unrelated": [10, 20, 30, 40, 50],
            }),
            "customers": pl.DataFrame({
                "customer_id": [1, 2, 3, 4, 5],
                "data": [10, 20, 30, 40, 50],
            }),
        }
        results = discover_relationships(sheets)
        # customer_id match should have higher confidence than unrelated/data match
        id_rels = [r for r in results if "customer_id" in r.left_column and "customer_id" in r.right_column]
        other_rels = [r for r in results if "customer_id" not in r.left_column or "customer_id" not in r.right_column]
        if id_rels and other_rels:
            assert id_rels[0].confidence >= other_rels[0].confidence


# ---------------------------------------------------------------------------
# Join suggestions
# ---------------------------------------------------------------------------


class TestSuggestJoins:
    def test_suggests_join(self):
        """Should suggest joining on matching columns."""
        sheets = {
            "orders": pl.DataFrame({
                "customer_id": [1, 2, 3, 4, 5],
                "amount": [100, 200, 300, 400, 500],
            }),
            "customers": pl.DataFrame({
                "customer_id": [1, 2, 3, 4, 5],
                "name": ["Alice", "Bob", "Carol", "Dave", "Eve"],
            }),
        }
        suggestions = suggest_joins(sheets)
        assert len(suggestions) >= 1
        assert suggestions[0].join_keys
        assert suggestions[0].join_type in ("inner", "left")

    def test_no_suggestion_without_relationships(self):
        """Unrelated data should not produce join suggestions."""
        sheets = {
            "a": pl.DataFrame({"x": [100, 200, 300]}),
            "b": pl.DataFrame({"y": [1, 2, 3]}),
        }
        suggestions = suggest_joins(sheets, min_match_rate=0.8)
        assert suggestions == []

    def test_suggestion_has_description(self):
        """Suggestions should have human-readable descriptions."""
        sheets = {
            "left": pl.DataFrame({"key": ["a", "b", "c", "d", "e"]}),
            "right": pl.DataFrame({"key": ["a", "b", "c", "d", "e"], "val": [1, 2, 3, 4, 5]}),
        }
        suggestions = suggest_joins(sheets)
        if suggestions:
            assert suggestions[0].description
            assert "join" in suggestions[0].description.lower() or "Join" in suggestions[0].description


# ---------------------------------------------------------------------------
# Auto-join
# ---------------------------------------------------------------------------


class TestAutoJoin:
    def test_auto_joins_matching_columns(self):
        """Should join two DataFrames by discovering the key."""
        left = pl.DataFrame({
            "order_id": [1, 2, 3, 4, 5],
            "customer_id": [101, 102, 103, 104, 105],
            "amount": [10, 20, 30, 40, 50],
        })
        right = pl.DataFrame({
            "customer_id": [101, 102, 103, 104, 105],
            "name": ["Alice", "Bob", "Carol", "Dave", "Eve"],
            "city": ["NYC", "LA", "Chicago", "Houston", "Phoenix"],
        })
        result, suggestion = auto_join(left, right, "orders", "customers")
        assert result.shape[0] == 5
        assert "name" in result.columns
        assert "city" in result.columns
        assert "amount" in result.columns
        assert suggestion is not None

    def test_auto_join_with_string_keys(self):
        """Should handle string join keys."""
        left = pl.DataFrame({
            "country_code": ["US", "UK", "DE", "FR", "JP"],
            "sales": [1000, 800, 600, 700, 500],
        })
        right = pl.DataFrame({
            "country_code": ["US", "UK", "DE", "FR", "JP", "CN"],
            "name": ["United States", "United Kingdom", "Germany", "France", "Japan", "China"],
        })
        result, suggestion = auto_join(left, right, "sales", "countries")
        assert result.shape[0] >= 5
        assert "name" in result.columns

    def test_auto_join_raises_on_no_key(self):
        """Should raise ValueError if no join key found."""
        left = pl.DataFrame({"x": [100, 200, 300]})
        right = pl.DataFrame({"y": [1, 2, 3]})
        with pytest.raises(ValueError, match="Could not discover"):
            auto_join(left, right, "a", "b", min_match_rate=0.9)

    def test_auto_join_override_type(self):
        """Should respect join_type override."""
        left = pl.DataFrame({
            "id": [1, 2, 3],
            "val": ["a", "b", "c"],
        })
        right = pl.DataFrame({
            "id": [1, 2, 3, 4, 5],
            "extra": ["x", "y", "z", "w", "v"],
        })
        result_inner, _ = auto_join(left, right, "l", "r", join_type="inner")
        result_left, _ = auto_join(left, right, "l", "r", join_type="left")
        # Inner should be <= left rows
        assert result_inner.shape[0] <= result_left.shape[0]

    def test_auto_join_no_column_collision(self):
        """Joined result should handle overlapping column names."""
        left = pl.DataFrame({
            "id": [1, 2, 3, 4, 5],
            "value": [10, 20, 30, 40, 50],
        })
        right = pl.DataFrame({
            "id": [1, 2, 3, 4, 5],
            "value": [100, 200, 300, 400, 500],
        })
        result, _ = auto_join(left, right, "left", "right")
        # Should have both value columns (one with suffix)
        assert result.shape[1] >= 3


# ---------------------------------------------------------------------------
# Relationship.to_dict / JoinSuggestion.to_dict
# ---------------------------------------------------------------------------


class TestSerialization:
    def test_relationship_to_dict(self):
        """Relationship should serialize to a clean dict."""
        r = Relationship(
            left_sheet="orders",
            left_column="customer_id",
            right_sheet="customers",
            right_column="id",
            kind="subset",
            match_rate=0.95,
            confidence=0.88,
            description="FK relationship",
        )
        d = r.to_dict()
        assert d["left_sheet"] == "orders"
        assert d["match_rate"] == 0.95
        assert d["confidence"] == 0.88

    def test_join_suggestion_to_dict(self):
        """JoinSuggestion should serialize to a clean dict."""
        s = JoinSuggestion(
            left_sheet="orders",
            right_sheet="customers",
            join_keys=[("customer_id", "id")],
            join_type="left",
            confidence=0.9,
            description="Join orders with customers",
        )
        d = s.to_dict()
        assert d["left_sheet"] == "orders"
        assert d["join_keys"] == [("customer_id", "id")]


# ---------------------------------------------------------------------------
# Workspace integration
# ---------------------------------------------------------------------------


class TestWorkspaceRelationships:
    @pytest.fixture
    def ws(self):
        from sweet.core.workspace import Workspace

        ws = Workspace()
        ws.load_df(
            pl.DataFrame({
                "order_id": [1, 2, 3, 4, 5],
                "customer_id": [101, 102, 103, 104, 105],
                "total": [50.0, 75.0, 100.0, 125.0, 150.0],
            }),
            name="orders",
        )
        ws.load_df(
            pl.DataFrame({
                "customer_id": [101, 102, 103, 104, 105],
                "name": ["Alice", "Bob", "Carol", "Dave", "Eve"],
                "tier": ["gold", "silver", "gold", "bronze", "silver"],
            }),
            name="customers",
        )
        return ws

    def test_discover_relationships(self, ws):
        results = ws.discover_relationships()
        assert isinstance(results, list)
        assert len(results) >= 1
        # Should find customer_id relationship
        id_rels = [
            r for r in results
            if "customer_id" in r["left_column"] or "customer_id" in r["right_column"]
        ]
        assert id_rels

    def test_suggest_joins(self, ws):
        results = ws.suggest_joins()
        assert isinstance(results, list)
        assert len(results) >= 1
        assert results[0]["join_type"] in ("inner", "left")

    def test_auto_join(self, ws):
        ws.auto_join("orders", "customers")
        assert ws.df.shape[0] == 5
        assert "name" in ws.df.columns
        assert "total" in ws.df.columns

    def test_auto_join_creates_new_sheet(self, ws):
        ws.auto_join("orders", "customers")
        assert "orders_customers_joined" in ws.sheet_names

    def test_auto_join_custom_name(self, ws):
        ws.auto_join("orders", "customers", target_name="enriched")
        assert "enriched" in ws.sheet_names

    def test_auto_join_invalid_sheet(self, ws):
        with pytest.raises(ValueError, match="Sheet not found"):
            ws.auto_join("orders", "nonexistent")

    def test_discover_no_relationships(self):
        """Unrelated sheets should return empty."""
        from sweet.core.workspace import Workspace

        ws = Workspace()
        ws.load_df(pl.DataFrame({"x": [100, 200, 300]}), name="a")
        ws.load_df(pl.DataFrame({"y": [1, 2, 3]}), name="b")
        results = ws.discover_relationships(min_match_rate=0.9)
        assert results == []
