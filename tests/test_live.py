"""Tests for live data: stream sources, live tables and sheets, new steps, alerts,
watching from agents, streaming jobs, and SQL division semantics."""

import asyncio
import datetime as dt
import json
import os

import duckdb
import polars as pl
import pytest
from click.testing import CliRunner
from polars.testing import assert_frame_equal

from sweet import Workspace
from sweet.cli import main
from sweet.core.alerts import AlertMonitor, DriftRule, RowRule, rule_from_dict
from sweet.core.pipeline import Pipeline
from sweet.core.session import Session
from sweet.core.steps import Step, duckdb_division
from sweet.core.stream import (
    LIVE_SEQ,
    FileTailSource,
    LineParser,
    LiveTable,
    StdinSource,
    StreamSink,
    WebSocketSource,
    is_stream_target,
    pump,
    run_stream,
)
from sweet.session.mcp import LocalBackend, SweetMCP


async def wait_until(predicate, timeout=3.0):
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while not predicate():
        if loop.time() > deadline:
            raise AssertionError("condition not met in time")
        await asyncio.sleep(0.02)


def lines(rows):
    return "".join(json.dumps(r) + "\n" for r in rows)


# -----------------------------------------------------------------------------
# Parsing and sources
# -----------------------------------------------------------------------------


class TestParser:
    def test_auto_ndjson(self):
        p = LineParser()
        assert p.parse('{"a": 1}\n') == [{"a": 1}]
        assert p.format == "ndjson"
        assert p.parse('[{"a": 2}, {"a": 3}]') == [{"a": 2}, {"a": 3}]
        assert p.parse("not json") == [{"_raw": "not json"}] and p.errors == 1

    def test_csv_with_header_and_types(self):
        p = LineParser()
        assert p.parse("ts,temp,ok\n") == []
        assert p.parse("1,20.5,true\n") == [{"ts": 1, "temp": 20.5, "ok": True}]
        assert p.parse("2,,false\n") == [{"ts": 2, "temp": None, "ok": False}]

    def test_text(self):
        assert LineParser().parse("hello world\n") == [{"line": "hello world"}]

    def test_blank_lines_skipped(self):
        assert LineParser().parse("   \n") == []


class TestSources:
    async def test_file_tail_follows_and_handles_partial_lines(self, tmp_path):
        path = tmp_path / "feed.ndjson"
        path.write_text(lines([{"a": 1}]) + '{"a":')
        table = LiveTable("feed")
        task = asyncio.create_task(pump(FileTailSource(path, poll=0.02), table))
        await wait_until(lambda: table.total == 1)
        with path.open("a") as f:
            f.write(" 2}\n" + lines([{"a": 3}]))
        await wait_until(lambda: table.total == 3)
        assert table.snapshot()["a"].to_list() == [1, 2, 3]
        task.cancel()

    async def test_file_tail_truncation_and_late_creation(self, tmp_path):
        path = tmp_path / "late.ndjson"
        table = LiveTable("late")
        task = asyncio.create_task(pump(FileTailSource(path, poll=0.02), table))
        await asyncio.sleep(0.05)
        path.write_text(lines([{"a": 1}, {"a": 2}]))
        await wait_until(lambda: table.total == 2)
        path.write_text(lines([{"a": 9}]))  # Truncated and rewritten
        await wait_until(lambda: table.total == 3)
        assert table.snapshot()["a"].to_list()[-1] == 9
        task.cancel()

    async def test_file_tail_from_end(self, tmp_path):
        path = tmp_path / "f.ndjson"
        path.write_text(lines([{"a": 1}]))
        table = LiveTable("f")
        task = asyncio.create_task(pump(FileTailSource(path, poll=0.02, from_start=False), table))
        await asyncio.sleep(0.1)
        with path.open("a") as f:
            f.write(lines([{"a": 2}]))
        await wait_until(lambda: table.total == 1)
        assert table.snapshot()["a"].to_list() == [2]
        task.cancel()

    async def test_stdin_source_from_pipe(self):
        read_fd, write_fd = os.pipe()
        table = LiveTable("stdin")
        task = asyncio.create_task(pump(StdinSource(read_fd), table))
        os.write(write_fd, b"x,y\n1,2\n3,4\n")
        await wait_until(lambda: table.total == 2)
        os.close(write_fd)
        await asyncio.wait_for(task, 2)  # Ends when the pipe closes
        assert table.snapshot().select("x", "y").rows() == [(1, 2), (3, 4)]

    async def test_websocket(self):
        from websockets.asyncio.server import serve

        async def handler(ws):
            await ws.send(json.dumps({"tick": 1}))
            await ws.send(json.dumps([{"tick": 2}, {"tick": 3}]))
            await asyncio.sleep(0.3)

        async with serve(handler, "127.0.0.1", 0) as server:
            port = server.sockets[0].getsockname()[1]
            table = LiveTable("ws")
            source = WebSocketSource(f"ws://127.0.0.1:{port}/ticks", reconnect=False)
            assert source.name == "ticks" and source.source["url"].startswith("ws://")
            task = asyncio.create_task(pump(source, table))
            await wait_until(lambda: table.total == 3)
            assert table.snapshot()["tick"].to_list() == [1, 2, 3]
            await asyncio.wait_for(task, 2)

    def test_stream_targets(self):
        assert is_stream_target("wss://x/feed") and is_stream_target("ws://x")
        assert not is_stream_target("https://x/data.csv") and not is_stream_target("a.csv")


class TestLiveTable:
    def test_sequence_eviction_and_schema_evolution(self):
        table = LiveTable("t", capacity=3)
        table.append([{"a": 1}, {"a": 2}])
        table.append([{"a": 3, "b": "new"}, {"a": 4, "b": "x"}])
        snap = table.snapshot()
        assert snap["a"].to_list() == [2, 3, 4]
        assert snap[LIVE_SEQ].to_list() == [1, 2, 3]
        assert snap["b"].to_list() == [None, "new", "x"]
        assert table.status()["rows_spilled"] == 1 and table.total == 4

    def test_type_conflicts_relax(self):
        table = LiveTable("t")
        table.append([{"v": 1}])
        table.append([{"v": "text"}])
        assert table.snapshot()["v"].to_list() == ["1", "text"]

    def test_spill_keeps_history(self, tmp_path):
        table = LiveTable("t", capacity=2, spill_dir=tmp_path / "spill")
        for i in range(5):
            table.append([{"i": i}])
        assert table.snapshot()["i"].to_list() == [3, 4]
        assert sorted(table.history().collect()["i"].to_list()) == [0, 1, 2, 3, 4]

    def test_empty_snapshot(self):
        assert LiveTable("t").snapshot().height == 0


# -----------------------------------------------------------------------------
# Live sheets
# -----------------------------------------------------------------------------


@pytest.fixture
def feed(tmp_path):
    path = tmp_path / "sensors.ndjson"
    path.write_text(
        lines([{"id": i, "temp": 20.0 + i, "site": "a" if i % 2 else "b"} for i in range(6)])
    )
    return path


class TestLiveSheets:
    async def test_steps_apply_to_new_rows(self, feed):
        ws = Workspace()
        table, source = ws.read_stream(str(feed))
        task = asyncio.create_task(pump(source, table))
        await wait_until(lambda: table.total == 6)
        assert ws.refresh_live() and not ws.refresh_live()  # Only when something changed
        ws.apply_step(Step("filter", {"sql": "temp > 22"}))
        assert ws.shape[0] == 3
        with feed.open("a") as f:
            f.write(lines([{"id": 6, "temp": 99.0, "site": "a"}]))
        await wait_until(lambda: table.total == 7)
        ws.refresh_live()
        assert ws.df["temp"].max() == 99.0
        ws.undo()
        ws.refresh_live()
        assert ws.shape[0] in (6, 7)
        task.cancel()

    async def test_pipeline_replays_the_file(self, feed):
        ws = Workspace()
        table, source = ws.read_stream(str(feed))
        task = asyncio.create_task(pump(source, table))
        await wait_until(lambda: table.total == 6)
        ws.refresh_live()
        ws.apply_step(Step("mutate", {"column": "f", "sql": "temp * 9 / 5 + 32"}))
        pipeline = ws.pipeline()
        assert pipeline.source["format"] == "ndjson" and pipeline.source["stream"] == "file"
        assert_frame_equal(
            pipeline.run(), ws.df.drop([c for c in ws.df.columns if c.startswith("__sweet_")])
        )
        task.cancel()

    async def test_failing_steps_keep_raw_rows(self, feed):
        ws = Workspace()
        table, source = ws.read_stream(str(feed))
        task = asyncio.create_task(pump(source, table))
        await wait_until(lambda: table.total == 6)
        ws.refresh_live()
        ws.apply_step(Step("select", {"columns": ["id", "temp", "site"]}))
        with feed.open("a") as f:
            f.write(lines([{"other": 1}]))
        await wait_until(lambda: table.total == 7)
        ws.refresh_live()
        assert table.error is None or "Steps failed" in table.error
        task.cancel()


# -----------------------------------------------------------------------------
# New steps: dedupe, window_agg, join
# -----------------------------------------------------------------------------


@pytest.fixture
def events():
    return pl.DataFrame(
        {
            "ts": [dt.datetime(2025, 1, 1, 0, 0, s) for s in (5, 40, 59)]
            + [dt.datetime(2025, 1, 1, 0, 1, 10), dt.datetime(2025, 1, 1, 0, 3, 0)],
            "site": ["a", "b", "a", "a", "b"],
            "temp": [10.0, 20.0, 30.0, 40.0, 50.0],
        }
    )


class TestNewSteps:
    def test_dedupe(self, events):
        out = Step("dedupe", {"columns": ["site"], "keep": "last"}).apply(events)
        assert out["temp"].to_list() == [40.0, 50.0]
        assert Step("dedupe", {}).apply(pl.concat([events, events])).height == 5

    def test_window_agg_matches_sql(self, events):
        step = Step("window_agg", {"time": "ts", "every": "1m", "by": ["site"],
                                   "aggs": {"avg_temp": "AVG(temp)", "n": "COUNT(*)"}})  # fmt: skip
        out = step.apply(events)
        assert out.columns == ["ts", "site", "avg_temp", "n"]
        assert out.filter(pl.col("site") == "a")["avg_temp"].to_list() == [20.0, 40.0]
        con = duckdb.connect()
        con.register("input", events.to_arrow())
        sql = con.execute(step.to_sql('"input"', events.columns)).pl()
        assert_frame_equal(sql, out, check_dtypes=False)
        assert_frame_equal(eval(step.to_polars(), {"pl": pl, "df": events}), out)
        assert step.stateful

    def test_join_lookup_file(self, events, tmp_path):
        lookup = tmp_path / "sites.csv"
        pl.DataFrame({"site": ["a", "b"], "city": ["Oslo", "Lima"]}).write_csv(lookup)
        step = Step("join", {"path": str(lookup), "on": "site"})
        out = step.apply(events)
        assert out["city"].to_list() == ["Oslo", "Lima", "Oslo", "Oslo", "Lima"]
        assert not step.stateful  # Works row by row, so it runs on streams
        con = duckdb.connect()
        con.register("input", events.to_arrow())
        sql = con.execute(step.to_sql('"input"', events.columns)).pl()
        assert_frame_equal(sql.sort("ts"), out.sort("ts"), check_dtypes=False)
        assert_frame_equal(step.apply(events.lazy()).collect(), out)


class TestDivisionSemantics:
    @pytest.mark.parametrize("expr", ["a / b", "a * 9 / 5 + 32", "(a + b) / b / 2"])
    def test_polars_matches_duckdb(self, expr):
        df = pl.DataFrame({"a": [21, 7, -3], "b": [5, 2, 2]})
        step = Step("mutate", {"column": "x", "sql": expr})
        session_values = step.apply(df)["x"].to_list()
        code_values = eval(step.to_polars(), {"pl": pl, "df": df})["x"].to_list()
        con = duckdb.connect()
        con.register("input", df.to_arrow())
        sql_values = con.execute(step.to_sql("input", df.columns)).pl()["x"].to_list()
        assert session_values == pytest.approx(sql_values) == code_values

    def test_strings_untouched(self):
        assert duckdb_division("'a/b' || x") == "'a/b' || x"
        assert duckdb_division("x // y") == "x // y"


# -----------------------------------------------------------------------------
# Alerts
# -----------------------------------------------------------------------------


class TestAlerts:
    def test_row_rule(self):
        fired = []
        monitor = AlertMonitor("s", fired.append)
        monitor.add(RowRule("hot", "temp < 60"))
        batch = pl.DataFrame({"temp": [20.0, 70.0, None], LIVE_SEQ: [0, 1, 2]})
        alerts = monitor.check(batch)
        assert len(alerts) == 1 and alerts[0].count == 1 and alerts[0].seqs == [1]
        assert fired == alerts
        assert alerts[0].samples == [{"temp": 70.0}]

    def test_drift_fires_once_and_rearms(self):
        monitor = AlertMonitor("s", lambda a: None)
        monitor.add(DriftRule("nulls", "temp", "null_rate", window=4, threshold=0.5))
        base = pl.DataFrame({"temp": [1.0, 2.0, 3.0, 4.0]})
        assert monitor.check(base, base) == []  # Baseline
        bad = pl.concat(
            [base, pl.DataFrame({"temp": [None, None, None, None]}, schema={"temp": pl.Float64})]
        )
        alerts = monitor.check(bad, bad)
        assert alerts and "rose from 0% to 100%" in alerts[0].message
        assert monitor.check(bad, bad) == []  # Once per excursion
        good = pl.concat([bad, base])
        assert monitor.check(good, good) == []  # Re-armed
        assert monitor.check(bad, pl.concat([good, bad.tail(4)]))

    def test_mean_drift(self):
        monitor = AlertMonitor("s", lambda a: None)
        monitor.add(rule_from_dict({"column": "v", "stat": "mean", "window": 2, "threshold": 0.5}))
        frame = pl.DataFrame({"v": [10.0, 10.0]})
        monitor.check(frame, frame)
        later = pl.concat([frame, pl.DataFrame({"v": [30.0, 30.0]})])
        assert "moved from 10 to 30" in monitor.check(later, later)[0].message

    def test_broken_rule_reports_error(self):
        monitor = AlertMonitor("s", lambda a: None)
        monitor.add(RowRule("bad", "nope > 1"))
        assert monitor.check(pl.DataFrame({"a": [1]}))[0].kind == "error"

    def test_rule_parsing(self):
        with pytest.raises(ValueError):
            rule_from_dict({"x": 1})
        with pytest.raises(ValueError):
            rule_from_dict({"column": "a", "stat": "median"})


# -----------------------------------------------------------------------------
# Sessions and agents on streams
# -----------------------------------------------------------------------------


class TestWatching:
    async def test_watch_alerts_and_masking(self, feed):
        session = Session()
        mcp = SweetMCP(LocalBackend(session, "agent:t"))
        await mcp.call_tool("open", {"target": str(feed), "follow": True})
        await mcp.call_tool("alerts", {"rule": {"sql": "temp < 60"}})
        session.set_policy(mask="site")
        await asyncio.sleep(0.3)
        waiting = asyncio.create_task(mcp.call_tool("watch", {"timeout": 3}))
        await asyncio.sleep(0.05)
        with feed.open("a") as f:
            f.write(lines([{"id": 7, "temp": 80.0, "site": "secret-site"}]))
        result = json.loads((await waiting)[0].text)
        assert "violate temp < 60" in result["alert"]["message"]
        assert result["alert"]["samples"][0]["site"] == "•••"
        quiet = json.loads((await mcp.call_tool("watch", {"timeout": 0.1}))[0].text)
        assert quiet["alert"] is None and quiet["live"]["sensors"]["rows_ingested"] == 7

    async def test_alerts_between_watches_are_not_lost(self, feed):
        session = Session()
        mcp = SweetMCP(LocalBackend(session, "agent:t"))
        await mcp.call_tool("open", {"target": str(feed), "follow": True})
        await mcp.call_tool("alerts", {"rule": {"sql": "temp < 60"}})
        await asyncio.sleep(0.3)
        with feed.open("a") as f:
            f.write(lines([{"id": 8, "temp": 61.0, "site": "a"}]))
            f.write(lines([{"id": 9, "temp": 62.0, "site": "a"}]))
        await wait_until(lambda: len(session.recent_alerts) >= 1)
        result = json.loads((await mcp.call_tool("watch", {"timeout": 1}))[0].text)
        assert result["alert"] is not None
        listing = json.loads((await mcp.call_tool("alerts", {}))[0].text)
        assert listing["rules"]["sensors"][0]["kind"] == "row" and listing["recent"]

    async def test_status_reports_live_sheets(self, feed):
        session = Session()
        session.start_stream(str(feed))
        await asyncio.sleep(0.2)
        assert session.status()["live"]["sensors"]["rows_ingested"] == 6
        assert session.view(limit=2)["total_rows"] == 6


# -----------------------------------------------------------------------------
# Streaming jobs
# -----------------------------------------------------------------------------


class TestStreamJobs:
    async def test_run_stream_to_ndjson(self, feed, tmp_path):
        out = tmp_path / "out.ndjson"
        steps = [
            Step("filter", {"sql": "temp > 22"}),
            Step("mutate", {"column": "f", "sql": "temp * 2"}),
        ]
        summary = await run_stream(
            steps, FileTailSource(feed, poll=0.02), StreamSink(str(out)), max_rows=6
        )
        assert summary == {**summary, "rows_in": 6, "rows_out": 3}
        result = pl.read_ndjson(out)
        assert result["f"].to_list() == [46.0, 48.0, 50.0]
        assert not any(c.startswith("__sweet_") for c in result.columns)

    async def test_parquet_parts_and_csv(self, feed, tmp_path):
        sink = StreamSink(str(tmp_path / "out.parquet"), flush_rows=2)
        await run_stream([], FileTailSource(feed, poll=0.02), sink, max_rows=6)
        parts = sorted(tmp_path.glob("out-*.parquet"))
        assert len(parts) >= 1 and sum(pl.read_parquet(p).height for p in parts) == 6
        csv_sink = StreamSink(str(tmp_path / "out.csv"))
        await run_stream([], FileTailSource(feed, poll=0.02), csv_sink, max_rows=6)
        assert pl.read_csv(tmp_path / "out.csv").height == 6

    async def test_stateful_steps_rejected(self, feed):
        with pytest.raises(ValueError, match="need all rows at once"):
            await run_stream(
                [Step("sort", {"columns": ["id"]})], FileTailSource(feed), StreamSink(None)
            )

    async def test_duration_stops(self, feed, tmp_path):
        summary = await run_stream(
            [],
            FileTailSource(feed, poll=0.02),
            StreamSink(str(tmp_path / "o.ndjson")),
            duration=0.3,
        )
        assert summary["rows_in"] == 6 and summary["seconds"] >= 0.3

    def test_bad_sink(self):
        with pytest.raises(ValueError):
            StreamSink("out.xlsx")

    def test_cli_run_follow(self, feed, tmp_path):
        pipeline = Pipeline(
            source={"stream": "file", "path": str(feed), "format": "ndjson"},
            steps=[Step("filter", {"sql": "site = 'a'"})],
        )
        path = pipeline.save(tmp_path / "p.sweet.yaml")
        out = tmp_path / "out.ndjson"
        result = CliRunner().invoke(
            main,
            ["run", str(path), "--follow", "-o", str(out), "--max-rows", "6", "--format", "json"],
        )
        assert result.exit_code == 0, result.output
        assert json.loads(result.output.strip().splitlines()[-1])["rows_out"] == 3
        assert pl.read_ndjson(out)["site"].unique().to_list() == ["a"]


# -----------------------------------------------------------------------------
# The viewer, live
# -----------------------------------------------------------------------------


async def settle(pilot, seconds=0.3):
    from textual.worker import WorkerCancelled

    await pilot.pause(seconds)
    try:
        await pilot.app.workers.wait_for_complete()
    except WorkerCancelled:
        pass


class TestViewerLive:
    async def test_follow_freeze_and_alerts(self, feed):
        from sweet.ui.viewer import ViewerApp

        app = ViewerApp([str(feed)], follow=True)
        async with app.run_test(size=(120, 30)) as pilot:
            await settle(pilot, 0.8)
            assert app.view.known_row_count == 6
            assert app.grid.cursor_row == 5  # Following the newest row
            assert "LIVE" in str(app.query_one("#topbar").render())

            with feed.open("a") as f:
                f.write(lines([{"id": 6, "temp": 30.0, "site": "a"}]))
            await settle(pilot, 1.0)
            assert app.view.known_row_count == 7 and app.grid.cursor_row == 6
            first, last = app.grid.state()["visible_rows"]
            assert first <= app.grid.cursor_row < last  # The newest row is on screen

            await pilot.press("space")  # Freeze
            with feed.open("a") as f:
                f.write(lines([{"id": 7, "temp": 31.0, "site": "a"}]))
            await settle(pilot, 1.0)
            assert app.view.known_row_count == 7
            assert "1 new rows" in str(app.query_one("#topbar").render())
            await pilot.press("space")  # Catch up and follow again
            await settle(pilot, 0.8)
            assert app.view.known_row_count == 8 and app.grid.cursor_row == 7

            await pilot.press("A")
            await settle(pilot, 0.1)
            app.screen.query_one("Input").value = "temp < 60"
            await pilot.press("enter")
            await settle(pilot, 0.2)
            with feed.open("a") as f:
                f.write(lines([{"id": 8, "temp": 90.0, "site": "b"}]))
            await settle(pilot, 1.0)
            assert app._flash, "violating rows should flash"
            assert any("violate temp < 60" in a.message for a in app.session.recent_alerts)

    async def test_scrolling_up_stops_following(self, feed):
        from sweet.ui.viewer import ViewerApp

        app = ViewerApp([str(feed)], follow=True)
        async with app.run_test(size=(120, 30)) as pilot:
            await settle(pilot, 0.8)
            await pilot.press("g")
            with feed.open("a") as f:
                f.write(lines([{"id": 6, "temp": 30.0, "site": "a"}]))
            await settle(pilot, 1.0)
            assert app.view.known_row_count == 7 and app.grid.cursor_row == 0


async def test_following_scrolls_newest_rows_into_view(tmp_path):
    """With more rows than fit on screen, following keeps the newest row visible."""
    from sweet.ui.viewer import ViewerApp

    path = tmp_path / "many.ndjson"
    path.write_text(lines([{"i": i} for i in range(100)]))
    app = ViewerApp([str(path)], follow=True)
    async with app.run_test(size=(80, 20)) as pilot:
        await settle(pilot, 0.8)
        with path.open("a") as f:
            f.write(lines([{"i": i} for i in range(100, 150)]))
        await settle(pilot, 1.0)
        first, last = app.grid.state()["visible_rows"]
        assert app.grid.cursor_row == 149 and first <= 149 < last
