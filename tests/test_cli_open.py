"""Tests for how `sweet` routes arguments: targets to the viewer, or subcommands."""

import pytest
from click.testing import CliRunner

from sweet import cli


@pytest.fixture
def launched(monkeypatch):
    calls = []
    monkeypatch.setattr(
        "sweet.ui.viewer.run_viewer",
        lambda targets, **kw: calls.append(("viewer", targets, kw)),
    )
    monkeypatch.setattr(
        cli, "_run_classic", lambda targets, db, stdin: calls.append(("classic", targets, db))
    )
    return calls


def run(*args, input=None):
    return CliRunner().invoke(cli.main, list(args), input=input, catch_exceptions=False)


def test_targets_open_the_viewer(launched):
    result = run("a.parquet", "logs/*.csv")
    assert result.exit_code == 0
    kind, targets, kw = launched[0]
    assert (kind, targets) == ("viewer", ["a.parquet", "logs/*.csv"])
    assert kw["stdin_data"] is None and kw["lazy"] is None
    assert kw["session"] == "" and kw["mask_pii"] is False  # Agents can attach by default


def test_file_option_and_lazy_flag(launched):
    run("--eager", "-f", "a.csv", "b.csv")
    assert launched[0][1] == ["a.csv", "b.csv"]
    assert launched[0][2]["lazy"] is False


def test_no_targets_opens_empty_viewer(launched):
    run()
    assert launched[0][:2] == ("viewer", [])


def test_session_flags(launched):
    run("--no-session", "a.csv")
    run("--session", "demo", "--mask-pii", "a.csv")
    assert launched[0][2]["session"] is None
    assert launched[1][2]["session"] == "demo" and launched[1][2]["mask_pii"] is True


def test_piped_stdin(launched):
    run(input="x,y\n1,2\n")
    kind, targets, kw = launched[0]
    assert targets == ["-"]
    assert kw["stdin_data"] == b"x,y\n1,2\n"


def test_classic_and_databases(launched):
    run("--classic", "a.csv")
    run("warehouse.duckdb")
    run("--db", "postgres://host/db")
    assert [c[0] for c in launched] == ["classic", "classic", "classic"]


def test_subcommands_still_work(launched, tmp_path):
    path = tmp_path / "d.csv"
    path.write_text("a,b\n1,2\n")
    result = run("profile", str(path))
    assert result.exit_code == 0
    assert launched == []
    assert "a" in result.output
