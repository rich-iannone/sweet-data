"""Specialized agents and pipeline orchestration for multi-agent workflows.

This module provides domain-specific agents that can be composed into
pipelines for complex, multi-step data tasks:

- IngestionAgent: Load, parse, and normalize data from various sources
- QualityAgent: Profile, validate, and flag data issues
- TransformAgent: Apply business logic, aggregations, joins
- ExportAgent: Format and write data to destinations
- Pipeline: Orchestrate multiple agents in sequence
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from ..core.workspace import Workspace
from .agent import AgentResult, DataAgent

if TYPE_CHECKING:
    from .memory import AgentMemory


# =============================================================================
# Specialized Agent Base
# =============================================================================


class SpecializedAgent(DataAgent):
    """Base for specialized agents with domain-specific defaults.

    Subclasses define a `domain` name and a set of default steps that
    execute when `run()` is called without explicit steps.
    """

    domain: str = "generic"
    default_steps: list[str] = []

    def run(self, **kwargs: Any) -> AgentResult:
        """Execute this agent's default steps.

        Returns:
            AgentResult from running the domain-specific steps.
        """
        return self.run_steps(self.default_steps, **kwargs)


# =============================================================================
# Ingestion Agent
# =============================================================================


def _step_infer_and_load(ws: Workspace) -> str:
    """Detect file format and load data (no-op if already loaded)."""
    if ws.df is not None:
        return f"Data already loaded: {ws.df.height} rows × {ws.df.width} columns."
    return "No data to load — use ws.load() first."


def _step_normalize_column_names(ws: Workspace) -> str:
    """Normalize column names to snake_case."""
    import re

    if ws.df is None:
        return "No data."

    columns = ws.df.columns
    renames = {}
    for col in columns:
        # Convert CamelCase / spaces / dashes to snake_case
        normalized = re.sub(r"([A-Z]+)", r"_\1", col).lower()
        normalized = re.sub(r"[\s\-\.]+", "_", normalized)
        normalized = re.sub(r"_+", "_", normalized).strip("_")
        if normalized != col:
            renames[col] = normalized

    if not renames:
        return "Column names already normalized."

    rename_exprs = ", ".join(
        f'pl.col("{old}").alias("{new}")' for old, new in renames.items()
    )
    keep_cols = [c for c in columns if c not in renames]
    keep_exprs = ", ".join(f'pl.col("{c}")' for c in keep_cols)

    all_exprs = ", ".join(filter(None, [keep_exprs, rename_exprs]))
    ws.transform(f"df.select([{all_exprs}])", description="Normalize column names to snake_case")

    return f"Renamed {len(renames)} column(s) to snake_case."


def _step_drop_unnamed_columns(ws: Workspace) -> str:
    """Drop columns with auto-generated names (Unnamed:, __index, etc.)."""
    if ws.df is None:
        return "No data."

    unnamed = [
        c
        for c in ws.df.columns
        if c.startswith("Unnamed:") or c.startswith("__") or c == "index"
    ]
    if not unnamed:
        return "No unnamed/index columns found."

    keep = [c for c in ws.df.columns if c not in unnamed]
    cols_expr = ", ".join(f'"{c}"' for c in keep)
    ws.transform(f"df.select([{cols_expr}])", description=f"Drop unnamed columns: {unnamed}")

    return f"Dropped {len(unnamed)} unnamed column(s): {', '.join(unnamed)}"


class IngestionAgent(SpecializedAgent):
    """Agent specialized for data loading, parsing, and initial normalization.

    Default steps:
    - infer_and_load: Verify data is loaded
    - detect_and_cast_types: Cast string dates, numbers, etc.
    - normalize_column_names: Convert to snake_case
    - drop_unnamed_columns: Remove auto-generated index columns
    - trim_whitespace: Strip leading/trailing spaces
    """

    domain = "ingestion"
    default_steps = [
        "infer_and_load",
        "detect_and_cast_types",
        "normalize_column_names",
        "drop_unnamed_columns",
        "trim_whitespace",
    ]

    def __init__(self, workspace: Workspace, **kwargs: Any) -> None:
        super().__init__(workspace, **kwargs)
        self.register_step("infer_and_load", _step_infer_and_load)
        self.register_step("normalize_column_names", _step_normalize_column_names)
        self.register_step("drop_unnamed_columns", _step_drop_unnamed_columns)


# =============================================================================
# Quality Agent
# =============================================================================


def _step_profile_data(ws: Workspace) -> str:
    """Generate a statistical profile summary."""
    info = ws.schema_info()
    n_rows = info["n_rows"]
    n_cols = info["n_cols"]
    columns = info["columns"]
    total_nulls = sum(c.get("null_count", 0) for c in columns)
    total_cells = n_rows * n_cols if n_rows > 0 else 0
    completeness = (1 - total_nulls / total_cells) * 100 if total_cells > 0 else 100

    n_numeric = sum(1 for c in columns if "Int" in c["dtype"] or "Float" in c["dtype"])
    n_string = sum(1 for c in columns if "Utf8" in c["dtype"] or "String" in c["dtype"])
    n_temporal = sum(1 for c in columns if "Date" in c["dtype"] or "Time" in c["dtype"])

    return (
        f"Profile: {n_rows} rows × {n_cols} columns. "
        f"Completeness: {completeness:.1f}%. "
        f"Types: {n_numeric} numeric, {n_string} string, {n_temporal} temporal."
    )


def _step_check_pii(ws: Workspace) -> str:
    """Scan for potential PII (emails, phones, SSNs, etc.)."""
    result = ws.detect_pii()
    if not result["pii_columns"]:
        return "No PII detected."

    parts = []
    for col_info in result["pii_columns"]:
        parts.append(f"{col_info['column']} ({col_info['pii_type']})")
    return f"⚠ Potential PII in {len(parts)} column(s): {'; '.join(parts)}"


def _step_check_relationships(ws: Workspace) -> str:
    """Detect potential join keys and relationships."""
    result = ws.detect_relationships()
    if not result.get("potential_keys"):
        return "No obvious key columns detected."

    keys = result["potential_keys"]
    return f"Potential key columns: {', '.join(k['column'] for k in keys[:5])}"


def _step_check_completeness(ws: Workspace) -> str:
    """Report on missing data patterns."""
    if ws.df is None:
        return "No data."

    cols_with_nulls = []
    for col in ws.df.columns:
        null_count = ws.df[col].null_count()
        if null_count > 0:
            pct = (null_count / ws.df.height) * 100
            cols_with_nulls.append(f"{col} ({pct:.1f}% null)")

    if not cols_with_nulls:
        return "Data is 100% complete — no missing values."
    return f"Missing data in {len(cols_with_nulls)} column(s): {'; '.join(cols_with_nulls[:10])}"


class QualityAgent(SpecializedAgent):
    """Agent specialized for data profiling, validation, and quality assessment.

    Default steps:
    - profile_data: Statistical overview
    - detect_outliers: Find anomalous values
    - check_completeness: Missing data patterns
    - check_pii: Scan for PII
    - validate: Run validation rules
    """

    domain = "quality"
    default_steps = [
        "profile_data",
        "detect_outliers",
        "check_completeness",
        "check_pii",
        "validate",
    ]

    def __init__(self, workspace: Workspace, **kwargs: Any) -> None:
        super().__init__(workspace, validate_between_steps=False, **kwargs)
        self.register_step("profile_data", _step_profile_data)
        self.register_step("check_pii", _step_check_pii)
        self.register_step("check_relationships", _step_check_relationships)
        self.register_step("check_completeness", _step_check_completeness)


# =============================================================================
# Transform Agent
# =============================================================================


def _step_remove_all_duplicates(ws: Workspace) -> str:
    """Remove fully duplicate rows."""
    if ws.df is None:
        return "No data."
    before = ws.df.height
    ws.transform("df.unique()", description="Remove duplicate rows")
    after = ws.df.height
    removed = before - after
    return f"Removed {removed} duplicate row(s)." if removed else "No duplicates found."


def _step_fill_nulls_with_default(ws: Workspace) -> str:
    """Fill null values with sensible defaults (0 for numeric, '' for string)."""
    if ws.df is None:
        return "No data."

    import polars as pl

    exprs = []
    filled_cols = []
    for col in ws.df.columns:
        dtype = ws.df[col].dtype
        if ws.df[col].null_count() == 0:
            continue
        if dtype in (pl.Int8, pl.Int16, pl.Int32, pl.Int64, pl.UInt8, pl.UInt16, pl.UInt32, pl.UInt64):
            exprs.append(f"pl.col('{col}').fill_null(0)")
            filled_cols.append(col)
        elif dtype in (pl.Float32, pl.Float64):
            exprs.append(f"pl.col('{col}').fill_null(0.0)")
            filled_cols.append(col)
        elif dtype in (pl.Utf8, pl.String):
            exprs.append(f"pl.col('{col}').fill_null('')")
            filled_cols.append(col)

    if not exprs:
        return "No null values to fill."

    ws.transform(
        f"df.with_columns([{', '.join(exprs)}])",
        description=f"Fill null values in {len(filled_cols)} column(s)",
    )
    return f"Filled nulls in {len(filled_cols)} column(s): {', '.join(filled_cols[:10])}"


def _step_sort_by_first_column(ws: Workspace) -> str:
    """Sort data by the first column (ascending)."""
    if ws.df is None or ws.df.width == 0:
        return "No data."

    first_col = ws.df.columns[0]
    ws.transform(f"df.sort('{first_col}')", description=f"Sort by {first_col}")
    return f"Sorted by '{first_col}' (ascending)."


class TransformAgent(SpecializedAgent):
    """Agent specialized for data transformation and business logic.

    Default steps:
    - detect_and_cast_types: Ensure correct types
    - remove_full_duplicates: Deduplicate
    - standardize_nulls: Normalize null representations
    - trim_whitespace: Clean strings
    - drop_all_null_rows: Remove empty rows
    """

    domain = "transform"
    default_steps = [
        "detect_and_cast_types",
        "remove_full_duplicates",
        "standardize_nulls",
        "trim_whitespace",
        "drop_all_null_rows",
    ]

    def __init__(self, workspace: Workspace, **kwargs: Any) -> None:
        super().__init__(workspace, **kwargs)
        self.register_step("fill_nulls", _step_fill_nulls_with_default)
        self.register_step("sort_by_first_column", _step_sort_by_first_column)
        self.register_step("remove_all_duplicates", _step_remove_all_duplicates)


# =============================================================================
# Export Agent
# =============================================================================


def _step_validate_for_export(ws: Workspace) -> str:
    """Validate data is ready for export (no all-null columns, types cast)."""
    if ws.df is None:
        return "No data to export."

    issues = []
    # Check for all-null columns
    all_null_cols = [c for c in ws.df.columns if ws.df[c].null_count() == ws.df.height]
    if all_null_cols:
        issues.append(f"{len(all_null_cols)} all-null column(s)")

    # Check for empty dataframe
    if ws.df.height == 0:
        issues.append("dataframe is empty")

    if issues:
        return f"⚠ Export concerns: {'; '.join(issues)}"
    return f"Data ready for export: {ws.df.height} rows × {ws.df.width} columns."


def _step_generate_export_report(ws: Workspace) -> str:
    """Generate a summary report of the exported data."""
    return ws.describe()


class ExportAgent(SpecializedAgent):
    """Agent specialized for preparing and exporting data.

    Default steps:
    - drop_all_null_columns: Clean up empty columns
    - validate_for_export: Pre-export validation
    - generate_report: Summary of final data
    """

    domain = "export"
    default_steps = [
        "drop_all_null_columns",
        "validate_for_export",
        "generate_report",
    ]

    def __init__(self, workspace: Workspace, **kwargs: Any) -> None:
        super().__init__(workspace, validate_between_steps=False, **kwargs)
        self.register_step("validate_for_export", _step_validate_for_export)
        self.register_step("generate_export_report", _step_generate_export_report)

    def export(self, dest: str, *, format: str | None = None) -> AgentResult:
        """Run export steps and write data to destination.

        Args:
            dest: Output file path.
            format: File format (csv, parquet, json). Auto-detected if None.

        Returns:
            AgentResult from the export workflow.
        """
        result = self.run()
        if result.success:
            self.workspace.export(dest, format=format)
        return result


# =============================================================================
# Pipeline
# =============================================================================


@dataclass
class PipelineStage:
    """A single stage in a pipeline.

    Attributes:
        name: Human-readable name for this stage.
        agent: The specialized agent to run.
        steps: Optional override of the agent's default steps.
        stop_on_failure: Whether to halt the pipeline if this stage fails.
    """

    name: str
    agent: SpecializedAgent
    steps: list[str] | None = None
    stop_on_failure: bool = True


@dataclass
class PipelineResult:
    """Result of a full pipeline execution.

    Attributes:
        stages: Results for each stage.
        total_duration_s: Total execution time.
        success: Whether all stages passed.
        summary: Human-readable summary.
        stopped_at: Stage name where pipeline halted (if it did).
    """

    stages: list[dict[str, Any]] = field(default_factory=list)
    total_duration_s: float = 0.0
    success: bool = False
    summary: str = ""
    stopped_at: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Serialize to dictionary."""
        return {
            "success": self.success,
            "total_duration_s": round(self.total_duration_s, 3),
            "n_stages": len(self.stages),
            "stopped_at": self.stopped_at,
            "summary": self.summary,
            "stages": self.stages,
        }


class Pipeline:
    """Orchestrate multiple specialized agents in sequence.

    A pipeline chains agents together, passing the workspace through
    each stage. Each agent operates on the same workspace, building
    on the previous stage's results.

    Example:
        >>> from sweet.core.workspace import Workspace
        >>> from sweet.agents.pipeline import Pipeline, IngestionAgent, QualityAgent, TransformAgent
        >>>
        >>> ws = Workspace()
        >>> ws.load("data.csv")
        >>>
        >>> pipeline = Pipeline(workspace=ws)
        >>> pipeline.add_stage("ingest", IngestionAgent(ws))
        >>> pipeline.add_stage("quality", QualityAgent(ws))
        >>> pipeline.add_stage("transform", TransformAgent(ws))
        >>>
        >>> result = pipeline.run()
    """

    def __init__(self, workspace: Workspace, *, memory: AgentMemory | None = None) -> None:
        self.workspace = workspace
        self.memory = memory
        self._stages: list[PipelineStage] = []

    def add_stage(
        self,
        name: str,
        agent: SpecializedAgent,
        *,
        steps: list[str] | None = None,
        stop_on_failure: bool = True,
    ) -> "Pipeline":
        """Add a stage to the pipeline.

        Args:
            name: Stage name.
            agent: The specialized agent for this stage.
            steps: Override the agent's default steps. If None, uses defaults.
            stop_on_failure: If True, pipeline halts when this stage fails.

        Returns:
            Self for chaining.
        """
        self._stages.append(
            PipelineStage(name=name, agent=agent, steps=steps, stop_on_failure=stop_on_failure)
        )
        return self

    @property
    def stages(self) -> list[str]:
        """List stage names."""
        return [s.name for s in self._stages]

    def run(self) -> PipelineResult:
        """Execute all stages in sequence.

        Returns:
            PipelineResult with per-stage details.
        """
        pipeline_result = PipelineResult()
        start = time.perf_counter()

        for stage in self._stages:
            # Run the agent with explicit steps or defaults
            if stage.steps:
                agent_result = stage.agent.run_steps(stage.steps)
            else:
                agent_result = stage.agent.run()

            stage_info = {
                "name": stage.name,
                "domain": stage.agent.domain,
                "success": agent_result.success,
                "n_passed": agent_result.n_passed,
                "n_failed": agent_result.n_failed,
                "n_rolled_back": agent_result.n_rolled_back,
                "duration_s": round(agent_result.total_duration_s, 3),
                "summary": agent_result.summary,
            }
            pipeline_result.stages.append(stage_info)

            if not agent_result.success and stage.stop_on_failure:
                pipeline_result.stopped_at = stage.name
                break

        pipeline_result.total_duration_s = time.perf_counter() - start
        pipeline_result.success = all(s["success"] for s in pipeline_result.stages)

        # Build summary
        n_stages = len(pipeline_result.stages)
        n_ok = sum(1 for s in pipeline_result.stages if s["success"])
        if pipeline_result.success:
            pipeline_result.summary = (
                f"✓ Pipeline completed: {n_stages} stage(s) in "
                f"{pipeline_result.total_duration_s:.2f}s."
            )
        elif pipeline_result.stopped_at:
            pipeline_result.summary = (
                f"Pipeline halted at '{pipeline_result.stopped_at}': "
                f"{n_ok}/{n_stages} stage(s) succeeded."
            )
        else:
            pipeline_result.summary = (
                f"Pipeline completed with issues: "
                f"{n_ok}/{n_stages} stage(s) fully succeeded."
            )

        if self.workspace.df is not None:
            pipeline_result.summary += (
                f" Final data: {self.workspace.df.height:,} rows × "
                f"{self.workspace.df.width} columns."
            )

        return pipeline_result

    @classmethod
    def standard(
        cls,
        workspace: Workspace,
        *,
        memory: AgentMemory | None = None,
        validate: bool = True,
    ) -> "Pipeline":
        """Create a standard Ingest → Quality → Transform → Export pipeline.

        Args:
            workspace: The workspace to operate on.
            memory: Optional memory for agents.
            validate: Whether to validate between transform steps.

        Returns:
            A Pipeline with 4 standard stages.
        """
        pipeline = cls(workspace=workspace, memory=memory)
        pipeline.add_stage("ingest", IngestionAgent(workspace, memory=memory))
        pipeline.add_stage(
            "quality",
            QualityAgent(workspace, memory=memory),
            stop_on_failure=False,
        )
        pipeline.add_stage(
            "transform",
            TransformAgent(workspace, memory=memory, validate_between_steps=validate),
        )
        pipeline.add_stage(
            "export",
            ExportAgent(workspace, memory=memory),
            stop_on_failure=False,
        )
        return pipeline
