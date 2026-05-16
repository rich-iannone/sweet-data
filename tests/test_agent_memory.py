"""Tests for Agent Memory: preferences, domain rules, run history, and suggestions."""

import json
from pathlib import Path

import polars as pl
import pytest

from sweet.agents import AgentMemory, DataAgent, RecipeRegistry
from sweet.agents.memory import DatasetFingerprint, RunRecord
from sweet.core.workspace import Workspace


# =============================================================================
# DatasetFingerprint
# =============================================================================


class TestDatasetFingerprint:
    """Tests for dataset fingerprinting and similarity."""

    def test_fingerprint_from_workspace(self):
        """Create fingerprint from a workspace."""
        ws = Workspace()
        ws.load_df(pl.DataFrame({"x": [1, 2, 3], "name": ["a", "b", "c"]}))

        fp = AgentMemory.fingerprint_workspace(ws)

        assert fp.columns == ["x", "name"]
        assert fp.row_count == 3
        assert fp.column_count == 2
        assert "x" in fp.dtypes
        assert fp.content_hash is not None

    def test_fingerprint_empty_workspace(self):
        """Fingerprint of empty workspace."""
        ws = Workspace()

        fp = AgentMemory.fingerprint_workspace(ws)

        assert fp.columns == []
        assert fp.row_count == 0

    def test_similarity_identical(self):
        """Identical fingerprints have similarity 1.0."""
        fp = DatasetFingerprint(
            columns=["x", "y", "z"],
            dtypes={"x": "Int64", "y": "Utf8", "z": "Float64"},
            row_count=100,
            column_count=3,
        )
        assert fp.similarity(fp) == 1.0

    def test_similarity_no_overlap(self):
        """No shared columns → similarity 0."""
        fp1 = DatasetFingerprint(
            columns=["a", "b"],
            dtypes={"a": "Int64", "b": "Utf8"},
            row_count=10,
            column_count=2,
        )
        fp2 = DatasetFingerprint(
            columns=["x", "y"],
            dtypes={"x": "Int64", "y": "Utf8"},
            row_count=10,
            column_count=2,
        )
        assert fp1.similarity(fp2) == 0.0

    def test_similarity_partial_overlap(self):
        """Partial column overlap gives intermediate similarity."""
        fp1 = DatasetFingerprint(
            columns=["x", "y", "z"],
            dtypes={"x": "Int64", "y": "Utf8", "z": "Float64"},
            row_count=100,
            column_count=3,
        )
        fp2 = DatasetFingerprint(
            columns=["x", "y", "w"],
            dtypes={"x": "Int64", "y": "Utf8", "w": "Boolean"},
            row_count=50,
            column_count=3,
        )
        score = fp1.similarity(fp2)
        assert 0.3 < score < 0.9  # Partial match

    def test_similarity_same_columns_different_types(self):
        """Same columns but different types → lower similarity."""
        fp1 = DatasetFingerprint(
            columns=["x", "y"],
            dtypes={"x": "Int64", "y": "Utf8"},
            row_count=10,
            column_count=2,
        )
        fp2 = DatasetFingerprint(
            columns=["x", "y"],
            dtypes={"x": "Utf8", "y": "Float64"},
            row_count=10,
            column_count=2,
        )
        score = fp1.similarity(fp2)
        # Same columns (jaccard=1.0) but types don't match (dtype_match=0)
        assert score == pytest.approx(0.6)

    def test_fingerprint_roundtrip(self):
        """Fingerprint serializes and deserializes."""
        fp = DatasetFingerprint(
            columns=["a", "b"],
            dtypes={"a": "Int64", "b": "Utf8"},
            row_count=42,
            column_count=2,
            file_name="test.csv",
            content_hash="abc123",
        )
        d = fp.to_dict()
        fp2 = DatasetFingerprint.from_dict(d)
        assert fp2.columns == fp.columns
        assert fp2.content_hash == fp.content_hash


# =============================================================================
# AgentMemory — Preferences
# =============================================================================


class TestAgentMemoryPreferences:
    """Tests for preference storage."""

    def test_set_and_get(self, tmp_path):
        """Set and get a preference."""
        mem = AgentMemory(_memory_dir=tmp_path)
        mem.set_preference("date_format", "ISO-8601")

        assert mem.get_preference("date_format") == "ISO-8601"

    def test_get_default(self, tmp_path):
        """Get with default for missing key."""
        mem = AgentMemory(_memory_dir=tmp_path)
        assert mem.get_preference("missing", "fallback") == "fallback"

    def test_persistence(self, tmp_path):
        """Preferences persist across load/save cycles."""
        mem = AgentMemory(_memory_dir=tmp_path)
        mem.set_preference("naming", "snake_case")
        mem.save()

        mem2 = AgentMemory.load(memory_dir=tmp_path)
        assert mem2.get_preference("naming") == "snake_case"


# =============================================================================
# AgentMemory — Domain Rules
# =============================================================================


class TestAgentMemoryRules:
    """Tests for domain rule storage."""

    def test_add_and_get_rule(self, tmp_path):
        """Add and retrieve a domain rule."""
        mem = AgentMemory(_memory_dir=tmp_path)
        mem.add_rule("revenue_positive", {"column": "revenue", "check": "> 0", "severity": "error"})

        rule = mem.get_rule("revenue_positive")
        assert rule is not None
        assert rule["column"] == "revenue"

    def test_list_rules(self, tmp_path):
        """List all rules."""
        mem = AgentMemory(_memory_dir=tmp_path)
        mem.add_rule("r1", {"check": "x > 0"})
        mem.add_rule("r2", {"check": "y is not null"})

        rules = mem.list_rules()
        assert len(rules) == 2
        names = [r["name"] for r in rules]
        assert "r1" in names
        assert "r2" in names

    def test_rules_persist(self, tmp_path):
        """Rules persist across save/load."""
        mem = AgentMemory(_memory_dir=tmp_path)
        mem.add_rule("test_rule", {"severity": "warning"})
        mem.save()

        mem2 = AgentMemory.load(memory_dir=tmp_path)
        assert mem2.get_rule("test_rule") is not None


# =============================================================================
# AgentMemory — Run History
# =============================================================================


class TestAgentMemoryHistory:
    """Tests for run history recording and querying."""

    def _make_record(self, success=True, recipe=None, columns=None):
        """Helper to create a RunRecord."""
        return RunRecord(
            timestamp="2026-05-16T10:00:00+00:00",
            recipe_name=recipe,
            steps=["validate"],
            dataset_fingerprint=DatasetFingerprint(
                columns=columns or ["x", "y"],
                dtypes={"x": "Int64", "y": "Utf8"},
                row_count=100,
                column_count=2,
            ),
            success=success,
            n_passed=1,
            n_failed=0,
            n_rolled_back=0,
            duration_s=0.5,
        )

    def test_record_run(self, tmp_path):
        """Record a run."""
        mem = AgentMemory(_memory_dir=tmp_path)
        record = self._make_record()
        mem.record_run(record)

        assert len(mem.run_history) == 1

    def test_history_cap(self, tmp_path):
        """History is capped at 500 records."""
        mem = AgentMemory(_memory_dir=tmp_path)
        for i in range(510):
            mem.record_run(self._make_record())

        assert len(mem.run_history) == 500

    def test_find_similar_runs(self, tmp_path):
        """Find runs on similar datasets."""
        mem = AgentMemory(_memory_dir=tmp_path)
        mem.record_run(self._make_record(columns=["x", "y"]))
        mem.record_run(self._make_record(columns=["a", "b", "c"]))

        fp = DatasetFingerprint(
            columns=["x", "y"],
            dtypes={"x": "Int64", "y": "Utf8"},
            row_count=50,
            column_count=2,
        )
        similar = mem.find_similar_runs(fp, threshold=0.5)
        assert len(similar) == 1

    def test_suggest_recipe(self, tmp_path):
        """Suggest recipe from successful past runs."""
        mem = AgentMemory(_memory_dir=tmp_path)
        mem.record_run(self._make_record(success=True, recipe="clean-csv", columns=["x", "y"]))
        mem.record_run(self._make_record(success=True, recipe="clean-csv", columns=["x", "y"]))
        mem.record_run(self._make_record(success=True, recipe="quality-check", columns=["x", "y"]))

        fp = DatasetFingerprint(
            columns=["x", "y"],
            dtypes={"x": "Int64", "y": "Utf8"},
            row_count=50,
            column_count=2,
        )
        suggestion = mem.suggest_recipe(fp)
        assert suggestion == "clean-csv"

    def test_suggest_recipe_no_history(self, tmp_path):
        """No suggestion when no history."""
        mem = AgentMemory(_memory_dir=tmp_path)
        fp = DatasetFingerprint(columns=["x"], dtypes={"x": "Int64"}, row_count=10, column_count=1)
        assert mem.suggest_recipe(fp) is None

    def test_history_persistence(self, tmp_path):
        """History persists across save/load."""
        mem = AgentMemory(_memory_dir=tmp_path)
        mem.record_run(self._make_record(recipe="test-recipe"))
        mem.save()

        mem2 = AgentMemory.load(memory_dir=tmp_path)
        assert len(mem2.run_history) == 1
        assert mem2.run_history[0].recipe_name == "test-recipe"


# =============================================================================
# AgentMemory — Summary
# =============================================================================


class TestAgentMemorySummary:
    """Tests for summary output."""

    def test_summary(self, tmp_path):
        """Summary returns correct counts."""
        mem = AgentMemory(_memory_dir=tmp_path)
        mem.set_preference("a", 1)
        mem.set_preference("b", 2)
        mem.add_rule("r1", {"check": "x > 0"})

        info = mem.summary()
        assert info["n_preferences"] == 2
        assert info["n_domain_rules"] == 1
        assert info["n_run_records"] == 0
        assert info["n_successful_runs"] == 0


# =============================================================================
# DataAgent + Memory Integration
# =============================================================================


class TestDataAgentMemoryIntegration:
    """Integration tests for DataAgent with memory."""

    def test_agent_records_to_memory(self, tmp_path):
        """DataAgent automatically records runs to memory."""
        mem = AgentMemory(_memory_dir=tmp_path)
        ws = Workspace()
        ws.load_df(pl.DataFrame({"x": [1, 2, 3]}))

        agent = DataAgent(workspace=ws, memory=mem, validate_between_steps=False)
        agent.run_steps(["validate"])

        assert len(mem.run_history) == 1
        assert mem.run_history[0].success is True
        assert mem.run_history[0].steps == ["validate"]

    def test_agent_records_recipe_name(self, tmp_path):
        """DataAgent records recipe name when running a recipe."""
        mem = AgentMemory(_memory_dir=tmp_path)
        ws = Workspace()
        ws.load_df(pl.DataFrame({"x": [1, 2, 3]}))

        registry = RecipeRegistry()
        recipe = registry.get("quality-check")

        agent = DataAgent(workspace=ws, memory=mem)
        agent.run_recipe(recipe)

        assert len(mem.run_history) >= 1
        # The recipe run is recorded (run_recipe calls run_steps which also records,
        # plus run_recipe records again — find the one with recipe_name)
        recipe_records = [r for r in mem.run_history if r.recipe_name is not None]
        assert len(recipe_records) >= 1
        assert recipe_records[0].recipe_name == "Data Quality Check"

    def test_agent_suggest_recipe_from_memory(self, tmp_path):
        """Agent suggests recipes based on memory."""
        mem = AgentMemory(_memory_dir=tmp_path)

        # First run: record a successful recipe
        ws1 = Workspace()
        ws1.load_df(pl.DataFrame({"val": [1, 2, 3], "label": ["a", "b", "c"]}))
        agent1 = DataAgent(workspace=ws1, memory=mem, validate_between_steps=False)

        registry = RecipeRegistry()
        recipe = registry.get("quality-check")
        agent1.run_recipe(recipe)
        mem.save()

        # Second run: similar data → should suggest same recipe
        ws2 = Workspace()
        ws2.load_df(pl.DataFrame({"val": [10, 20], "label": ["x", "y"]}))
        agent2 = DataAgent(workspace=ws2, memory=mem, validate_between_steps=False)

        suggestion = agent2.suggest_recipe()
        assert suggestion == "Data Quality Check"

    def test_agent_without_memory_no_error(self):
        """DataAgent works fine without memory."""
        ws = Workspace()
        ws.load_df(pl.DataFrame({"x": [1, 2, 3]}))

        agent = DataAgent(workspace=ws, validate_between_steps=False)
        result = agent.run_steps(["validate"])

        assert result.success is True
        assert agent.suggest_recipe() is None
