"""Recipes — Reusable, parameterized multi-step data workflows.

A recipe is a YAML-defined sequence of steps that can be executed against
a Workspace. Steps reference built-in operations (transform, filter, sort,
deduplicate, cast, fill_null, validate, etc.) or natural-language transforms.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import polars as pl

# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------


@dataclass
class Parameter:
    """A recipe parameter with optional default."""

    name: str
    description: str = ""
    default: object = None
    required: bool = False


@dataclass
class Step:
    """A single step in a recipe."""

    action: str  # The operation to perform
    description: str = ""
    args: dict = field(default_factory=dict)


@dataclass
class Recipe:
    """A reusable data workflow definition."""

    name: str
    description: str = ""
    steps: list[Step] = field(default_factory=list)
    parameters: list[Parameter] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "steps": [{"action": s.action, "description": s.description, **s.args} for s in self.steps],
            "parameters": [
                {"name": p.name, "description": p.description, "default": p.default}
                for p in self.parameters
            ],
            "tags": self.tags,
        }


@dataclass
class StepResult:
    """Result of executing a single step."""

    step_index: int
    action: str
    success: bool
    message: str = ""
    rows_before: int = 0
    rows_after: int = 0


@dataclass
class RecipeResult:
    """Result of executing a complete recipe."""

    recipe_name: str
    success: bool
    steps_completed: int
    total_steps: int
    step_results: list[StepResult] = field(default_factory=list)
    error: str | None = None

    def to_dict(self) -> dict:
        return {
            "recipe_name": self.recipe_name,
            "success": self.success,
            "steps_completed": self.steps_completed,
            "total_steps": self.total_steps,
            "error": self.error,
            "step_results": [
                {
                    "step": r.step_index + 1,
                    "action": r.action,
                    "success": r.success,
                    "message": r.message,
                    "rows_before": r.rows_before,
                    "rows_after": r.rows_after,
                }
                for r in self.step_results
            ],
        }


# ---------------------------------------------------------------------------
# Recipe parsing
# ---------------------------------------------------------------------------


def parse_recipe(raw: dict) -> Recipe:
    """Parse a recipe from a YAML-loaded dict.

    Expected structure:
        name: str
        description: str (optional)
        steps:
          - action: str
            description: str (optional)
            ...additional args...
        parameters:
          - name: str
            description: str (optional)
            default: any (optional)
        tags: list[str] (optional)
    """
    name = raw.get("name", "unnamed")
    description = raw.get("description", "")
    tags = raw.get("tags", [])

    # Parse steps
    steps: list[Step] = []
    for step_raw in raw.get("steps", []):
        if isinstance(step_raw, str):
            # Short form: just the action name
            steps.append(Step(action=step_raw))
        elif isinstance(step_raw, dict):
            action = step_raw.get("action", "")
            desc = step_raw.get("description", "")
            args = {k: v for k, v in step_raw.items() if k not in ("action", "description")}
            steps.append(Step(action=action, description=desc, args=args))

    # Parse parameters
    parameters: list[Parameter] = []
    for param_raw in raw.get("parameters", []):
        if isinstance(param_raw, dict):
            parameters.append(
                Parameter(
                    name=param_raw.get("name", ""),
                    description=param_raw.get("description", ""),
                    default=param_raw.get("default"),
                    required=param_raw.get("required", False),
                )
            )

    return Recipe(
        name=name,
        description=description,
        steps=steps,
        parameters=parameters,
        tags=tags,
    )


def load_recipe(path: str | Path) -> Recipe:
    """Load a recipe from a YAML file."""
    from yaml12 import read_yaml

    data = read_yaml(str(path))
    return parse_recipe(data)


# ---------------------------------------------------------------------------
# Built-in step actions
# ---------------------------------------------------------------------------

# Maps action names to handler functions.
# Each handler takes (df, step_args, params) and returns (df, message).
_ACTION_HANDLERS: dict[str, object] = {}


def _action(name: str):
    """Register a step action handler."""

    def decorator(func):
        _ACTION_HANDLERS[name] = func
        return func

    return decorator


@_action("transform")
def _do_transform(df: pl.DataFrame, args: dict, params: dict) -> tuple[pl.DataFrame, str]:
    """Apply a raw Polars expression."""
    expr = _substitute_params(args.get("expression", ""), params)
    if not expr:
        return df, "No expression provided"
    local_ns: dict = {"pl": pl, "df": df}
    result = eval(expr, {"__builtins__": {}}, local_ns)  # noqa: S307
    if isinstance(result, pl.DataFrame):
        return result, f"Applied: {expr}"
    return df, f"Expression did not return DataFrame: {expr}"


@_action("filter")
def _do_filter(df: pl.DataFrame, args: dict, params: dict) -> tuple[pl.DataFrame, str]:
    """Filter rows by a condition."""
    condition = _substitute_params(args.get("condition", ""), params)
    if not condition:
        return df, "No condition provided"
    local_ns: dict = {"pl": pl, "df": df}
    result = eval(f"df.filter({condition})", {"__builtins__": {}}, local_ns)  # noqa: S307
    return result, f"Filtered: {condition}"


@_action("sort")
def _do_sort(df: pl.DataFrame, args: dict, params: dict) -> tuple[pl.DataFrame, str]:
    """Sort by column(s)."""
    by = args.get("by", "")
    descending = args.get("descending", False)
    if not by:
        return df, "No sort column specified"
    cols = [c.strip() for c in by.split(",")] if isinstance(by, str) else by
    return df.sort(cols, descending=descending), f"Sorted by {cols}"


@_action("select")
def _do_select(df: pl.DataFrame, args: dict, params: dict) -> tuple[pl.DataFrame, str]:
    """Select specific columns."""
    columns = args.get("columns", [])
    if isinstance(columns, str):
        columns = [c.strip() for c in columns.split(",")]
    if not columns:
        return df, "No columns specified"
    return df.select(columns), f"Selected {len(columns)} columns"


@_action("drop")
def _do_drop(df: pl.DataFrame, args: dict, params: dict) -> tuple[pl.DataFrame, str]:
    """Drop columns."""
    columns = args.get("columns", [])
    if isinstance(columns, str):
        columns = [c.strip() for c in columns.split(",")]
    if not columns:
        return df, "No columns specified"
    return df.drop(columns), f"Dropped {len(columns)} columns"


@_action("rename")
def _do_rename(df: pl.DataFrame, args: dict, params: dict) -> tuple[pl.DataFrame, str]:
    """Rename columns."""
    mapping = args.get("mapping", {})
    if not mapping:
        return df, "No rename mapping"
    return df.rename(mapping), f"Renamed {len(mapping)} columns"


@_action("deduplicate")
def _do_deduplicate(df: pl.DataFrame, args: dict, params: dict) -> tuple[pl.DataFrame, str]:
    """Remove duplicate rows."""
    subset = args.get("subset")
    if subset:
        if isinstance(subset, str):
            subset = [c.strip() for c in subset.split(",")]
        result = df.unique(subset=subset)
    else:
        result = df.unique()
    removed = len(df) - len(result)
    return result, f"Removed {removed} duplicate rows"


@_action("cast")
def _do_cast(df: pl.DataFrame, args: dict, params: dict) -> tuple[pl.DataFrame, str]:
    """Cast column types."""
    column = args.get("column", "")
    to_type = args.get("to", "")
    if not column or not to_type:
        return df, "Missing column or target type"

    type_map = {
        "int": pl.Int64,
        "integer": pl.Int64,
        "float": pl.Float64,
        "string": pl.Utf8,
        "str": pl.Utf8,
        "utf8": pl.Utf8,
        "date": pl.Date,
        "bool": pl.Boolean,
        "boolean": pl.Boolean,
    }
    pl_type = type_map.get(to_type.lower())
    if not pl_type:
        return df, f"Unknown type: {to_type}"
    return df.with_columns(pl.col(column).cast(pl_type)), f"Cast {column} to {to_type}"


@_action("fill_null")
def _do_fill_null(df: pl.DataFrame, args: dict, params: dict) -> tuple[pl.DataFrame, str]:
    """Fill null values."""
    column = args.get("column", "")
    value = args.get("value")
    strategy = args.get("strategy")

    if not column:
        return df, "No column specified"

    if strategy:
        if strategy == "forward":
            return df.with_columns(pl.col(column).forward_fill()), f"Forward-filled {column}"
        elif strategy == "backward":
            return df.with_columns(pl.col(column).backward_fill()), f"Backward-filled {column}"
        elif strategy == "mean":
            mean_val = df[column].mean()
            return df.with_columns(pl.col(column).fill_null(mean_val)), f"Filled {column} with mean"
        elif strategy == "zero":
            return df.with_columns(pl.col(column).fill_null(0)), f"Filled {column} with 0"

    if value is not None:
        return df.with_columns(pl.col(column).fill_null(value)), f"Filled {column} with {value}"

    return df, "No value or strategy specified"


@_action("standardize_nulls")
def _do_standardize_nulls(df: pl.DataFrame, args: dict, params: dict) -> tuple[pl.DataFrame, str]:
    """Convert common null-like strings to actual nulls."""
    null_values = args.get("values", ["", "N/A", "NA", "null", "NULL", "None", "none", "n/a"])
    count = 0
    for col_name in df.columns:
        if df[col_name].dtype == pl.Utf8:
            mask = df[col_name].is_in(null_values)
            replacements = mask.sum()
            if replacements > 0:
                count += replacements
                df = df.with_columns(
                    pl.when(pl.col(col_name).is_in(null_values))
                    .then(None)
                    .otherwise(pl.col(col_name))
                    .alias(col_name)
                )
    return df, f"Standardized {count} null-like values to null"


@_action("trim_whitespace")
def _do_trim_whitespace(df: pl.DataFrame, args: dict, params: dict) -> tuple[pl.DataFrame, str]:
    """Strip leading/trailing whitespace from string columns."""
    trimmed = 0
    for col_name in df.columns:
        if df[col_name].dtype == pl.Utf8:
            df = df.with_columns(pl.col(col_name).str.strip_chars())
            trimmed += 1
    return df, f"Trimmed whitespace in {trimmed} string columns"


@_action("head")
def _do_head(df: pl.DataFrame, args: dict, params: dict) -> tuple[pl.DataFrame, str]:
    """Keep only the first N rows."""
    n = int(args.get("n", 10))
    return df.head(n), f"Kept first {n} rows"


@_action("tail")
def _do_tail(df: pl.DataFrame, args: dict, params: dict) -> tuple[pl.DataFrame, str]:
    """Keep only the last N rows."""
    n = int(args.get("n", 10))
    return df.tail(n), f"Kept last {n} rows"


@_action("sample")
def _do_sample(df: pl.DataFrame, args: dict, params: dict) -> tuple[pl.DataFrame, str]:
    """Sample N random rows."""
    n = int(args.get("n", 10))
    n = min(n, len(df))
    return df.sample(n), f"Sampled {n} rows"


@_action("nl")
def _do_nl(df: pl.DataFrame, args: dict, params: dict) -> tuple[pl.DataFrame, str]:
    """Apply a natural language transform."""
    from .nl_translate import translate

    text = _substitute_params(args.get("text", ""), params)
    if not text:
        return df, "No text provided"
    result = translate(text)
    if result is None:
        return df, f"Could not translate: {text}"
    local_ns: dict = {"pl": pl, "df": df}
    new_df = eval(result.expression, {"__builtins__": {}}, local_ns)  # noqa: S307
    if isinstance(new_df, pl.DataFrame):
        return new_df, f"NL: {text} → {result.expression}"
    return df, f"NL expression did not return DataFrame: {result.expression}"


@_action("validate")
def _do_validate(df: pl.DataFrame, args: dict, params: dict) -> tuple[pl.DataFrame, str]:
    """Run validation rules (does not modify data, just reports)."""
    from .rules import parse_rules, validate

    rules_raw = args.get("rules", [])
    if not rules_raw:
        return df, "No rules specified"
    rules = parse_rules(rules_raw)
    result = validate(df, rules)
    if result.passed:
        return df, f"Validation passed: {result.rules_passed}/{result.rules_checked} rules OK"
    return df, (
        f"Validation: {result.rules_passed}/{result.rules_checked} passed, "
        f"{result.error_count} errors, {result.warning_count} warnings"
    )


# ---------------------------------------------------------------------------
# Recipe execution
# ---------------------------------------------------------------------------


def execute_recipe(
    df: pl.DataFrame,
    recipe: Recipe,
    params: dict | None = None,
    *,
    stop_on_error: bool = True,
) -> tuple[pl.DataFrame, RecipeResult]:
    """Execute a recipe against a DataFrame.

    Parameters
    ----------
    df
        Input DataFrame.
    recipe
        The recipe to execute.
    params
        Parameter values (overriding recipe defaults).
    stop_on_error
        If True, stop at the first failing step.

    Returns
    -------
    tuple[pl.DataFrame, RecipeResult]
        The transformed DataFrame and execution report.
    """
    # Resolve parameters: defaults + overrides
    resolved_params = {}
    for p in recipe.parameters:
        if p.default is not None:
            resolved_params[p.name] = p.default
    if params:
        resolved_params.update(params)

    step_results: list[StepResult] = []
    current_df = df
    error_msg: str | None = None

    for i, step in enumerate(recipe.steps):
        rows_before = len(current_df)
        handler = _ACTION_HANDLERS.get(step.action)

        if handler is None:
            sr = StepResult(
                step_index=i,
                action=step.action,
                success=False,
                message=f"Unknown action: {step.action}",
                rows_before=rows_before,
                rows_after=rows_before,
            )
            step_results.append(sr)
            if stop_on_error:
                error_msg = sr.message
                break
            continue

        try:
            merged_args = _substitute_all_args(step.args, resolved_params)
            new_df, message = handler(current_df, merged_args, resolved_params)
            rows_after = len(new_df)
            step_results.append(
                StepResult(
                    step_index=i,
                    action=step.action,
                    success=True,
                    message=message,
                    rows_before=rows_before,
                    rows_after=rows_after,
                )
            )
            current_df = new_df
        except Exception as e:
            sr = StepResult(
                step_index=i,
                action=step.action,
                success=False,
                message=f"{type(e).__name__}: {e}",
                rows_before=rows_before,
                rows_after=rows_before,
            )
            step_results.append(sr)
            if stop_on_error:
                error_msg = sr.message
                break

    completed = sum(1 for r in step_results if r.success)
    any_failed = any(not r.success for r in step_results)
    return current_df, RecipeResult(
        recipe_name=recipe.name,
        success=not any_failed,
        steps_completed=completed,
        total_steps=len(recipe.steps),
        step_results=step_results,
        error=error_msg,
    )


# ---------------------------------------------------------------------------
# Built-in recipes
# ---------------------------------------------------------------------------

BUILTIN_RECIPES: dict[str, Recipe] = {}


def _register_builtin(recipe: Recipe) -> None:
    BUILTIN_RECIPES[recipe.name] = recipe


_register_builtin(
    Recipe(
        name="clean-csv",
        description="Standard CSV cleaning: standardize nulls, trim whitespace, deduplicate.",
        steps=[
            Step(action="standardize_nulls", description="Convert null-like strings to null"),
            Step(action="trim_whitespace", description="Strip whitespace from strings"),
            Step(action="deduplicate", description="Remove duplicate rows"),
        ],
        tags=["cleaning", "csv"],
    )
)

_register_builtin(
    Recipe(
        name="quick-profile",
        description="Keep first 1000 rows, deduplicate, sort by first column.",
        steps=[
            Step(action="head", description="Limit to first N rows", args={"n": "{{sample_size}}"}),
            Step(action="deduplicate", description="Remove duplicates"),
        ],
        parameters=[
            Parameter(name="sample_size", description="Rows to keep", default=1000),
        ],
        tags=["profiling", "sampling"],
    )
)

_register_builtin(
    Recipe(
        name="normalize-strings",
        description="Standardize null-like values and trim whitespace in all string columns.",
        steps=[
            Step(action="standardize_nulls", description="Convert null-like strings to null"),
            Step(action="trim_whitespace", description="Strip whitespace"),
        ],
        tags=["cleaning", "strings"],
    )
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _substitute_params(text: str, params: dict) -> str:
    """Replace {{param_name}} placeholders with parameter values."""
    if not params or "{{" not in text:
        return text

    def replacer(m: re.Match) -> str:
        key = m.group(1).strip()
        return str(params.get(key, m.group(0)))

    return re.sub(r"\{\{(\w+)\}\}", replacer, text)


def _substitute_all_args(args: dict, params: dict) -> dict:
    """Apply parameter substitution to all string values in args."""
    if not params:
        return {**args}
    result = {}
    for k, v in args.items():
        if isinstance(v, str):
            substituted = _substitute_params(v, params)
            # Try to coerce back to numeric if the original was a placeholder
            if substituted != v:
                try:
                    result[k] = int(substituted)
                    continue
                except ValueError:
                    try:
                        result[k] = float(substituted)
                        continue
                    except ValueError:
                        pass
            result[k] = substituted
        else:
            result[k] = v
    return result


def list_recipes(
    include_builtin: bool = True,
    recipe_dir: str | Path | None = None,
) -> list[dict]:
    """List available recipes.

    Parameters
    ----------
    include_builtin
        Include built-in recipes.
    recipe_dir
        Optional directory to scan for user recipe YAML files.

    Returns
    -------
    list[dict]
        List of recipe summaries.
    """
    recipes: list[dict] = []

    if include_builtin:
        for r in BUILTIN_RECIPES.values():
            recipes.append({
                "name": r.name,
                "description": r.description,
                "steps": len(r.steps),
                "tags": r.tags,
                "source": "builtin",
            })

    if recipe_dir:
        recipe_path = Path(recipe_dir)
        if recipe_path.is_dir():
            for f in sorted(recipe_path.glob("*.yaml")) + sorted(recipe_path.glob("*.yml")):
                try:
                    r = load_recipe(f)
                    recipes.append({
                        "name": r.name,
                        "description": r.description,
                        "steps": len(r.steps),
                        "tags": r.tags,
                        "source": str(f),
                    })
                except Exception:
                    pass

    return recipes
