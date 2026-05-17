"""Tests for the recipes module (Phase 3.2)."""

from __future__ import annotations

from pathlib import Path

import polars as pl
import pytest

from sweet.core.recipes import (
    BUILTIN_RECIPES,
    Parameter,
    Recipe,
    Step,
    execute_recipe,
    list_recipes,
    load_recipe,
    parse_recipe,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def sample_df() -> pl.DataFrame:
    return pl.DataFrame(
        {
            "name": ["  Alice  ", "Bob", "Charlie", "  Alice  ", "N/A"],
            "age": [30, 25, 35, 30, None],
            "city": ["NYC", "LA", "NYC", "NYC", "null"],
        }
    )


@pytest.fixture
def numeric_df() -> pl.DataFrame:
    return pl.DataFrame(
        {
            "x": [1, 2, 3, 4, 5, 6, 7, 8, 9, 10],
            "y": [10, 20, 30, 40, 50, 60, 70, 80, 90, 100],
        }
    )


# ---------------------------------------------------------------------------
# parse_recipe
# ---------------------------------------------------------------------------


class TestParseRecipe:
    def test_basic_parsing(self):
        raw = {
            "name": "test-recipe",
            "description": "A test recipe",
            "steps": [
                {"action": "trim_whitespace"},
                {"action": "deduplicate", "description": "Remove dupes"},
            ],
            "tags": ["test"],
        }
        recipe = parse_recipe(raw)
        assert recipe.name == "test-recipe"
        assert recipe.description == "A test recipe"
        assert len(recipe.steps) == 2
        assert recipe.steps[0].action == "trim_whitespace"
        assert recipe.steps[1].action == "deduplicate"
        assert recipe.steps[1].description == "Remove dupes"
        assert recipe.tags == ["test"]

    def test_parameters_parsing(self):
        raw = {
            "name": "param-recipe",
            "steps": [],
            "parameters": [
                {"name": "limit", "description": "Row limit", "default": 100},
                {"name": "col", "required": True},
            ],
        }
        recipe = parse_recipe(raw)
        assert len(recipe.parameters) == 2
        assert recipe.parameters[0].name == "limit"
        assert recipe.parameters[0].default == 100
        assert recipe.parameters[1].required is True

    def test_string_step_shortform(self):
        raw = {"name": "short", "steps": ["trim_whitespace", "deduplicate"]}
        recipe = parse_recipe(raw)
        assert len(recipe.steps) == 2
        assert recipe.steps[0].action == "trim_whitespace"
        assert recipe.steps[1].action == "deduplicate"

    def test_step_args(self):
        raw = {
            "name": "with-args",
            "steps": [{"action": "head", "n": 5}],
        }
        recipe = parse_recipe(raw)
        assert recipe.steps[0].args == {"n": 5}

    def test_empty_recipe(self):
        raw = {}
        recipe = parse_recipe(raw)
        assert recipe.name == "unnamed"
        assert recipe.steps == []
        assert recipe.parameters == []

    def test_to_dict(self):
        recipe = Recipe(
            name="test",
            description="desc",
            steps=[Step(action="head", args={"n": 5})],
            parameters=[Parameter(name="x", description="param x", default=10)],
            tags=["t"],
        )
        d = recipe.to_dict()
        assert d["name"] == "test"
        assert d["steps"][0]["action"] == "head"
        assert d["steps"][0]["n"] == 5
        assert d["parameters"][0]["name"] == "x"


# ---------------------------------------------------------------------------
# load_recipe (YAML file)
# ---------------------------------------------------------------------------


class TestLoadRecipe:
    def test_load_yaml(self, tmp_path: Path):
        yaml_content = """name: file-recipe
description: Loaded from file
steps:
  - action: trim_whitespace
  - action: deduplicate
tags:
  - file
"""
        recipe_file = tmp_path / "recipe.yaml"
        recipe_file.write_text(yaml_content)
        recipe = load_recipe(recipe_file)
        assert recipe.name == "file-recipe"
        assert len(recipe.steps) == 2
        assert recipe.tags == ["file"]

    def test_load_with_params(self, tmp_path: Path):
        yaml_content = """name: param-file
steps:
  - action: head
    n: "{{limit}}"
parameters:
  - name: limit
    default: 50
"""
        recipe_file = tmp_path / "recipe.yaml"
        recipe_file.write_text(yaml_content)
        recipe = load_recipe(recipe_file)
        assert recipe.parameters[0].name == "limit"
        assert recipe.parameters[0].default == 50


# ---------------------------------------------------------------------------
# Action handlers
# ---------------------------------------------------------------------------


class TestActionHandlers:
    def test_trim_whitespace(self, sample_df: pl.DataFrame):
        recipe = Recipe(name="t", steps=[Step(action="trim_whitespace")])
        result_df, result = execute_recipe(sample_df, recipe)
        assert result.success
        assert result_df["name"][0] == "Alice"

    def test_deduplicate(self, sample_df: pl.DataFrame):
        recipe = Recipe(name="t", steps=[Step(action="deduplicate")])
        result_df, result = execute_recipe(sample_df, recipe)
        assert result.success
        assert len(result_df) < len(sample_df)

    def test_deduplicate_subset(self, sample_df: pl.DataFrame):
        recipe = Recipe(
            name="t", steps=[Step(action="deduplicate", args={"subset": "name"})]
        )
        result_df, result = execute_recipe(sample_df, recipe)
        assert result.success

    def test_standardize_nulls(self, sample_df: pl.DataFrame):
        recipe = Recipe(name="t", steps=[Step(action="standardize_nulls")])
        result_df, result = execute_recipe(sample_df, recipe)
        assert result.success
        # "N/A" and "null" should become actual null
        assert result_df["name"][4] is None
        assert result_df["city"][4] is None

    def test_head(self, numeric_df: pl.DataFrame):
        recipe = Recipe(name="t", steps=[Step(action="head", args={"n": 3})])
        result_df, result = execute_recipe(numeric_df, recipe)
        assert result.success
        assert len(result_df) == 3

    def test_tail(self, numeric_df: pl.DataFrame):
        recipe = Recipe(name="t", steps=[Step(action="tail", args={"n": 3})])
        result_df, result = execute_recipe(numeric_df, recipe)
        assert result.success
        assert len(result_df) == 3
        assert result_df["x"][0] == 8

    def test_sample(self, numeric_df: pl.DataFrame):
        recipe = Recipe(name="t", steps=[Step(action="sample", args={"n": 5})])
        result_df, result = execute_recipe(numeric_df, recipe)
        assert result.success
        assert len(result_df) == 5

    def test_sort(self, sample_df: pl.DataFrame):
        recipe = Recipe(name="t", steps=[Step(action="sort", args={"by": "age"})])
        result_df, result = execute_recipe(sample_df, recipe)
        assert result.success

    def test_sort_descending(self, numeric_df: pl.DataFrame):
        recipe = Recipe(
            name="t", steps=[Step(action="sort", args={"by": "x", "descending": True})]
        )
        result_df, result = execute_recipe(numeric_df, recipe)
        assert result.success
        assert result_df["x"][0] == 10

    def test_select(self, sample_df: pl.DataFrame):
        recipe = Recipe(
            name="t", steps=[Step(action="select", args={"columns": "name,age"})]
        )
        result_df, result = execute_recipe(sample_df, recipe)
        assert result.success
        assert result_df.columns == ["name", "age"]

    def test_select_list(self, sample_df: pl.DataFrame):
        recipe = Recipe(
            name="t",
            steps=[Step(action="select", args={"columns": ["name", "city"]})],
        )
        result_df, result = execute_recipe(sample_df, recipe)
        assert result.success
        assert result_df.columns == ["name", "city"]

    def test_drop(self, sample_df: pl.DataFrame):
        recipe = Recipe(
            name="t", steps=[Step(action="drop", args={"columns": "city"})]
        )
        result_df, result = execute_recipe(sample_df, recipe)
        assert result.success
        assert "city" not in result_df.columns

    def test_rename(self, sample_df: pl.DataFrame):
        recipe = Recipe(
            name="t",
            steps=[Step(action="rename", args={"mapping": {"name": "full_name"}})],
        )
        result_df, result = execute_recipe(sample_df, recipe)
        assert result.success
        assert "full_name" in result_df.columns
        assert "name" not in result_df.columns

    def test_cast(self, sample_df: pl.DataFrame):
        recipe = Recipe(
            name="t",
            steps=[Step(action="cast", args={"column": "age", "to": "float"})],
        )
        result_df, result = execute_recipe(sample_df, recipe)
        assert result.success
        assert result_df["age"].dtype == pl.Float64

    def test_cast_unknown_type(self, sample_df: pl.DataFrame):
        recipe = Recipe(
            name="t",
            steps=[Step(action="cast", args={"column": "age", "to": "unknown_type"})],
        )
        result_df, result = execute_recipe(sample_df, recipe)
        # Unknown type returns a message but doesn't error
        assert result.success
        assert "Unknown type" in result.step_results[0].message

    def test_fill_null_value(self):
        df = pl.DataFrame({"x": [1, None, 3]})
        recipe = Recipe(
            name="t",
            steps=[Step(action="fill_null", args={"column": "x", "value": 0})],
        )
        result_df, result = execute_recipe(df, recipe)
        assert result.success
        assert result_df["x"][1] == 0

    def test_fill_null_strategy_forward(self):
        df = pl.DataFrame({"x": [1, None, 3]})
        recipe = Recipe(
            name="t",
            steps=[Step(action="fill_null", args={"column": "x", "strategy": "forward"})],
        )
        result_df, result = execute_recipe(df, recipe)
        assert result.success
        assert result_df["x"][1] == 1

    def test_fill_null_strategy_mean(self):
        df = pl.DataFrame({"x": [1.0, None, 3.0]})
        recipe = Recipe(
            name="t",
            steps=[Step(action="fill_null", args={"column": "x", "strategy": "mean"})],
        )
        result_df, result = execute_recipe(df, recipe)
        assert result.success
        assert result_df["x"][1] == 2.0

    def test_filter(self, numeric_df: pl.DataFrame):
        recipe = Recipe(
            name="t",
            steps=[Step(action="filter", args={"condition": "pl.col('x') > 5"})],
        )
        result_df, result = execute_recipe(numeric_df, recipe)
        assert result.success
        assert len(result_df) == 5

    def test_transform(self, numeric_df: pl.DataFrame):
        recipe = Recipe(
            name="t",
            steps=[
                Step(
                    action="transform",
                    args={"expression": "df.with_columns((pl.col('x') * 2).alias('x2'))"},
                )
            ],
        )
        result_df, result = execute_recipe(numeric_df, recipe)
        assert result.success
        assert "x2" in result_df.columns
        assert result_df["x2"][0] == 2

    def test_validate(self, numeric_df: pl.DataFrame):
        recipe = Recipe(
            name="t",
            steps=[
                Step(
                    action="validate",
                    args={
                        "rules": [
                            {"name": "x_positive", "check": "col('x') > 0"},
                        ]
                    },
                )
            ],
        )
        result_df, result = execute_recipe(numeric_df, recipe)
        assert result.success
        # validate should not modify data
        assert result_df.equals(numeric_df)

    def test_sort_no_column(self, numeric_df: pl.DataFrame):
        recipe = Recipe(name="t", steps=[Step(action="sort", args={})])
        result_df, result = execute_recipe(numeric_df, recipe)
        assert result.success
        assert "No sort column" in result.step_results[0].message


# ---------------------------------------------------------------------------
# Parameter substitution
# ---------------------------------------------------------------------------


class TestParameterSubstitution:
    def test_param_in_head(self, numeric_df: pl.DataFrame):
        recipe = Recipe(
            name="t",
            steps=[Step(action="head", args={"n": "{{limit}}"})],
            parameters=[Parameter(name="limit", default=3)],
        )
        result_df, result = execute_recipe(numeric_df, recipe)
        assert result.success
        assert len(result_df) == 3

    def test_param_override(self, numeric_df: pl.DataFrame):
        recipe = Recipe(
            name="t",
            steps=[Step(action="head", args={"n": "{{limit}}"})],
            parameters=[Parameter(name="limit", default=3)],
        )
        result_df, result = execute_recipe(numeric_df, recipe, params={"limit": 7})
        assert result.success
        assert len(result_df) == 7

    def test_param_in_filter(self, numeric_df: pl.DataFrame):
        recipe = Recipe(
            name="t",
            steps=[Step(action="filter", args={"condition": "pl.col('x') > {{threshold}}"})],
            parameters=[Parameter(name="threshold", default=5)],
        )
        result_df, result = execute_recipe(numeric_df, recipe)
        assert result.success
        assert len(result_df) == 5

    def test_param_override_in_filter(self, numeric_df: pl.DataFrame):
        recipe = Recipe(
            name="t",
            steps=[Step(action="filter", args={"condition": "pl.col('x') > {{threshold}}"})],
            parameters=[Parameter(name="threshold", default=5)],
        )
        result_df, result = execute_recipe(numeric_df, recipe, params={"threshold": 8})
        assert result.success
        assert len(result_df) == 2


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------


class TestErrorHandling:
    def test_unknown_action_stops(self, numeric_df: pl.DataFrame):
        recipe = Recipe(
            name="t",
            steps=[
                Step(action="head", args={"n": 5}),
                Step(action="nonexistent_action"),
                Step(action="tail", args={"n": 3}),
            ],
        )
        result_df, result = execute_recipe(numeric_df, recipe)
        assert not result.success
        assert result.steps_completed == 1
        assert result.error == "Unknown action: nonexistent_action"
        # Data still reflects first successful step
        assert len(result_df) == 5

    def test_unknown_action_continue(self, numeric_df: pl.DataFrame):
        recipe = Recipe(
            name="t",
            steps=[
                Step(action="head", args={"n": 5}),
                Step(action="nonexistent_action"),
                Step(action="tail", args={"n": 3}),
            ],
        )
        result_df, result = execute_recipe(numeric_df, recipe, stop_on_error=False)
        # Should still fail overall but execute all it can
        assert not result.success
        assert result.steps_completed == 2  # head + tail
        assert len(result_df) == 3

    def test_step_exception(self, numeric_df: pl.DataFrame):
        recipe = Recipe(
            name="t",
            steps=[Step(action="select", args={"columns": "nonexistent_col"})],
        )
        result_df, result = execute_recipe(numeric_df, recipe)
        assert not result.success
        assert result.steps_completed == 0

    def test_result_to_dict(self, numeric_df: pl.DataFrame):
        recipe = Recipe(name="t", steps=[Step(action="head", args={"n": 3})])
        _, result = execute_recipe(numeric_df, recipe)
        d = result.to_dict()
        assert d["recipe_name"] == "t"
        assert d["success"] is True
        assert d["steps_completed"] == 1
        assert d["total_steps"] == 1
        assert d["step_results"][0]["step"] == 1
        assert d["step_results"][0]["action"] == "head"


# ---------------------------------------------------------------------------
# Built-in recipes
# ---------------------------------------------------------------------------


class TestBuiltinRecipes:
    def test_clean_csv(self, sample_df: pl.DataFrame):
        recipe = BUILTIN_RECIPES["clean-csv"]
        result_df, result = execute_recipe(sample_df, recipe)
        assert result.success
        assert result.steps_completed == 3
        # Should have trimmed, standardized nulls, and deduped
        assert "Alice" in result_df["name"].to_list()

    def test_quick_profile(self, numeric_df: pl.DataFrame):
        recipe = BUILTIN_RECIPES["quick-profile"]
        result_df, result = execute_recipe(numeric_df, recipe, params={"sample_size": 5})
        assert result.success
        assert len(result_df) <= 5

    def test_normalize_strings(self, sample_df: pl.DataFrame):
        recipe = BUILTIN_RECIPES["normalize-strings"]
        result_df, result = execute_recipe(sample_df, recipe)
        assert result.success
        assert result_df["name"][0] == "Alice"
        assert result_df["name"][4] is None

    def test_all_builtins_listed(self):
        assert "clean-csv" in BUILTIN_RECIPES
        assert "quick-profile" in BUILTIN_RECIPES
        assert "normalize-strings" in BUILTIN_RECIPES


# ---------------------------------------------------------------------------
# list_recipes
# ---------------------------------------------------------------------------


class TestListRecipes:
    def test_list_builtins(self):
        recipes = list_recipes(include_builtin=True)
        names = [r["name"] for r in recipes]
        assert "clean-csv" in names
        assert "normalize-strings" in names
        assert all(r["source"] == "builtin" for r in recipes)

    def test_list_from_dir(self, tmp_path: Path):
        yaml_content = """name: dir-recipe
description: From dir
steps:
  - action: head
    n: 5
tags:
  - custom
"""
        (tmp_path / "my_recipe.yaml").write_text(yaml_content)
        recipes = list_recipes(include_builtin=False, recipe_dir=tmp_path)
        assert len(recipes) == 1
        assert recipes[0]["name"] == "dir-recipe"
        assert recipes[0]["source"] == str(tmp_path / "my_recipe.yaml")

    def test_list_empty_dir(self, tmp_path: Path):
        recipes = list_recipes(include_builtin=False, recipe_dir=tmp_path)
        assert recipes == []

    def test_list_no_builtins(self):
        recipes = list_recipes(include_builtin=False)
        assert recipes == []


# ---------------------------------------------------------------------------
# Workspace integration
# ---------------------------------------------------------------------------


class TestWorkspaceIntegration:
    def test_run_builtin_recipe(self, sample_df: pl.DataFrame):
        from sweet.core.workspace import Workspace

        ws = Workspace()
        ws.load_df(sample_df, name="test")
        result = ws.run_recipe("clean-csv")
        assert result["success"] is True
        assert result["steps_completed"] == 3
        # Verify data was modified
        assert "Alice" in ws.df["name"].to_list()

    def test_run_recipe_with_params(self, numeric_df: pl.DataFrame):
        from sweet.core.workspace import Workspace

        ws = Workspace()
        ws.load_df(numeric_df, name="numbers")
        result = ws.run_recipe("quick-profile", params={"sample_size": 3})
        assert result["success"] is True
        assert len(ws.df) <= 3

    def test_run_recipe_from_file(self, tmp_path: Path, sample_df: pl.DataFrame):
        from sweet.core.workspace import Workspace

        yaml_content = """name: file-test
steps:
  - action: trim_whitespace
  - action: standardize_nulls
"""
        recipe_file = tmp_path / "recipe.yaml"
        recipe_file.write_text(yaml_content)

        ws = Workspace()
        ws.load_df(sample_df, name="test")
        result = ws.run_recipe(str(recipe_file))
        assert result["success"] is True
        assert ws.df["name"][0] == "Alice"

    def test_run_recipe_failure_does_not_modify(self, numeric_df: pl.DataFrame):
        from sweet.core.workspace import Workspace

        ws = Workspace()
        ws.load_df(numeric_df, name="test")
        original_len = len(ws.df)

        result = ws.run_recipe("clean-csv")
        # clean-csv tries standardize_nulls which won't fail but let's test
        # with a recipe that will fail
        recipe = Recipe(
            name="bad",
            steps=[Step(action="select", args={"columns": "nonexistent"})],
        )
        from sweet.core.recipes import BUILTIN_RECIPES

        BUILTIN_RECIPES["_test_bad"] = recipe
        try:
            result = ws.run_recipe("_test_bad")
            assert result["success"] is False
            # Data should not have changed
            assert len(ws.df) == original_len
        finally:
            del BUILTIN_RECIPES["_test_bad"]

    def test_list_recipes_from_workspace(self):
        from sweet.core.workspace import Workspace

        ws = Workspace()
        recipes = ws.list_recipes()
        assert len(recipes) >= 3
        names = [r["name"] for r in recipes]
        assert "clean-csv" in names
