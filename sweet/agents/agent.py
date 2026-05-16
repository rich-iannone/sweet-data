"""DataAgent: The core agent runtime for Sweet.

The DataAgent executes multi-step data tasks against a Workspace, with:
- Validation after each step (did data quality improve or degrade?)
- Automatic rollback when a step degrades quality
- Progress reporting and result summaries
- Checkpoint/pause support for human-in-the-loop decisions
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import TYPE_CHECKING, Any, Callable

import polars as pl

if TYPE_CHECKING:
    from .recipes import Recipe

from ..core.workspace import Workspace


class StepStatus(str, Enum):
    """Status of an individual step execution."""

    PENDING = "pending"
    RUNNING = "running"
    PASSED = "passed"
    FAILED = "failed"
    ROLLED_BACK = "rolled_back"
    SKIPPED = "skipped"
    PAUSED = "paused"


@dataclass
class StepResult:
    """Result of executing a single agent step.

    Attributes:
        step_name: Name/description of the step.
        status: Execution status.
        duration_s: Execution time in seconds.
        rows_before: Row count before the step.
        rows_after: Row count after the step.
        cols_before: Column count before.
        cols_after: Column count after.
        quality_before: Fraction of all-passed validation steps before.
        quality_after: Fraction of all-passed validation steps after.
        message: Human-readable summary of what happened.
        error: Error message if failed.
        metadata: Additional step-specific data.
    """

    step_name: str
    status: StepStatus = StepStatus.PENDING
    duration_s: float = 0.0
    rows_before: int = 0
    rows_after: int = 0
    cols_before: int = 0
    cols_after: int = 0
    quality_before: float | None = None
    quality_after: float | None = None
    message: str = ""
    error: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class AgentResult:
    """Result of a full agent run.

    Attributes:
        steps: List of step results in execution order.
        total_duration_s: Total execution time.
        started_at: When the run started.
        completed_at: When the run finished.
        success: Whether all steps passed.
        summary: Human-readable summary.
        paused_at_step: If the agent paused, which step index.
    """

    steps: list[StepResult] = field(default_factory=list)
    total_duration_s: float = 0.0
    started_at: datetime | None = None
    completed_at: datetime | None = None
    success: bool = False
    summary: str = ""
    paused_at_step: int | None = None

    @property
    def n_passed(self) -> int:
        return sum(1 for s in self.steps if s.status == StepStatus.PASSED)

    @property
    def n_failed(self) -> int:
        return sum(1 for s in self.steps if s.status == StepStatus.FAILED)

    @property
    def n_rolled_back(self) -> int:
        return sum(1 for s in self.steps if s.status == StepStatus.ROLLED_BACK)

    def to_dict(self) -> dict[str, Any]:
        """Serialize to dictionary for JSON output."""
        return {
            "success": self.success,
            "total_duration_s": round(self.total_duration_s, 3),
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
            "summary": self.summary,
            "n_steps": len(self.steps),
            "n_passed": self.n_passed,
            "n_failed": self.n_failed,
            "n_rolled_back": self.n_rolled_back,
            "paused_at_step": self.paused_at_step,
            "steps": [
                {
                    "step_name": s.step_name,
                    "status": s.status.value,
                    "duration_s": round(s.duration_s, 3),
                    "rows_before": s.rows_before,
                    "rows_after": s.rows_after,
                    "message": s.message,
                    "error": s.error,
                }
                for s in self.steps
            ],
        }


# Type for step functions: take workspace, return description of what was done
StepFn = Callable[[Workspace], str]


# Built-in step implementations
def _step_detect_and_cast_types(ws: Workspace) -> str:
    """Apply high-confidence type casts."""
    suggestions = ws.suggest_casts()
    if not suggestions:
        return "No type casts needed — all columns already have appropriate types."
    ws.apply_casts()
    cols = [s["column"] for s in suggestions if s["confidence"] >= 0.9]
    return f"Cast {len(cols)} column(s): {', '.join(cols)}" if cols else "No high-confidence casts."


def _step_remove_duplicates(ws: Workspace) -> str:
    """Remove fully duplicate rows."""
    if ws.df is None:
        return "No data."
    before = ws.df.height
    ws.transform("df.unique()", description="Remove duplicate rows")
    after = ws.df.height
    removed = before - after
    return f"Removed {removed} duplicate row(s)." if removed else "No duplicates found."


def _step_standardize_nulls(ws: Workspace) -> str:
    """Convert common null-like strings to actual nulls."""
    if ws.df is None:
        return "No data."

    null_values = ['""', "N/A", "n/a", "NA", "null", "NULL", "None", "none", "-", ""]
    str_cols = [col for col in ws.df.columns if ws.df[col].dtype in (pl.Utf8, pl.String)]

    if not str_cols:
        return "No string columns to standardize."

    # Build expression to replace null-like values
    exprs = []
    for col in str_cols:
        exprs.append(
            f"pl.when(pl.col('{col}').is_in({null_values}))"
            f".then(None).otherwise(pl.col('{col}')).alias('{col}')"
        )

    if exprs:
        ws.transform(
            f"df.with_columns([{', '.join(exprs)}])",
            description="Standardize null-like values to None",
        )

    return f"Standardized null values across {len(str_cols)} string column(s)."


def _step_trim_whitespace(ws: Workspace) -> str:
    """Trim leading/trailing whitespace from string columns."""
    if ws.df is None:
        return "No data."

    str_cols = [col for col in ws.df.columns if ws.df[col].dtype in (pl.Utf8, pl.String)]
    if not str_cols:
        return "No string columns to trim."

    exprs = ", ".join(f"pl.col('{col}').str.strip_chars()" for col in str_cols)
    ws.transform(
        f"df.with_columns([{exprs}])",
        description="Trim whitespace from string columns",
    )

    return f"Trimmed whitespace in {len(str_cols)} column(s)."


def _step_drop_all_null_columns(ws: Workspace) -> str:
    """Drop columns that are entirely null."""
    if ws.df is None:
        return "No data."

    all_null_cols = [col for col in ws.df.columns if ws.df[col].null_count() == ws.df.height]
    if not all_null_cols:
        return "No all-null columns found."

    keep_cols = [col for col in ws.df.columns if col not in all_null_cols]
    cols_expr = ", ".join(f'"{c}"' for c in keep_cols)
    ws.transform(
        f"df.select([{cols_expr}])",
        description=f"Drop all-null columns: {', '.join(all_null_cols)}",
    )

    return f"Dropped {len(all_null_cols)} all-null column(s): {', '.join(all_null_cols)}"


def _step_drop_all_null_rows(ws: Workspace) -> str:
    """Drop rows where every column is null."""
    if ws.df is None:
        return "No data."

    before = ws.df.height
    # A row is all-null if every column is null
    n_cols = ws.df.width
    condition = " & ".join(f"pl.col('{col}').is_null()" for col in ws.df.columns)
    ws.transform(
        f"df.filter(~({condition}))",
        description="Drop rows where all values are null",
    )
    after = ws.df.height
    removed = before - after

    return f"Dropped {removed} all-null row(s)." if removed else "No all-null rows found."


def _step_detect_outliers(ws: Workspace) -> str:
    """Detect and report outliers (does not remove them)."""
    result = ws.detect_outliers()
    cols_with_outliers = [c for c in result["columns"] if c["n_outliers"] > 0]
    if not cols_with_outliers:
        return "No outliers detected."

    parts = []
    for col_info in cols_with_outliers:
        parts.append(f"{col_info['column']} ({col_info['n_outliers']} outliers)")
    return f"Outliers detected in: {'; '.join(parts)}"


def _step_validate(ws: Workspace) -> str:
    """Run default validation and report results."""
    result = ws.validate()
    if result["all_passed"]:
        return f"All {result['n_steps']} validation checks passed."
    failed = [s for s in result["steps"] if not s["all_passed"]]
    return f"{len(failed)}/{result['n_steps']} validation steps have failures."


def _step_generate_report(ws: Workspace) -> str:
    """Generate a text description of the current data state."""
    return ws.describe()


# Registry of built-in step functions
BUILTIN_STEPS: dict[str, StepFn] = {
    "detect_and_cast_types": _step_detect_and_cast_types,
    "remove_duplicates": _step_remove_duplicates,
    "remove_full_duplicates": _step_remove_duplicates,
    "standardize_nulls": _step_standardize_nulls,
    "trim_whitespace": _step_trim_whitespace,
    "drop_all_null_columns": _step_drop_all_null_columns,
    "drop_all_null_rows": _step_drop_all_null_rows,
    "detect_outliers": _step_detect_outliers,
    "validate": _step_validate,
    "generate_report": _step_generate_report,
}


class CheckpointAction(str, Enum):
    """Actions a human can take at a checkpoint."""

    CONTINUE = "continue"
    SKIP = "skip"
    ABORT = "abort"
    ROLLBACK = "rollback"


@dataclass
class Checkpoint:
    """A point where the agent pauses for human input.

    Attributes:
        step_index: Which step triggered the checkpoint.
        step_name: Name of the paused step.
        message: Description of what needs human attention.
        options: Available actions the human can take.
        data: Additional context (e.g., preview of changes).
    """

    step_index: int
    step_name: str
    message: str
    options: list[CheckpointAction] = field(
        default_factory=lambda: [
            CheckpointAction.CONTINUE,
            CheckpointAction.SKIP,
            CheckpointAction.ABORT,
        ]
    )
    data: dict[str, Any] = field(default_factory=dict)


class DataAgent:
    """Agent runtime for executing multi-step data workflows.

    The DataAgent orchestrates a sequence of steps against a Workspace,
    with validation between steps to ensure quality is maintained or improved.

    Args:
        workspace: The Workspace to operate on.
        validate_between_steps: If True, run validation after each step and
            auto-rollback if quality degrades. Default True.
        rollback_on_failure: If True, automatically undo a step that fails
            or degrades quality. Default True.
        checkpoint_fn: Optional callback invoked at checkpoints. Receives a
            Checkpoint and returns a CheckpointAction. If None, checkpoints
            default to CONTINUE.

    Example:
        >>> from sweet import Workspace
        >>> from sweet.agents import DataAgent
        >>> ws = Workspace()
        >>> ws.load("data.csv")
        >>> agent = DataAgent(workspace=ws)
        >>> result = agent.run_steps([
        ...     "detect_and_cast_types",
        ...     "remove_duplicates",
        ...     "standardize_nulls",
        ...     "validate",
        ... ])
    """

    def __init__(
        self,
        workspace: Workspace,
        *,
        validate_between_steps: bool = True,
        rollback_on_failure: bool = True,
        checkpoint_fn: Callable[[Checkpoint], CheckpointAction] | None = None,
    ) -> None:
        self.workspace = workspace
        self.validate_between_steps = validate_between_steps
        self.rollback_on_failure = rollback_on_failure
        self._checkpoint_fn = checkpoint_fn
        self._custom_steps: dict[str, StepFn] = {}

    def register_step(self, name: str, fn: StepFn) -> None:
        """Register a custom step function.

        Args:
            name: Step name (used in step lists and recipes).
            fn: Callable that takes a Workspace and returns a message string.
        """
        self._custom_steps[name] = fn

    def _resolve_step(self, name: str) -> StepFn | None:
        """Look up a step function by name."""
        return self._custom_steps.get(name) or BUILTIN_STEPS.get(name)

    def _measure_quality(self) -> float | None:
        """Measure current data quality as fraction of passing checks."""
        if self.workspace.df is None:
            return None
        try:
            result = self.workspace.validate()
            if result["n_steps"] == 0:
                return 1.0
            passed = sum(1 for s in result["steps"] if s["all_passed"])
            return passed / result["n_steps"]
        except Exception:
            return None

    def run_steps(
        self,
        steps: list[str | StepFn],
        *,
        checkpoint_before: list[int] | None = None,
    ) -> AgentResult:
        """Execute a sequence of named steps.

        Args:
            steps: List of step names (strings referencing built-in or registered
                steps) or callable step functions.
            checkpoint_before: List of step indices (0-based) where the agent
                should pause for human input before executing.

        Returns:
            AgentResult with details of each step's execution.
        """
        import time

        checkpoint_set = set(checkpoint_before or [])

        result = AgentResult(started_at=datetime.now(timezone.utc))
        run_start = time.perf_counter()

        quality_before = self._measure_quality() if self.validate_between_steps else None

        for i, step_spec in enumerate(steps):
            # Resolve step function
            if callable(step_spec):
                step_fn = step_spec
                step_name = getattr(step_fn, "__name__", f"step_{i}")
            else:
                step_name = step_spec
                step_fn = self._resolve_step(step_name)
                if step_fn is None:
                    step_result = StepResult(
                        step_name=step_name,
                        status=StepStatus.FAILED,
                        error=f"Unknown step: '{step_name}'",
                    )
                    result.steps.append(step_result)
                    if self.rollback_on_failure:
                        continue
                    break

            # Check for checkpoint
            if i in checkpoint_set:
                checkpoint = Checkpoint(
                    step_index=i,
                    step_name=step_name,
                    message=f"About to execute step '{step_name}'. Proceed?",
                )
                action = self._handle_checkpoint(checkpoint)

                if action == CheckpointAction.SKIP:
                    result.steps.append(
                        StepResult(step_name=step_name, status=StepStatus.SKIPPED)
                    )
                    continue
                elif action == CheckpointAction.ABORT:
                    result.paused_at_step = i
                    break
                elif action == CheckpointAction.ROLLBACK:
                    # Undo last step if possible
                    if self.workspace.can_undo:
                        self.workspace.undo()
                        if result.steps:
                            result.steps[-1].status = StepStatus.ROLLED_BACK
                    result.paused_at_step = i
                    break
                # CONTINUE → proceed

            # Capture pre-step state
            rows_before = self.workspace.df.height if self.workspace.df is not None else 0
            cols_before = self.workspace.df.width if self.workspace.df is not None else 0

            # Execute the step
            step_start = time.perf_counter()
            step_result = StepResult(
                step_name=step_name,
                status=StepStatus.RUNNING,
                rows_before=rows_before,
                cols_before=cols_before,
            )

            try:
                message = step_fn(self.workspace)
                step_result.duration_s = time.perf_counter() - step_start
                step_result.message = message
                step_result.rows_after = (
                    self.workspace.df.height if self.workspace.df is not None else 0
                )
                step_result.cols_after = (
                    self.workspace.df.width if self.workspace.df is not None else 0
                )

                # Validate quality if configured
                if self.validate_between_steps:
                    quality_after = self._measure_quality()
                    step_result.quality_before = quality_before
                    step_result.quality_after = quality_after

                    if (
                        quality_before is not None
                        and quality_after is not None
                        and quality_after < quality_before
                        and self.rollback_on_failure
                    ):
                        # Quality degraded — rollback
                        if self.workspace.can_undo:
                            self.workspace.undo()
                            step_result.status = StepStatus.ROLLED_BACK
                            step_result.message += " (rolled back: quality degraded)"
                        else:
                            step_result.status = StepStatus.PASSED
                    else:
                        step_result.status = StepStatus.PASSED
                        quality_before = quality_after
                else:
                    step_result.status = StepStatus.PASSED

            except Exception as e:
                step_result.duration_s = time.perf_counter() - step_start
                step_result.status = StepStatus.FAILED
                step_result.error = f"{type(e).__name__}: {e}"
                step_result.rows_after = (
                    self.workspace.df.height if self.workspace.df is not None else 0
                )
                step_result.cols_after = (
                    self.workspace.df.width if self.workspace.df is not None else 0
                )

                if self.rollback_on_failure and self.workspace.can_undo:
                    self.workspace.undo()
                    step_result.status = StepStatus.ROLLED_BACK
                    step_result.message = f"Failed and rolled back: {e}"

            result.steps.append(step_result)

        # Finalize result
        result.total_duration_s = time.perf_counter() - run_start
        result.completed_at = datetime.now(timezone.utc)
        result.success = all(
            s.status in (StepStatus.PASSED, StepStatus.SKIPPED) for s in result.steps
        )

        # Build summary
        parts = []
        if result.success:
            parts.append(f"✓ All {len(result.steps)} steps completed successfully.")
        else:
            parts.append(
                f"Completed with issues: {result.n_passed} passed, "
                f"{result.n_failed} failed, {result.n_rolled_back} rolled back."
            )

        if self.workspace.df is not None:
            parts.append(
                f"Final data: {self.workspace.df.height:,} rows × "
                f"{self.workspace.df.width} columns."
            )

        result.summary = " ".join(parts)

        return result

    def run_recipe(self, recipe: "Recipe", **params: Any) -> AgentResult:
        """Execute a Recipe.

        Args:
            recipe: A Recipe instance to execute.
            **params: Parameter overrides for the recipe.

        Returns:
            AgentResult with details of each step's execution.
        """
        # Merge recipe defaults with overrides
        resolved_params = {**recipe.default_params, **params}

        # Set any parameters that affect step behavior
        # (Currently params are informational — step functions read from workspace)
        _ = resolved_params

        return self.run_steps(
            recipe.steps,
            checkpoint_before=recipe.checkpoints,
        )

    def _handle_checkpoint(self, checkpoint: Checkpoint) -> CheckpointAction:
        """Handle a checkpoint pause point."""
        if self._checkpoint_fn is not None:
            return self._checkpoint_fn(checkpoint)
        # Default: continue without pausing
        return CheckpointAction.CONTINUE
