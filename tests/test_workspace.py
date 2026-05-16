"""Tests for the Workspace programmatic API."""

import pytest
import polars as pl
from pathlib import Path

from sweet.core.workspace import Workspace, OperationKind


@pytest.fixture
def sample_csv(tmp_path):
    """Create a sample CSV file for testing."""
    csv_file = tmp_path / "test.csv"
    csv_file.write_text("name,age,city\nAlice,30,NYC\nBob,25,LA\nCharlie,35,Chicago\n")
    return csv_file


@pytest.fixture
def sample_df():
    """Create a sample Polars DataFrame."""
    return pl.DataFrame(
        {
            "name": ["Alice", "Bob", "Charlie"],
            "age": [30, 25, 35],
            "city": ["NYC", "LA", "Chicago"],
        }
    )


@pytest.fixture
def loaded_workspace(sample_csv):
    """A workspace with one sheet loaded from CSV."""
    ws = Workspace()
    ws.load(sample_csv)
    return ws


class TestWorkspaceCreation:
    def test_empty_workspace(self):
        ws = Workspace()
        assert ws.current_sheet_name is None
        assert ws.current_sheet is None
        assert ws.sheet_names == []
        assert ws.df is None
        assert ws.shape is None
        assert ws.schema == {}

    def test_no_active_sheet_raises(self):
        ws = Workspace()
        with pytest.raises(ValueError, match="No active sheet"):
            ws.transform("df.head(1)")


class TestLoading:
    def test_load_csv(self, sample_csv):
        ws = Workspace()
        result = ws.load(sample_csv)

        # Returns self for chaining
        assert result is ws
        assert ws.current_sheet_name == "test"
        assert ws.shape == (3, 3)
        assert ws.schema == {"name": "String", "age": "Int64", "city": "String"}

    def test_load_with_custom_name(self, sample_csv):
        ws = Workspace()
        ws.load(sample_csv, name="my_data")

        assert ws.current_sheet_name == "my_data"

    def test_load_parquet(self, tmp_path, sample_df):
        parquet_file = tmp_path / "test.parquet"
        sample_df.write_parquet(parquet_file)

        ws = Workspace()
        ws.load(parquet_file)

        assert ws.shape == (3, 3)
        assert ws.df.equals(sample_df)

    def test_load_json(self, tmp_path, sample_df):
        json_file = tmp_path / "test.json"
        sample_df.write_json(json_file)

        ws = Workspace()
        ws.load(json_file)

        assert ws.shape == (3, 3)

    def test_load_nonexistent_file(self):
        ws = Workspace()
        with pytest.raises(FileNotFoundError):
            ws.load("/nonexistent/path.csv")

    def test_load_unsupported_format(self, tmp_path):
        bad_file = tmp_path / "data.xlsx"
        bad_file.write_text("fake")

        ws = Workspace()
        with pytest.raises(ValueError, match="Cannot detect format"):
            ws.load(bad_file)

    def test_load_df(self, sample_df):
        ws = Workspace()
        result = ws.load_df(sample_df, name="direct")

        assert result is ws
        assert ws.current_sheet_name == "direct"
        assert ws.df.equals(sample_df)
        assert ws.shape == (3, 3)

    def test_load_records_operation(self, sample_csv):
        ws = Workspace()
        ws.load(sample_csv)

        history = ws.history()
        assert len(history) == 1
        assert history[0].kind == OperationKind.LOAD
        assert history[0].sheet == "test"
        assert history[0].metadata["source"] == str(sample_csv)

    def test_load_multiple_sheets(self, tmp_path):
        csv1 = tmp_path / "a.csv"
        csv2 = tmp_path / "b.csv"
        csv1.write_text("x,y\n1,2\n")
        csv2.write_text("p,q,r\n3,4,5\n")

        ws = Workspace()
        ws.load(csv1)
        ws.load(csv2)

        assert set(ws.sheet_names) == {"a", "b"}
        # First loaded sheet remains current (workbook behavior)
        assert ws.current_sheet_name == "a"


class TestTransform:
    def test_basic_transform(self, loaded_workspace):
        ws = loaded_workspace
        result = ws.transform("df.filter(pl.col('age') > 25)")

        assert result is ws
        assert ws.shape == (2, 3)  # Alice (30) and Charlie (35)

    def test_transform_records_operation(self, loaded_workspace):
        ws = loaded_workspace
        ws.transform("df.filter(pl.col('age') > 25)", description="Filter adults")

        history = ws.history()
        # load + transform
        assert len(history) == 2
        op = history[1]
        assert op.kind == OperationKind.TRANSFORM
        assert op.expr == "df.filter(pl.col('age') > 25)"
        assert op.metadata["description"] == "Filter adults"
        assert op.input_hash != ""
        assert op.output_hash != ""

    def test_transform_chaining(self, loaded_workspace):
        ws = loaded_workspace
        ws.transform("df.filter(pl.col('age') > 25)").transform("df.select(['name', 'age'])")

        assert ws.shape == (2, 2)
        assert list(ws.df.columns) == ["name", "age"]

    def test_transform_invalid_expr(self, loaded_workspace):
        ws = loaded_workspace
        with pytest.raises(ValueError):
            ws.transform("df.nonexistent_method()")

    def test_transform_dangerous_expr(self, loaded_workspace):
        ws = loaded_workspace
        with pytest.raises(ValueError, match="dangerous"):
            ws.transform("__import__('os').system('rm -rf /')")

    def test_filter_convenience(self, loaded_workspace):
        ws = loaded_workspace
        ws.filter("pl.col('age') > 25")

        assert ws.shape == (2, 3)

    def test_select_convenience(self, loaded_workspace):
        ws = loaded_workspace
        ws.select("name", "age")

        assert list(ws.df.columns) == ["name", "age"]

    def test_sort_convenience(self, loaded_workspace):
        ws = loaded_workspace
        ws.sort("age")

        assert ws.df["age"].to_list() == [25, 30, 35]

    def test_sort_descending(self, loaded_workspace):
        ws = loaded_workspace
        ws.sort("age", descending=True)

        assert ws.df["age"].to_list() == [35, 30, 25]

    def test_transform_no_data_raises(self):
        ws = Workspace()
        ws._workbook.add_sheet("empty")
        with pytest.raises(ValueError, match="No data loaded"):
            ws.transform("df.head(1)")


class TestQuery:
    def test_basic_sql_query(self, loaded_workspace):
        ws = loaded_workspace
        ws.query(f"SELECT name, age FROM {ws.current_sheet_name} WHERE age > 25")

        assert ws.shape[0] == 2
        assert "name" in ws.df.columns
        assert "age" in ws.df.columns

    def test_query_records_operation(self, loaded_workspace):
        ws = loaded_workspace
        ws.query(f"SELECT * FROM {ws.current_sheet_name} LIMIT 1")

        history = ws.history()
        assert len(history) == 2
        op = history[1]
        assert op.kind == OperationKind.TRANSFORM
        assert "sql" in op.metadata.get("type", "")


class TestBranching:
    def test_branch_creates_copy(self, loaded_workspace):
        ws = loaded_workspace
        original_name = ws.current_sheet_name
        ws.branch("experiment")

        assert ws.current_sheet_name == "experiment"
        assert "experiment" in ws.sheet_names
        assert original_name in ws.sheet_names

    def test_branch_is_independent(self, loaded_workspace):
        ws = loaded_workspace
        original_name = ws.current_sheet_name

        ws.branch("experiment")
        ws.transform("df.filter(pl.col('age') > 30)")

        # Branch has filtered data
        assert ws.shape == (1, 3)

        # Original is unchanged
        ws.switch(original_name)
        assert ws.shape == (3, 3)

    def test_branch_duplicate_name_raises(self, loaded_workspace):
        ws = loaded_workspace
        ws.branch("experiment")
        with pytest.raises(ValueError):
            ws.branch("experiment")

    def test_switch_sheet(self, loaded_workspace):
        ws = loaded_workspace
        ws.branch("other")
        ws.switch("test")

        assert ws.current_sheet_name == "test"

    def test_switch_nonexistent_raises(self, loaded_workspace):
        ws = loaded_workspace
        with pytest.raises(ValueError, match="not found"):
            ws.switch("nonexistent")


class TestInspect:
    def test_inspect_loaded_data(self, loaded_workspace):
        ws = loaded_workspace
        info = ws.inspect()

        assert info["name"] == "test"
        assert info["shape"] == (3, 3)
        assert "name" in info["schema"]
        assert "age" in info["schema"]
        assert "city" in info["schema"]
        assert len(info["sample"]) == 3
        assert info["null_counts"]["name"] == 0

    def test_inspect_sample_rows(self, loaded_workspace):
        ws = loaded_workspace
        info = ws.inspect(n_rows=1)

        assert len(info["sample"]) == 1

    def test_inspect_empty_sheet(self):
        ws = Workspace()
        ws._workbook.add_sheet("empty")

        info = ws.inspect()
        assert info["shape"] == (0, 0)
        assert info["schema"] == {}

    def test_sample(self, loaded_workspace):
        ws = loaded_workspace
        sampled = ws.sample(2)

        assert sampled is not None
        assert sampled.shape[0] == 2


class TestExport:
    def test_export_csv(self, loaded_workspace, tmp_path):
        ws = loaded_workspace
        out = tmp_path / "output.csv"
        ws.export(out)

        assert out.exists()
        df = pl.read_csv(out)
        assert df.shape == (3, 3)

    def test_export_parquet(self, loaded_workspace, tmp_path):
        ws = loaded_workspace
        out = tmp_path / "output.parquet"
        ws.export(out)

        assert out.exists()
        df = pl.read_parquet(out)
        assert df.shape == (3, 3)

    def test_export_json(self, loaded_workspace, tmp_path):
        ws = loaded_workspace
        out = tmp_path / "output.json"
        ws.export(out)

        assert out.exists()

    def test_export_records_operation(self, loaded_workspace, tmp_path):
        ws = loaded_workspace
        out = tmp_path / "output.csv"
        ws.export(out)

        history = ws.history()
        assert history[-1].kind == OperationKind.EXPORT
        assert history[-1].metadata["dest"] == str(out)

    def test_export_no_data_raises(self):
        ws = Workspace()
        ws._workbook.add_sheet("empty")
        with pytest.raises(ValueError, match="No data to export"):
            ws.export("/tmp/out.csv")


class TestUndoRedo:
    def test_undo_transform(self, loaded_workspace):
        ws = loaded_workspace
        assert ws.shape == (3, 3)

        ws.transform("df.filter(pl.col('age') > 25)")
        assert ws.shape == (2, 3)

        ws.undo()
        assert ws.shape == (3, 3)

    def test_redo_after_undo(self, loaded_workspace):
        ws = loaded_workspace
        ws.transform("df.filter(pl.col('age') > 25)")
        ws.undo()
        assert ws.shape == (3, 3)

        ws.redo()
        assert ws.shape == (2, 3)

    def test_multiple_undo(self, loaded_workspace):
        ws = loaded_workspace
        ws.transform("df.filter(pl.col('age') > 20)")  # All 3 rows
        ws.transform("df.filter(pl.col('age') > 25)")  # 2 rows
        ws.transform("df.filter(pl.col('age') > 30)")  # 1 row

        assert ws.shape == (1, 3)

        ws.undo()
        assert ws.shape == (2, 3)

        ws.undo()
        assert ws.shape == (3, 3)

    def test_undo_nothing_raises(self, loaded_workspace):
        ws = loaded_workspace
        # Only a load operation — nothing undoable
        with pytest.raises(ValueError, match="Nothing to undo"):
            ws.undo()

    def test_redo_nothing_raises(self, loaded_workspace):
        ws = loaded_workspace
        with pytest.raises(ValueError, match="Nothing to redo"):
            ws.redo()

    def test_new_transform_clears_redo_stack(self, loaded_workspace):
        ws = loaded_workspace
        ws.transform("df.filter(pl.col('age') > 25)")
        ws.undo()
        assert ws.can_redo

        # New transform clears redo stack
        ws.transform("df.filter(pl.col('age') > 20)")
        assert not ws.can_redo

    def test_can_undo_property(self, loaded_workspace):
        ws = loaded_workspace
        assert not ws.can_undo

        ws.transform("df.head(1)")
        assert ws.can_undo

    def test_can_redo_property(self, loaded_workspace):
        ws = loaded_workspace
        assert not ws.can_redo

        ws.transform("df.head(1)")
        ws.undo()
        assert ws.can_redo


class TestHistory:
    def test_empty_history(self):
        ws = Workspace()
        assert ws.history() == []
        assert ws.history_summary() == []

    def test_history_tracks_all_ops(self, loaded_workspace):
        ws = loaded_workspace
        ws.transform("df.head(2)")
        ws.branch("exp")
        ws.transform("df.head(1)")

        history = ws.history()
        assert len(history) == 4  # load + transform + branch + transform

    def test_history_summary(self, loaded_workspace):
        ws = loaded_workspace
        ws.transform("df.head(2)", description="Take first 2")

        summary = ws.history_summary()
        assert len(summary) == 2
        assert summary[1]["kind"] == "transform"
        assert summary[1]["expr"] == "df.head(2)"
        assert summary[1]["description"] == "Take first 2"

    def test_generate_code(self, loaded_workspace):
        ws = loaded_workspace
        ws.transform("df.filter(pl.col('age') > 25)")
        ws.transform("df.select(['name'])")

        code = ws.generate_code()
        assert "import polars as pl" in code
        assert "df.filter(pl.col('age') > 25)" in code
        assert "df.select(['name'])" in code


class TestMethodChaining:
    """Verify the fluent API works end-to-end."""

    def test_full_chain(self, sample_csv, tmp_path):
        out = tmp_path / "result.parquet"

        ws = (
            Workspace()
            .load(sample_csv)
            .transform("df.filter(pl.col('age') > 25)")
            .sort("age", descending=True)
            .export(out)
        )

        assert out.exists()
        result = pl.read_parquet(out)
        assert result.shape == (2, 3)
        assert result["age"].to_list() == [35, 30]


class TestEdgeCases:
    def test_load_duplicate_name_raises(self, sample_csv):
        ws = Workspace()
        ws.load(sample_csv, name="data")
        with pytest.raises(ValueError, match="already exists"):
            ws.load(sample_csv, name="data")

    def test_explicit_format_override(self, tmp_path):
        # Write CSV but name it .txt
        txt_file = tmp_path / "data.txt"
        txt_file.write_text("a,b\n1,2\n")

        ws = Workspace()
        ws.load(txt_file, format="csv")
        assert ws.shape == (1, 2)
