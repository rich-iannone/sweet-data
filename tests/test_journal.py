"""Tests for the hash-chained audit journal and Workspace step/undo integration."""

import dataclasses

import polars as pl
import pytest
from polars.testing import assert_frame_equal

from sweet import Workspace
from sweet.core.journal import GENESIS_HASH, Journal
from sweet.core.pipeline import Pipeline
from sweet.core.steps import Step


class TestJournal:
    def test_empty(self):
        j = Journal()
        assert len(j) == 0
        assert j.head_hash == GENESIS_HASH
        assert j.verify() is None

    def test_chain(self):
        j = Journal()
        a = j.append("load", sheet="s", payload={"path": "x.csv"})
        b = j.append("step", sheet="s", author="agent:claude", payload={"k": 1})
        assert a.prev_hash == GENESIS_HASH
        assert b.prev_hash == a.hash
        assert j.head_hash == b.hash
        assert j.verify() is None

    def test_tamper_detected(self):
        j = Journal()
        j.append("load", payload={"path": "x.csv"})
        j.append("step", payload={"k": 1})
        j.append("step", payload={"k": 2})
        j._entries[1] = dataclasses.replace(j._entries[1], author="someone-else")
        assert j.verify() == 1

    def test_removal_detected(self):
        j = Journal()
        for i in range(3):
            j.append("step", payload={"i": i})
        del j._entries[1]
        assert j.verify() == 1

    def test_payload_is_copied(self):
        payload = {"a": [1]}
        j = Journal()
        j.append("step", payload=payload)
        payload["a"].append(2)
        assert j.entries[0].payload == {"a": [1]}
        assert j.verify() is None

    def test_jsonl_round_trip(self, tmp_path):
        j = Journal()
        j.append("load", payload={"path": "x.csv"})
        j.append("step", author="agent:x", payload={"step": {"kind": "filter"}})
        path = j.export_jsonl(tmp_path / "audit.jsonl")
        again = Journal.load_jsonl(path)
        assert again.verify() is None
        assert again.head_hash == j.head_hash

    def test_jsonl_tamper_detected(self, tmp_path):
        j = Journal()
        j.append("step", payload={"v": 1})
        path = j.export_jsonl(tmp_path / "audit.jsonl")
        path.write_text(path.read_text().replace('"v":1', '"v":2'))
        assert Journal.load_jsonl(path).verify() == 0


@pytest.fixture
def csv_file(tmp_path):
    path = tmp_path / "people.csv"
    pl.DataFrame(
        {"name": ["Alice", "Bob", "Charlie"], "age": [30, 25, 35], "score": [1.0, 2.0, 3.0]}
    ).write_csv(path)
    return path


@pytest.fixture
def ws(csv_file):
    return Workspace().load(csv_file)


class TestWorkspaceSteps:
    def test_apply_step_and_dict(self, ws):
        ws.apply_step(Step("filter", {"sql": "age > 26"}))
        ws.apply_step({"kind": "select", "params": {"columns": ["name"]}})
        assert ws.df["name"].to_list() == ["Alice", "Charlie"]
        assert [op.step["kind"] for op in ws.history() if op.step] == ["filter", "select"]

    def test_author_recorded(self, ws):
        ws.apply_step(Step("filter", {"sql": "age > 26"}), author="agent:claude")
        assert ws.history()[-1].author == "agent:claude"
        assert ws.audit.entries[-1].author == "agent:claude"

    def test_legacy_methods_produce_steps(self, ws):
        ws.filter("pl.col('age') > 26").sort("age").select("name", "age")
        kinds = [s.kind for s in ws.pipeline().steps]
        assert kinds == ["filter", "sort", "select"]

    def test_pipeline_reproduces_workspace(self, ws, tmp_path):
        ws.apply_step(Step("filter", {"sql": "age > 26"}))
        ws.transform("df.with_columns((pl.col('score') * 10).alias('s10'))")
        ws.query("SELECT name, s10 FROM people ORDER BY name")
        path = ws.save_pipeline(tmp_path / "p.sweet.yaml")
        assert_frame_equal(Pipeline.load(path).run(), ws.df)

    def test_pipeline_has_source_and_schema(self, ws, csv_file):
        p = ws.pipeline()
        assert p.source == {"path": str(csv_file), "format": "csv"}
        assert p.schema == {"name": "String", "age": "Int64", "score": "Float64"}

    def test_events(self, ws):
        events = []
        unsubscribe = ws.subscribe(lambda kind, details: events.append((kind, details)))
        ws.apply_step(Step("filter", {"sql": "age > 26"}))
        ws.undo()
        ws.redo()
        unsubscribe()
        ws.apply_step(Step("drop", {"columns": ["score"]}))
        assert [e[0] for e in events] == ["step", "undo", "redo"]
        assert events[0][1]["step"]["kind"] == "filter"

    def test_broken_listener_does_not_break_engine(self, ws):
        ws.subscribe(lambda *_: 1 / 0)
        ws.apply_step(Step("filter", {"sql": "age > 26"}))
        assert ws.shape == (2, 3)

    def test_audit_chain_covers_session(self, ws):
        ws.apply_step(Step("filter", {"sql": "age > 26"}))
        ws.undo()
        ws.redo()
        actions = [e.action for e in ws.audit.entries]
        assert actions == ["load", "step", "undo", "redo"]
        assert ws.audit.verify() is None


class TestUndoRedoFixes:
    def test_sql_redo(self, ws):
        """Redo used to re-evaluate SQL as a Python expression."""
        ws.query("SELECT name FROM people WHERE age > 26")
        after = ws.df
        ws.undo()
        assert ws.shape == (3, 3)
        ws.redo()
        assert_frame_equal(ws.df, after)

    def test_undo_redo_restore_transform_steps(self, ws):
        ws.filter("pl.col('age') > 26")
        ws.undo()
        assert ws.current_sheet.transform_steps == []
        ws.redo()
        assert len(ws.current_sheet.transform_steps) == 1
        assert len(ws.pipeline().steps) == 1

    def test_undo_impute_and_augment(self, ws):
        before = ws.df
        ws.augment("row_number")
        assert ws.shape[1] == 4
        ws.undo()
        assert_frame_equal(ws.df, before)

    def test_new_step_clears_redo(self, ws):
        ws.filter("pl.col('age') > 26")
        ws.undo()
        assert ws.can_redo
        ws.sort("age")
        assert not ws.can_redo

    def test_branch_records_source_sheet(self, ws):
        ws.branch("experiment")
        assert ws.history()[-1].metadata["from_sheet"] == "people"
        assert ws.pipeline().source["path"].endswith("people.csv")
