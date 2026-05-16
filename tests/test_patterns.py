"""Tests for sweet.core.patterns — Usage pattern learning."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import polars as pl
import pytest

from sweet.core.patterns import (
    PatternEntry,
    PatternStore,
    _glob_to_regex,
    _is_camel,
    _is_snake,
    observe_transform,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def tmp_store(tmp_path):
    """Create a PatternStore using a temp directory."""
    return PatternStore(memory_dir=tmp_path)


SAMPLE_SCHEMA = {
    "name": "Utf8",
    "age": "Int64",
    "score": "Float64",
    "created": "Utf8",
}


# ---------------------------------------------------------------------------
# PatternStore basics
# ---------------------------------------------------------------------------


class TestPatternStore:
    def test_empty_store(self, tmp_store):
        assert tmp_store.pattern_count == 0
        assert tmp_store.patterns == []

    def test_observe_creates_entry(self, tmp_store):
        entry = tmp_store.observe("cast", "dtype:Utf8", "cast to Date")
        assert entry.kind == "cast"
        assert entry.trigger == "dtype:Utf8"
        assert entry.action == "cast to Date"
        assert entry.count == 1

    def test_observe_increments_count(self, tmp_store):
        tmp_store.observe("trim", "all:strings", "trim whitespace")
        tmp_store.observe("trim", "all:strings", "trim whitespace")
        entry = tmp_store.observe("trim", "all:strings", "trim whitespace")
        assert entry.count == 3

    def test_observe_different_actions_separate(self, tmp_store):
        tmp_store.observe("cast", "dtype:Utf8", "cast to Date")
        tmp_store.observe("cast", "dtype:Utf8", "cast to Int64")
        assert tmp_store.pattern_count == 2

    def test_observe_with_metadata(self, tmp_store):
        entry = tmp_store.observe("cast", "dtype:Utf8", "cast to Date", metadata={"col": "d"})
        assert entry.metadata == {"col": "d"}

    def test_persistence(self, tmp_path):
        store1 = PatternStore(memory_dir=tmp_path)
        store1.observe("cast", "dtype:Utf8", "cast to Date")
        store1.observe("cast", "dtype:Utf8", "cast to Date")

        # New store loads from same directory
        store2 = PatternStore(memory_dir=tmp_path)
        assert store2.pattern_count == 1
        assert store2.patterns[0].count == 2

    def test_timestamps(self, tmp_store):
        entry = tmp_store.observe("trim", "all:x", "y")
        assert entry.first_seen != ""
        assert entry.last_seen != ""


# ---------------------------------------------------------------------------
# Querying
# ---------------------------------------------------------------------------


class TestQuery:
    def test_query_by_kind(self, tmp_store):
        tmp_store.observe("cast", "dtype:Utf8", "to Date")
        tmp_store.observe("trim", "all:strings", "trim")
        tmp_store.observe("cast", "dtype:Int64", "to Float")

        results = tmp_store.query(kind="cast")
        assert len(results) == 2
        assert all(p.kind == "cast" for p in results)

    def test_query_by_trigger_exact(self, tmp_store):
        tmp_store.observe("cast", "dtype:Utf8", "to Date")
        tmp_store.observe("cast", "dtype:Int64", "to Float")

        results = tmp_store.query(trigger="dtype:Utf8")
        assert len(results) == 1
        assert results[0].action == "to Date"

    def test_query_by_trigger_regex(self, tmp_store):
        tmp_store.observe("cast", "dtype:Utf8", "to Date")
        tmp_store.observe("cast", "dtype:Int64", "to Float")
        tmp_store.observe("trim", "all:strings", "trim")

        results = tmp_store.query(trigger="^dtype:")
        assert len(results) == 2

    def test_query_by_min_count(self, tmp_store):
        tmp_store.observe("cast", "dtype:Utf8", "to Date")
        tmp_store.observe("cast", "dtype:Utf8", "to Date")
        tmp_store.observe("cast", "dtype:Utf8", "to Date")
        tmp_store.observe("trim", "all:x", "y")  # Only 1 time

        results = tmp_store.query(min_count=3)
        assert len(results) == 1
        assert results[0].count == 3

    def test_query_sorted_by_count(self, tmp_store):
        for _ in range(5):
            tmp_store.observe("a", "t1", "action1")
        for _ in range(2):
            tmp_store.observe("b", "t2", "action2")
        for _ in range(8):
            tmp_store.observe("c", "t3", "action3")

        results = tmp_store.query()
        assert results[0].count == 8
        assert results[1].count == 5


# ---------------------------------------------------------------------------
# Suggestions for columns
# ---------------------------------------------------------------------------


class TestSuggestionsFor:
    def test_matches_by_dtype(self, tmp_store):
        # Observe a cast pattern enough times
        for _ in range(5):
            tmp_store.observe("cast", "dtype:Utf8", "cast to Date")

        suggestions = tmp_store.suggestions_for(SAMPLE_SCHEMA)
        assert len(suggestions) > 0
        assert suggestions[0]["kind"] == "cast"
        assert suggestions[0]["source"] == "learned"

    def test_matches_by_name_glob(self, tmp_store):
        for _ in range(4):
            tmp_store.observe("drop", "name:*_id", "drop column")

        cols = {"user_id": "Int64", "order_id": "Int64", "name": "Utf8"}
        suggestions = tmp_store.suggestions_for(cols)
        assert len(suggestions) > 0

    def test_matches_all_trigger(self, tmp_store):
        for _ in range(3):
            tmp_store.observe("trim", "all:string_columns", "trim whitespace")

        suggestions = tmp_store.suggestions_for(SAMPLE_SCHEMA)
        assert len(suggestions) > 0
        assert suggestions[0]["kind"] == "trim"

    def test_no_match_below_threshold(self, tmp_store):
        tmp_store.observe("cast", "dtype:Utf8", "cast to Date")  # Only 1 time
        suggestions = tmp_store.suggestions_for(SAMPLE_SCHEMA)
        assert len(suggestions) == 0

    def test_custom_min_count(self, tmp_store):
        tmp_store.observe("cast", "dtype:Utf8", "cast to Date")
        tmp_store.observe("cast", "dtype:Utf8", "cast to Date")
        suggestions = tmp_store.suggestions_for(SAMPLE_SCHEMA, min_count=2)
        assert len(suggestions) > 0

    def test_confidence_scales(self, tmp_store):
        for _ in range(10):
            tmp_store.observe("cast", "dtype:Utf8", "cast to Date")
        suggestions = tmp_store.suggestions_for(SAMPLE_SCHEMA)
        # High count = high confidence
        assert suggestions[0]["confidence"] > 0.7

    def test_no_match_wrong_dtype(self, tmp_store):
        for _ in range(5):
            tmp_store.observe("cast", "dtype:Boolean", "to Int")
        # SAMPLE_SCHEMA has no Boolean columns
        suggestions = tmp_store.suggestions_for(SAMPLE_SCHEMA)
        assert len(suggestions) == 0


# ---------------------------------------------------------------------------
# Top patterns and summary
# ---------------------------------------------------------------------------


class TestSummary:
    def test_summary_empty(self, tmp_store):
        info = tmp_store.summary()
        assert info["total_patterns"] == 0
        assert info["actionable_patterns"] == 0

    def test_summary_with_data(self, tmp_store):
        for _ in range(5):
            tmp_store.observe("cast", "dtype:Utf8", "to Date")
        tmp_store.observe("trim", "all:x", "y")

        info = tmp_store.summary()
        assert info["total_patterns"] == 2
        assert info["actionable_patterns"] == 1  # Only cast has count >= 3
        assert info["kinds"]["cast"] == 1
        assert info["kinds"]["trim"] == 1

    def test_top_patterns(self, tmp_store):
        for _ in range(10):
            tmp_store.observe("a", "t", "act_a")
        for _ in range(3):
            tmp_store.observe("b", "t", "act_b")

        top = tmp_store.top_patterns(limit=1)
        assert len(top) == 1
        assert top[0]["count"] == 10


# ---------------------------------------------------------------------------
# Forget
# ---------------------------------------------------------------------------


class TestForget:
    def test_forget_all(self, tmp_store):
        tmp_store.observe("cast", "dtype:Utf8", "to Date")
        tmp_store.observe("trim", "all:x", "y")
        removed = tmp_store.forget()
        assert removed == 2
        assert tmp_store.pattern_count == 0

    def test_forget_by_kind(self, tmp_store):
        tmp_store.observe("cast", "dtype:Utf8", "to Date")
        tmp_store.observe("trim", "all:x", "y")
        removed = tmp_store.forget(kind="cast")
        assert removed == 1
        assert tmp_store.pattern_count == 1

    def test_forget_by_trigger(self, tmp_store):
        tmp_store.observe("cast", "dtype:Utf8", "to Date")
        tmp_store.observe("cast", "dtype:Int64", "to Float")
        removed = tmp_store.forget(trigger="dtype:Utf8")
        assert removed == 1
        assert tmp_store.pattern_count == 1


# ---------------------------------------------------------------------------
# observe_transform
# ---------------------------------------------------------------------------


class TestObserveTransform:
    def test_detects_cast_pattern(self, tmp_store):
        expr = "df.with_columns(pl.col('created').cast(pl.Date))"
        recorded = observe_transform(tmp_store, expr, SAMPLE_SCHEMA)
        assert len(recorded) > 0
        assert recorded[0].kind == "cast"
        assert "Date" in recorded[0].action

    def test_detects_trim_pattern(self, tmp_store):
        expr = "df.with_columns(pl.col('name').str.strip_chars())"
        recorded = observe_transform(tmp_store, expr, SAMPLE_SCHEMA)
        assert len(recorded) > 0
        assert recorded[0].kind == "trim"

    def test_detects_rename_pattern(self, tmp_store):
        expr = "df.rename({'firstName': 'first_name', 'lastName': 'last_name'})"
        schema = {"firstName": "Utf8", "lastName": "Utf8"}
        recorded = observe_transform(tmp_store, expr, schema)
        assert len(recorded) > 0
        assert recorded[0].kind == "rename"
        assert "snake_case" in recorded[0].action

    def test_detects_drop_pattern(self, tmp_store):
        expr = "df.drop('age')"
        recorded = observe_transform(tmp_store, expr, SAMPLE_SCHEMA)
        assert len(recorded) > 0
        assert recorded[0].kind == "drop"

    def test_no_match_for_unknown_pattern(self, tmp_store):
        expr = "df.head(10)"
        recorded = observe_transform(tmp_store, expr, SAMPLE_SCHEMA)
        assert len(recorded) == 0


# ---------------------------------------------------------------------------
# Workspace integration
# ---------------------------------------------------------------------------


class TestWorkspacePatterns:
    def test_transform_records_pattern(self, tmp_path):
        from sweet.core.workspace import Workspace

        ws = Workspace()
        ws._pattern_store = PatternStore(memory_dir=tmp_path)
        ws.load_df(
            pl.DataFrame({"name": ["Alice", "Bob"], "age": [30, 25]}),
            name="data",
        )
        ws.transform(
            "df.with_columns(pl.col('name').str.strip_chars())",
            description="trim names",
        )

        store = ws._pattern_store
        assert store.pattern_count > 0
        patterns = store.query(kind="trim")
        assert len(patterns) == 1

    def test_learned_suggestions_from_workspace(self, tmp_path):
        from sweet.core.workspace import Workspace

        ws = Workspace()
        store = PatternStore(memory_dir=tmp_path)
        # Pre-populate with enough observations
        for _ in range(5):
            store.observe("trim", "all:string_columns", "trim whitespace")
        ws._pattern_store = store

        ws.load_df(pl.DataFrame({"x": ["a", "b"]}), name="test")
        suggestions = ws.learned_suggestions()
        assert len(suggestions) > 0
        assert suggestions[0]["source"] == "learned"

    def test_patterns_summary_from_workspace(self, tmp_path):
        from sweet.core.workspace import Workspace

        ws = Workspace()
        ws._pattern_store = PatternStore(memory_dir=tmp_path)
        ws._pattern_store.observe("cast", "dtype:Utf8", "to Date")

        info = ws.patterns_summary()
        assert info["total_patterns"] == 1

    def test_forget_patterns_from_workspace(self, tmp_path):
        from sweet.core.workspace import Workspace

        ws = Workspace()
        store = PatternStore(memory_dir=tmp_path)
        store.observe("cast", "dtype:Utf8", "to Date")
        store.observe("trim", "all:x", "y")
        ws._pattern_store = store

        removed = ws.forget_patterns(kind="cast")
        assert removed == 1
        assert ws._pattern_store.pattern_count == 1

    def test_learning_disabled(self, tmp_path):
        from sweet.core.workspace import Workspace

        ws = Workspace()
        ws._pattern_store = PatternStore(memory_dir=tmp_path)
        ws._learning_enabled = False
        ws.load_df(pl.DataFrame({"x": ["a", "b"]}), name="test")
        ws.transform("df.with_columns(pl.col('x').str.strip_chars())")

        assert ws._pattern_store.pattern_count == 0


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class TestHelpers:
    def test_glob_to_regex_star(self):
        r = _glob_to_regex("*_id")
        assert r.match("user_id")
        assert r.match("order_id")
        assert not r.match("id_column")

    def test_glob_to_regex_question(self):
        r = _glob_to_regex("col?")
        assert r.match("col1")
        assert r.match("colA")
        assert not r.match("column")

    def test_is_camel(self):
        assert _is_camel("firstName")
        assert _is_camel("lastName")
        assert not _is_camel("first_name")
        assert not _is_camel("name")

    def test_is_snake(self):
        assert _is_snake("first_name")
        assert _is_snake("last_name")
        assert not _is_snake("firstName")
        assert not _is_snake("name")


# ---------------------------------------------------------------------------
# PatternEntry serialization
# ---------------------------------------------------------------------------


class TestPatternEntry:
    def test_to_dict(self):
        entry = PatternEntry(
            kind="cast",
            trigger="dtype:Utf8",
            action="to Date",
            count=5,
            first_seen="2024-01-01",
            last_seen="2024-06-01",
        )
        d = entry.to_dict()
        assert d["kind"] == "cast"
        assert d["count"] == 5

    def test_from_dict(self):
        d = {
            "kind": "trim",
            "trigger": "all:x",
            "action": "y",
            "count": 3,
            "first_seen": "2024-01-01",
            "last_seen": "2024-06-01",
            "metadata": {"key": "val"},
        }
        entry = PatternEntry.from_dict(d)
        assert entry.kind == "trim"
        assert entry.count == 3
        assert entry.metadata == {"key": "val"}
