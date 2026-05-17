"""Tests for sweet.core.synthesis — Data synthesis & augmentation."""

from __future__ import annotations

from datetime import date

import polars as pl
import pytest

from sweet.core.synthesis import (
    augment_fill_rate,
    augment_row_hash,
    augment_row_number,
    impute,
    synthesize,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

SAMPLE_DF = pl.DataFrame(
    {
        "id": [1, 2, 3, 4, 5],
        "name": ["Alice", "Bob", "Carol", "Dave", "Eve"],
        "score": [95.5, 87.3, None, 76.0, 91.2],
        "active": [True, False, True, True, False],
        "category": ["A", "B", "A", "C", "B"],
    }
)

NUMERIC_DF = pl.DataFrame(
    {
        "x": [1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 9.0, 10.0],
        "y": [10, 20, 30, 40, 50, 60, 70, 80, 90, 100],
    }
)


# ---------------------------------------------------------------------------
# synthesize
# ---------------------------------------------------------------------------


class TestSynthesize:
    def test_basic_synthesis(self):
        result = synthesize(SAMPLE_DF, rows=10, seed=42)
        assert result.shape[0] == 10
        assert result.columns == SAMPLE_DF.columns

    def test_preserves_dtypes(self):
        result = synthesize(SAMPLE_DF, rows=10, seed=42)
        for col in SAMPLE_DF.columns:
            assert result[col].dtype == SAMPLE_DF[col].dtype, f"dtype mismatch for {col}"

    def test_seed_reproducibility(self):
        r1 = synthesize(SAMPLE_DF, rows=20, seed=123)
        r2 = synthesize(SAMPLE_DF, rows=20, seed=123)
        assert r1.equals(r2)

    def test_different_seeds_differ(self):
        r1 = synthesize(SAMPLE_DF, rows=50, seed=1)
        r2 = synthesize(SAMPLE_DF, rows=50, seed=2)
        assert not r1.equals(r2)

    def test_preserves_null_rate(self):
        # score has 1/5 = 20% null rate; synthetic should have roughly similar
        result = synthesize(SAMPLE_DF, rows=1000, seed=42)
        null_rate = result["score"].null_count() / 1000
        assert 0.05 < null_rate < 0.4  # Generous bounds

    def test_numeric_within_range(self):
        result = synthesize(NUMERIC_DF, rows=100, seed=42)
        x_vals = result["x"].drop_nulls().to_list()
        assert all(1.0 <= v <= 10.0 for v in x_vals)

    def test_categorical_values(self):
        result = synthesize(SAMPLE_DF, rows=100, seed=42)
        categories = set(result["category"].drop_nulls().to_list())
        assert categories <= {"A", "B", "C"}

    def test_boolean_generation(self):
        result = synthesize(SAMPLE_DF, rows=100, seed=42)
        bools = result["active"].drop_nulls().to_list()
        assert all(isinstance(v, bool) for v in bools)

    def test_date_column(self):
        df = pl.DataFrame({
            "d": [date(2024, 1, 1), date(2024, 6, 15), date(2024, 12, 31)],
        })
        result = synthesize(df, rows=50, seed=42)
        dates = result["d"].drop_nulls().to_list()
        assert all(date(2024, 1, 1) <= d <= date(2024, 12, 31) for d in dates)

    def test_custom_row_count(self):
        result = synthesize(SAMPLE_DF, rows=7, seed=42)
        assert result.shape[0] == 7

    def test_single_row_source(self):
        df = pl.DataFrame({"x": [42], "name": ["solo"]})
        result = synthesize(df, rows=5, seed=42)
        assert result.shape[0] == 5

    def test_empty_source_raises(self):
        df = pl.DataFrame({"x": pl.Series([], dtype=pl.Int64)})
        with pytest.raises(ValueError, match="empty"):
            synthesize(df, rows=10)


# ---------------------------------------------------------------------------
# impute
# ---------------------------------------------------------------------------


class TestImpute:
    @pytest.fixture
    def df_with_nulls(self):
        return pl.DataFrame({
            "value": [1.0, None, 3.0, None, 5.0],
            "name": ["a", None, "c", None, "e"],
            "count": [10, 20, None, 40, 50],
        })

    def test_median(self, df_with_nulls):
        result = impute(df_with_nulls, "value", method="median")
        assert result["value"].null_count() == 0

    def test_mean(self, df_with_nulls):
        result = impute(df_with_nulls, "value", method="mean")
        assert result["value"].null_count() == 0

    def test_mode(self, df_with_nulls):
        result = impute(df_with_nulls, "name", method="mode")
        assert result["name"].null_count() == 0

    def test_forward_fill(self, df_with_nulls):
        result = impute(df_with_nulls, "value", method="forward")
        # First value is non-null, so forward fill works for all
        assert result["value"].null_count() == 0
        assert result["value"][1] == 1.0  # Forward-filled from index 0

    def test_backward_fill(self, df_with_nulls):
        result = impute(df_with_nulls, "value", method="backward")
        assert result["value"].null_count() == 0
        assert result["value"][1] == 3.0  # Backward-filled from index 2

    def test_zero_numeric(self, df_with_nulls):
        result = impute(df_with_nulls, "value", method="zero")
        assert result["value"].null_count() == 0
        assert result["value"][1] == 0.0

    def test_zero_string(self, df_with_nulls):
        result = impute(df_with_nulls, "name", method="zero")
        assert result["name"].null_count() == 0
        assert result["name"][1] == ""

    def test_interpolate(self, df_with_nulls):
        result = impute(df_with_nulls, "value", method="interpolate")
        assert result["value"].null_count() == 0

    def test_missing_column_raises(self, df_with_nulls):
        with pytest.raises(ValueError, match="not found"):
            impute(df_with_nulls, "nonexistent")

    def test_mean_on_string_raises(self, df_with_nulls):
        with pytest.raises(ValueError, match="non-numeric"):
            impute(df_with_nulls, "name", method="mean")

    def test_median_on_string_raises(self, df_with_nulls):
        with pytest.raises(ValueError, match="non-numeric"):
            impute(df_with_nulls, "name", method="median")

    def test_interpolate_on_string_raises(self, df_with_nulls):
        with pytest.raises(ValueError, match="non-numeric"):
            impute(df_with_nulls, "name", method="interpolate")

    def test_invalid_method(self, df_with_nulls):
        with pytest.raises(ValueError, match="Unknown"):
            impute(df_with_nulls, "value", method="bogus")

    def test_no_nulls_unchanged(self):
        df = pl.DataFrame({"x": [1.0, 2.0, 3.0]})
        result = impute(df, "x", method="mean")
        assert result.equals(df)


# ---------------------------------------------------------------------------
# augment
# ---------------------------------------------------------------------------


class TestAugmentFillRate:
    def test_adds_column(self):
        result = augment_fill_rate(SAMPLE_DF)
        assert "_fill_rate" in result.columns

    def test_full_rows(self):
        df = pl.DataFrame({"a": [1, 2], "b": ["x", "y"]})
        result = augment_fill_rate(df)
        rates = result["_fill_rate"].to_list()
        assert all(r == 1.0 for r in rates)

    def test_partial_nulls(self):
        df = pl.DataFrame({"a": [1, None], "b": ["x", "y"]})
        result = augment_fill_rate(df)
        assert result["_fill_rate"][0] == 1.0
        assert result["_fill_rate"][1] == 0.5


class TestAugmentRowHash:
    def test_adds_column(self):
        result = augment_row_hash(SAMPLE_DF)
        assert "_row_hash" in result.columns

    def test_same_rows_same_hash(self):
        df = pl.DataFrame({"x": [1, 1], "y": ["a", "a"]})
        result = augment_row_hash(df)
        hashes = result["_row_hash"].to_list()
        assert hashes[0] == hashes[1]

    def test_different_rows_different_hash(self):
        df = pl.DataFrame({"x": [1, 2], "y": ["a", "b"]})
        result = augment_row_hash(df)
        hashes = result["_row_hash"].to_list()
        assert hashes[0] != hashes[1]


class TestAugmentRowNumber:
    def test_adds_column(self):
        result = augment_row_number(SAMPLE_DF)
        assert "_row_number" in result.columns

    def test_sequential(self):
        result = augment_row_number(SAMPLE_DF)
        nums = result["_row_number"].to_list()
        assert nums == [1, 2, 3, 4, 5]


# ---------------------------------------------------------------------------
# Workspace integration
# ---------------------------------------------------------------------------


class TestWorkspaceSynthesis:
    def test_synthesize(self):
        from sweet.core.workspace import Workspace

        ws = Workspace()
        ws.load_df(SAMPLE_DF, name="data")
        ws.synthesize(rows=20, seed=42)
        assert "data_synthetic" in ws.sheet_names
        assert ws.current_sheet_name == "data_synthetic"
        assert ws.df.shape[0] == 20

    def test_impute(self):
        from sweet.core.workspace import Workspace

        ws = Workspace()
        ws.load_df(SAMPLE_DF.clone(), name="data")
        assert ws.df["score"].null_count() == 1
        ws.impute("score", method="mean")
        assert ws.df["score"].null_count() == 0

    def test_augment_fill_rate(self):
        from sweet.core.workspace import Workspace

        ws = Workspace()
        ws.load_df(SAMPLE_DF.clone(), name="data")
        ws.augment("fill_rate")
        assert "_fill_rate" in ws.df.columns

    def test_augment_row_hash(self):
        from sweet.core.workspace import Workspace

        ws = Workspace()
        ws.load_df(SAMPLE_DF.clone(), name="data")
        ws.augment("row_hash")
        assert "_row_hash" in ws.df.columns

    def test_augment_row_number(self):
        from sweet.core.workspace import Workspace

        ws = Workspace()
        ws.load_df(SAMPLE_DF.clone(), name="data")
        ws.augment("row_number")
        assert "_row_number" in ws.df.columns

    def test_augment_invalid(self):
        from sweet.core.workspace import Workspace

        ws = Workspace()
        ws.load_df(SAMPLE_DF.clone(), name="data")
        with pytest.raises(ValueError, match="Unknown augmentation"):
            ws.augment("bogus")
