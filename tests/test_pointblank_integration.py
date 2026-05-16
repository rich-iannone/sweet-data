"""Tests for Phase 2: Pointblank integration (scan, validate, schema_info)."""

import polars as pl
import pytest

from sweet.core.workspace import Workspace


# =============================================================================
# DataScan Integration
# =============================================================================


class TestScan:
    """Tests for Workspace.scan() powered by Pointblank DataScan."""

    def test_scan_basic(self):
        """Scan returns per-column statistics."""
        df = pl.DataFrame(
            {
                "id": [1, 2, 3, 4, 5],
                "name": ["Alice", "Bob", "Charlie", "Diana", "Eve"],
                "score": [85.5, 92.0, 78.3, 95.1, 88.7],
            }
        )
        ws = Workspace()
        ws.load_df(df, name="students")

        result = ws.scan()

        assert result["name"] == "students"
        assert result["shape"] == (5, 3)
        assert len(result["columns"]) == 3

        # Check column names are present
        col_names = [c["colname"] for c in result["columns"]]
        assert col_names == ["id", "name", "score"]

    def test_scan_column_types(self):
        """Scan correctly reports column types."""
        df = pl.DataFrame(
            {
                "int_col": [1, 2, 3],
                "float_col": [1.0, 2.5, 3.7],
                "str_col": ["a", "b", "c"],
            }
        )
        ws = Workspace()
        ws.load_df(df, name="typed")

        result = ws.scan()
        types = {c["colname"]: c["coltype"] for c in result["columns"]}

        assert types["int_col"] == "Int64"
        assert types["float_col"] == "Float64"
        assert types["str_col"] == "String"

    def test_scan_missingness(self):
        """Scan detects null counts."""
        df = pl.DataFrame(
            {
                "complete": [1, 2, 3, 4, 5],
                "some_nulls": [1.0, None, 3.0, None, 5.0],
                "all_nulls": pl.Series([None, None, None, None, None], dtype=pl.Float64),
            }
        )
        ws = Workspace()
        ws.load_df(df, name="nulls")

        result = ws.scan()
        missing = {c["colname"]: c["n_missing"] for c in result["columns"]}

        assert missing["complete"] == 0
        assert missing["some_nulls"] == 2
        assert missing["all_nulls"] == 5

    def test_scan_uniqueness(self):
        """Scan reports unique value counts."""
        df = pl.DataFrame(
            {
                "all_unique": [1, 2, 3, 4, 5],
                "some_dupes": ["a", "b", "a", "c", "b"],
                "all_same": [1, 1, 1, 1, 1],
            }
        )
        ws = Workspace()
        ws.load_df(df, name="uniq")

        result = ws.scan()
        unique = {c["colname"]: c["n_unique"] for c in result["columns"]}

        assert unique["all_unique"] == 5
        assert unique["some_dupes"] == 3
        assert unique["all_same"] == 1

    def test_scan_numeric_statistics(self):
        """Scan returns meaningful statistics for numeric columns."""
        df = pl.DataFrame({"values": [10.0, 20.0, 30.0, 40.0, 50.0]})
        ws = Workspace()
        ws.load_df(df, name="nums")

        result = ws.scan()
        col = result["columns"][0]

        assert col["mean"] == 30.0
        assert col["median"] == 30.0
        # DataScan returns min/max as native types for numeric columns
        assert float(col["min"]) == 10.0
        assert float(col["max"]) == 50.0

    def test_scan_no_data_raises(self):
        """Scan raises ValueError when no data is loaded."""
        ws = Workspace()
        with pytest.raises(ValueError, match="No active sheet"):
            ws.scan()

    def test_scan_empty_df(self):
        """Scan handles empty DataFrame (no rows)."""
        df = pl.DataFrame({"a": pl.Series([], dtype=pl.Int64)})
        ws = Workspace()
        ws.load_df(df, name="empty")

        result = ws.scan()
        assert result["shape"] == (0, 1)
        assert len(result["columns"]) == 1


# =============================================================================
# Validation Integration
# =============================================================================


class TestValidate:
    """Tests for Workspace.validate() powered by Pointblank Validate."""

    def test_validate_default_not_null(self):
        """Default validation checks all columns for non-null."""
        df = pl.DataFrame(
            {
                "a": [1, 2, 3],
                "b": ["x", "y", "z"],
            }
        )
        ws = Workspace()
        ws.load_df(df, name="clean")

        result = ws.validate()

        assert result["all_passed"] is True
        assert result["n_steps"] == 2
        assert all(s["all_passed"] for s in result["steps"])

    def test_validate_default_detects_nulls(self):
        """Default validation detects null values."""
        df = pl.DataFrame(
            {
                "a": [1, None, 3],
                "b": ["x", "y", None],
            }
        )
        ws = Workspace()
        ws.load_df(df, name="with_nulls")

        result = ws.validate()

        assert result["all_passed"] is False
        # Both columns have nulls
        failed_steps = [s for s in result["steps"] if not s["all_passed"]]
        assert len(failed_steps) == 2

    def test_validate_custom_checks_gt(self):
        """Custom check: col_vals_gt."""
        df = pl.DataFrame({"price": [10.0, 20.0, 5.0, 30.0]})
        ws = Workspace()
        ws.load_df(df, name="prices")

        result = ws.validate(
            checks=[{"type": "col_vals_gt", "column": "price", "value": 0}]
        )

        assert result["all_passed"] is True
        assert result["steps"][0]["n_passed"] == 4

    def test_validate_custom_checks_between(self):
        """Custom check: col_vals_between."""
        df = pl.DataFrame({"score": [50, 75, 100, 25, 110]})
        ws = Workspace()
        ws.load_df(df, name="scores")

        result = ws.validate(
            checks=[
                {"type": "col_vals_between", "column": "score", "left": 0, "right": 100}
            ]
        )

        assert result["all_passed"] is False
        assert result["steps"][0]["n_passed"] == 4
        assert result["steps"][0]["n_failed"] == 1

    def test_validate_custom_checks_in_set(self):
        """Custom check: col_vals_in_set."""
        df = pl.DataFrame({"status": ["active", "inactive", "active", "unknown"]})
        ws = Workspace()
        ws.load_df(df, name="statuses")

        result = ws.validate(
            checks=[
                {
                    "type": "col_vals_in_set",
                    "column": "status",
                    "set": ["active", "inactive"],
                }
            ]
        )

        assert result["all_passed"] is False
        assert result["steps"][0]["n_failed"] == 1

    def test_validate_multiple_checks(self):
        """Multiple checks in one validation."""
        df = pl.DataFrame(
            {
                "age": [25, 30, -5, 40, 150],
                "name": ["Alice", "Bob", None, "Diana", "Eve"],
            }
        )
        ws = Workspace()
        ws.load_df(df, name="people")

        result = ws.validate(
            checks=[
                {"type": "col_vals_between", "column": "age", "left": 0, "right": 120},
                {"type": "col_vals_not_null", "column": "name"},
            ]
        )

        assert result["all_passed"] is False
        assert result["n_steps"] == 2
        # age: -5 and 150 fail
        assert result["steps"][0]["n_failed"] == 2
        # name: 1 null
        assert result["steps"][1]["n_failed"] == 1

    def test_validate_all_pass(self):
        """Validation where everything passes."""
        df = pl.DataFrame(
            {
                "id": [1, 2, 3],
                "value": [10.0, 20.0, 30.0],
            }
        )
        ws = Workspace()
        ws.load_df(df, name="good")

        result = ws.validate(
            checks=[
                {"type": "col_vals_gt", "column": "value", "value": 0},
                {"type": "col_vals_not_null", "column": "id"},
            ]
        )

        assert result["all_passed"] is True

    def test_validate_unknown_method_raises(self):
        """Unknown validation method raises ValueError."""
        df = pl.DataFrame({"x": [1, 2, 3]})
        ws = Workspace()
        ws.load_df(df, name="test")

        with pytest.raises(ValueError, match="Unknown validation method"):
            ws.validate(checks=[{"type": "col_vals_fake_method", "column": "x"}])

    def test_validate_no_data_raises(self):
        """Validate raises ValueError with no data."""
        ws = Workspace()
        with pytest.raises(ValueError, match="No active sheet"):
            ws.validate()

    def test_validate_result_structure(self):
        """Validate returns well-structured results."""
        df = pl.DataFrame({"x": [1, 2, 3]})
        ws = Workspace()
        ws.load_df(df, name="test")

        result = ws.validate()

        assert "all_passed" in result
        assert "n_steps" in result
        assert "steps" in result
        assert isinstance(result["steps"], list)
        step = result["steps"][0]
        assert "step" in step
        assert "type" in step
        assert "column" in step
        assert "n" in step
        assert "n_passed" in step
        assert "n_failed" in step
        assert "all_passed" in step


# =============================================================================
# Schema Integration
# =============================================================================


class TestSchemaInfo:
    """Tests for Workspace.schema_info() powered by Pointblank Schema."""

    def test_schema_info_basic(self):
        """Schema info returns column names and types."""
        df = pl.DataFrame(
            {
                "id": [1, 2, 3],
                "name": ["a", "b", "c"],
                "value": [1.0, 2.0, 3.0],
            }
        )
        ws = Workspace()
        ws.load_df(df, name="typed")

        result = ws.schema_info()

        assert result["name"] == "typed"
        assert result["n_rows"] == 3
        assert result["n_cols"] == 3
        assert len(result["columns"]) == 3

        col_map = {c["name"]: c["dtype"] for c in result["columns"]}
        assert col_map["id"] == "Int64"
        assert col_map["name"] == "String"
        assert col_map["value"] == "Float64"

    def test_schema_info_various_types(self):
        """Schema info handles various Polars types."""
        df = pl.DataFrame(
            {
                "bool_col": [True, False, True],
                "date_col": pl.Series(["2024-01-01", "2024-02-01", "2024-03-01"]).str.to_date(),
                "list_col": [[1, 2], [3, 4], [5, 6]],
            }
        )
        ws = Workspace()
        ws.load_df(df, name="varied")

        result = ws.schema_info()
        col_map = {c["name"]: c["dtype"] for c in result["columns"]}

        assert col_map["bool_col"] == "Boolean"
        assert "Date" in col_map["date_col"]

    def test_schema_info_no_data_raises(self):
        """Schema info raises ValueError with no data."""
        ws = Workspace()
        with pytest.raises(ValueError, match="No active sheet"):
            ws.schema_info()

    def test_schema_info_after_transform(self):
        """Schema info reflects transformed data."""
        df = pl.DataFrame(
            {
                "x": [1, 2, 3],
                "y": [4, 5, 6],
            }
        )
        ws = Workspace()
        ws.load_df(df, name="data")
        ws.transform("df.with_columns((pl.col('x') + pl.col('y')).alias('z'))")

        result = ws.schema_info()
        col_names = [c["name"] for c in result["columns"]]

        assert "z" in col_names
        assert result["n_cols"] == 3


# =============================================================================
# Integration: Scan + Validate + Transform
# =============================================================================


class TestIntegrationWorkflows:
    """End-to-end workflows combining scan, validate, and transforms."""

    def test_scan_then_validate_nulls(self):
        """Scan identifies nulls, validate confirms them."""
        df = pl.DataFrame(
            {
                "id": [1, 2, 3, 4, 5],
                "email": ["a@b.com", None, "c@d.com", None, "e@f.com"],
            }
        )
        ws = Workspace()
        ws.load_df(df, name="users")

        # Scan detects nulls
        scan = ws.scan()
        email_col = next(c for c in scan["columns"] if c["colname"] == "email")
        assert email_col["n_missing"] == 2

        # Validate confirms
        val = ws.validate(
            checks=[{"type": "col_vals_not_null", "column": "email"}]
        )
        assert val["steps"][0]["n_failed"] == 2

    def test_validate_after_cleaning(self):
        """Validation passes after cleaning data."""
        df = pl.DataFrame(
            {
                "value": [1.0, None, 3.0, None, 5.0],
            }
        )
        ws = Workspace()
        ws.load_df(df, name="dirty")

        # Fails before cleaning
        result_before = ws.validate()
        assert result_before["all_passed"] is False

        # Clean: drop nulls
        ws.transform("df.drop_nulls()")

        # Passes after cleaning
        result_after = ws.validate()
        assert result_after["all_passed"] is True

    def test_validate_range_after_clip(self):
        """Validate range constraint passes after clipping outliers."""
        df = pl.DataFrame({"score": [10, 20, 30, -5, 150]})
        ws = Workspace()
        ws.load_df(df, name="scores")

        # Fails: -5 and 150 out of [0, 100]
        result = ws.validate(
            checks=[{"type": "col_vals_between", "column": "score", "left": 0, "right": 100}]
        )
        assert result["steps"][0]["n_failed"] == 2

        # Clip to range
        ws.transform("df.with_columns(pl.col('score').clip(0, 100))")

        # Passes now
        result = ws.validate(
            checks=[{"type": "col_vals_between", "column": "score", "left": 0, "right": 100}]
        )
        assert result["all_passed"] is True

    def test_schema_info_consistent_with_scan(self):
        """Schema info column names match scan column names."""
        df = pl.DataFrame(
            {
                "a": [1, 2, 3],
                "b": ["x", "y", "z"],
                "c": [1.0, 2.0, 3.0],
            }
        )
        ws = Workspace()
        ws.load_df(df, name="test")

        scan = ws.scan()
        schema = ws.schema_info()

        scan_names = [c["colname"] for c in scan["columns"]]
        schema_names = [c["name"] for c in schema["columns"]]
        assert scan_names == schema_names
