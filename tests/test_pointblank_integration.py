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
        """Default validation checks all columns for non-null plus row checks."""
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
        # 2 col checks + rows_distinct + rows_complete = 4
        assert result["n_steps"] == 4
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
        # Both columns have nulls + rows_complete fails = 3 failing steps
        failed_steps = [s for s in result["steps"] if not s["all_passed"]]
        assert len(failed_steps) == 3

    def test_validate_custom_checks_gt(self):
        """Custom check: col_vals_gt."""
        df = pl.DataFrame({"price": [10.0, 20.0, 5.0, 30.0]})
        ws = Workspace()
        ws.load_df(df, name="prices")

        result = ws.validate(checks=[{"type": "col_vals_gt", "column": "price", "value": 0}])

        assert result["all_passed"] is True
        assert result["steps"][0]["n_passed"] == 4

    def test_validate_custom_checks_between(self):
        """Custom check: col_vals_between."""
        df = pl.DataFrame({"score": [50, 75, 100, 25, 110]})
        ws = Workspace()
        ws.load_df(df, name="scores")

        result = ws.validate(
            checks=[{"type": "col_vals_between", "column": "score", "left": 0, "right": 100}]
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
        assert "f_passed" in step
        assert "f_failed" in step
        assert "all_passed" in step
        assert "warning" in step
        assert "error" in step
        assert "critical" in step

    def test_validate_thresholds(self):
        """Thresholds produce graduated severity flags."""
        df = pl.DataFrame({"x": [1, 2, None, None, None]})
        ws = Workspace()
        ws.load_df(df, name="test")

        result = ws.validate(
            checks=[{"type": "col_vals_not_null", "column": "x"}],
            thresholds={"warning": 0.1, "error": 0.3, "critical": 0.8},
        )

        step = result["steps"][0]
        assert step["n_failed"] == 3
        # 3/5 = 0.6 failed → warning (>0.1) and error (>0.3), not critical (<0.8)
        assert step["warning"] is True
        assert step["error"] is True
        assert step["critical"] is False

    def test_validate_extracts(self):
        """get_extracts returns failing rows."""
        df = pl.DataFrame({"val": [10, 20, -5, 30, -1]})
        ws = Workspace()
        ws.load_df(df, name="test")

        result = ws.validate(
            checks=[{"type": "col_vals_gt", "column": "val", "value": 0}],
            get_extracts=True,
        )

        step = result["steps"][0]
        assert step["n_failed"] == 2
        assert "extracts" in step
        assert len(step["extracts"]) == 2
        # Failing values are -5 and -1
        extract_vals = sorted([r["val"] for r in step["extracts"]])
        assert extract_vals == [-5, -1]

    def test_validate_extracts_not_included_when_false(self):
        """Extracts not included by default."""
        df = pl.DataFrame({"val": [10, -5]})
        ws = Workspace()
        ws.load_df(df, name="test")

        result = ws.validate(
            checks=[{"type": "col_vals_gt", "column": "val", "value": 0}],
            get_extracts=False,
        )

        step = result["steps"][0]
        assert "extracts" not in step

    def test_validate_rows_distinct(self):
        """rows_distinct check works as a custom check."""
        df = pl.DataFrame({"a": [1, 1, 2], "b": ["x", "x", "y"]})
        ws = Workspace()
        ws.load_df(df, name="dups")

        result = ws.validate(checks=[{"type": "rows_distinct"}])

        assert result["all_passed"] is False

    def test_validate_rows_complete(self):
        """rows_complete check detects incomplete rows."""
        df = pl.DataFrame({"a": [1, None, 3], "b": ["x", "y", None]})
        ws = Workspace()
        ws.load_df(df, name="incomplete")

        result = ws.validate(checks=[{"type": "rows_complete"}])

        assert result["all_passed"] is False
        assert result["steps"][0]["n_failed"] == 2

    def test_get_sundered_data(self):
        """get_sundered_data splits into pass/fail DataFrames."""
        df = pl.DataFrame(
            {
                "id": [1, 2, 3, 4],
                "name": ["Alice", None, "Charlie", "Dave"],
                "score": [90.0, 85.0, None, 70.0],
            }
        )
        ws = Workspace()
        ws.load_df(df, name="mixed")

        sundered = ws.get_sundered_data()

        assert "pass" in sundered
        assert "fail" in sundered
        # Row 1 and 4 pass all non-null checks
        assert sundered["pass"].shape[0] == 2
        # Rows 2 and 3 have nulls
        assert sundered["fail"].shape[0] == 2

    def test_get_sundered_data_all_clean(self):
        """Sundered data with all clean rows returns empty fail."""
        df = pl.DataFrame({"a": [1, 2, 3], "b": ["x", "y", "z"]})
        ws = Workspace()
        ws.load_df(df, name="clean")

        sundered = ws.get_sundered_data()

        assert sundered["pass"].shape[0] == 3
        assert sundered["fail"].shape[0] == 0


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
        val = ws.validate(checks=[{"type": "col_vals_not_null", "column": "email"}])
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


# =============================================================================
# Semantic Type Detection
# =============================================================================


class TestDetectTypes:
    """Tests for Workspace.detect_types()."""

    def test_detect_iso_dates(self):
        """Detects ISO date strings."""
        df = pl.DataFrame(
            {
                "date_col": [
                    "2024-01-01",
                    "2024-02-15",
                    "2024-03-20",
                    "2024-04-10",
                    "2024-05-05",
                ],
                "other": ["hello", "world", "foo", "bar", "baz"],
            }
        )
        ws = Workspace()
        ws.load_df(df, name="dates")

        result = ws.detect_types()
        suggestions = {s["column"]: s for s in result["suggestions"]}

        assert suggestions["date_col"]["detected_type"] == "iso_date"
        assert suggestions["date_col"]["confidence"] == 1.0
        assert "pl.Date" in suggestions["date_col"]["suggestion"]
        assert suggestions["other"]["detected_type"] is None

    def test_detect_emails(self):
        """Detects email patterns and flags PII."""
        df = pl.DataFrame(
            {
                "email": ["alice@example.com", "bob@test.org", "charlie@foo.io"],
            }
        )
        ws = Workspace()
        ws.load_df(df, name="emails")

        result = ws.detect_types()
        email_s = result["suggestions"][0]

        assert email_s["detected_type"] == "email"
        assert email_s["pii"] is True

    def test_detect_urls(self):
        """Detects URL patterns."""
        df = pl.DataFrame(
            {
                "url": [
                    "https://example.com/page1",
                    "http://test.org/path",
                    "https://foo.io/bar",
                    "https://baz.dev/qux",
                ],
            }
        )
        ws = Workspace()
        ws.load_df(df, name="urls")

        result = ws.detect_types()
        assert result["suggestions"][0]["detected_type"] == "url"

    def test_detect_integers_in_strings(self):
        """Detects integer-like strings."""
        df = pl.DataFrame({"str_nums": ["100", "200", "300", "400", "500"]})
        ws = Workspace()
        ws.load_df(df, name="nums")

        result = ws.detect_types()
        s = result["suggestions"][0]
        assert s["detected_type"] == "integer"
        assert "Int64" in s["suggestion"]

    def test_detect_booleans_in_strings(self):
        """Detects boolean-like strings."""
        df = pl.DataFrame({"flag": ["true", "false", "true", "false", "true"]})
        ws = Workspace()
        ws.load_df(df, name="bools")

        result = ws.detect_types()
        s = result["suggestions"][0]
        assert s["detected_type"] == "boolean"
        assert "Boolean" in s["suggestion"]

    def test_pii_from_column_name(self):
        """Flags PII based on column name patterns."""
        df = pl.DataFrame(
            {
                "ssn": ["123-45-6789", "987-65-4321", "555-12-3456"],
                "phone_number": ["+1-555-0100", "+1-555-0200", "+1-555-0300"],
                "safe_col": [1, 2, 3],
            }
        )
        ws = Workspace()
        ws.load_df(df, name="pii_test")

        result = ws.detect_types()
        suggestions = {s["column"]: s for s in result["suggestions"]}

        assert suggestions["ssn"]["pii"] is True
        assert suggestions["phone_number"]["pii"] is True
        assert suggestions["safe_col"]["pii"] is False

    def test_detect_types_non_string_cols_skipped(self):
        """Non-string columns don't get cast suggestions."""
        df = pl.DataFrame(
            {
                "int_col": [1, 2, 3],
                "float_col": [1.0, 2.0, 3.0],
            }
        )
        ws = Workspace()
        ws.load_df(df, name="numeric")

        result = ws.detect_types()
        for s in result["suggestions"]:
            assert s["detected_type"] is None
            assert s["suggestion"] is None

    def test_detect_types_no_data_raises(self):
        """detect_types raises ValueError with no data."""
        ws = Workspace()
        with pytest.raises(ValueError, match="No active sheet"):
            ws.detect_types()

    def test_detect_mixed_patterns_below_threshold(self):
        """Mixed patterns below 80% threshold don't trigger detection."""
        df = pl.DataFrame(
            {
                "mixed": ["2024-01-01", "hello", "2024-03-20", "world", "2024-05-05"],
            }
        )
        ws = Workspace()
        ws.load_df(df, name="mixed")

        result = ws.detect_types()
        # Only 3/5 = 60% match iso_date, below threshold
        assert result["suggestions"][0]["detected_type"] is None


# =============================================================================
# Outlier Detection
# =============================================================================


class TestDetectOutliers:
    """Tests for Workspace.detect_outliers()."""

    def test_iqr_detects_outliers(self):
        """IQR method detects extreme values."""
        # Normal values + one extreme outlier
        df = pl.DataFrame({"values": [10.0, 12.0, 11.0, 13.0, 10.5, 100.0]})
        ws = Workspace()
        ws.load_df(df, name="outliers")

        result = ws.detect_outliers(method="iqr")

        assert result["method"] == "iqr"
        assert result["threshold"] == 1.5
        assert len(result["columns"]) == 1
        assert result["columns"][0]["n_outliers"] >= 1
        # The outlier at index 5 (value 100.0) should be detected
        assert 5 in result["columns"][0]["outlier_indices"]

    def test_zscore_detects_outliers(self):
        """Z-score method detects outliers beyond threshold."""
        df = pl.DataFrame({"values": [10.0, 11.0, 12.0, 10.0, 11.0, 50.0]})
        ws = Workspace()
        ws.load_df(df, name="outliers")

        result = ws.detect_outliers(method="zscore", threshold=2.0)

        assert result["method"] == "zscore"
        assert result["threshold"] == 2.0
        assert result["columns"][0]["n_outliers"] >= 1

    def test_no_outliers_in_uniform_data(self):
        """Uniform data has no outliers."""
        df = pl.DataFrame({"values": [10.0, 10.0, 10.0, 10.0, 10.0]})
        ws = Workspace()
        ws.load_df(df, name="uniform")

        result = ws.detect_outliers(method="iqr")

        if result["columns"]:
            assert result["columns"][0]["n_outliers"] == 0

    def test_skips_non_numeric_columns(self):
        """Non-numeric columns are not analyzed."""
        df = pl.DataFrame(
            {
                "name": ["Alice", "Bob", "Charlie"],
                "score": [85.0, 92.0, 78.0],
            }
        )
        ws = Workspace()
        ws.load_df(df, name="mixed")

        result = ws.detect_outliers()

        col_names = [c["column"] for c in result["columns"]]
        assert "name" not in col_names

    def test_multiple_numeric_columns(self):
        """Analyzes multiple numeric columns independently."""
        df = pl.DataFrame(
            {
                "a": [1.0, 2.0, 3.0, 4.0, 100.0],
                "b": [10, 11, 12, 13, 14],
            }
        )
        ws = Workspace()
        ws.load_df(df, name="multi")

        result = ws.detect_outliers()
        col_map = {c["column"]: c for c in result["columns"]}

        # Column 'a' has an outlier (100.0), 'b' does not
        assert col_map["a"]["n_outliers"] >= 1
        assert col_map["b"]["n_outliers"] == 0

    def test_invalid_method_raises(self):
        """Invalid method raises ValueError."""
        df = pl.DataFrame({"x": [1.0, 2.0, 3.0, 4.0, 5.0]})
        ws = Workspace()
        ws.load_df(df, name="test")

        with pytest.raises(ValueError, match="Unknown outlier method"):
            ws.detect_outliers(method="invalid")

    def test_no_data_raises(self):
        """detect_outliers raises ValueError with no data."""
        ws = Workspace()
        with pytest.raises(ValueError, match="No active sheet"):
            ws.detect_outliers()

    def test_outlier_result_structure(self):
        """Result has expected keys."""
        df = pl.DataFrame({"x": [1.0, 2.0, 3.0, 4.0, 50.0]})
        ws = Workspace()
        ws.load_df(df, name="test")

        result = ws.detect_outliers()

        assert "name" in result
        assert "method" in result
        assert "threshold" in result
        assert "columns" in result
        if result["columns"]:
            col = result["columns"][0]
            assert "column" in col
            assert "n_outliers" in col
            assert "lower_bound" in col
            assert "upper_bound" in col
            assert "outlier_indices" in col


# =============================================================================
# Natural Language Description
# =============================================================================


class TestDescribe:
    """Tests for Workspace.describe()."""

    def test_describe_basic(self):
        """Describe returns a non-empty string."""
        df = pl.DataFrame(
            {
                "id": [1, 2, 3],
                "name": ["Alice", "Bob", "Charlie"],
                "score": [85.5, 92.0, 78.3],
            }
        )
        ws = Workspace()
        ws.load_df(df, name="students")

        desc = ws.describe()

        assert isinstance(desc, str)
        assert len(desc) > 50
        assert "students" in desc
        assert "3" in desc  # rows

    def test_describe_includes_completeness(self):
        """Describe mentions missing values when present."""
        df = pl.DataFrame(
            {
                "a": [1, 2, None],
                "b": ["x", None, "z"],
            }
        )
        ws = Workspace()
        ws.load_df(df, name="incomplete")

        desc = ws.describe()
        assert "missing" in desc.lower() or "Missing" in desc

    def test_describe_no_nulls_message(self):
        """Describe notes when data is fully complete."""
        df = pl.DataFrame({"x": [1, 2, 3], "y": ["a", "b", "c"]})
        ws = Workspace()
        ws.load_df(df, name="complete")

        desc = ws.describe()
        assert "complete" in desc.lower() or "no missing" in desc.lower()

    def test_describe_numeric_ranges(self):
        """Describe includes numeric column highlights."""
        df = pl.DataFrame({"price": [10.0, 50.0, 100.0, 200.0, 500.0]})
        ws = Workspace()
        ws.load_df(df, name="prices")

        desc = ws.describe()
        assert "price" in desc
        assert "10" in desc
        assert "500" in desc

    def test_describe_categorical_cardinality(self):
        """Describe mentions low-cardinality string columns."""
        df = pl.DataFrame(
            {
                "status": ["active", "inactive", "active", "pending", "active"],
            }
        )
        ws = Workspace()
        ws.load_df(df, name="statuses")

        desc = ws.describe()
        assert "status" in desc
        assert "3" in desc  # 3 unique values

    def test_describe_duplicates(self):
        """Describe mentions duplicate rows when present."""
        df = pl.DataFrame(
            {
                "x": [1, 2, 3, 1, 2],
                "y": ["a", "b", "c", "a", "b"],
            }
        )
        ws = Workspace()
        ws.load_df(df, name="dupes")

        desc = ws.describe()
        assert "duplicate" in desc.lower()

    def test_describe_no_data_raises(self):
        """describe raises ValueError with no data."""
        ws = Workspace()
        with pytest.raises(ValueError, match="No active sheet"):
            ws.describe()


# =============================================================================
# Integration: Full Phase 2 Workflow
# =============================================================================


class TestPhase2Workflow:
    """End-to-end Phase 2 workflows."""

    def test_detect_types_then_cast(self):
        """Detect date strings, apply cast, validate result."""
        df = pl.DataFrame(
            {
                "event_date": ["2024-01-15", "2024-02-20", "2024-03-10"],
                "amount": [100.0, 200.0, 300.0],
            }
        )
        ws = Workspace()
        ws.load_df(df, name="events")

        # Detect: event_date is iso_date
        types = ws.detect_types()
        date_col = next(s for s in types["suggestions"] if s["column"] == "event_date")
        assert date_col["detected_type"] == "iso_date"

        # Cast it
        ws.transform("df.with_columns(pl.col('event_date').str.to_date('%Y-%m-%d'))")

        # Schema now shows Date
        schema = ws.schema_info()
        col_map = {c["name"]: c["dtype"] for c in schema["columns"]}
        assert "Date" in col_map["event_date"]

    def test_detect_outliers_then_clip(self):
        """Detect outliers, clip them, verify they're gone."""
        df = pl.DataFrame({"value": [10.0, 11.0, 12.0, 13.0, 14.0, 100.0, -50.0]})
        ws = Workspace()
        ws.load_df(df, name="data")

        # Detect outliers
        outliers = ws.detect_outliers()
        assert outliers["columns"][0]["n_outliers"] >= 2

        # Clip to bounds
        lower = outliers["columns"][0]["lower_bound"]
        upper = outliers["columns"][0]["upper_bound"]
        ws.transform(f"df.with_columns(pl.col('value').clip({lower}, {upper}))")

        # No outliers now
        outliers_after = ws.detect_outliers()
        if outliers_after["columns"]:
            assert outliers_after["columns"][0]["n_outliers"] == 0

    def test_full_data_understanding_pipeline(self):
        """Full pipeline: scan → detect types → validate → describe."""
        df = pl.DataFrame(
            {
                "user_id": [1, 2, 3, 4, 5],
                "email": ["a@b.com", "c@d.com", None, "e@f.com", "g@h.com"],
                "join_date": [
                    "2024-01-01",
                    "2024-02-15",
                    "2024-03-20",
                    "2024-04-10",
                    "2024-05-05",
                ],
                "score": [85.0, 92.0, 78.0, 95.0, 88.0],
            }
        )
        ws = Workspace()
        ws.load_df(df, name="users")

        # 1. Scan for overview
        scan = ws.scan()
        assert scan["shape"] == (5, 4)

        # 2. Detect types
        types = ws.detect_types()
        suggestions = {s["column"]: s for s in types["suggestions"]}
        assert suggestions["join_date"]["detected_type"] == "iso_date"
        assert suggestions["email"]["pii"] is True

        # 3. Validate
        val = ws.validate(
            checks=[
                {"type": "col_vals_not_null", "column": "email"},
                {"type": "col_vals_gt", "column": "score", "value": 0},
            ]
        )
        assert val["steps"][0]["n_failed"] == 1  # 1 null email

        # 4. Describe
        desc = ws.describe()
        assert "users" in desc
        assert "5" in desc
