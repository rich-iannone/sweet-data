"""Tests for editable pipelines, proposals, exports, and the run/compile/diff CLI."""

import json
import sys
import types

import duckdb
import polars as pl
import pytest
from click.testing import CliRunner
from polars.testing import assert_frame_equal

from sweet import Workspace
from sweet.cli import main
from sweet.core.pipeline import Pipeline
from sweet.core.steps import Step


@pytest.fixture
def csv_file(tmp_path):
    path = tmp_path / "orders.csv"
    pl.DataFrame(
        {
            "id": [1, 2, 3, 4, 5],
            "region": ["N", "S", "N", "E", "S"],
            "amount": [100.0, -5.0, 250.0, 80.0, None],
        }
    ).write_csv(path)
    return path


@pytest.fixture(params=["eager", "lazy"])
def ws(request, csv_file):
    ws = Workspace().read(csv_file, lazy=request.param == "lazy")
    ws.apply_step(Step("filter", {"sql": "amount > 0"}, id="f1"))
    ws.apply_step(Step("mutate", {"column": "amount_k", "sql": "amount / 1000"}, id="m1"))
    ws.apply_step(Step("sort", {"columns": ["amount"], "descending": True}, id="s1"))
    return ws


def ids(ws):
    return ws.df["id"].to_list()


class TestEditing:
    def test_toggle(self, ws):
        assert ids(ws) == [3, 1, 4]
        ws.toggle_step("f1")
        assert ids(ws) == [5, 3, 1, 4, 2]  # nulls sort first by default
        assert ws.steps()[0].enabled is False
        ws.toggle_step("f1")
        assert ids(ws) == [3, 1, 4]

    def test_remove_and_undo(self, ws):
        ws.remove_step("s1")
        assert ids(ws) == [1, 3, 4]
        assert [s.id for s in ws.steps()] == ["f1", "m1"]
        ws.undo()
        assert ids(ws) == [3, 1, 4]
        assert [s.id for s in ws.steps()] == ["f1", "m1", "s1"]
        ws.redo()
        assert [s.id for s in ws.steps()] == ["f1", "m1"]

    def test_move(self, ws):
        ws.move_step("s1", 0)
        assert [s.id for s in ws.steps()] == ["s1", "f1", "m1"]
        assert ids(ws) == [3, 1, 4]

    def test_replace_keeps_id_and_position(self, ws):
        ws.replace_step("f1", Step("filter", {"sql": "region = 'N'"}))
        assert [s.id for s in ws.steps()] == ["f1", "m1", "s1"]
        assert ids(ws) == [3, 1]

    def test_insert(self, ws):
        ws.insert_step(1, Step("filter", {"sql": "region <> 'E'"}))
        assert ids(ws) == [3, 1]
        assert len(ws.steps()) == 4

    def test_failed_edit_changes_nothing(self, ws):
        with pytest.raises(ValueError):
            ws.replace_step("f1", Step("filter", {"sql": "nope > 1"}))
        assert ids(ws) == [3, 1, 4]
        assert [s.id for s in ws.steps()] == ["f1", "m1", "s1"]

    def test_unknown_step(self, ws):
        with pytest.raises(ValueError, match="No step"):
            ws.remove_step("zzz")

    def test_frame_at_time_travel(self, ws):
        assert ws.frame_at(0).lazy().collect().height == 5
        assert ws.frame_at(1).lazy().collect().height == 3
        assert "amount_k" in ws.frame_at(2).lazy().collect_schema()
        assert ids(ws) == [3, 1, 4]  # unchanged

    def test_branch_at(self, ws):
        ws.branch_at("raw_ish", 1)
        assert ws.current_sheet_name == "raw_ish"
        assert [s.id for s in ws.steps()] == ["f1"]
        assert sorted(ids(ws)) == [1, 3, 4]
        ws.apply_step(Step("filter", {"sql": "region = 'E'"}))
        assert ids(ws) == [4]
        ws.switch("orders")
        assert ids(ws) == [3, 1, 4]

    def test_pipeline_matches_after_edits(self, ws):
        ws.toggle_step("s1")
        ws.move_step("m1", 0)
        assert_frame_equal(ws.pipeline().run(), ws.df)

    def test_disabled_steps_excluded_from_codegen(self, ws):
        ws.toggle_step("f1")
        assert "amount > 0" not in ws.generate_code()

    def test_non_step_change_blocks_editing(self, csv_file):
        ws = Workspace().read(csv_file)
        ws.apply_step(Step("filter", {"sql": "amount > 0"}, id="f1"))
        ws.impute("amount", method="zero")
        with pytest.raises(ValueError, match="aren't steps"):
            ws.toggle_step("f1")

    def test_manual_step_blocks_replay(self, csv_file):
        ws = Workspace().read(csv_file)
        ws.record_manual(ws.df.head(2))
        ws.apply_step(Step("filter", {"sql": "amount > 0"}, id="f1"))
        with pytest.raises(ValueError, match="manual edit"):
            ws.toggle_step("f1")


class TestPreviewsAndProposals:
    def test_preview_step_changes_nothing(self, ws):
        d = ws.preview_step(Step("filter", {"sql": "region = 'N'"}))
        assert d.rows_removed == 1
        assert ids(ws) == [3, 1, 4]

    def test_preview_steps(self, ws):
        steps = ws.steps()
        steps[0].enabled = False
        d = ws.preview_steps(steps)
        assert d.rows_added == 2
        assert ids(ws) == [3, 1, 4]

    def test_propose_accept(self, ws):
        p = ws.propose(Step("filter", {"sql": "region = 'N'"}), author="agent:claude")
        assert ws.proposals == [p]
        assert ids(ws) == [3, 1, 4]
        ws.accept(p.id)
        assert ids(ws) == [3, 1]
        assert ws.steps()[-1].author == "agent:claude"
        assert ws.proposals == []
        actions = [e.action for e in ws.audit.entries]
        assert actions[-3:] == ["propose", "accept", "step"]

    def test_propose_reject(self, ws):
        p = ws.propose(Step("filter", {"sql": "region = 'N'"}))
        ws.reject(p.id)
        assert ws.proposals == [] and ids(ws) == [3, 1, 4]
        assert ws.audit.entries[-1].action == "reject"

    def test_invalid_proposal_rejected_up_front(self, ws):
        with pytest.raises(ValueError):
            ws.propose(Step("filter", {"sql": "nope = 1"}))


# -----------------------------------------------------------------------------
# Exports: each one reproduces the session's result
# -----------------------------------------------------------------------------


@pytest.fixture
def pipeline(csv_file):
    ws = Workspace().read(csv_file)
    ws.apply_step(Step("filter", {"sql": "amount > 0"}))
    ws.apply_step(Step("mutate", {"column": "amount_k", "sql": "amount / 1000"}))
    ws.apply_step(Step("rename", {"mapping": {"region": "area"}}))
    ws.apply_step(Step("sort", {"columns": ["amount"], "descending": True}))
    return ws.pipeline(), ws.df


def test_dbt_model_reproduces_result(pipeline, csv_file):
    p, expected = pipeline
    model = p.to_dbt(source_name="raw")
    assert "{{ source('raw', 'orders') }}" in model
    compiled = model.replace("{{ source('raw', 'orders') }}", f"read_csv_auto('{csv_file}')")
    result = pl.from_arrow(duckdb.connect().execute(compiled).fetch_arrow_table())
    assert_frame_equal(result, expected, check_dtypes=False)
    sources = p.to_dbt_sources()
    assert "name: raw" in sources and "name: orders" in sources


def _fake_marimo():
    """A stand-in for marimo that runs cells in order, passing returned names along."""

    class App:
        def __init__(self, **_):
            self.cells = []

        def cell(self, fn):
            self.cells.append(fn)
            return fn

        def run(self):
            import inspect

            namespace = {}
            for fn in self.cells:
                args = [namespace[name] for name in inspect.signature(fn).parameters]
                result = fn(*args)
                names = fn.__code__.co_consts  # noqa: F841 (documentation only)
                returned = result if isinstance(result, tuple) else (result,)
                source = inspect.getsource(fn).strip().splitlines()[-1]
                labels = source.replace("return (", "").rstrip(",)").split(",")
                for label, value in zip([x.strip() for x in labels if x.strip()], returned):
                    namespace[label] = value
            return namespace

    return types.SimpleNamespace(App=App)


def test_marimo_notebook_reproduces_result(pipeline, tmp_path, monkeypatch):
    p, expected = pipeline
    source = p.to_marimo()
    path = tmp_path / "nb.py"
    path.write_text(source)
    monkeypatch.setitem(sys.modules, "marimo", _fake_marimo())
    module = types.ModuleType("nb")
    module.__file__ = str(path)
    exec(compile(source, str(path), "exec"), module.__dict__)
    namespace = module.app.run()
    last = f"df_{len(p.active_steps)}"
    assert_frame_equal(namespace[last], expected)


def test_marimo_with_sql_and_statement_steps(csv_file, tmp_path, monkeypatch):
    ws = Workspace().read(csv_file)
    ws.apply_step(Step("polars", {"code": "df = df.drop_nulls()\ndf = df.head(3)"}))
    ws.apply_step(
        Step("sql", {"query": "SELECT region, sum(amount) AS total FROM df GROUP BY 1 ORDER BY 1"})
    )
    source = ws.pipeline().to_marimo()
    assert "import duckdb" in source
    path = tmp_path / "nb.py"
    path.write_text(source)
    monkeypatch.setitem(sys.modules, "marimo", _fake_marimo())
    module = types.ModuleType("nb")
    exec(compile(source, str(path), "exec"), module.__dict__)
    assert_frame_equal(module.app.run()["df_2"], ws.df)


# -----------------------------------------------------------------------------
# CLI
# -----------------------------------------------------------------------------


@pytest.fixture
def saved_pipeline(pipeline, tmp_path):
    p, expected = pipeline
    return p.save(tmp_path / "orders.sweet.yaml"), expected


def invoke(*args):
    return CliRunner().invoke(main, [str(a) for a in args], catch_exceptions=False)


def test_cli_run_replays_pipeline(saved_pipeline, tmp_path):
    path, expected = saved_pipeline
    out = tmp_path / "out.parquet"
    result = invoke("run", path, "-o", out)
    assert result.exit_code == 0, result.output
    assert "4 step(s)" in result.output
    assert_frame_equal(pl.read_parquet(out), expected)


def test_cli_run_on_new_input_reports_drift(saved_pipeline, tmp_path):
    path, _ = saved_pipeline
    new = tmp_path / "new.csv"
    pl.DataFrame({"id": [9], "region": ["W"], "amount": [5.0], "extra": [1]}).write_csv(new)
    result = invoke("run", path, "-i", new, "--format", "json")
    data = json.loads(result.output)
    assert data["rows"] == 1
    assert data["schema_drift"] == ["new input column 'extra'"]


def test_cli_run_agent_steps_still_work(csv_file):
    result = invoke("run", csv_file, "trim_whitespace")
    assert result.exit_code == 0
    assert "Agent run" in result.output


@pytest.mark.parametrize("target", ["polars", "sql", "dbt", "marimo"])
def test_cli_compile(saved_pipeline, tmp_path, target):
    path, _ = saved_pipeline
    out = tmp_path / f"out_{target}.txt"
    result = invoke("compile", path, "--to", target, "-o", out)
    assert result.exit_code == 0, result.output
    assert out.read_text().strip()
    if target == "dbt":
        assert (tmp_path / "sources.yml").exists()


def test_cli_compile_polars_script_runs(saved_pipeline, tmp_path):
    path, expected = saved_pipeline
    script = invoke("compile", path, "--to", "polars").output
    namespace = {}
    exec(script, namespace)
    assert_frame_equal(namespace["df"], expected)


def test_cli_compile_unexportable(csv_file, tmp_path):
    ws = Workspace().read(csv_file)
    ws.apply_step(Step("polars", {"code": "df.head(1)"}))
    path = ws.save_pipeline(tmp_path / "p.sweet.yaml")
    result = CliRunner().invoke(main, ["compile", str(path), "--to", "sql"])
    assert result.exit_code != 0
    assert "can't be exported to SQL" in result.output


def test_cli_diff(tmp_path):
    a, b = tmp_path / "a.csv", tmp_path / "b.parquet"
    pl.DataFrame({"id": [1, 2, 3], "x": [1, 2, 3]}).write_csv(a)
    pl.DataFrame({"id": [2, 3, 4], "x": [2, 30, 4]}).write_parquet(b)
    text = CliRunner().invoke(main, ["diff", str(a), str(b), "-k", "id"])
    assert text.exit_code == 1  # Differences found
    assert "+1 rows · −1 rows · 1 col changed (1 cell)" in text.output
    data = json.loads(
        CliRunner().invoke(main, ["diff", str(a), str(b), "-k", "id", "--format", "json"]).output
    )
    assert data["rows"] == {
        "before": 3,
        "after": 3,
        "added": 1,
        "removed": 1,
        "changed": 1,
        "unchanged": 1,
    }
    same = CliRunner().invoke(main, ["diff", str(a), str(a), "-k", "id"])
    assert same.exit_code == 0 and "no changes" in same.output
