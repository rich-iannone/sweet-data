"""Tests for lazy data: sources, lazy steps and sheets, statistics, and TableView."""

import datetime as dt
import io

import polars as pl
import pytest
from polars.testing import assert_frame_equal

from sweet import Workspace
from sweet.core.sources import open_source, sniff_format
from sweet.core.stats import sparkline, summarize
from sweet.core.steps import Step
from sweet.core.view import ROW_ID, TableView


@pytest.fixture
def df():
    return pl.DataFrame(
        {
            "id": list(range(10)),
            "group": ["a", "b"] * 5,
            "value": [5.0, None, 3.0, 9.0, 1.0, 7.0, 2.0, None, 8.0, 4.0],
        }
    )


@pytest.fixture
def parquet_file(tmp_path, df):
    path = tmp_path / "data.parquet"
    df.write_parquet(path)
    return path


# -----------------------------------------------------------------------------
# Sources
# -----------------------------------------------------------------------------


class TestSources:
    def test_small_file_is_eager(self, parquet_file, df):
        opened = open_source(str(parquet_file))
        assert not opened.lazy
        assert opened.name == "data"
        assert opened.source == {"path": str(parquet_file), "format": "parquet"}
        assert_frame_equal(opened.frame, df)

    def test_large_file_is_lazy(self, parquet_file, df):
        opened = open_source(str(parquet_file), eager_threshold=0)
        assert opened.lazy
        assert_frame_equal(opened.frame.collect(), df)

    def test_force_lazy(self, tmp_path, df):
        path = tmp_path / "d.csv"
        df.write_csv(path)
        opened = open_source(str(path), lazy=True)
        assert opened.lazy
        assert_frame_equal(opened.frame.collect(), df)

    def test_tsv(self, tmp_path, df):
        path = tmp_path / "d.tsv"
        df.write_csv(path, separator="\t")
        for lazy in (True, False):
            frame = open_source(str(path), lazy=lazy).frame
            assert_frame_equal(frame.lazy().collect(), df)

    def test_glob(self, tmp_path, df):
        for i in range(3):
            df.with_columns(pl.lit(i).alias("part")).write_parquet(tmp_path / f"p{i}.parquet")
        opened = open_source(str(tmp_path / "p*.parquet"))
        assert opened.lazy
        assert opened.frame.select(pl.len()).collect().item() == 30

    def test_directory_with_hive_partitions(self, tmp_path, df):
        for year in (2024, 2025):
            part = tmp_path / "events" / f"year={year}"
            part.mkdir(parents=True)
            df.write_parquet(part / "0.parquet")
        opened = open_source(str(tmp_path / "events"))
        assert opened.lazy
        assert opened.name == "events"
        out = opened.frame.collect()
        assert out.height == 20
        assert sorted(out["year"].unique().to_list()) == [2024, 2025]

    def test_unknown_extension_reads_as_csv(self, tmp_path):
        path = tmp_path / "notes.txt"
        path.write_text("a,b\n1,2\n")
        assert open_source(str(path)).frame.columns == ["a", "b"]

    def test_missing_file(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            open_source(str(tmp_path / "nope.csv"))

    def test_database_file_points_to_classic(self, tmp_path):
        path = tmp_path / "x.duckdb"
        path.write_bytes(b"")
        with pytest.raises(ValueError, match="--classic"):
            open_source(str(path))

    @pytest.mark.parametrize(
        "data,fmt,columns",
        [
            (b"a,b\n1,2\n3,4\n", "csv", ["a", "b"]),
            (b"a\tb\n1\t2\n", "csv", ["a", "b"]),
            (b'{"a": 1}\n{"a": 2}\n', "ndjson", ["a"]),
            (b'[{"a": 1}, {"a": 2}]', "json", ["a"]),
        ],
    )
    def test_stdin(self, data, fmt, columns):
        assert sniff_format(data) == fmt
        opened = open_source("-", stdin=io.BytesIO(data))
        assert opened.name == "stdin"
        assert opened.frame.columns == columns
        assert opened.source == {"uri": "stdin", "format": fmt}

    def test_empty_stdin(self):
        with pytest.raises(ValueError, match="No data"):
            open_source("-", stdin=io.BytesIO(b"  \n"))


# -----------------------------------------------------------------------------
# Lazy steps and sheets
# -----------------------------------------------------------------------------

LAZY_CASES = [
    Step("filter", {"sql": "value > 3"}),
    Step("filter", {"expr": "pl.col('group') == 'a'"}),
    Step("sort", {"columns": ["value"], "descending": True, "nulls_last": True}),
    Step("select", {"columns": ["id", "value"]}),
    Step("drop", {"columns": ["group"]}),
    Step("rename", {"mapping": {"value": "v"}}),
    Step("cast", {"columns": {"id": "Float64"}}),
    Step("mutate", {"column": "double", "sql": "value * 2"}),
    Step("mutate", {"column": "new", "sql": "id + 1", "position": 1}),
    Step("edit_cell", {"row": 3, "column": "value", "value": 0.5}),
    Step("insert_row", {"values": {"id": 99}, "index": 2}),
    Step("insert_row", {"values": {"id": 100}}),
    Step("delete_rows", {"rows": [0, 5]}),
    Step("polars", {"code": "df.head(3)"}),
    Step("sql", {"query": "SELECT \"group\", count(*) AS n FROM df GROUP BY 1 ORDER BY 1"}),
]


@pytest.mark.parametrize("step", LAZY_CASES, ids=lambda s: s.label)
def test_lazy_matches_eager(step, df):
    lazy_result = step.apply(df.lazy())
    assert isinstance(lazy_result, pl.LazyFrame)
    assert_frame_equal(lazy_result.collect(), step.apply(df))


class TestLazyWorkspace:
    def test_open_lazy_and_steps_stay_lazy(self, parquet_file, df):
        ws = Workspace().read(parquet_file, lazy=True)
        sheet = ws.current_sheet
        assert sheet.is_lazy
        ws.apply_step(Step("filter", {"sql": "value > 3"}))
        ws.apply_step(Step("sort", {"columns": ["value"]}))
        assert sheet.is_lazy  # Nothing materialized
        assert ws.shape == (5, 3)
        assert ws.df["value"].to_list() == [4.0, 5.0, 7.0, 8.0, 9.0]

    def test_undo_restores_plan(self, parquet_file):
        ws = Workspace().read(parquet_file, lazy=True)
        ws.apply_step(Step("filter", {"sql": "value > 3"}))
        ws.undo()
        assert ws.current_sheet.is_lazy
        assert ws.shape == (10, 3)
        ws.redo()
        assert ws.shape == (5, 3)

    def test_eager_only_step_on_lazy_sheet(self, parquet_file):
        ws = Workspace().read(parquet_file, lazy=True)
        ws.apply_step(Step("polars", {"code": "df.head(2)"}))
        assert ws.shape == (2, 3)

    def test_pipeline_replays(self, parquet_file):
        ws = Workspace().read(parquet_file, lazy=True)
        ws.apply_step(Step("filter", {"sql": "value > 3"}))
        ws.apply_step(Step("mutate", {"column": "v2", "sql": "value * 2"}))
        p = ws.pipeline()
        assert p.source == {"path": str(parquet_file), "format": "parquet"}
        assert_frame_equal(p.run(), ws.df)

    def test_open_names_are_unique(self, parquet_file):
        ws = Workspace().read(parquet_file).read(parquet_file)
        assert ws.sheet_names == ["data", "data_2"]
        assert ws.current_sheet_name == "data_2"

    def test_branch_shares_plan(self, parquet_file):
        ws = Workspace().read(parquet_file, lazy=True)
        ws.branch("b")
        assert ws.current_sheet.is_lazy
        ws.apply_step(Step("filter", {"sql": "id < 2"}))
        ws.switch("data")
        assert ws.shape == (10, 3)


# -----------------------------------------------------------------------------
# Statistics
# -----------------------------------------------------------------------------


class TestStats:
    def test_numeric(self, df):
        st = summarize(df, ["value"], bins=4)["value"]
        assert st.kind == "numeric"
        assert (st.count, st.null_count) == (10, 2)
        assert (st.min, st.max) == (1.0, 9.0)
        assert st.mean == pytest.approx(39 / 8)
        assert sum(st.histogram) == 8
        assert st.bin_edges == [1.0, 3.0, 5.0, 7.0, 9.0]
        assert st.quantiles["q50"] in (4.0, 5.0)

    def test_string_top_values(self, df):
        st = summarize(df, ["group"])["group"]
        assert st.kind == "string"
        assert dict(st.top_values) == {"a": 5, "b": 5}

    def test_temporal(self):
        frame = pl.DataFrame({"d": [dt.date(2024, 1, d) for d in (1, 2, 3, 10)]})
        st = summarize(frame, bins=3)["d"]
        assert st.kind == "temporal"
        assert st.min == dt.date(2024, 1, 1)
        assert st.histogram == [3, 0, 1]
        assert st.bin_edges[0] == dt.date(2024, 1, 1)

    def test_lazy_equals_eager(self, df):
        eager = summarize(df)
        lazy = summarize(df.lazy())
        assert {k: v.to_dict() for k, v in eager.items()} == {
            k: v.to_dict() for k, v in lazy.items()
        }

    def test_edge_cases(self):
        frame = pl.DataFrame(
            {
                "const": [7, 7, 7],
                "nulls": pl.Series([None, None, None], dtype=pl.Int64),
                "nested": [[1], [2], None],
            }
        )
        st = summarize(frame)
        assert st["const"].histogram == [3]
        assert st["nulls"].histogram is None and st["nulls"].null_count == 3
        assert st["nested"].kind == "other" and st["nested"].n_unique is None

    def test_to_dict_is_json_safe(self, df):
        import json

        json.dumps({k: v.to_dict() for k, v in summarize(df).items()})

    def test_sparkline(self):
        assert sparkline([0, 1, 2, 4]) == " ▃▅█"
        assert sparkline([]) == ""


# -----------------------------------------------------------------------------
# TableView
# -----------------------------------------------------------------------------


@pytest.fixture
def big_ws(tmp_path):
    frame = pl.DataFrame({"i": range(1000), "k": [i % 7 for i in range(1000)]})
    path = tmp_path / "big.parquet"
    frame.write_parquet(path)
    return Workspace().read(path, lazy=True)


class TestTableView:
    def test_windows_across_chunks(self, big_ws):
        view = TableView(big_ws, "big", chunk_size=64)
        window = view.fetch(60, 10)
        assert window["i"].to_list() == list(range(60, 70))
        assert window[ROW_ID].to_list() == list(range(60, 70))
        assert view.row(65) == {ROW_ID: 65, "i": 65, "k": 65 % 7}

    def test_row_count(self, big_ws):
        view = TableView(big_ws, "big")
        assert view.known_row_count is None
        assert view.row_count() == 1000
        assert view.known_row_count == 1000

    def test_row_count_learned_from_last_chunk(self, big_ws):
        view = TableView(big_ws, "big", chunk_size=300)
        view.load_chunk(3)
        assert view.known_row_count == 1000

    def test_sort_keeps_row_ids(self, big_ws):
        view = TableView(big_ws, "big")
        view.toggle_sort("k", descending=True)
        window = view.fetch(0, 3)
        assert window["k"].to_list() == [6, 6, 6]
        assert window[ROW_ID].to_list() == [6, 13, 20]
        view.toggle_sort("k", descending=True)  # Same request again removes it
        assert view.sort == []

    def test_sort_unknown_column(self, big_ws):
        with pytest.raises(ValueError, match="not found"):
            TableView(big_ws, "big").set_sort([("nope", False)])

    def test_edit_through_row_id(self, big_ws):
        view = TableView(big_ws, "big")
        view.toggle_sort("i", descending=True)
        target = view.fetch(0, 1).row(0, named=True)  # i == 999
        big_ws.apply_step(Step("edit_cell", {"row": target[ROW_ID], "column": "k", "value": -1}))
        view.invalidate()
        assert view.fetch(0, 1)["k"].to_list() == [-1]

    def test_invalidate_after_step(self, big_ws):
        view = TableView(big_ws, "big")
        assert view.row_count() == 1000
        big_ws.apply_step(Step("filter", {"sql": "k = 0"}))
        view.invalidate()
        assert view.row_count() == 143

    def test_stats_cached(self, big_ws):
        view = TableView(big_ws, "big")
        first = view.stats(["k"])["k"]
        assert view.cached_stats("k") is first
        assert first.n_unique == 7

    def test_markdown(self, big_ws):
        text = TableView(big_ws, "big").to_markdown(0, 2)
        assert text.splitlines()[0] == "| # | i | k |"
        assert text.splitlines()[2] == "| 0 | 0 | 0 |"
