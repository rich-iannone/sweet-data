"""Tests for sweet.core.versioning — Version control for tabular data."""

from __future__ import annotations

import polars as pl
import pytest

from sweet.core.versioning import Commit, DiffResult, VersionStore, diff


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

SAMPLE_DF = pl.DataFrame(
    {
        "id": [1, 2, 3, 4],
        "name": ["Alice", "Bob", "Carol", "Dave"],
        "score": [95.5, 87.3, 76.0, 92.1],
    }
)

MODIFIED_DF = pl.DataFrame(
    {
        "id": [1, 2, 3, 4],
        "name": ["Alice", "Bobby", "Carol", "Dave"],
        "score": [95.5, 90.0, 76.0, 92.1],
    }
)


# ---------------------------------------------------------------------------
# VersionStore
# ---------------------------------------------------------------------------


class TestVersionStore:
    def test_commit_basic(self):
        store = VersionStore()
        c = store.commit(SAMPLE_DF, "data", "initial load")
        assert c.message == "initial load"
        assert c.sheet_name == "data"
        assert c.shape == (4, 3)
        assert c.parent_id is None
        assert len(c.id) == 8

    def test_commit_stores_snapshot(self):
        store = VersionStore()
        c = store.commit(SAMPLE_DF, "data", "snapshot test")
        # Snapshot is independent copy
        assert c.snapshot.equals(SAMPLE_DF)
        assert c.snapshot is not SAMPLE_DF

    def test_commit_chain_parent(self):
        store = VersionStore()
        c1 = store.commit(SAMPLE_DF, "data", "first")
        c2 = store.commit(MODIFIED_DF, "data", "second")
        assert c2.parent_id == c1.id

    def test_commit_different_sheets_no_parent(self):
        store = VersionStore()
        c1 = store.commit(SAMPLE_DF, "sheet_a", "a commit")
        c2 = store.commit(SAMPLE_DF, "sheet_b", "b commit")
        assert c2.parent_id is None

    def test_commit_metadata(self):
        store = VersionStore()
        c = store.commit(SAMPLE_DF, "data", "with meta", metadata={"ops": 5})
        assert c.metadata == {"ops": 5}

    def test_log_all(self):
        store = VersionStore()
        store.commit(SAMPLE_DF, "data", "first")
        store.commit(MODIFIED_DF, "data", "second")
        log = store.log()
        assert len(log) == 2
        # Most recent first
        assert log[0].message == "second"
        assert log[1].message == "first"

    def test_log_filter_by_sheet(self):
        store = VersionStore()
        store.commit(SAMPLE_DF, "sheet_a", "a")
        store.commit(SAMPLE_DF, "sheet_b", "b")
        store.commit(MODIFIED_DF, "sheet_a", "a2")
        log = store.log("sheet_a")
        assert len(log) == 2
        assert all(c.sheet_name == "sheet_a" for c in log)

    def test_log_limit(self):
        store = VersionStore()
        store.commit(SAMPLE_DF, "data", "first")
        store.commit(MODIFIED_DF, "data", "second")
        store.commit(SAMPLE_DF, "data", "third")
        log = store.log(limit=2)
        assert len(log) == 2

    def test_get_commit_by_id(self):
        store = VersionStore()
        c = store.commit(SAMPLE_DF, "data", "find me")
        found = store.get_commit(c.id)
        assert found is c

    def test_get_commit_by_prefix(self):
        store = VersionStore()
        c = store.commit(SAMPLE_DF, "data", "prefix test")
        found = store.get_commit(c.id[:4])
        assert found is c

    def test_get_commit_not_found(self):
        store = VersionStore()
        with pytest.raises(ValueError, match="No commit found"):
            store.get_commit("nonexistent")

    def test_checkout_restores_data(self):
        store = VersionStore()
        c = store.commit(SAMPLE_DF, "data", "checkpoint")
        store.commit(MODIFIED_DF, "data", "modified")
        restored = store.checkout(c.id)
        assert restored.equals(SAMPLE_DF)

    def test_checkout_is_independent_clone(self):
        store = VersionStore()
        c = store.commit(SAMPLE_DF, "data", "checkpoint")
        restored = store.checkout(c.id)
        assert restored is not c.snapshot


# ---------------------------------------------------------------------------
# Diff
# ---------------------------------------------------------------------------


class TestDiff:
    def test_identical_dataframes(self):
        result = diff(SAMPLE_DF, SAMPLE_DF)
        assert not result.has_changes
        assert result.summary() == "No changes detected."

    def test_row_modifications_positional(self):
        result = diff(SAMPLE_DF, MODIFIED_DF)
        assert result.has_changes
        assert result.rows_modified == 1  # Row 2: Bob→Bobby + 87.3→90.0
        assert result.rows_added == 0
        assert result.rows_removed == 0

    def test_rows_added(self):
        extra = pl.DataFrame(
            {"id": [1, 2, 3, 4, 5], "name": ["A", "B", "C", "D", "E"], "score": [1.0] * 5}
        )
        result = diff(SAMPLE_DF, extra)
        assert result.rows_added == 1

    def test_rows_removed(self):
        fewer = SAMPLE_DF.head(2)
        result = diff(SAMPLE_DF, fewer)
        assert result.rows_removed == 2

    def test_columns_added(self):
        with_col = SAMPLE_DF.with_columns(pl.lit("x").alias("extra"))
        result = diff(SAMPLE_DF, with_col)
        assert result.columns_added == ["extra"]

    def test_columns_removed(self):
        without_col = SAMPLE_DF.select("id", "name")
        result = diff(SAMPLE_DF, without_col)
        assert result.columns_removed == ["score"]

    def test_schema_change(self):
        retyped = SAMPLE_DF.with_columns(pl.col("id").cast(pl.Float64))
        result = diff(SAMPLE_DF, retyped)
        assert "id" in result.schema_changes

    def test_key_based_diff(self):
        result = diff(SAMPLE_DF, MODIFIED_DF, key_columns=["id"])
        assert result.rows_modified == 1
        assert result.rows_added == 0
        assert result.rows_removed == 0

    def test_key_based_diff_added_rows(self):
        extra = pl.concat(
            [SAMPLE_DF, pl.DataFrame({"id": [5], "name": ["Eve"], "score": [88.0]})]
        )
        result = diff(SAMPLE_DF, extra, key_columns=["id"])
        assert result.rows_added == 1
        assert result.rows_removed == 0

    def test_key_based_diff_removed_rows(self):
        fewer = SAMPLE_DF.filter(pl.col("id") != 3)
        result = diff(SAMPLE_DF, fewer, key_columns=["id"])
        assert result.rows_removed == 1
        assert result.rows_added == 0

    def test_sample_changes_positional(self):
        result = diff(SAMPLE_DF, MODIFIED_DF, max_sample=5)
        assert len(result.sample_changes) > 0
        # Should contain row index and changed values
        change = result.sample_changes[0]
        assert "_row" in change

    def test_sample_changes_keyed(self):
        result = diff(SAMPLE_DF, MODIFIED_DF, key_columns=["id"], max_sample=5)
        assert len(result.sample_changes) > 0
        change = result.sample_changes[0]
        assert "_keys" in change

    def test_no_shared_columns(self):
        left = pl.DataFrame({"a": [1, 2]})
        right = pl.DataFrame({"b": [3, 4]})
        result = diff(left, right)
        assert result.columns_added == ["b"]
        assert result.columns_removed == ["a"]
        assert result.rows_removed == 2
        assert result.rows_added == 2

    def test_summary_format(self):
        result = diff(SAMPLE_DF, MODIFIED_DF)
        summary = result.summary()
        assert "4×3" in summary
        assert "Rows modified" in summary


# ---------------------------------------------------------------------------
# DiffResult
# ---------------------------------------------------------------------------


class TestDiffResult:
    def test_has_changes_empty(self):
        result = DiffResult()
        assert not result.has_changes

    def test_has_changes_with_adds(self):
        result = DiffResult(rows_added=1)
        assert result.has_changes

    def test_has_changes_with_col_add(self):
        result = DiffResult(columns_added=["x"])
        assert result.has_changes

    def test_summary_no_changes(self):
        result = DiffResult()
        assert result.summary() == "No changes detected."


# ---------------------------------------------------------------------------
# Workspace integration
# ---------------------------------------------------------------------------


class TestWorkspaceVersioning:
    def test_commit_from_workspace(self):
        from sweet.core.workspace import Workspace

        ws = Workspace()
        ws.load_df(SAMPLE_DF, name="data")
        result = ws.commit("initial state")
        assert result["id"]
        assert result["message"] == "initial state"
        assert result["shape"] == (4, 3)

    def test_version_log_from_workspace(self):
        from sweet.core.workspace import Workspace

        ws = Workspace()
        ws.load_df(SAMPLE_DF, name="data")
        ws.commit("first")
        ws.commit("second")
        log = ws.version_log()
        assert len(log) == 2
        assert log[0]["message"] == "second"

    def test_checkout_from_workspace(self):
        from sweet.core.workspace import Workspace

        ws = Workspace()
        ws.load_df(SAMPLE_DF, name="data")
        c = ws.commit("before transform")
        ws.transform("df.with_columns(pl.col('score') * 2)")
        ws.checkout(c["id"])
        assert ws.df.equals(SAMPLE_DF)

    def test_diff_against_commit(self):
        from sweet.core.workspace import Workspace

        ws = Workspace()
        ws.load_df(SAMPLE_DF, name="data")
        ws.commit("baseline")
        ws.transform("df.with_columns(pl.col('score') + 10)")
        result = ws.diff()
        assert result["has_changes"]
        assert result["rows_modified"] == 4

    def test_diff_against_sheet(self):
        from sweet.core.workspace import Workspace

        ws = Workspace()
        ws.load_df(SAMPLE_DF, name="original")
        ws.load_df(MODIFIED_DF, name="modified")
        ws.switch("modified")
        result = ws.diff("original")
        assert result["has_changes"]
        assert result["rows_modified"] == 1

    def test_diff_no_commits_raises(self):
        from sweet.core.workspace import Workspace

        ws = Workspace()
        ws.load_df(SAMPLE_DF, name="data")
        with pytest.raises(ValueError, match="No commits"):
            ws.diff()

    def test_version_log_empty(self):
        from sweet.core.workspace import Workspace

        ws = Workspace()
        ws.load_df(SAMPLE_DF, name="data")
        log = ws.version_log()
        assert log == []

    def test_commit_no_data_raises(self):
        from sweet.core.workspace import Workspace

        ws = Workspace()
        with pytest.raises(ValueError):
            ws.commit("no data")
