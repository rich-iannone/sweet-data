"""An agent attached over the session socket to a running viewer (Textual pilot)."""

import asyncio
import shutil
import tempfile
from pathlib import Path

import polars as pl
import pytest
from textual.worker import WorkerCancelled

from sweet.session import RemoteError, SessionClient
from sweet.ui.viewer import ViewerApp

SIZE = (130, 34)
SSN = "123-45-6789"


@pytest.fixture(autouse=True)
def sessions_dir(monkeypatch):
    directory = Path(tempfile.mkdtemp(dir="/tmp", prefix="sw"))
    monkeypatch.setenv("SWEET_SESSIONS_DIR", str(directory))
    yield directory
    shutil.rmtree(directory, ignore_errors=True)


@pytest.fixture
def people(tmp_path):
    path = tmp_path / "people.csv"
    pl.DataFrame(
        {
            "id": [1, 2, 3, 4],
            "name": ["Ann", "Bo", "Cy", "Di"],
            "ssn": [SSN, "987-65-4321", "555-12-3456", "111-22-3333"],
            "amount": [10.0, -5.0, 30.0, -1.0],
        }
    ).write_csv(path)
    return path


async def settle(pilot, seconds=0.3):
    await pilot.pause(seconds)
    try:
        await pilot.app.workers.wait_for_complete()
    except WorkerCancelled:
        pass
    await pilot.pause(0.05)


async def attach(pilot, name="live"):
    await settle(pilot)
    client = await SessionClient.connect(name, client="claude")
    await settle(pilot, 0.1)
    return client


async def test_agent_attaches_and_is_shown(people):
    app = ViewerApp([str(people)], session="live")
    async with app.run_test(size=SIZE) as pilot:
        client = await attach(pilot)
        strip = app.query_one("#agent-strip")
        assert not strip.has_class("hidden")
        assert "agent:claude" in str(strip.render())
        await client.close()
        await settle(pilot)
        assert strip.has_class("hidden")


async def test_masking_from_the_viewer(people):
    app = ViewerApp([str(people)], session="live")
    async with app.run_test(size=SIZE) as pilot:
        client = await attach(pilot)
        assert SSN in (await client.call("view"))["markdown"]  # Masking is off by default
        app.grid.move_cursor(column="ssn")
        assert app.grid.render_line(1).text.count("PII?") == 1  # Detected, not masked
        await pilot.press("m")
        await settle(pilot)
        assert "🔒" in app.grid.render_line(1).text
        view = await client.call("view")
        assert SSN not in view["markdown"] and view["masked"] == {"ssn": "redact"}
        with pytest.raises(RemoteError):
            await client.call("set_policy", unmask="ssn")
        assert SSN in app.grid.render_line(4).text  # The person still sees raw values
        await client.close()


async def test_proposal_reviewed_by_the_person(people):
    app = ViewerApp([str(people)], session="live")
    async with app.run_test(size=SIZE) as pilot:
        client = await attach(pilot)
        result = await client.call(
            "propose_step", step={"kind": "filter", "params": {"sql": "amount > 0"}}
        )
        assert result["status"] == "proposed"
        await settle(pilot)
        assert app.view.known_row_count == 4  # Nothing applied yet
        await pilot.press("t")
        await settle(pilot)
        options = app.steps_panel.option_list
        options.highlighted = options.option_count - 1  # The proposal
        await pilot.press("enter")
        await settle(pilot)
        assert app.screen_state()["mode"] == "preview"
        await pilot.press("a")
        await settle(pilot)
        assert app.view.known_row_count == 2
        assert app.workspace.steps()[0].author == "agent:claude"
        with pytest.raises(RemoteError):  # Agents can't accept things themselves
            await client.call("command", command_id="preview.accept")
        await client.close()


async def test_auto_mode_applies_live(people):
    app = ViewerApp([str(people)], session="live")
    async with app.run_test(size=SIZE) as pilot:
        client = await attach(pilot)
        await pilot.press("M")  # propose -> auto
        await settle(pilot)
        assert app.session.policy.mode == "auto"
        await client.call("propose_step", step={"kind": "filter", "params": {"sql": "amount > 0"}})
        await settle(pilot)
        assert app.view.known_row_count == 2
        await pilot.press("ctrl+x")  # Stop agents
        await settle(pilot)
        assert app.session.policy.mode == "read-only"
        with pytest.raises(RemoteError):
            await client.call("propose_step", step={"kind": "filter", "params": {"sql": "id > 1"}})
        await client.close()


async def test_highlight_selection_screen_and_commands(people):
    app = ViewerApp([str(people)], session="live")
    async with app.run_test(size=SIZE) as pilot:
        client = await attach(pilot)
        await client.call("highlight", row=1, column="amount", note="negative amount", color="red")
        await settle(pilot)
        assert app.grid.cursor_row == 1 and app.grid.current_column == "amount"
        assert "negative amount" in str(app.query_one("#status").render())

        app.grid.move_cursor(row=0, column="name")
        await pilot.press("shift+down", "shift+right")
        await settle(pilot)
        selection = await client.call("get_selection")
        assert selection["selection"]["rows"] == [0, 2]
        assert selection["selection"]["columns"] == ["name", "ssn"]

        app.grid.move_cursor(row=0, column="amount")
        await client.call("command", command_id="view.sort_descending")
        await settle(pilot)
        screen = await client.call("screen")
        assert screen["sort"] == [{"column": "amount", "descending": True}]
        assert "Cy" in screen["visible_markdown"].splitlines()[2]
        assert screen["highlights"][0]["note"] == "negative amount"

        await pilot.press("H")
        await settle(pilot)
        assert app.session.highlights == {}
        await client.close()


async def test_demo_step_mode_and_take_over(people):
    app = ViewerApp([str(people)], session="live")
    async with app.run_test(size=SIZE) as pilot:
        client = await attach(pilot)
        await client.call("start_demo", mode="step")
        task = asyncio.ensure_future(client.call("narrate", text="Let's look at amounts"))
        await settle(pilot, 0.2)
        assert not task.done()  # Step mode: each visible action waits for space
        await pilot.press("space")
        await asyncio.wait_for(task, 2)
        await settle(pilot)
        assert "Let's look at amounts" in str(app.query_one("#agent-strip").render())

        pending = asyncio.ensure_future(client.call("highlight", column="name"))
        await settle(pilot, 0.2)
        await pilot.press("down")  # Any other key: the person takes control
        with pytest.raises(RemoteError) as e:
            await asyncio.wait_for(pending, 2)
        assert e.value.code == -32002
        assert (await client.call("status"))["control"] == "human"
        await pilot.press("ctrl+r")
        await settle(pilot)
        assert (await client.call("status"))["control"] == "shared"
        await client.call("end_demo")
        await client.close()


async def test_open_from_agent(people, tmp_path):
    other = tmp_path / "other.csv"
    pl.DataFrame({"x": [1, 2]}).write_csv(other)
    app = ViewerApp([str(people)], session="live")
    async with app.run_test(size=SIZE) as pilot:
        client = await attach(pilot)
        result = await client.call("open", target=str(other))
        await settle(pilot)
        assert result["sheet"] == "other"
        assert app.screen_state()["sheets"] == ["people", "other"]
        await client.close()
