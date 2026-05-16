"""Tests for Phase 3: Agent Runtime, Recipes, and Checkpoints."""

from pathlib import Path

import polars as pl
import pytest

from sweet.agents import DataAgent, RecipeRegistry, StepResult
from sweet.agents.agent import (
    AgentResult,
    BUILTIN_STEPS,
    Checkpoint,
    CheckpointAction,
    StepStatus,
)
from sweet.agents.recipes import Recipe
from sweet.core.workspace import Workspace


# =============================================================================
# DataAgent — Basic Execution
# =============================================================================


class TestDataAgentBasic:
    """Tests for DataAgent step execution."""

    def test_run_single_step(self):
        """Run a single named step."""
        ws = Workspace()
        ws.load_df(pl.DataFrame({"x": ["  hello  ", "  world  "]}))

        agent = DataAgent(workspace=ws)
        result = agent.run_steps(["trim_whitespace"])

        assert result.success is True
        assert result.n_passed == 1
        assert ws.df["x"].to_list() == ["hello", "world"]

    def test_run_multiple_steps(self):
        """Run multiple steps in sequence."""
        ws = Workspace()
        ws.load_df(
            pl.DataFrame(
                {
                    "name": ["  Alice  ", "Bob", "Bob"],
                    "val": ["10", "20", "20"],
                }
            )
        )

        agent = DataAgent(workspace=ws, validate_between_steps=False)
        result = agent.run_steps(["trim_whitespace", "detect_and_cast_types", "remove_duplicates"])

        assert result.n_passed == 3
        assert ws.df.height == 2  # Duplicate removed
        assert ws.df["val"].dtype == pl.Int64  # Cast

    def test_unknown_step_fails(self):
        """Unknown step is marked as failed."""
        ws = Workspace()
        ws.load_df(pl.DataFrame({"x": [1, 2, 3]}))

        agent = DataAgent(workspace=ws, validate_between_steps=False)
        result = agent.run_steps(["nonexistent_step"])

        assert result.success is False
        assert result.steps[0].status == StepStatus.FAILED
        assert "Unknown step" in result.steps[0].error

    def test_callable_step(self):
        """Accept callable as a step."""
        ws = Workspace()
        ws.load_df(pl.DataFrame({"x": [1, 2, 3]}))

        def custom_step(workspace: Workspace) -> str:
            workspace.transform("df.with_columns(pl.col('x') * 2)", description="double x")
            return "Doubled x"

        agent = DataAgent(workspace=ws, validate_between_steps=False)
        result = agent.run_steps([custom_step])

        assert result.success is True
        assert ws.df["x"].to_list() == [2, 4, 6]

    def test_result_captures_row_counts(self):
        """Step results capture before/after row counts."""
        ws = Workspace()
        ws.load_df(pl.DataFrame({"a": [1, 1, 2, 3], "b": ["x", "x", "y", "z"]}))

        agent = DataAgent(workspace=ws, validate_between_steps=False)
        result = agent.run_steps(["remove_duplicates"])

        step = result.steps[0]
        assert step.rows_before == 4
        assert step.rows_after == 3

    def test_result_to_dict(self):
        """AgentResult serializes to dict."""
        ws = Workspace()
        ws.load_df(pl.DataFrame({"x": [1, 2, 3]}))

        agent = DataAgent(workspace=ws, validate_between_steps=False)
        result = agent.run_steps(["validate"])

        d = result.to_dict()
        assert "success" in d
        assert "steps" in d
        assert "total_duration_s" in d
        assert d["n_steps"] == 1


# =============================================================================
# DataAgent — Validation & Rollback
# =============================================================================


class TestDataAgentValidation:
    """Tests for validation-between-steps and rollback."""

    def test_rollback_on_quality_degradation(self):
        """Step is rolled back when quality degrades."""
        # Start with clean data
        ws = Workspace()
        ws.load_df(pl.DataFrame({"x": [1, 2, 3], "y": ["a", "b", "c"]}))

        # Define a step that introduces nulls (degrades quality)
        def introduce_nulls(workspace: Workspace) -> str:
            workspace.transform(
                "df.with_columns(pl.when(pl.col('x') == 2).then(None).otherwise(pl.col('y')).alias('y'))",
                description="Introduce null",
            )
            return "Introduced null"

        agent = DataAgent(workspace=ws, validate_between_steps=True, rollback_on_failure=True)
        result = agent.run_steps([introduce_nulls])

        # Should be rolled back
        assert result.steps[0].status == StepStatus.ROLLED_BACK
        # Data should still be clean
        assert ws.df["y"].null_count() == 0

    def test_no_rollback_when_disabled(self):
        """No rollback when rollback_on_failure=False."""
        ws = Workspace()
        ws.load_df(pl.DataFrame({"x": [1, 2, 3], "y": ["a", "b", "c"]}))

        def introduce_nulls(workspace: Workspace) -> str:
            workspace.transform(
                "df.with_columns(pl.when(pl.col('x') == 2).then(None).otherwise(pl.col('y')).alias('y'))",
                description="Introduce null",
            )
            return "Introduced null"

        agent = DataAgent(workspace=ws, validate_between_steps=True, rollback_on_failure=False)
        result = agent.run_steps([introduce_nulls])

        # Should pass (no rollback)
        assert result.steps[0].status == StepStatus.PASSED
        # Data has the null
        assert ws.df["y"].null_count() == 1

    def test_no_validation_when_disabled(self):
        """No quality check when validate_between_steps=False."""
        ws = Workspace()
        ws.load_df(pl.DataFrame({"x": [1, 2, 3], "y": ["a", "b", "c"]}))

        def introduce_nulls(workspace: Workspace) -> str:
            workspace.transform(
                "df.with_columns(pl.when(pl.col('x') == 2).then(None).otherwise(pl.col('y')).alias('y'))",
                description="Introduce null",
            )
            return "Introduced null"

        agent = DataAgent(workspace=ws, validate_between_steps=False)
        result = agent.run_steps([introduce_nulls])

        assert result.steps[0].status == StepStatus.PASSED
        assert ws.df["y"].null_count() == 1

    def test_error_in_step_triggers_rollback(self):
        """Exception in a step triggers rollback when undo is available."""
        ws = Workspace()
        ws.load_df(pl.DataFrame({"x": [1, 2, 3]}))

        # First, do a valid transform so undo is available
        ws.transform("df.with_columns(pl.col('x') + 1)", description="add one")

        def bad_step(workspace: Workspace) -> str:
            # Mutate the df then raise
            workspace.transform("df.with_columns(pl.lit(99).alias('y'))", description="add y")
            raise ValueError("Intentional failure after transform")

        agent = DataAgent(workspace=ws, validate_between_steps=False, rollback_on_failure=True)
        result = agent.run_steps([bad_step])

        assert result.steps[0].status == StepStatus.ROLLED_BACK
        # y column should be gone (rolled back)
        assert "y" not in ws.df.columns


# =============================================================================
# DataAgent — Checkpoints
# =============================================================================


class TestDataAgentCheckpoints:
    """Tests for checkpoint/pause mechanism."""

    def test_checkpoint_continue(self):
        """Checkpoint with CONTINUE action proceeds normally."""
        ws = Workspace()
        ws.load_df(pl.DataFrame({"x": [1, 2, 3]}))

        def always_continue(cp: Checkpoint) -> CheckpointAction:
            return CheckpointAction.CONTINUE

        agent = DataAgent(workspace=ws, validate_between_steps=False, checkpoint_fn=always_continue)
        result = agent.run_steps(["validate"], checkpoint_before=[0])

        assert result.success is True
        assert result.n_passed == 1

    def test_checkpoint_skip(self):
        """Checkpoint with SKIP action skips the step."""
        ws = Workspace()
        ws.load_df(pl.DataFrame({"x": [1, 2, 3]}))

        def always_skip(cp: Checkpoint) -> CheckpointAction:
            return CheckpointAction.SKIP

        agent = DataAgent(workspace=ws, validate_between_steps=False, checkpoint_fn=always_skip)
        result = agent.run_steps(["remove_duplicates", "validate"], checkpoint_before=[0])

        # First step skipped, second runs
        assert result.steps[0].status == StepStatus.SKIPPED
        assert result.steps[1].status == StepStatus.PASSED

    def test_checkpoint_abort(self):
        """Checkpoint with ABORT stops execution."""
        ws = Workspace()
        ws.load_df(pl.DataFrame({"x": [1, 2, 3]}))

        def always_abort(cp: Checkpoint) -> CheckpointAction:
            return CheckpointAction.ABORT

        agent = DataAgent(workspace=ws, validate_between_steps=False, checkpoint_fn=always_abort)
        result = agent.run_steps(["remove_duplicates", "validate"], checkpoint_before=[0])

        # Only 0 steps executed (aborted before first step)
        assert result.paused_at_step == 0
        assert len(result.steps) == 0

    def test_checkpoint_receives_info(self):
        """Checkpoint callback receives step info."""
        ws = Workspace()
        ws.load_df(pl.DataFrame({"x": [1, 2, 3]}))

        received = []

        def capture(cp: Checkpoint) -> CheckpointAction:
            received.append(cp)
            return CheckpointAction.CONTINUE

        agent = DataAgent(workspace=ws, validate_between_steps=False, checkpoint_fn=capture)
        agent.run_steps(["validate", "generate_report"], checkpoint_before=[1])

        assert len(received) == 1
        assert received[0].step_index == 1
        assert received[0].step_name == "generate_report"


# =============================================================================
# Built-in Steps
# =============================================================================


class TestBuiltinSteps:
    """Tests for individual built-in step functions."""

    def test_detect_and_cast_types(self):
        """detect_and_cast_types step casts string dates."""
        ws = Workspace()
        ws.load_df(pl.DataFrame({"date": ["2024-01-01", "2024-02-01"]}))

        msg = BUILTIN_STEPS["detect_and_cast_types"](ws)
        assert "Cast" in msg or "No type casts" in msg

    def test_remove_duplicates(self):
        """remove_duplicates removes exact duplicate rows."""
        ws = Workspace()
        ws.load_df(pl.DataFrame({"a": [1, 1, 2], "b": ["x", "x", "y"]}))

        msg = BUILTIN_STEPS["remove_duplicates"](ws)
        assert "1 duplicate" in msg
        assert ws.df.height == 2

    def test_trim_whitespace(self):
        """trim_whitespace strips string columns."""
        ws = Workspace()
        ws.load_df(pl.DataFrame({"name": ["  Alice  ", "  Bob  "]}))

        msg = BUILTIN_STEPS["trim_whitespace"](ws)
        assert "1 column" in msg
        assert ws.df["name"].to_list() == ["Alice", "Bob"]

    def test_drop_all_null_columns(self):
        """drop_all_null_columns removes columns that are entirely null."""
        ws = Workspace()
        ws.load_df(pl.DataFrame({"good": [1, 2, 3], "bad": [None, None, None]}))

        msg = BUILTIN_STEPS["drop_all_null_columns"](ws)
        assert "bad" in msg
        assert ws.df.columns == ["good"]

    def test_drop_all_null_rows(self):
        """drop_all_null_rows removes rows where every value is null."""
        ws = Workspace()
        ws.load_df(
            pl.DataFrame(
                {"a": [1, None, 3], "b": ["x", None, "z"]},
            )
        )

        msg = BUILTIN_STEPS["drop_all_null_rows"](ws)
        assert "1 all-null" in msg
        assert ws.df.height == 2

    def test_detect_outliers_step(self):
        """detect_outliers step reports outlier info."""
        ws = Workspace()
        ws.load_df(pl.DataFrame({"x": [1.0, 2.0, 3.0, 2.5, 100.0]}))

        msg = BUILTIN_STEPS["detect_outliers"](ws)
        assert "outlier" in msg.lower()

    def test_validate_step(self):
        """validate step reports check results."""
        ws = Workspace()
        ws.load_df(pl.DataFrame({"x": [1, 2, 3]}))

        msg = BUILTIN_STEPS["validate"](ws)
        assert "passed" in msg

    def test_generate_report(self):
        """generate_report returns a description."""
        ws = Workspace()
        ws.load_df(pl.DataFrame({"x": [1, 2, 3]}))

        msg = BUILTIN_STEPS["generate_report"](ws)
        assert "3 rows" in msg


# =============================================================================
# Recipes
# =============================================================================


class TestRecipes:
    """Tests for Recipe loading and registry."""

    def test_recipe_from_yaml(self, tmp_path):
        """Load recipe from YAML file."""
        yaml_content = """
name: Test Recipe
description: A test
steps:
  - trim_whitespace
  - validate
parameters:
  - name: threshold
    default: 0.5
tags:
  - test
"""
        yaml_file = tmp_path / "test.yaml"
        yaml_file.write_text(yaml_content)

        recipe = Recipe.from_yaml(yaml_file)

        assert recipe.name == "Test Recipe"
        assert recipe.steps == ["trim_whitespace", "validate"]
        assert recipe.default_params == {"threshold": 0.5}
        assert recipe.tags == ["test"]

    def test_recipe_from_dict(self):
        """Create recipe from dict."""
        recipe = Recipe.from_dict(
            {
                "name": "My Recipe",
                "description": "Testing",
                "steps": ["validate"],
                "parameters": [{"name": "x", "default": 10}],
            }
        )

        assert recipe.name == "My Recipe"
        assert recipe.steps == ["validate"]
        assert recipe.default_params == {"x": 10}

    def test_recipe_to_dict(self):
        """Serialize recipe to dict."""
        recipe = Recipe(
            name="Test",
            description="desc",
            steps=["a", "b"],
            default_params={"k": "v"},
            tags=["t"],
        )

        d = recipe.to_dict()
        assert d["name"] == "Test"
        assert d["steps"] == ["a", "b"]
        assert d["tags"] == ["t"]

    def test_recipe_file_not_found(self):
        """from_yaml raises FileNotFoundError."""
        with pytest.raises(FileNotFoundError):
            Recipe.from_yaml("/nonexistent/recipe.yaml")

    def test_registry_has_builtins(self):
        """Registry includes built-in recipes."""
        registry = RecipeRegistry()
        recipes = registry.list()

        keys = [r["key"] for r in recipes]
        assert "clean-csv" in keys
        assert "quality-check" in keys
        assert "prepare-export" in keys

    def test_registry_get(self):
        """Registry.get returns recipe by key."""
        registry = RecipeRegistry()

        recipe = registry.get("clean-csv")
        assert recipe is not None
        assert recipe.name == "Standard CSV Cleaning"

    def test_registry_get_unknown(self):
        """Registry.get returns None for unknown key."""
        registry = RecipeRegistry()
        assert registry.get("nonexistent") is None

    def test_registry_loads_from_directory(self, tmp_path):
        """Registry loads .yaml files from a directory."""
        yaml_content = """
name: Dir Recipe
description: Loaded from dir
steps:
  - validate
"""
        (tmp_path / "my-recipe.yaml").write_text(yaml_content)

        registry = RecipeRegistry(recipe_dir=tmp_path)
        recipe = registry.get("my-recipe")

        assert recipe is not None
        assert recipe.name == "Dir Recipe"

    def test_registry_register(self):
        """Register custom recipe."""
        registry = RecipeRegistry()
        recipe = Recipe(name="Custom", steps=["validate"])
        registry.register("custom", recipe)

        assert registry.get("custom") is not None


# =============================================================================
# Agent + Recipe Integration
# =============================================================================


class TestAgentRecipeIntegration:
    """Integration tests for agent running recipes."""

    def test_run_clean_csv_recipe(self):
        """Run the clean-csv recipe on messy data."""
        ws = Workspace()
        ws.load_df(
            pl.DataFrame(
                {
                    "name": ["  Alice  ", "Bob", "Bob"],
                    "score": ["85", "92", "92"],
                }
            )
        )

        agent = DataAgent(workspace=ws)
        registry = RecipeRegistry()
        recipe = registry.get("clean-csv")

        result = agent.run_recipe(recipe)

        assert result.n_passed >= 3
        # Should have cast score and trimmed names
        assert ws.df["score"].dtype == pl.Int64

    def test_run_quality_check_recipe(self):
        """Run quality-check recipe."""
        ws = Workspace()
        ws.load_df(
            pl.DataFrame(
                {
                    "val": [1.0, 2.0, 3.0, 4.0, 100.0],
                    "date": ["2024-01-01", "2024-02-01", "2024-03-01", "2024-04-01", "2024-05-01"],
                }
            )
        )

        agent = DataAgent(workspace=ws)
        registry = RecipeRegistry()
        recipe = registry.get("quality-check")

        result = agent.run_recipe(recipe)

        assert len(result.steps) == 4  # 4 steps in quality-check

    def test_run_custom_yaml_recipe(self, tmp_path):
        """Run a custom YAML recipe."""
        yaml_content = """
name: Mini Clean
description: Minimal cleaning
steps:
  - trim_whitespace
  - validate
"""
        yaml_file = tmp_path / "mini.yaml"
        yaml_file.write_text(yaml_content)

        ws = Workspace()
        ws.load_df(pl.DataFrame({"x": ["  hi  ", "  bye  "]}))

        recipe = Recipe.from_yaml(yaml_file)
        agent = DataAgent(workspace=ws, validate_between_steps=False)
        result = agent.run_recipe(recipe)

        assert result.success is True
        assert ws.df["x"].to_list() == ["hi", "bye"]

    def test_agent_register_custom_step_in_recipe(self):
        """Custom registered step works in a run."""
        ws = Workspace()
        ws.load_df(pl.DataFrame({"x": [1, 2, 3]}))

        def double_x(workspace: Workspace) -> str:
            workspace.transform("df.with_columns(pl.col('x') * 2)", description="double")
            return "Doubled"

        agent = DataAgent(workspace=ws, validate_between_steps=False)
        agent.register_step("double_x", double_x)
        result = agent.run_steps(["double_x", "validate"])

        assert result.success is True
        assert ws.df["x"].to_list() == [2, 4, 6]
