"""TUI tests (Textual pilot): the grid runs on the Workspace engine."""

import polars as pl
import pytest
from polars.testing import assert_frame_equal

from sweet.core.pipeline import Pipeline
from sweet.ui.app import SweetApp
from sweet.ui.grid import SORT_INDEX_COLUMN

SIZE = (120, 40)


@pytest.fixture
def people_csv(tmp_path):
    path = tmp_path / "people.csv"
    pl.DataFrame({"name": ["Alice", "Bob", "Cara"], "age": [30, 25, 35]}).write_csv(path)
    return path


async def open_app(pilot):
    await pilot.pause(0.3)
    return pilot.app._data_grid


async def edit_cell(pilot, row: int, col: int, text: str) -> None:
    """Edit a cell with the keyboard. Display row 0 is the header row."""
    grid = pilot.app._data_grid
    grid._table.focus()
    grid._table.move_cursor(row=row, column=col)
    await pilot.pause(0.05)
    await pilot.press("enter")
    await pilot.pause(0.1)
    pilot.app.screen.query("Input").first().value = ""
    await pilot.press(*text, "enter")
    await pilot.pause(0.2)


async def run_command(pilot, command: str) -> None:
    await pilot.press("colon")
    await pilot.pause(0.05)
    await pilot.press(*command, "enter")
    await pilot.pause(0.2)


async def test_open_file_loads_into_workspace(people_csv):
    app = SweetApp(startup_file=str(people_csv))
    async with app.run_test(size=SIZE) as pilot:
        grid = await open_app(pilot)
        assert grid.data.shape == (3, 2)
        assert grid.workspace.sheet_names == ["people"]
        assert grid.workspace.pipeline().source == {"path": str(people_csv), "format": "csv"}


async def test_cell_edit_is_a_step(people_csv):
    app = SweetApp(startup_file=str(people_csv))
    async with app.run_test(size=SIZE) as pilot:
        grid = await open_app(pilot)
        await edit_cell(pilot, row=2, col=1, text="99")
        assert grid.data["age"].to_list() == [30, 99, 35]
        assert [s.kind for s in grid.workspace.pipeline().steps] == ["edit_cell"]
        assert grid.has_changes


async def test_header_edit_renames_column(people_csv):
    app = SweetApp(startup_file=str(people_csv))
    async with app.run_test(size=SIZE) as pilot:
        grid = await open_app(pilot)
        await edit_cell(pilot, row=0, col=0, text="full_name")
        assert grid.data.columns == ["full_name", "age"]
        assert grid.workspace.pipeline().steps[0].params == {"mapping": {"name": "full_name"}}


async def test_undo_redo_commands(people_csv):
    app = SweetApp(startup_file=str(people_csv))
    async with app.run_test(size=SIZE) as pilot:
        grid = await open_app(pilot)
        await edit_cell(pilot, row=1, col=0, text="Ann")
        assert grid.data["name"][0] == "Ann"
        await run_command(pilot, "undo")
        assert grid.data["name"][0] == "Alice"
        await run_command(pilot, "redo")
        assert grid.data["name"][0] == "Ann"
        assert [e.action for e in grid.workspace.audit.entries] == ["load", "step", "undo", "redo"]


async def test_rows_and_columns_are_steps(people_csv):
    app = SweetApp(startup_file=str(people_csv))
    async with app.run_test(size=SIZE) as pilot:
        grid = await open_app(pilot)
        grid.action_add_row()
        grid.action_add_column()
        grid._insert_row(1)
        grid._delete_row(2)  # the original first row ("Alice")
        grid._insert_column(1)
        grid._delete_column(0)
        await pilot.pause(0.2)
        kinds = [s.kind for s in grid.workspace.pipeline().steps]
        assert kinds == ["insert_row", "mutate", "insert_row", "delete_rows", "mutate", "drop"]
        assert grid.data.columns == ["Column_2", "age", "Column_1"]
        assert grid.data["age"].to_list() == [None, 25, 35, None]
        # The session is fully reproducible from the source file
        assert_frame_equal(grid.workspace.pipeline().run(), grid.workspace.df)


async def test_view_sort_does_not_change_engine_data(people_csv):
    app = SweetApp(startup_file=str(people_csv))
    async with app.run_test(size=SIZE) as pilot:
        grid = await open_app(pilot)
        grid._sort_column(1, ascending=False)
        await pilot.pause(0.1)
        assert grid.data["name"].to_list() == ["Cara", "Alice", "Bob"]
        assert grid.workspace.df["name"].to_list() == ["Alice", "Bob", "Cara"]
        assert grid.workspace.pipeline().steps == []

        # Editing in the sorted view edits the right underlying row
        await edit_cell(pilot, row=1, col=1, text="40")
        assert grid.workspace.df["age"].to_list() == [30, 25, 40]
        assert grid.data["name"].to_list() == ["Cara", "Alice", "Bob"]  # rows stay put


async def test_save_while_sorted_writes_clean_sorted_file(people_csv, tmp_path):
    app = SweetApp(startup_file=str(people_csv))
    async with app.run_test(size=SIZE) as pilot:
        grid = await open_app(pilot)
        grid._sort_column(1, ascending=True)
        await pilot.pause(0.1)
        out = tmp_path / "out.csv"
        assert grid.save_data(str(out))
        saved = pl.read_csv(out)
        assert SORT_INDEX_COLUMN not in saved.columns  # used to leak into the file
        assert saved["name"].to_list() == ["Bob", "Alice", "Cara"]
        assert [s.kind for s in grid.workspace.pipeline().steps] == ["sort"]
        assert_frame_equal(grid.workspace.pipeline().run(), saved)


async def test_legacy_mutation_is_recorded_as_manual_edit(people_csv):
    app = SweetApp(startup_file=str(people_csv))
    async with app.run_test(size=SIZE) as pilot:
        grid = await open_app(pilot)
        grid.data = grid.data.with_columns(pl.col("age") + 1)
        steps = grid.workspace.pipeline().steps
        assert [s.kind for s in steps] == ["manual"]
        assert grid.undo()
        assert grid.data["age"].to_list() == [30, 25, 35]


async def test_code_panel_transform_is_a_step(people_csv):
    app = SweetApp(startup_file=str(people_csv))
    async with app.run_test(size=SIZE) as pilot:
        grid = await open_app(pilot)
        tools = app.query_one("#tools-panel")
        tools.query_one("#code-input").text = "df = df.filter(pl.col('age') > 26)"
        tools._execute_code()
        await pilot.pause(0.2)
        assert grid.data["name"].to_list() == ["Alice", "Cara"]
        assert grid.workspace.pipeline().steps[0].kind == "polars"
        assert grid.undo()
        assert grid.data.height == 3


async def test_generated_code_runs_restricted(people_csv):
    app = SweetApp(startup_file=str(people_csv))
    async with app.run_test(size=SIZE) as pilot:
        grid = await open_app(pilot)
        tools = app.query_one("#tools-panel")
        tools._apply_generated_code("import os\ndf = df.head(1)")
        await pilot.pause(0.1)
        assert grid.data.height == 3  # rejected: imports aren't allowed

        tools._apply_generated_code("df = df.head(1)")
        await pilot.pause(0.1)
        assert grid.data.height == 1
        step = grid.workspace.pipeline().steps[-1]
        assert step.author == "agent:assistant"


async def test_pipeline_command_saves_replayable_file(people_csv, tmp_path):
    app = SweetApp(startup_file=str(people_csv))
    async with app.run_test(size=SIZE) as pilot:
        grid = await open_app(pilot)
        await edit_cell(pilot, row=1, col=1, text="31")
        target = tmp_path / "people.sweet.yaml"
        await run_command(pilot, f"pipeline {target}")
        assert target.exists()
        assert_frame_equal(Pipeline.load(target).run(), grid.workspace.df)
