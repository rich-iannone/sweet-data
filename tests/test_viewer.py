"""Pilot tests for the new viewer (sweet.ui.viewer)."""

import asyncio

import polars as pl
import pytest
from polars.testing import assert_frame_equal
from textual.worker import WorkerCancelled

from sweet.core.pipeline import Pipeline
from sweet.ui.viewer import ViewerApp
from sweet.ui.viewer.commands import COMMANDS
from sweet.ui.viewer.data_view import HEADER_LINES, format_value, short_type
from sweet.ui.viewer.panels import OverviewScreen, PromptScreen

SIZE = (120, 32)
N = 5_000


@pytest.fixture
def trips(tmp_path):
    df = pl.DataFrame({"trip_id": pl.int_range(0, N, eager=True)}).with_columns(
        vendor=pl.when(pl.col("trip_id") % 3 == 0)
        .then(pl.lit("CMT"))
        .when(pl.col("trip_id") % 3 == 1)
        .then(pl.lit("VTS"))
        .otherwise(None),
        fare=(pl.col("trip_id") * 7919 % 1000) / 20.0,
    )
    path = tmp_path / "trips.parquet"
    df.write_parquet(path)
    return path


async def settle(pilot, seconds: float = 0.4):
    await pilot.pause(seconds)
    try:
        await pilot.app.workers.wait_for_complete()
    except WorkerCancelled:
        pass  # Superseded background work (e.g. stats for a previous sheet)
    await pilot.pause(0.05)


async def wait_for(pilot, predicate, timeout: float = 5.0):
    """Wait until `predicate()` is true (rows load asynchronously after cursor moves)."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    await settle(pilot, 0.05)  # Always let pending messages run first
    while not predicate():
        if loop.time() > deadline:
            raise AssertionError("condition not met in time")
        await settle(pilot, 0.05)


async def answer_prompt(pilot, text: str):
    assert isinstance(pilot.app.screen, PromptScreen)
    pilot.app.screen.query_one("Input").value = text
    await pilot.press("enter")
    await settle(pilot)


async def test_opens_lazily_with_counts_and_stats(trips):
    app = ViewerApp([str(trips)], lazy=True)
    async with app.run_test(size=SIZE) as pilot:
        await settle(pilot)
        assert app.view.is_lazy
        state = app.screen_state()
        assert state["total_rows"] == N and state["row_count_known"]
        assert state["columns"] == ["trip_id", "vendor", "fare"]
        assert app.view.cached_stats("fare") is not None
        header = app.grid.render_line(0).text
        assert "trip_id" in header and "fare" in header
        assert "5,000 rows × 3 cols" in str(app.query_one("#topbar").render())


async def test_scrolls_to_the_bottom(trips):
    app = ViewerApp([str(trips)], lazy=True)
    async with app.run_test(size=SIZE) as pilot:
        await settle(pilot)
        await pilot.press("G")
        await settle(pilot)
        assert app.grid.cursor_row == N - 1
        assert app.grid.current_row()["trip_id"] == N - 1
        last_line = app.grid.render_line(app.grid.size.height - 1).text
        assert f"{N:,}" in last_line
        await pilot.press("g")
        await settle(pilot)
        assert app.grid.current_row()["trip_id"] == 0


async def test_sort_is_a_view(trips):
    app = ViewerApp([str(trips)], lazy=True)
    async with app.run_test(size=SIZE) as pilot:
        await settle(pilot)
        app.grid.move_cursor(column="fare")
        await pilot.press("S")
        await settle(pilot)
        assert app.grid.current_row()["fare"] == 49.95
        assert app.view.sort == [("fare", True)]
        assert app.screen_state()["steps"] == []
        await pilot.press("c")
        await settle(pilot)
        assert app.grid.current_row()["trip_id"] == 0


async def test_filter_and_exclude_value(trips):
    app = ViewerApp([str(trips)], lazy=True)
    async with app.run_test(size=SIZE) as pilot:
        await settle(pilot)
        app.grid.move_cursor(row=0, column="vendor")  # CMT
        await settle(pilot)
        await pilot.press("f")
        await settle(pilot)
        assert app.view.known_row_count == 1667
        assert app.screen_state()["steps"] == ["Filter: vendor = 'CMT'"]
        await pilot.press("u")
        await settle(pilot)
        assert app.view.known_row_count == N
        await pilot.press("exclamation_mark")
        await settle(pilot)
        assert app.view.known_row_count == N - 1667  # Nulls are kept


async def test_edit_cell_in_sorted_view(trips):
    app = ViewerApp([str(trips)], lazy=True)
    async with app.run_test(size=SIZE) as pilot:
        await settle(pilot)
        app.grid.move_cursor(column="trip_id")
        await pilot.press("S")  # trip_id descending: the first row is the last trip
        await settle(pilot)
        app.grid.move_cursor(column="fare")
        await pilot.press("enter")
        await answer_prompt(pilot, "123.5")
        df = app.workspace.df
        assert df["fare"][N - 1] == 123.5
        assert app.grid.current_row()["fare"] == 123.5
        step = app.workspace.pipeline().steps[-1]
        assert step.params["row"] == N - 1


async def test_edit_to_null_and_bad_value(trips):
    app = ViewerApp([str(trips)], lazy=True)
    async with app.run_test(size=SIZE) as pilot:
        await settle(pilot)
        app.grid.move_cursor(row=0, column="fare")
        await pilot.press("enter")
        await answer_prompt(pilot, "")
        assert app.workspace.df["fare"][0] is None
        await pilot.press("enter")
        await answer_prompt(pilot, "not a number")
        assert len(app.workspace.pipeline().steps) == 1  # Rejected, data unchanged


async def test_drop_undo_redo(trips):
    app = ViewerApp([str(trips)], lazy=True)
    async with app.run_test(size=SIZE) as pilot:
        await settle(pilot)
        app.grid.move_cursor(column="vendor")
        await pilot.press("d")
        await settle(pilot)
        assert app.grid.columns == ["trip_id", "fare"]
        await pilot.press("u")
        await settle(pilot)
        assert app.grid.columns == ["trip_id", "vendor", "fare"]
        await pilot.press("U")
        await settle(pilot)
        assert app.grid.columns == ["trip_id", "fare"]


async def test_inspector_filters_on_top_value(trips):
    app = ViewerApp([str(trips)], lazy=True)
    async with app.run_test(size=SIZE) as pilot:
        await settle(pilot)
        app.grid.move_cursor(column="vendor")
        await pilot.press("i")
        await settle(pilot)
        assert app.screen_state()["inspector_open"]
        options = app.inspector.query_one("#inspector-top")
        assert options.option_count == 3
        options.focus()
        options.highlighted = 0
        await pilot.press("enter")
        await settle(pilot)
        assert app.view.known_row_count in (1666, 1667)
        assert app.workspace.pipeline().steps[0].kind == "filter"


async def test_overview_jumps_to_column(trips):
    app = ViewerApp([str(trips)], lazy=True)
    async with app.run_test(size=SIZE) as pilot:
        await settle(pilot)
        await pilot.press("o")
        await settle(pilot)
        assert isinstance(app.screen, OverviewScreen)
        await pilot.press("down", "down", "enter")
        await settle(pilot)
        assert app.grid.current_column == "fare"


async def test_save_data_and_pipeline(trips, tmp_path):
    app = ViewerApp([str(trips)], lazy=True)
    async with app.run_test(size=SIZE) as pilot:
        await settle(pilot)
        app.grid.move_cursor(row=0, column="vendor")
        await settle(pilot)
        await pilot.press("f")
        await settle(pilot)
        app.grid.move_cursor(column="fare")
        await pilot.press("S")
        await settle(pilot)

        out = tmp_path / "out.csv"
        await pilot.press("w")
        await answer_prompt(pilot, str(out))
        saved = pl.read_csv(out)
        assert saved.height == 1667
        assert saved["fare"].is_sorted(descending=True)
        assert [s.kind for s in app.workspace.pipeline().steps] == ["filter", "sort"]

        target = tmp_path / "trips.sweet.yaml"
        await pilot.press("p")
        await answer_prompt(pilot, str(target))
        assert_frame_equal(Pipeline.load(target).run(), saved, check_dtypes=False)


async def test_multiple_sheets(trips, tmp_path):
    other = tmp_path / "other.csv"
    pl.DataFrame({"a": [1, 2]}).write_csv(other)
    app = ViewerApp([str(trips), str(other)])
    async with app.run_test(size=SIZE) as pilot:
        await settle(pilot)
        assert app.screen_state()["sheets"] == ["trips", "other"]
        assert app.sheet == "other"
        await pilot.press("right_square_bracket")
        await settle(pilot)
        assert app.sheet == "trips"
        assert app.grid.columns == ["trip_id", "vendor", "fare"]


async def test_stdin_and_bad_target(tmp_path):
    app = ViewerApp(["-", str(tmp_path / "missing.csv")], stdin_data=b"x,y\n1,2\n3,4\n")
    async with app.run_test(size=SIZE) as pilot:
        await settle(pilot)
        assert app.screen_state()["sheets"] == ["stdin"]
        assert app.screen_state()["total_rows"] == 2


async def test_empty_state():
    app = ViewerApp([])
    async with app.run_test(size=SIZE) as pilot:
        await settle(pilot)
        assert app.query_one("#empty").display
        assert not app.grid.display
        await pilot.press("s", "f", "d", "u")  # No data: nothing breaks


async def test_run_command_by_id(trips):
    app = ViewerApp([str(trips)], lazy=True)
    async with app.run_test(size=SIZE) as pilot:
        await settle(pilot)
        app.grid.move_cursor(column="fare")
        app.run_command("view.sort_descending")
        await settle(pilot)
        assert app.view.sort == [("fare", True)]


def test_every_command_has_an_action():
    for command in COMMANDS:
        name = command.action.split("(")[0]
        assert hasattr(ViewerApp, f"action_{name}"), command.id


def test_formatting_helpers():
    assert format_value(None) == "∅"
    assert format_value(1.0) == "1"
    assert format_value(0.1 + 0.2) == "0.3"
    assert format_value(float("nan")) == "NaN"
    assert format_value("a\nb") == "a↵b"
    assert short_type(pl.Int64) == "i64"
    assert short_type(pl.Datetime("us")) == "datetime"
    assert HEADER_LINES == 4


# -----------------------------------------------------------------------------
# M2: steps panel, previews, composer, time travel
# -----------------------------------------------------------------------------

from sweet.core.steps import Step  # noqa: E402
from sweet.ui.viewer.steps_panel import ComposerScreen  # noqa: E402


async def with_steps(pilot):
    app = pilot.app
    await settle(pilot)
    app.workspace.apply_step(Step("filter", {"sql": "fare > 10"}, id="f1"))
    app.workspace.apply_step(Step("mutate", {"column": "fare2", "sql": "fare * 2"}, id="m1"))
    app.view.invalidate()
    app._data_changed()
    await settle(pilot)
    return app


async def test_new_step_preview_then_accept(trips):
    app = ViewerApp([str(trips)], lazy=True)
    async with app.run_test(size=SIZE) as pilot:
        await settle(pilot)
        await pilot.press("n")
        await settle(pilot)
        assert isinstance(app.screen, ComposerScreen)
        app.screen.query_one("#composer-code").text = "vendor = 'CMT'"
        await pilot.press("ctrl+s")
        await settle(pilot)
        state = app.screen_state()
        assert state["mode"] == "preview"
        assert state["preview"]["rows"]["removed"] == N - 1667
        assert state["steps"] == []  # Not applied yet
        await pilot.press("N")  # Next change: the first removed row
        await wait_for(pilot, lambda: app.grid.cell_change() is not None)
        assert app.grid.cell_change() == ("removed", None)
        await pilot.press("e")  # Editing is blocked during a preview
        assert not isinstance(app.screen, PromptScreen)
        await pilot.press("a")
        await settle(pilot)
        assert app.screen_state()["mode"] == "live"
        assert app.view.known_row_count == 1667
        assert app.screen_state()["steps"] == ["Filter: vendor = 'CMT'"]


async def test_preview_reject_and_changed_cells(trips):
    app = ViewerApp([str(trips)], lazy=True)
    async with app.run_test(size=SIZE) as pilot:
        await settle(pilot)
        app._preview_new_step(Step("mutate", {"column": "fare", "sql": "fare + 1"}))
        await settle(pilot)
        app.grid.move_cursor(row=0, column="fare")
        await wait_for(pilot, lambda: app.grid.cell_change() is not None)
        assert app.grid.cell_change() == ("changed", 0.0)
        assert "was" in str(app.query_one("#status").render())
        await pilot.press("r")
        await settle(pilot)
        assert app.screen_state()["mode"] == "live"
        assert app.workspace.steps() == []
        assert app.grid.current_value() == 0.0


async def test_composer_polars_column(trips):
    app = ViewerApp([str(trips)], lazy=True)
    async with app.run_test(size=SIZE) as pilot:
        await settle(pilot)
        await pilot.press("n")
        await settle(pilot)
        screen = app.screen
        screen.query_one("#composer-kind").query("RadioButton")[1].value = True  # column
        screen.query_one("#composer-language").query("RadioButton")[1].value = True  # Polars
        await settle(pilot, 0.1)
        screen.query_one("#composer-column").value = "fare_x10"
        screen.query_one("#composer-code").text = "pl.col('fare') * 10"
        await pilot.press("ctrl+s")
        await settle(pilot)
        assert app.view.diff.added_columns == ["fare_x10"]
        await pilot.press("a")
        await settle(pilot)
        step = app.workspace.steps()[0]
        assert step.params == {"column": "fare_x10", "expr": "pl.col('fare') * 10"}


async def test_composer_validation_error(trips):
    app = ViewerApp([str(trips)], lazy=True)
    async with app.run_test(size=SIZE) as pilot:
        await settle(pilot)
        await pilot.press("n")
        await settle(pilot)
        await pilot.press("ctrl+s")  # Empty code
        await settle(pilot)
        assert isinstance(app.screen, ComposerScreen)
        assert "Write some code" in str(app.screen.query_one("#composer-error").render())


async def test_steps_panel_time_travel(trips):
    app = ViewerApp([str(trips)], lazy=True)
    async with app.run_test(size=SIZE) as pilot:
        await with_steps(pilot)
        await pilot.press("t")
        await settle(pilot)
        assert app.screen_state()["steps_panel_open"]
        options = app.steps_panel.option_list
        assert options.option_count == 3  # source + 2 steps
        assert options.highlighted == 2  # The latest step
        await pilot.press("up", "up", "enter")  # The source: before any steps
        await settle(pilot)
        assert app.screen_state()["mode"] == "history"
        assert app.view.known_row_count == N
        assert "fare2" not in app.grid.columns
        await pilot.press("s")  # Read-only: sorting is refused
        assert app.view.sort == []
        await pilot.press("escape")
        await settle(pilot)
        assert app.screen_state()["mode"] == "live"
        assert "fare2" in app.grid.columns


async def test_steps_panel_toggle_remove_move(trips):
    app = ViewerApp([str(trips)], lazy=True)
    async with app.run_test(size=SIZE) as pilot:
        await with_steps(pilot)
        live_rows = app.view.known_row_count
        await pilot.press("t")
        await settle(pilot)
        app.steps_panel.option_list.highlighted = 1  # f1
        await pilot.press("space")
        await settle(pilot)
        assert app.workspace.steps()[0].enabled is False
        assert app.view.known_row_count == N
        await pilot.press("space")
        await settle(pilot)
        assert app.view.known_row_count == live_rows
        app.steps_panel.option_list.highlighted = 2  # m1
        await pilot.press("K")
        await settle(pilot)
        assert [s.id for s in app.workspace.steps()] == ["m1", "f1"]
        app.steps_panel.option_list.highlighted = 1  # m1 again (now first)
        await pilot.press("x")
        await settle(pilot)
        assert [s.id for s in app.workspace.steps()] == ["f1"]
        assert "fare2" not in app.grid.columns


async def test_steps_panel_edit_with_preview(trips):
    app = ViewerApp([str(trips)], lazy=True)
    async with app.run_test(size=SIZE) as pilot:
        await with_steps(pilot)
        await pilot.press("t")
        await settle(pilot)
        app.steps_panel.option_list.highlighted = 1  # f1
        await pilot.press("e")
        await settle(pilot)
        assert isinstance(app.screen, ComposerScreen)
        assert app.screen.query_one("#composer-code").text == "fare > 10"
        app.screen.query_one("#composer-code").text = "fare > 40"
        await pilot.press("ctrl+s")
        await settle(pilot)
        assert app.screen_state()["mode"] == "preview"
        assert app.view.diff.rows_removed > 0
        await pilot.press("a")
        await settle(pilot)
        steps = app.workspace.steps()
        assert steps[0].id == "f1" and steps[0].params == {"sql": "fare > 40"}
        assert app.workspace.df["fare"].min() > 40


async def test_steps_panel_branch(trips):
    app = ViewerApp([str(trips)], lazy=True)
    async with app.run_test(size=SIZE) as pilot:
        await with_steps(pilot)
        await pilot.press("t")
        await settle(pilot)
        app.steps_panel.option_list.highlighted = 1  # After f1
        await pilot.press("b")
        await answer_prompt(pilot, "filtered_only")
        assert app.screen_state()["sheets"] == ["trips", "filtered_only"]
        assert app.sheet == "filtered_only"
        assert [s.id for s in app.workspace.steps("filtered_only")] == ["f1"]
        assert "fare2" not in app.grid.columns


async def test_proposal_in_panel(trips):
    app = ViewerApp([str(trips)], lazy=True)
    async with app.run_test(size=SIZE) as pilot:
        await settle(pilot)
        app.workspace.propose(
            Step("filter", {"sql": "vendor IS NOT NULL AND fare < 5"}), author="agent:test"
        )
        await pilot.press("t")
        await settle(pilot)
        options = app.steps_panel.option_list
        assert options.option_count == 2  # source + proposal
        options.highlighted = 1
        await pilot.press("enter")
        await settle(pilot)
        assert app.screen_state()["mode"] == "preview"
        await pilot.press("a")
        await settle(pilot)
        assert app.workspace.proposals == []
        assert app.workspace.steps()[0].author == "agent:test"
