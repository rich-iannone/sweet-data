"""Tests for the live co-pilot: policy, sessions, the session server, and `sweet mcp`."""

import asyncio
import json
import shutil
import tempfile
from pathlib import Path

import polars as pl
import pytest

from sweet.core.policy import Policy, PolicyError, detect_pii
from sweet.core.session import ControlError, Selection, Session, SessionError
from sweet.core.steps import Step
from sweet.session import RemoteError, SessionClient, SessionServer, list_sessions
from sweet.session.mcp import TOOLS, LocalBackend, SweetMCP
from sweet.session.registry import SessionInfo

AGENT = "agent:test"
SSNS = ["123-45-6789", "987-65-4321", "555-12-3456"]


@pytest.fixture
def people(tmp_path):
    path = tmp_path / "people.csv"
    pl.DataFrame(
        {
            "id": [1, 2, 3],
            "name": ["Ann", "Bo", "Cy"],
            "email": ["ann@x.com", "bo@y.org", "cy@z.net"],
            "ssn": SSNS,
            "amount": [10.0, -5.0, 30.0],
        }
    ).write_csv(path)
    return path


@pytest.fixture
def session(people):
    s = Session()
    s.open(str(people))
    return s


def leaks(text) -> list[str]:
    text = json.dumps(text, default=str) if not isinstance(text, str) else text
    return [v for v in SSNS if v in text or v[-4:] + '"' in text]


# -----------------------------------------------------------------------------
# Policy
# -----------------------------------------------------------------------------


class TestPolicy:
    def test_detect_pii(self, people):
        found = detect_pii(pl.read_csv(people))
        assert found == {"email": "email", "ssn": "ssn"}

    @pytest.mark.parametrize(
        "method,first", [("redact", "•••"), ("partial", "•••6789"), ("null", None)]
    )
    def test_methods(self, method, first):
        p = Policy()
        p.add_mask("ssn", method)
        out = p.mask_frame(pl.DataFrame({"ssn": [SSNS[0], None]}))
        assert out["ssn"].to_list() == [first, None]

    def test_hash_is_stable_and_joinable(self):
        p = Policy()
        p.add_mask("k", "hash")
        a = p.mask_frame(pl.DataFrame({"k": ["x", "y", "x"]}))["k"].to_list()
        assert a[0] == a[2] != a[1] and a[0].startswith("h:")

    def test_agents_only_tighten(self):
        p = Policy(mode="auto")
        p.add_mask("ssn", "redact")
        with pytest.raises(PolicyError):
            p.remove_mask("ssn", author=AGENT)
        with pytest.raises(PolicyError):
            p.add_mask("ssn", "partial", author=AGENT)
        p.add_mask("ssn", "null", author=AGENT)  # Stronger is fine
        with pytest.raises(PolicyError):
            p.set_mask_pii(True, author=AGENT) or p.set_mask_pii(False, author=AGENT)
        p.set_mode("propose", author=AGENT)
        with pytest.raises(PolicyError):
            p.set_mode("auto", author=AGENT)

    def test_taint(self):
        p = Policy()
        p.add_mask("ssn")
        cols = ["id", "ssn"]
        assert p.propagate(Step("rename", {"mapping": {"ssn": "s"}}), cols, ["id", "s"]) == ["s"]
        assert p.propagate(
            Step("mutate", {"column": "c", "sql": "UPPER(ssn)"}), cols, [*cols, "c"]
        ) == ["c"]
        assert (
            p.propagate(Step("mutate", {"column": "d", "sql": "id * 2"}), cols, [*cols, "d"]) == []
        )
        assert p.propagate(Step("sql", {"query": "SELECT ssn AS id FROM df"}), cols, ["id"]) == [
            "id"
        ]

    def test_load_policy_file(self, tmp_path):
        (tmp_path / ".sweet").mkdir()
        (tmp_path / ".sweet" / "policy.yaml").write_text(
            "mode: read-only\nmask_pii: true\nmasks:\n  salary: hash\n"
        )
        sub = tmp_path / "a" / "b"
        sub.mkdir(parents=True)
        p = Policy.discover(sub)
        assert p.mode == "read-only" and p.mask_pii and p.masks["salary"].method == "hash"


# -----------------------------------------------------------------------------
# Masking can't leak through any agent-facing method
# -----------------------------------------------------------------------------


class TestNoLeaks:
    @pytest.fixture(autouse=True)
    def _mask(self, session):
        session.set_policy(mask="ssn", mode="auto")

    def test_view_and_markdown(self, session):
        out = session.view(author=AGENT)
        assert not leaks(out) and out["masked"] == {"ssn": "redact"}
        assert "•••" in out["markdown"]

    def test_human_sees_raw(self, session):
        assert SSNS[0] in session.view()["markdown"]

    @pytest.mark.parametrize(
        "sql",
        [
            "SELECT ssn FROM people",
            "SELECT ssn AS name FROM people",
            "SELECT UPPER(ssn) AS x, SUBSTR(ssn, 1, 3) AS y FROM people",
            "SELECT name || ssn AS z FROM people",
            "SELECT MAX(ssn) AS m FROM people",
        ],
    )
    def test_queries(self, session, sql):
        assert not leaks(session.query(sql, author=AGENT))

    def test_profile_hides_values(self, session):
        stats = session.profile(author=AGENT)["columns"]["ssn"]
        assert stats["masked"] == "redact" and "top_values" not in stats and "min" not in stats
        assert not leaks(stats)

    def test_derived_columns_stay_masked(self, session):
        for step in [
            Step("mutate", {"column": "copy", "sql": "ssn"}),
            Step("rename", {"mapping": {"ssn": "tax_id"}}),
            Step("sql", {"query": "SELECT tax_id AS amount2, * FROM df"}),
        ]:
            result = session.propose_step(step, author=AGENT)
            assert not leaks(result)
        assert not leaks(session.view(author=AGENT))
        assert not leaks(session.query("SELECT * FROM people", author=AGENT))

    def test_preview_masks_columns_before_they_exist(self, session):
        session.set_policy(mode="propose")
        result = session.propose_step(
            Step("mutate", {"column": "leak", "sql": "CASE WHEN id = 1 THEN ssn END"}), author=AGENT
        )
        assert result["status"] == "proposed" and not leaks(result)

    def test_diff_samples(self, session):
        session.workspace.branch("b")
        session.workspace.apply_step(Step("mutate", {"column": "ssn", "sql": "ssn || '!'"}))
        assert not leaks(session.diff("people", "b", key=["id"], author=AGENT))

    def test_selection(self, session):
        session.set_selection(Selection("people", 0, 3, ["name", "ssn"]))
        out = session.get_selection(author=AGENT)
        assert not leaks(out) and SSNS[0] in json.dumps(session.get_selection())

    def test_data_export_blocked_pipeline_allowed(self, session, tmp_path):
        with pytest.raises(PolicyError):
            session.export(str(tmp_path / "x.csv"), author=AGENT)
        session.export(str(tmp_path / "p.sweet.yaml"), what="pipeline", author=AGENT)
        session.export(str(tmp_path / "x.csv"))  # The person may

    def test_mask_pii(self, session):
        session.set_policy(unmask="ssn")
        session.set_policy(mask_pii=True, author=AGENT)  # Agents may turn it on
        out = session.view(author=AGENT)
        assert not leaks(out) and "ann@x.com" not in json.dumps(out)


# -----------------------------------------------------------------------------
# Permissions and attention
# -----------------------------------------------------------------------------


class TestPermissions:
    def test_read_only(self, session):
        session.set_policy(mode="read-only")
        with pytest.raises(PolicyError):
            session.propose_step(Step("filter", {"sql": "amount > 0"}), author=AGENT)
        assert session.view(author=AGENT)["total_rows"] == 3

    def test_propose_mode(self, session):
        result = session.propose_step(Step("filter", {"sql": "amount > 0"}), author=AGENT)
        assert result["status"] == "proposed"
        assert session.view()["total_rows"] == 3
        with pytest.raises(PolicyError):
            session.undo(author=AGENT)
        with pytest.raises(PolicyError):
            session.decide(result["proposal"], True, author=AGENT)
        session.decide(result["proposal"], True)
        assert session.view()["total_rows"] == 2
        assert session.workspace.steps()[0].author == AGENT

    def test_auto_mode(self, session):
        session.set_policy(mode="auto")
        result = session.propose_step(Step("filter", {"sql": "amount > 0"}), author=AGENT)
        assert result["status"] == "applied"
        step_id = session.workspace.steps()[0].id
        session.step_action("toggle", step_id, author=AGENT)
        assert session.view()["total_rows"] == 3
        session.undo(author=AGENT)
        assert session.view()["total_rows"] == 2

    def test_highlights(self, session):
        h = session.highlight(row=1, column="amount", note="negative", author=AGENT)
        assert h["kind"] == "cell"
        session.highlight(column="name", author="human")
        session.clear_highlights(author=AGENT)  # Agents clear only their own
        assert [x.author for x in session.highlights.values()] == ["human"]
        with pytest.raises(SessionError):
            session.highlight(column="nope", author=AGENT)

    def test_view_options(self, session):
        out = session.view(where="amount > 0", sort=["-amount"], columns=["name"], author=AGENT)
        assert out["total_rows"] == 2 and [r["name"] for r in out["rows"]] == ["Cy", "Ann"]

    def test_markdown_budget(self, session):
        out = session.view(max_chars=120)
        assert out["returned"] < 3 and "more rows not shown" in out["markdown"]

    def test_screen_without_ui(self, session):
        assert session.screen(author=AGENT)["ui"] is False
        with pytest.raises(SessionError):
            session.command("view.sort_ascending", author=AGENT)


class TestDemoGate:
    async def test_continuous_paces(self, session):
        session.start_demo("continuous", speed=4.0)
        loop = asyncio.get_running_loop()
        start = loop.time()
        await session.gate(AGENT)
        assert loop.time() - start >= 0.25

    async def test_step_mode_waits_for_advance(self, session):
        session.start_demo("step")
        task = asyncio.create_task(session.gate(AGENT))
        await asyncio.sleep(0.05)
        assert not task.done()
        session.advance()
        await asyncio.wait_for(task, 1)

    async def test_take_over_and_resume(self, session):
        session.start_demo("step")
        task = asyncio.create_task(session.gate(AGENT))
        await asyncio.sleep(0.05)
        session.take_control()
        with pytest.raises(ControlError):
            await asyncio.wait_for(task, 1)
        with pytest.raises(ControlError):
            await session.gate(AGENT)
        await session.gate("human")  # People are never gated
        session.resume()
        session.end_demo()
        await session.gate(AGENT)
        actions = [e.action for e in session.workspace.audit.entries]
        assert "take_control" in actions and "resume" in actions


# -----------------------------------------------------------------------------
# Session server
# -----------------------------------------------------------------------------


@pytest.fixture
def sessions_dir(monkeypatch):
    # Unix socket paths must be short, so avoid pytest's long tmp paths
    directory = Path(tempfile.mkdtemp(dir="/tmp", prefix="sw"))
    monkeypatch.setenv("SWEET_SESSIONS_DIR", str(directory))
    yield directory
    shutil.rmtree(directory, ignore_errors=True)


class TestServer:
    async def test_round_trip(self, session, sessions_dir):
        server = SessionServer(session, name="t")
        info = await server.start()
        assert (sessions_dir / "t.json").stat().st_mode & 0o777 == 0o600
        client = await SessionClient.connect("t", client="claude")
        assert client.status["agents"] == ["agent:claude"]
        assert (await client.call("view", limit=1))["returned"] == 1
        with pytest.raises(RemoteError) as e:
            await client.call("undo")
        assert e.value.code == -32001
        with pytest.raises(RemoteError) as e:
            await client.call("frobnicate")
        assert e.value.code == -32601
        with pytest.raises(RemoteError):
            await client.call("view", nonsense=1)
        await client.close()
        await asyncio.sleep(0.05)
        assert session.agents == {}
        await server.stop()
        assert list_sessions(sessions_dir) == []
        assert not Path(info.socket).exists()

    async def test_bad_token(self, session, sessions_dir):
        server = SessionServer(session, name="t")
        info = await server.start()
        with pytest.raises(RemoteError, match="Authentication"):
            await SessionClient.connect(info=SessionInfo(**{**info.__dict__, "token": "x"}))
        await server.stop()

    async def test_clients_cannot_claim_to_be_human(self, session, sessions_dir):
        server = SessionServer(session, name="t")
        await server.start()
        client = await SessionClient.connect("t", client="human")
        assert client.status["agents"] == ["agent:human"]
        result = await client.call(
            "propose_step", step={"kind": "filter", "params": {"sql": "id > 1"}}, author="human"
        )
        assert result["status"] == "proposed"  # Still an agent: not applied directly
        await client.close()
        await server.stop()

    async def test_events(self, session, sessions_dir):
        server = SessionServer(session, name="t")
        await server.start()
        client = await SessionClient.connect("t", client="a")
        events = client.subscribe()
        first = asyncio.ensure_future(events.__anext__())
        await asyncio.sleep(0.05)
        session.highlight(column="name")
        event = await asyncio.wait_for(first, 2)
        assert event["type"] == "highlight"
        await client.close()
        await server.stop()

    async def test_stale_sessions_cleaned(self, sessions_dir):
        stale = SessionInfo("old", str(sessions_dir / "old.sock"), "t", 999999, "2020", "/")
        (sessions_dir / "old.json").write_text(json.dumps(stale.__dict__))
        assert list_sessions(sessions_dir) == []
        assert not (sessions_dir / "old.json").exists()


# -----------------------------------------------------------------------------
# MCP surface
# -----------------------------------------------------------------------------


class TestMCP:
    @pytest.fixture
    def mcp(self, session):
        return SweetMCP(LocalBackend(session, AGENT))

    async def call(self, mcp, name, **args):
        return (await mcp.call_tool(name, args))[0].text

    async def test_tool_surface_is_slim(self, mcp):
        tools = await mcp.list_tools()
        assert len(tools) <= 20
        from sweet import mcp as legacy

        legacy_tools = await legacy.list_tools()
        size = len(json.dumps([t.model_dump() for t in tools]))
        legacy_size = len(json.dumps([t.model_dump() for t in legacy_tools]))
        assert size < legacy_size / 2  # Far less context per session

    async def test_tools(self, mcp, session, tmp_path):
        assert "propose" in await self.call(mcp, "status")
        view = await self.call(mcp, "view", limit=2)
        assert view.startswith("people: rows 0–2 of 3")
        assert "Ann" in await self.call(mcp, "query", sql="SELECT name FROM people WHERE id = 1")
        assert "amount: Float64" in await self.call(mcp, "profile")
        proposal = await self.call(
            mcp, "propose_step", step={"kind": "filter", "params": {"sql": "amount > 0"}}
        )
        assert proposal.startswith("Proposed") and "−1 rows" in proposal
        assert "waiting for the person" in await self.call(mcp, "steps")
        assert "cell" in await self.call(mcp, "highlight", row=1, column="amount", note="negative")
        assert "cleared" in await self.call(mcp, "highlight", clear=True)
        assert "steps" in await self.call(
            mcp, "export", path=str(tmp_path / "p.sweet.yaml"), what="pipeline"
        )
        assert "masks" in await self.call(mcp, "set_policy", mask="ssn")
        assert "•••" in await self.call(mcp, "view")
        assert "Nothing is selected" in await self.call(mcp, "get_selection")
        assert '"ui":false' in await self.call(mcp, "screen")

    async def test_errors_are_tool_errors(self, mcp):
        with pytest.raises(RuntimeError, match="propose"):
            await self.call(mcp, "undo")

    async def test_labs_adds_legacy_tools(self, session):
        labs = SweetMCP(LocalBackend(session, AGENT), labs=True)
        names = [t.name for t in await labs.list_tools()]
        assert "sweet_scan" in names and "view" in names
        result = await labs.call_tool("sweet_sheets", {})
        assert "people" in result[0].text  # Shares the session's workspace


def test_tool_names_unique():
    names = [t.name for t in TOOLS]
    assert len(names) == len(set(names))


async def test_sweet_mcp_over_stdio(people, sessions_dir):
    """`sweet mcp --headless` speaks MCP over stdio (official client)."""
    import sys

    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "sweet", "mcp", "--headless", "--client", "e2e"],
        env={"SWEET_SESSIONS_DIR": str(sessions_dir), "PATH": "/usr/bin:/bin"},
    )
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as client:
            await client.initialize()
            tools = {t.name for t in (await client.list_tools()).tools}
            assert {"open", "view", "propose_step", "screen", "set_policy"} <= tools
            opened = await client.call_tool("open", {"target": str(people)})
            assert not opened.isError
            view = await client.call_tool("view", {"limit": 2})
            assert "people: rows 0–2 of 3" in view.content[0].text
            denied = await client.call_tool("undo", {})
            assert denied.isError


async def test_launch_session_outside_tmux_explains(session, monkeypatch):
    monkeypatch.delenv("TMUX", raising=False)
    mcp = SweetMCP(LocalBackend(session, AGENT))
    with pytest.raises(RuntimeError, match="sweet --session"):
        await mcp.call_tool("launch_session", {"targets": ["data.csv"]})


async def test_launch_session_in_tmux_attaches(session, sessions_dir, monkeypatch):
    """With tmux, the viewer is started in a split pane and the server attaches to it."""
    monkeypatch.setenv("TMUX", "/tmp/tmux-1/default")
    started = {}

    def fake_run(cmd, check):
        started["cmd"] = cmd
        name = cmd[-1].split()[2]  # sweet --session <name> ...
        server = SessionServer(Session(), name=name)
        started["server"] = server
        asyncio.get_event_loop().create_task(server.start())

    monkeypatch.setattr("sweet.session.mcp.subprocess.run", fake_run)
    monkeypatch.setattr("sweet.session.mcp.shutil.which", lambda _: "/usr/bin/tmux")
    mcp = SweetMCP(LocalBackend(session, AGENT))
    text = (await mcp.call_tool("launch_session", {"targets": ["a.csv"]}))[0].text
    assert "attached" in text and started["cmd"][:3] == ["tmux", "split-window", "-h"]
    assert mcp.backend.attached
    await mcp.backend.client.close()
    await started["server"].stop()
