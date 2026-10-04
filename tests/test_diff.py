"""Tests for the diff engine (sweet.core.diff)."""

import datetime as dt
import json

import polars as pl
import pytest

from sweet.core.diff import OLD_PREFIX, STATUS, diff_frames, diff_step, old_column
from sweet.core.steps import Step
from sweet.core.view import ROW_ID


@pytest.fixture
def df():
    return pl.DataFrame({"id": [1, 2, 3, 4], "name": ["a", "b", "c", "d"], "v": [10, None, 30, 40]})


class TestKeyedDiff:
    def test_added_removed_changed(self):
        before = pl.DataFrame({"k": [1, 2, 3], "x": [1, 2, 3]})
        after = pl.DataFrame({"k": [2, 3, 4], "x": [2, 30, 4], "y": [0, 0, 0]})
        d = diff_frames(before, after, key="k")
        assert (d.rows_added, d.rows_removed, d.rows_changed, d.rows_unchanged) == (1, 1, 1, 1)
        assert (d.rows_before, d.rows_after) == (3, 3)
        assert d.added_columns == ["y"]
        assert d.changed_by_column == {"x": 1}
        assert d.summary() == "+1 rows · −1 rows · +1 col · 1 col changed (1 cell)"

    def test_frame_has_status_and_old_values(self):
        before = pl.DataFrame({"k": [1, 2], "x": [1, 2]})
        after = pl.DataFrame({"k": [1, 2], "x": [1, 20]})
        frame = diff_frames(before, after, key="k").frame.collect()
        assert frame[STATUS].to_list() == ["=", "~"]
        assert frame[old_column("x")].to_list() == [None, 2]
        assert frame["x"].to_list() == [1, 20]

    def test_removed_rows_show_before_values_in_before_order(self):
        before = pl.DataFrame({"k": [5, 3, 9], "x": ["e", "c", "i"]})
        after = pl.DataFrame({"k": [9, 7], "x": ["i", "g"]})
        frame = diff_frames(before, after, key="k").frame.collect()
        assert frame.select("k", "x", STATUS).rows() == [
            (5, "e", "-"),
            (3, "c", "-"),
            (9, "i", "="),
            (7, "g", "+"),
        ]

    def test_nulls_compare_equal(self):
        before = pl.DataFrame({"k": [1], "x": [None]}, schema={"k": pl.Int64, "x": pl.Int64})
        d = diff_frames(before, before, key="k")
        assert not d.has_changes

    def test_type_change_without_value_change(self):
        before = pl.DataFrame({"k": [1, 2], "x": [1, 2]})
        after = before.with_columns(pl.col("x").cast(pl.String))
        d = diff_frames(before, after, key="k")
        assert d.type_changes == {"x": ("Int64", "String")}
        assert d.changed_by_column == {"x": 0}
        assert d.has_changes

    def test_composite_key(self):
        before = pl.DataFrame({"a": [1, 1], "b": ["x", "y"], "v": [1, 2]})
        after = pl.DataFrame({"a": [1, 1], "b": ["x", "y"], "v": [1, 3]})
        assert diff_frames(before, after, key=["a", "b"]).rows_changed == 1

    def test_missing_key(self):
        with pytest.raises(ValueError, match="not in both"):
            diff_frames(pl.DataFrame({"a": [1]}), pl.DataFrame({"b": [1]}), key="a")

    def test_without_key_counts_only(self):
        d = diff_frames(pl.DataFrame({"a": [1, 2]}), pl.DataFrame({"a": [1]}))
        assert d.key is None and d.frame is None
        assert d.summary() == "-1 rows"
        assert d.has_changes

    def test_lazy_inputs(self):
        before = pl.LazyFrame({"k": [1, 2], "x": [1, 2]})
        after = pl.LazyFrame({"k": [1], "x": [5]})
        d = diff_frames(before, after, key="k")
        assert (d.rows_changed, d.rows_removed) == (1, 1)

    def test_temporal_values(self):
        before = pl.DataFrame({"k": [1], "d": [dt.date(2024, 1, 1)]})
        after = pl.DataFrame({"k": [1], "d": [dt.date(2024, 1, 2)]})
        assert diff_frames(before, after, key="k").changed_by_column == {"d": 1}


class TestReports:
    def test_samples_and_json(self):
        before = pl.DataFrame({"k": [1, 2], "x": [1, 2]})
        after = pl.DataFrame({"k": [2, 3], "x": [20, 3]})
        d = diff_frames(before, after, key="k")
        samples = d.samples(10)
        assert {s["status"] for s in samples} == {"-", "~", "+"}
        changed = next(s for s in samples if s["status"] == "~")
        assert changed == {"status": "~", "key": {"k": 2}, "changes": {"x": {"from": 2, "to": 20}}}
        data = json.loads(d.to_json(sample=2))
        assert data["rows"]["added"] == 1 and len(data["samples"]) == 2

    def test_markdown(self):
        d = diff_frames(
            pl.DataFrame({"k": [1], "x": [1]}), pl.DataFrame({"k": [1], "x": [2]}), key="k"
        )
        md = d.to_markdown()
        assert md.startswith("**Diff:** 1 col changed (1 cell)")
        assert "| x | 1 |" in md


class TestStepDiffs:
    @pytest.mark.parametrize(
        "step,summary",
        [
            (Step("filter", {"sql": "v > 15"}), "−2 rows"),
            (Step("mutate", {"column": "v", "sql": "v * 2"}), "1 col changed (3 cells)"),
            (
                Step("edit_cell", {"row": 1, "column": "name", "value": "B"}),
                "1 col changed (1 cell)",
            ),
            (Step("sort", {"columns": ["v"], "descending": True}), "rows reordered"),
            (Step("select", {"columns": ["name"]}), "−2 cols"),
            (Step("insert_row", {"values": {"id": 9}}), "+1 rows"),
            (Step("delete_rows", {"rows": [0]}), "−1 rows"),
            (Step("mutate", {"column": "w", "sql": "v + 1"}), "+1 col"),
            (Step("rename", {"mapping": {"name": "label"}}), "+1 col · −1 col"),
        ],
        ids=lambda x: x.label if isinstance(x, Step) else "",
    )
    def test_summaries(self, df, step, summary):
        assert diff_step(df, step).summary() == summary

    def test_aggregation_falls_back_to_counts(self, df):
        d = diff_step(df, Step("sql", {"query": "SELECT count(*) AS n FROM df"}))
        assert d.key is None
        assert d.rows_before == 4 and d.rows_after == 1

    def test_lineage_key_hidden_from_display_columns(self, df):
        d = diff_step(df, Step("filter", {"sql": "v > 15"}))
        assert d.key == [ROW_ID]
        cols = d.frame.collect_schema().names()
        assert cols[:4] == [ROW_ID, "id", "name", "v"]
        assert all(c.startswith("__sweet_") for c in cols if c not in ("id", "name", "v"))
        assert any(c.startswith(OLD_PREFIX) for c in cols)

    def test_sorted_preview_uses_after_order(self, df):
        frame = diff_step(df, Step("sort", {"columns": ["id"], "descending": True})).frame.collect()
        assert frame["id"].to_list() == [4, 3, 2, 1]

    def test_lazy_step_diff(self, df):
        d = diff_step(df.lazy(), Step("filter", {"sql": "v > 15"}))
        assert d.rows_removed == 2
