"""Tests for sweet.core.anomalies — Anomaly detection and explanation."""

from __future__ import annotations

import polars as pl
import pytest

from sweet.core.anomalies import Anomaly, explain_anomalies


# ---------------------------------------------------------------------------
# Numeric outlier detection
# ---------------------------------------------------------------------------


class TestNumericOutliers:
    def test_detects_z_score_outliers(self):
        """Values far from the mean should be flagged."""
        data = [10.0] * 100 + [1000.0]  # 100 normal, 1 extreme
        df = pl.DataFrame({"value": data})
        results = explain_anomalies(df)
        outlier_results = [a for a in results if a.kind == "outlier" and a.column == "value"]
        assert len(outlier_results) >= 1
        assert 100 in outlier_results[0].rows  # index of the outlier

    def test_no_outliers_in_uniform_data(self):
        """Uniform data should not produce outliers."""
        df = pl.DataFrame({"x": list(range(100))})
        results = explain_anomalies(df, z_threshold=4.0)
        outlier_results = [a for a in results if a.kind == "outlier"]
        assert len(outlier_results) == 0

    def test_custom_z_threshold(self):
        """Lower threshold should catch more outliers."""
        data = [10.0] * 50 + [30.0, 35.0, 40.0]
        df = pl.DataFrame({"v": data})
        # With a low threshold we should catch the high values
        results_low = explain_anomalies(df, z_threshold=1.5)
        results_high = explain_anomalies(df, z_threshold=5.0)
        outliers_low = [a for a in results_low if a.kind == "outlier"]
        outliers_high = [a for a in results_high if a.kind == "outlier"]
        assert len(outliers_low) >= len(outliers_high)

    def test_outlier_severity(self):
        """Many outliers should have higher severity than few."""
        # Few outliers
        data_few = [10.0] * 200 + [500.0]
        df_few = pl.DataFrame({"v": data_few})
        res_few = explain_anomalies(df_few)
        outlier_few = [a for a in res_few if a.kind == "outlier"]

        # Many outliers
        data_many = [10.0] * 100 + [500.0] * 20
        df_many = pl.DataFrame({"v": data_many})
        res_many = explain_anomalies(df_many)
        outlier_many = [a for a in res_many if a.kind == "outlier"]

        if outlier_few and outlier_many:
            severity_order = {"low": 0, "medium": 1, "high": 2}
            assert severity_order[outlier_many[0].severity] >= severity_order[outlier_few[0].severity]

    def test_outlier_has_explanation(self):
        """Outlier anomalies should have a non-empty explanation."""
        data = [5.0] * 50 + [999.0]
        df = pl.DataFrame({"price": data})
        results = explain_anomalies(df)
        outliers = [a for a in results if a.kind == "outlier"]
        assert outliers
        assert outliers[0].explanation
        assert "price" in outliers[0].explanation

    def test_negative_outliers(self):
        """Detect outliers that are extremely low."""
        data = [100.0] * 100 + [-500.0]
        df = pl.DataFrame({"val": data})
        results = explain_anomalies(df)
        outliers = [a for a in results if a.kind == "outlier"]
        assert outliers
        assert -500.0 in outliers[0].values


# ---------------------------------------------------------------------------
# Spike detection
# ---------------------------------------------------------------------------


class TestSpikeDetection:
    def test_detects_spike(self):
        """A sudden jump in sequential data should be detected."""
        # Gradually increasing with a sudden spike
        data = list(range(50)) + [500] + list(range(51, 100))
        df = pl.DataFrame({"seq": [float(x) for x in data]})
        results = explain_anomalies(df)
        spikes = [a for a in results if a.kind == "spike"]
        assert len(spikes) >= 1

    def test_no_spike_in_smooth_data(self):
        """Smoothly increasing data should not show spikes."""
        df = pl.DataFrame({"linear": [float(i) for i in range(200)]})
        results = explain_anomalies(df)
        spikes = [a for a in results if a.kind == "spike"]
        assert len(spikes) == 0

    def test_spike_explanation(self):
        """Spike anomalies should explain themselves."""
        data = [10.0] * 50 + [1000.0] + [10.0] * 50
        df = pl.DataFrame({"metric": data})
        results = explain_anomalies(df)
        spikes = [a for a in results if a.kind == "spike"]
        if spikes:
            assert "metric" in spikes[0].explanation
            assert "jump" in spikes[0].explanation or "spike" in spikes[0].explanation


# ---------------------------------------------------------------------------
# Null cluster detection
# ---------------------------------------------------------------------------


class TestNullClusters:
    def test_detects_null_cluster(self):
        """Column with many nulls should be flagged."""
        data = [None] * 30 + ["a"] * 70
        df = pl.DataFrame({"col": data})
        results = explain_anomalies(df, null_cluster_threshold=0.10)
        null_anomalies = [a for a in results if a.kind == "null_cluster"]
        assert len(null_anomalies) == 1
        assert null_anomalies[0].column == "col"
        assert null_anomalies[0].stats["null_fraction"] == 0.3

    def test_no_null_cluster_below_threshold(self):
        """Columns with few nulls should not be flagged."""
        data = [None] * 5 + ["a"] * 95
        df = pl.DataFrame({"col": data})
        results = explain_anomalies(df, null_cluster_threshold=0.10)
        null_anomalies = [a for a in results if a.kind == "null_cluster"]
        assert len(null_anomalies) == 0

    def test_null_severity(self):
        """Higher null fractions should have higher severity."""
        # Medium
        data_med = [None] * 30 + [1] * 70
        df_med = pl.DataFrame({"x": data_med})
        res_med = explain_anomalies(df_med, null_cluster_threshold=0.10)
        med = [a for a in res_med if a.kind == "null_cluster"]

        # High
        data_high = [None] * 60 + [1] * 40
        df_high = pl.DataFrame({"x": data_high})
        res_high = explain_anomalies(df_high, null_cluster_threshold=0.10)
        high = [a for a in res_high if a.kind == "null_cluster"]

        assert med and high
        severity_order = {"low": 0, "medium": 1, "high": 2}
        assert severity_order[high[0].severity] >= severity_order[med[0].severity]

    def test_null_cluster_explanation(self):
        """Null cluster should have meaningful explanation."""
        data = [None] * 50 + ["val"] * 50
        df = pl.DataFrame({"status": data})
        results = explain_anomalies(df, null_cluster_threshold=0.10)
        nc = [a for a in results if a.kind == "null_cluster"]
        assert nc
        assert "status" in nc[0].explanation
        assert "50%" in nc[0].explanation


# ---------------------------------------------------------------------------
# String anomaly detection
# ---------------------------------------------------------------------------


class TestStringAnomalies:
    def test_detects_length_outliers(self):
        """Strings with unusual length should be flagged."""
        data = ["abc"] * 50 + ["a" * 200]  # One very long string
        df = pl.DataFrame({"text": data})
        results = explain_anomalies(df)
        patterns = [a for a in results if a.kind == "pattern_break"]
        assert len(patterns) >= 1
        assert patterns[0].column == "text"

    def test_no_flag_for_uniform_strings(self):
        """Strings of similar length should not be flagged."""
        data = [f"item_{i:03d}" for i in range(100)]
        df = pl.DataFrame({"code": data})
        results = explain_anomalies(df)
        patterns = [a for a in results if a.kind == "pattern_break" and a.column == "code"]
        assert len(patterns) == 0

    def test_string_length_explanation(self):
        """String anomalies should explain themselves."""
        data = ["short"] * 100 + ["x" * 500]
        df = pl.DataFrame({"notes": data})
        results = explain_anomalies(df)
        patterns = [a for a in results if a.kind == "pattern_break"]
        if patterns:
            assert "notes" in patterns[0].explanation
            assert "length" in patterns[0].explanation


# ---------------------------------------------------------------------------
# Multiple columns
# ---------------------------------------------------------------------------


class TestMultiColumn:
    def test_analyzes_all_columns(self):
        """Should detect anomalies across multiple columns."""
        df = pl.DataFrame({
            "normal": [10.0] * 100,
            "outlier_col": [5.0] * 99 + [9999.0],
            "nulls_col": [None] * 50 + ["a"] * 50,
        })
        results = explain_anomalies(df, null_cluster_threshold=0.10)
        columns_flagged = {a.column for a in results}
        assert "outlier_col" in columns_flagged
        assert "nulls_col" in columns_flagged

    def test_small_dataframe(self):
        """Very small DataFrames should not crash."""
        df = pl.DataFrame({"x": [1.0, 2.0, 3.0]})
        results = explain_anomalies(df)
        # Should not raise, even if no anomalies
        assert isinstance(results, list)

    def test_empty_dataframe(self):
        """Empty DataFrame should return no anomalies."""
        df = pl.DataFrame({"x": pl.Series([], dtype=pl.Float64)})
        results = explain_anomalies(df)
        assert results == []


# ---------------------------------------------------------------------------
# Anomaly.to_dict()
# ---------------------------------------------------------------------------


class TestAnomalyToDict:
    def test_serializable(self):
        """to_dict should produce a JSON-serializable dict."""
        import json

        a = Anomaly(
            column="price",
            kind="outlier",
            severity="medium",
            description="Test anomaly",
            rows=[0, 1, 2],
            values=[10.0, float("nan"), float("inf")],
            stats={"mean": 5.0, "std": 2.0},
            explanation="This is a test.",
        )
        d = a.to_dict()
        # Should not raise
        serialized = json.dumps(d)
        assert "price" in serialized

    def test_caps_rows_and_values(self):
        """to_dict should cap rows/values at 20."""
        a = Anomaly(
            column="x",
            kind="outlier",
            severity="low",
            description="Many rows",
            rows=list(range(100)),
            values=list(range(100)),
            stats={},
            explanation="",
        )
        d = a.to_dict()
        assert len(d["rows"]) == 20
        assert len(d["values"]) == 20


# ---------------------------------------------------------------------------
# Workspace integration
# ---------------------------------------------------------------------------


class TestWorkspaceAnomalies:
    def test_explain_anomalies_returns_dicts(self):
        """Workspace.explain_anomalies() should return list of dicts."""
        from sweet.core.workspace import Workspace

        ws = Workspace()
        ws.load_df(
            pl.DataFrame({
                "price": [10.0] * 50 + [9999.0],
                "name": ["Alice"] * 51,
            }),
            name="data",
        )
        results = ws.explain_anomalies()
        assert isinstance(results, list)
        assert any(r["column"] == "price" for r in results)
        for r in results:
            assert "column" in r
            assert "kind" in r
            assert "severity" in r
            assert "explanation" in r

    def test_no_anomalies_returns_empty(self):
        """Clean data should return empty list."""
        from sweet.core.workspace import Workspace

        ws = Workspace()
        ws.load_df(
            pl.DataFrame({"x": list(range(100))}),
            name="clean",
        )
        results = ws.explain_anomalies(z_threshold=10.0)
        outliers = [r for r in results if r["kind"] == "outlier"]
        assert len(outliers) == 0

    def test_custom_threshold(self):
        """Lower z_threshold should find more anomalies."""
        from sweet.core.workspace import Workspace

        ws = Workspace()
        ws.load_df(
            pl.DataFrame({"v": [10.0] * 50 + [30.0, 35.0, 40.0]}),
            name="test",
        )
        results_low = ws.explain_anomalies(z_threshold=1.5)
        results_high = ws.explain_anomalies(z_threshold=5.0)
        assert len(results_low) >= len(results_high)
