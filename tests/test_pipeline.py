"""Tests for Phase 3.5: Specialized Agents and Pipeline Orchestration."""

import polars as pl
import pytest

from sweet.agents import (
    ExportAgent,
    IngestionAgent,
    Pipeline,
    PipelineResult,
    QualityAgent,
    TransformAgent,
)
from sweet.core.workspace import Workspace


# =============================================================================
# IngestionAgent
# =============================================================================


class TestIngestionAgent:
    """Tests for the IngestionAgent."""

    def test_default_steps(self):
        """Runs default ingestion steps."""
        ws = Workspace()
        ws.load_df(
            pl.DataFrame(
                {
                    "FirstName": ["  Alice  ", "  Bob  "],
                    "LastName": ["Smith", "Jones"],
                    "HireDate": ["2024-01-15", "2024-03-01"],
                }
            )
        )

        agent = IngestionAgent(ws)
        result = agent.run()

        assert result.n_passed >= 3
        # Column names should be snake_case
        assert "first_name" in ws.df.columns
        assert "last_name" in ws.df.columns
        # Whitespace trimmed
        assert ws.df["first_name"].to_list() == ["Alice", "Bob"]

    def test_normalize_column_names(self):
        """Normalizes CamelCase and spaces to snake_case."""
        ws = Workspace()
        ws.load_df(pl.DataFrame({"MyColumn": [1], "another-col": [2], "already_ok": [3]}))

        agent = IngestionAgent(ws)
        result = agent.run_steps(["normalize_column_names"])

        assert result.success is True
        cols = ws.df.columns
        assert "my_column" in cols
        assert "another_col" in cols
        assert "already_ok" in cols

    def test_drop_unnamed_columns(self):
        """Drops Unnamed: and __index columns."""
        ws = Workspace()
        ws.load_df(
            pl.DataFrame({"Unnamed: 0": [1, 2], "good": ["a", "b"], "__index": [0, 1]})
        )

        agent = IngestionAgent(ws)
        result = agent.run_steps(["drop_unnamed_columns"])

        assert result.success is True
        assert ws.df.columns == ["good"]

    def test_already_loaded_data(self):
        """infer_and_load reports data already loaded."""
        ws = Workspace()
        ws.load_df(pl.DataFrame({"x": [1, 2, 3]}))

        agent = IngestionAgent(ws)
        result = agent.run_steps(["infer_and_load"])

        assert result.success is True
        assert "already loaded" in result.steps[0].message


# =============================================================================
# QualityAgent
# =============================================================================


class TestQualityAgent:
    """Tests for the QualityAgent."""

    def test_default_steps(self):
        """Runs default quality assessment steps."""
        ws = Workspace()
        ws.load_df(
            pl.DataFrame(
                {
                    "id": [1, 2, 3, 4, 5],
                    "value": [10.0, 20.0, 30.0, 40.0, 500.0],
                    "name": ["Alice", None, "Charlie", "Dave", "Eve"],
                }
            )
        )

        agent = QualityAgent(ws)
        result = agent.run()

        # All quality steps should pass (they're read-only assessments)
        assert result.n_passed >= 4

    def test_profile_data(self):
        """Profile step reports statistics."""
        ws = Workspace()
        ws.load_df(pl.DataFrame({"x": [1, 2, 3], "name": ["a", "b", "c"]}))

        agent = QualityAgent(ws)
        result = agent.run_steps(["profile_data"])

        assert "3 rows" in result.steps[0].message
        assert "2 columns" in result.steps[0].message

    def test_check_completeness(self):
        """Completeness check reports nulls."""
        ws = Workspace()
        ws.load_df(pl.DataFrame({"x": [1, None, 3], "y": ["a", "b", "c"]}))

        agent = QualityAgent(ws)
        result = agent.run_steps(["check_completeness"])

        assert "x" in result.steps[0].message
        assert "null" in result.steps[0].message.lower()

    def test_check_completeness_perfect(self):
        """Completeness check reports 100% when no nulls."""
        ws = Workspace()
        ws.load_df(pl.DataFrame({"x": [1, 2, 3], "y": ["a", "b", "c"]}))

        agent = QualityAgent(ws)
        result = agent.run_steps(["check_completeness"])

        assert "100%" in result.steps[0].message

    def test_no_validation_by_default(self):
        """QualityAgent doesn't validate between steps by default."""
        ws = Workspace()
        ws.load_df(pl.DataFrame({"x": [1, 2, 3]}))

        agent = QualityAgent(ws)
        assert agent.validate_between_steps is False


# =============================================================================
# TransformAgent
# =============================================================================


class TestTransformAgent:
    """Tests for the TransformAgent."""

    def test_default_steps(self):
        """Runs default transform steps."""
        ws = Workspace()
        ws.load_df(
            pl.DataFrame(
                {
                    "name": ["  Alice  ", "Bob", "Bob"],
                    "score": ["85", "92", "92"],
                }
            )
        )

        agent = TransformAgent(ws, validate_between_steps=False)
        result = agent.run()

        assert result.n_passed >= 3
        assert ws.df["score"].dtype == pl.Int64  # Cast

    def test_fill_nulls(self):
        """fill_nulls step fills with defaults."""
        ws = Workspace()
        ws.load_df(
            pl.DataFrame(
                {
                    "x": [1, None, 3],
                    "name": ["a", None, "c"],
                }
            )
        )

        agent = TransformAgent(ws, validate_between_steps=False)
        result = agent.run_steps(["fill_nulls"])

        assert result.success is True
        assert ws.df["x"].null_count() == 0
        assert ws.df["name"].null_count() == 0

    def test_sort_by_first_column(self):
        """sort_by_first_column sorts ascending."""
        ws = Workspace()
        ws.load_df(pl.DataFrame({"id": [3, 1, 2], "val": ["c", "a", "b"]}))

        agent = TransformAgent(ws, validate_between_steps=False)
        result = agent.run_steps(["sort_by_first_column"])

        assert result.success is True
        assert ws.df["id"].to_list() == [1, 2, 3]


# =============================================================================
# ExportAgent
# =============================================================================


class TestExportAgent:
    """Tests for the ExportAgent."""

    def test_default_steps(self):
        """Runs default export prep steps."""
        ws = Workspace()
        ws.load_df(pl.DataFrame({"x": [1, 2, 3], "y": ["a", "b", "c"]}))

        agent = ExportAgent(ws)
        result = agent.run()

        assert result.n_passed >= 2

    def test_validate_for_export_clean(self):
        """Validate for export passes on clean data."""
        ws = Workspace()
        ws.load_df(pl.DataFrame({"x": [1, 2, 3]}))

        agent = ExportAgent(ws)
        result = agent.run_steps(["validate_for_export"])

        assert "ready for export" in result.steps[0].message

    def test_validate_for_export_issues(self):
        """Validate for export flags all-null columns."""
        ws = Workspace()
        ws.load_df(pl.DataFrame({"good": [1, 2], "bad": [None, None]}))

        agent = ExportAgent(ws)
        result = agent.run_steps(["validate_for_export"])

        assert "all-null" in result.steps[0].message

    def test_export_writes_file(self, tmp_path):
        """Export agent writes to file."""
        ws = Workspace()
        ws.load_df(pl.DataFrame({"x": [1, 2, 3]}))

        agent = ExportAgent(ws)
        out_file = tmp_path / "out.csv"
        result = agent.export(str(out_file))

        assert result.success is True
        assert out_file.exists()


# =============================================================================
# Pipeline
# =============================================================================


class TestPipeline:
    """Tests for Pipeline orchestration."""

    def test_standard_pipeline(self):
        """Standard pipeline runs all 4 stages."""
        ws = Workspace()
        ws.load_df(
            pl.DataFrame(
                {
                    "Name": ["  Alice  ", "Bob", "Bob"],
                    "Score": ["85", "92", "92"],
                }
            )
        )

        pipe = Pipeline.standard(ws, validate=False)
        result = pipe.run()

        assert len(result.stages) == 4
        assert result.stages[0]["domain"] == "ingestion"
        assert result.stages[1]["domain"] == "quality"
        assert result.stages[2]["domain"] == "transform"
        assert result.stages[3]["domain"] == "export"

    def test_custom_pipeline(self):
        """Pipeline with custom stages."""
        ws = Workspace()
        ws.load_df(pl.DataFrame({"x": [1, 2, 3]}))

        pipe = Pipeline(workspace=ws)
        pipe.add_stage("ingest", IngestionAgent(ws))
        pipe.add_stage("export", ExportAgent(ws))

        result = pipe.run()

        assert len(result.stages) == 2
        assert result.stages[0]["name"] == "ingest"
        assert result.stages[1]["name"] == "export"

    def test_pipeline_stops_on_failure(self):
        """Pipeline halts at a failing stage with stop_on_failure=True."""
        ws = Workspace()
        ws.load_df(pl.DataFrame({"x": [1, 2, 3]}))

        def failing_step(workspace):
            raise ValueError("Intentional failure")

        pipe = Pipeline(workspace=ws)

        # Create a quality agent with a guaranteed-to-fail custom step
        quality = QualityAgent(ws)
        quality.register_step("always_fail", failing_step)

        pipe.add_stage("will_fail", quality, steps=["always_fail"], stop_on_failure=True)
        pipe.add_stage("never_reached", ExportAgent(ws))

        result = pipe.run()

        assert result.success is False
        assert result.stopped_at == "will_fail"
        assert len(result.stages) == 1  # Second stage never ran

    def test_pipeline_continues_on_failure(self):
        """Pipeline continues past a failing stage when stop_on_failure=False."""
        ws = Workspace()
        ws.load_df(pl.DataFrame({"x": [1, 2, 3]}))

        def failing_step(workspace):
            raise ValueError("Intentional failure")

        quality = QualityAgent(ws)
        quality.register_step("always_fail", failing_step)

        pipe = Pipeline(workspace=ws)
        pipe.add_stage("may_fail", quality, steps=["always_fail"], stop_on_failure=False)
        pipe.add_stage("export", ExportAgent(ws))

        result = pipe.run()

        assert len(result.stages) == 2  # Both stages ran
        assert result.stages[0]["success"] is False
        assert result.stages[1]["success"] is True
        assert result.stopped_at is None

    def test_pipeline_chaining(self):
        """add_stage returns self for chaining."""
        ws = Workspace()
        ws.load_df(pl.DataFrame({"x": [1, 2, 3]}))

        pipe = (
            Pipeline(workspace=ws)
            .add_stage("a", IngestionAgent(ws))
            .add_stage("b", ExportAgent(ws))
        )

        assert pipe.stages == ["a", "b"]

    def test_pipeline_result_to_dict(self):
        """PipelineResult serializes."""
        ws = Workspace()
        ws.load_df(pl.DataFrame({"x": [1, 2, 3]}))

        pipe = Pipeline(workspace=ws)
        pipe.add_stage("export", ExportAgent(ws))
        result = pipe.run()

        d = result.to_dict()
        assert "success" in d
        assert "stages" in d
        assert "total_duration_s" in d

    def test_pipeline_stage_with_custom_steps(self):
        """Pipeline stage can override agent's default steps."""
        ws = Workspace()
        ws.load_df(pl.DataFrame({"x": [1, 2, 3]}))

        pipe = Pipeline(workspace=ws)
        pipe.add_stage("just_validate", QualityAgent(ws), steps=["validate"])

        result = pipe.run()

        assert result.stages[0]["n_passed"] == 1  # Only ran validate
