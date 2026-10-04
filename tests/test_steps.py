"""Tests for sweet.core.steps, sweet.core.pipeline, and sweet.core.registry."""

import duckdb
import polars as pl
import pytest
from polars.testing import assert_frame_equal

from sweet.core.pipeline import Pipeline
from sweet.core.registry import Registry
from sweet.core.steps import (
    STEP_TYPES,
    NotExportable,
    Step,
    StepError,
    StepType,
    eval_polars_expr,
    step_type,
)


@pytest.fixture
def df():
    return pl.DataFrame(
        {
            "name": ["Alice", "Bob", "Charlie", "Diana"],
            "age": [30, 25, 35, 28],
            "city": ["NYC", "LA", "NYC", "Chicago"],
            "revenue": [100.0, -5.0, 250.0, 80.0],
        }
    )


def run_code(code: str, df: pl.DataFrame) -> pl.DataFrame:
    """Execute a step's Polars rendering against `df`."""
    namespace = {"pl": pl, "df": df, "duckdb": duckdb}
    return eval(code, namespace)


# -----------------------------------------------------------------------------
# Registry
# -----------------------------------------------------------------------------


class TestRegistry:
    def test_register_and_get(self):
        reg = Registry("thing")
        reg.register("a", 1)
        assert reg.get("a") == 1
        assert "a" in reg
        assert reg.names() == ["a"]

    def test_decorator(self):
        reg = Registry("thing")

        @reg.register("f")
        def f():
            return 42

        assert reg.get("f")() == 42

    def test_duplicate_rejected_unless_replace(self):
        reg = Registry("thing")
        reg.register("a", 1)
        with pytest.raises(ValueError, match="already registered"):
            reg.register("a", 2)
        reg.register("a", 2, replace=True)
        assert reg.get("a") == 2

    def test_unknown_lists_known(self):
        reg = Registry("thing")
        reg.register("a", 1)
        with pytest.raises(KeyError, match="Known: a"):
            reg.get("zzz")

    def test_builtin_step_types_registered(self):
        for kind in [
            "filter", "sort", "select", "drop", "rename", "cast", "mutate",
            "edit_cell", "insert_row", "delete_rows", "polars", "sql",
        ]:  # fmt: skip
            assert kind in STEP_TYPES


# -----------------------------------------------------------------------------
# Individual step types: apply + Polars rendering agree
# -----------------------------------------------------------------------------

POLARS_CASES = [
    Step("filter", {"expr": "pl.col('age') > 26"}),
    Step("filter", {"sql": "age > 26 AND city = 'NYC'"}),
    Step("sort", {"columns": ["age"], "descending": True}),
    Step("select", {"columns": ["name", "age"]}),
    Step("drop", {"columns": ["city"]}),
    Step("rename", {"mapping": {"name": "full_name"}}),
    Step("cast", {"columns": {"age": "float"}}),
    Step("cast", {"columns": {"age": "str"}, "strict": False}),
    Step("mutate", {"column": "double_age", "expr": "pl.col('age') * 2"}),
    Step("mutate", {"column": "age", "sql": "age + 1"}),
    Step("edit_cell", {"row": 1, "column": "age", "value": 99, "dtype": "Int64"}),
    Step("edit_cell", {"row": 0, "column": "city", "value": None}),
    Step("insert_row", {"values": {"name": "Eve", "age": 40}}),
    Step("insert_row", {"values": {"name": "Zed"}, "index": 1}),
    Step("delete_rows", {"rows": [0, 2]}),
    Step("polars", {"code": "df.head(2)"}),
    Step("sql", {"query": "SELECT name, age * 10 AS a10 FROM df ORDER BY name"}),
]


@pytest.mark.parametrize("step", POLARS_CASES, ids=lambda s: s.label)
def test_polars_rendering_matches_apply(step, df):
    expected = step.apply(df)
    actual = run_code(step.to_polars(), df)
    assert_frame_equal(actual, expected)


SQL_CASES = [
    Step("filter", {"sql": "age > 26"}),
    Step("sort", {"columns": ["age", "name"], "descending": [True, False]}),
    Step("select", {"columns": ["name", "age"]}),
    Step("drop", {"columns": ["city"]}),
    Step("rename", {"mapping": {"name": "full_name"}}),
    Step("cast", {"columns": {"age": "Float64"}}),
    Step("mutate", {"column": "double_age", "sql": "age * 2"}),
    Step("mutate", {"column": "age", "sql": "age + 1"}),
    Step("sql", {"query": "SELECT city, count(*) AS n FROM df GROUP BY city ORDER BY city"}),
]


@pytest.mark.parametrize("step", SQL_CASES, ids=lambda s: s.label)
def test_sql_rendering_matches_apply(step, df):
    expected = step.apply(df)
    conn = duckdb.connect()
    conn.register("input", df.to_arrow())
    query = step.to_sql('"input"', df.columns)
    if step.kind != "sort" and "ORDER BY" not in query:
        query = f"SELECT * FROM ({query}) ORDER BY ALL"
        expected = expected.sort(expected.columns)
    actual = pl.from_arrow(conn.execute(query).fetch_arrow_table())
    assert_frame_equal(actual, expected, check_dtypes=False)


@pytest.mark.parametrize(
    "step",
    [
        Step("filter", {"expr": "pl.col('age') > 1"}),
        Step("mutate", {"column": "x", "expr": "pl.lit(1)"}),
        Step("edit_cell", {"row": 0, "column": "age", "value": 1}),
        Step("delete_rows", {"rows": [0]}),
        Step("polars", {"code": "df.head(1)"}),
    ],
    ids=lambda s: s.kind,
)
def test_non_sql_steps_raise_not_exportable(step, df):
    with pytest.raises(NotExportable, match="can't be exported to SQL"):
        step.to_sql('"input"', df.columns)


class TestStepBehavior:
    def test_edit_cell_sets_value(self, df):
        out = Step("edit_cell", {"row": 2, "column": "name", "value": "Chuck"}).apply(df)
        assert out["name"].to_list() == ["Alice", "Bob", "Chuck", "Diana"]

    def test_edit_cell_casts_to_column_type(self, df):
        out = Step("edit_cell", {"row": 0, "column": "age", "value": "41"}).apply(df)
        assert out["age"][0] == 41
        assert out.schema["age"] == pl.Int64

    def test_edit_cell_out_of_range(self, df):
        with pytest.raises(StepError, match="out of range"):
            Step("edit_cell", {"row": 10, "column": "age", "value": 1}).apply(df)

    def test_insert_row_positions(self, df):
        out = Step("insert_row", {"values": {"name": "X"}, "index": 0}).apply(df)
        assert out["name"].to_list()[:2] == ["X", "Alice"]
        assert out.height == 5

    def test_delete_rows(self, df):
        out = Step("delete_rows", {"rows": [1, 3]}).apply(df)
        assert out["name"].to_list() == ["Alice", "Charlie"]

    def test_missing_column_is_clear_error(self, df):
        with pytest.raises(StepError, match="not found: nope"):
            Step("select", {"columns": ["nope"]}).apply(df)

    def test_missing_param(self, df):
        with pytest.raises(StepError, match="missing parameter"):
            Step("rename", {}).apply(df)

    def test_unknown_dtype(self, df):
        with pytest.raises(StepError, match="Unknown data type"):
            Step("cast", {"columns": {"age": "Wibble"}}).apply(df)

    def test_column_names_with_open_or_file_are_fine(self):
        # The legacy string-eval guard rejected any expression containing "open"/"file"
        df = pl.DataFrame({"file_name": ["a", "b"], "opened": [1, 2]})
        out = Step("filter", {"expr": "pl.col('opened') > 1"}).apply(df)
        assert out["file_name"].to_list() == ["b"]

    def test_unknown_kind(self, df):
        with pytest.raises(KeyError, match="Unknown step type"):
            Step("teleport").apply(df)

    def test_labels(self):
        assert Step("filter", {"sql": "a > 1"}).label == "Filter: a > 1"
        assert Step("sort", {"columns": ["a"]}).label == "Sort by: a (asc)"
        assert Step("select", {"columns": ["a"]}, description="Keep a").label == "Keep a"

    def test_stateful_flags(self):
        assert not Step("filter", {"sql": "a"}).stateful
        assert not Step("mutate", {"column": "a", "sql": "1"}).stateful
        assert Step("sort", {"columns": ["a"]}).stateful
        assert Step("sql", {"query": "select 1"}).stateful


class TestExpressionSafety:
    def test_rejects_dunder(self):
        with pytest.raises(StepError, match="private"):
            eval_polars_expr("pl.col('a').__class__")

    def test_rejects_import(self):
        with pytest.raises(StepError):
            eval_polars_expr("__import__('os')")

    def test_no_dangerous_builtins(self):
        with pytest.raises(StepError, match="Could not evaluate"):
            eval_polars_expr("open('x')")

    def test_must_return_expr(self):
        with pytest.raises(StepError, match="Expected a Polars expression"):
            eval_polars_expr("1 + 1")

    def test_safe_builtins_available(self):
        assert isinstance(eval_polars_expr("pl.lit(len([1, 2]))"), pl.Expr)


class TestStepSerialization:
    def test_round_trip(self):
        step = Step("cast", {"columns": {"a": "Int64"}}, author="agent:claude", enabled=False)
        again = Step.from_dict(step.to_dict())
        assert again == step

    def test_defaults_omitted(self):
        d = Step("filter", {"sql": "a > 1"}, id="abc").to_dict()
        assert d == {"id": "abc", "kind": "filter", "params": {"sql": "a > 1"}}

    def test_missing_kind(self):
        with pytest.raises(StepError, match="missing 'kind'"):
            Step.from_dict({"params": {}})


class TestCustomStepType:
    def test_plugin_style_registration(self, df):
        @step_type("upper_test")
        class UpperStep(StepType):
            required = ("column",)

            def apply(self, df, params):
                return df.with_columns(pl.col(params["column"]).str.to_uppercase())

            def to_polars(self, params):
                return f"df.with_columns(pl.col({params['column']!r}).str.to_uppercase())"

        try:
            step = Step("upper_test", {"column": "name"})
            assert step.apply(df)["name"][0] == "ALICE"
            assert_frame_equal(run_code(step.to_polars(), df), step.apply(df))
        finally:
            STEP_TYPES.unregister("upper_test")


# -----------------------------------------------------------------------------
# Pipelines
# -----------------------------------------------------------------------------


@pytest.fixture
def csv_file(tmp_path, df):
    path = tmp_path / "people.csv"
    df.write_csv(path)
    return path


@pytest.fixture
def pipeline(csv_file, df):
    return Pipeline(
        name="clean-people",
        source={"path": str(csv_file), "format": "csv"},
        schema={c: str(t) for c, t in df.schema.items()},
        steps=[
            Step("filter", {"sql": "revenue > 0"}),
            Step("mutate", {"column": "rev_k", "sql": "revenue / 1000"}),
            Step("cast", {"columns": {"age": "Float64"}}),
            Step("rename", {"mapping": {"city": "metro"}}),
            Step("sort", {"columns": ["age"]}),
        ],
    )


class TestPipeline:
    def test_run_from_source(self, pipeline, df):
        out = pipeline.run()
        assert out.columns == ["name", "age", "metro", "revenue", "rev_k"]
        assert out["name"].to_list() == ["Diana", "Alice", "Charlie"]

    def test_run_upto(self, pipeline, df):
        assert pipeline.run(df, upto=1).height == 3
        assert "rev_k" not in pipeline.run(df, upto=1).columns
        assert_frame_equal(pipeline.run(df, upto=0), df)

    def test_disabled_steps_skipped(self, pipeline, df):
        pipeline.steps[0].enabled = False
        assert pipeline.run(df).height == 4

    def test_yaml_round_trip(self, pipeline, tmp_path):
        path = pipeline.save(tmp_path / "p.sweet.yaml")
        again = Pipeline.load(path)
        assert again.to_dict() == pipeline.to_dict()
        assert_frame_equal(again.run(), pipeline.run())

    def test_yaml_is_readable(self, pipeline):
        text = pipeline.to_yaml()
        assert "kind: filter" in text
        assert "sql: revenue > 0" in text

    def test_future_version_rejected(self):
        with pytest.raises(StepError, match="newer than this Sweet supports"):
            Pipeline.from_dict({"version": 99, "steps": []})

    def test_polars_script_reproduces_result(self, pipeline, tmp_path):
        out_path = tmp_path / "out.parquet"
        script = pipeline.to_polars_script(output=str(out_path))
        exec(compile(script, "pipeline.py", "exec"), {})
        assert_frame_equal(pl.read_parquet(out_path), pipeline.run())

    def test_sql_reproduces_result(self, pipeline):
        result = pl.from_arrow(duckdb.connect().execute(pipeline.to_sql()).fetch_arrow_table())
        assert_frame_equal(result, pipeline.run(), check_dtypes=False)

    def test_sql_export_names_offending_steps(self, pipeline):
        pipeline.steps.append(Step("polars", {"code": "df.head(1)"}))
        with pytest.raises(NotExportable, match="polars"):
            pipeline.to_sql()

    def test_sql_with_sql_step(self, csv_file, df):
        p = Pipeline(
            source={"path": str(csv_file)},
            steps=[
                Step("filter", {"sql": "age < 35"}),
                Step("sql", {"query": "SELECT city, sum(revenue) AS total FROM df GROUP BY city"}),
            ],
        )
        result = pl.from_arrow(duckdb.connect().execute(p.to_sql()).fetch_arrow_table())
        assert_frame_equal(result.sort("city"), p.run().sort("city"), check_dtypes=False)

    def test_run_without_source_needs_df(self):
        with pytest.raises(StepError, match="no file source"):
            Pipeline(steps=[]).run()
