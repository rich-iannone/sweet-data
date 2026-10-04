"""M2 exit criterion: a messy-CSV-to-clean-Parquet session, done interactively in the
viewer, exports to code that reproduces the saved file exactly."""

import duckdb
import polars as pl
from click.testing import CliRunner
from polars.testing import assert_frame_equal
from textual.worker import WorkerCancelled

from sweet.cli import main
from sweet.core.pipeline import Pipeline
from sweet.ui.viewer import ViewerApp
from sweet.ui.viewer.panels import PromptScreen
from sweet.ui.viewer.steps_panel import ComposerScreen

MESSY = """id,name,region,amount
1,  Alice ,north,100.5
2,Bob,South ,N/A
3, Cara,north, 250
4,Dan,TEST,10
5,Eve ,east,-3
6,  Finn,south,42
"""


async def settle(pilot, seconds=0.3):
    await pilot.pause(seconds)
    try:
        await pilot.app.workers.wait_for_complete()
    except WorkerCancelled:
        pass
    await pilot.pause(0.05)


async def compose_step(pilot, code, *, column=None):
    """Write a SQL step in the composer, preview it, and accept it."""
    await pilot.press("n")
    await settle(pilot, 0.1)
    screen = pilot.app.screen
    assert isinstance(screen, ComposerScreen)
    if column is not None:
        screen.query_one("#composer-kind").query("RadioButton")[1].value = True
        await settle(pilot, 0.05)
        screen.query_one("#composer-column").value = column
    screen.query_one("#composer-code").text = code
    await pilot.press("ctrl+s")
    await settle(pilot)
    assert pilot.app.screen_state()["mode"] == "preview", "preview didn't open"
    await pilot.press("a")
    await settle(pilot)


async def prompt(pilot, key, text):
    await pilot.press(key)
    await settle(pilot, 0.1)
    assert isinstance(pilot.app.screen, PromptScreen)
    pilot.app.screen.query_one("Input").value = text
    await pilot.press("enter")
    await settle(pilot)


async def test_messy_csv_to_clean_parquet_reproduces(tmp_path):
    messy = tmp_path / "messy.csv"
    messy.write_text(MESSY)
    clean = tmp_path / "clean.parquet"
    pipeline_file = tmp_path / "clean.sweet.yaml"

    app = ViewerApp([str(messy)])
    async with app.run_test(size=(120, 32)) as pilot:
        await settle(pilot)
        await compose_step(pilot, "TRIM(name)", column="name")
        await compose_step(pilot, "UPPER(TRIM(region))", column="region")
        await compose_step(
            pilot, "CAST(NULLIF(TRIM(CAST(amount AS VARCHAR)), 'N/A') AS DOUBLE)", column="amount"
        )
        await compose_step(pilot, "amount > 0")

        # Exclude the test region with the "exclude this value" key
        app.grid.move_cursor(row=2, column="region")  # Dan / TEST
        await settle(pilot)
        assert app.grid.current_value() == "TEST"
        await pilot.press("exclamation_mark")
        await settle(pilot)

        # Sort the view by amount, then save (the sort becomes a step)
        app.grid.move_cursor(column="amount")
        await pilot.press("S")
        await settle(pilot)
        await prompt(pilot, "w", str(clean))
        await prompt(pilot, "p", str(pipeline_file))

        assert len(app.workspace.steps()) == 6

    saved = pl.read_parquet(clean)
    assert saved["name"].to_list() == ["Cara", "Alice", "Finn"]
    assert saved["region"].to_list() == ["NORTH", "NORTH", "SOUTH"]
    assert saved["amount"].to_list() == [250.0, 100.5, 42.0]

    pipeline = Pipeline.load(pipeline_file)

    # 1. Replaying the pipeline file
    assert_frame_equal(pipeline.run(), saved)

    # 2. The generated Polars script
    namespace = {}
    exec(pipeline.to_polars_script(), namespace)
    assert_frame_equal(namespace["df"], saved)

    # 3. The generated SQL, run in DuckDB
    result = pl.from_arrow(duckdb.connect().execute(pipeline.to_sql()).fetch_arrow_table())
    assert_frame_equal(result, saved, check_dtypes=False)

    # 4. `sweet run` on the command line
    out = tmp_path / "rerun.parquet"
    run = CliRunner().invoke(main, ["run", str(pipeline_file), "-o", str(out)])
    assert run.exit_code == 0, run.output
    assert_frame_equal(pl.read_parquet(out), saved)
