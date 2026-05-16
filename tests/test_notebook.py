"""Tests for sweet.notebook — Notebook integration (widget + magic)."""

from __future__ import annotations

import polars as pl
import pytest

from sweet.notebook import SweetWidget, _ensure_polars, _parse_magic_args


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

SAMPLE_DF = pl.DataFrame(
    {
        "name": ["Alice", "Bob", "Carol", "Dave"],
        "age": [30, None, 35, 28],
        "score": [95.5, 87.3, None, 92.1],
    }
)


# ---------------------------------------------------------------------------
# SweetWidget
# ---------------------------------------------------------------------------


class TestSweetWidget:
    def test_basic_creation(self):
        widget = SweetWidget(SAMPLE_DF)
        assert widget.df is SAMPLE_DF
        assert widget.max_rows == 10
        assert widget.title is None

    def test_with_title(self):
        widget = SweetWidget(SAMPLE_DF, title="My Data")
        assert widget.title == "My Data"

    def test_custom_max_rows(self):
        widget = SweetWidget(SAMPLE_DF, max_rows=2)
        assert widget.max_rows == 2

    def test_repr_html(self):
        widget = SweetWidget(SAMPLE_DF, title="Test Table")
        html = widget._repr_html_()
        assert "sweet-widget" in html
        assert "Test Table" in html
        assert "4 rows" in html
        assert "3 cols" in html

    def test_repr_html_shape_badge(self):
        widget = SweetWidget(SAMPLE_DF)
        html = widget._repr_html_()
        assert "4 rows" in html

    def test_shows_null_info(self):
        widget = SweetWidget(SAMPLE_DF, show_profile=True)
        html = widget._repr_html_()
        # Should mention null columns
        assert "Nulls" in html
        assert "age" in html

    def test_no_profile(self):
        widget = SweetWidget(SAMPLE_DF, show_profile=False)
        html = widget._repr_html_()
        # Should not show null warning
        assert "Nulls" not in html

    def test_no_schema(self):
        widget = SweetWidget(SAMPLE_DF, show_schema=False)
        html = widget._repr_html_()
        # Should still render without schema section
        assert "sweet-widget" in html

    def test_schema_shows_type_counts(self):
        widget = SweetWidget(SAMPLE_DF, show_schema=True)
        html = widget._repr_html_()
        # Should show type badges
        assert "String" in html or "Utf8" in html or "Int" in html or "Float" in html

    def test_truncation_message(self):
        large_df = pl.DataFrame({"x": list(range(100))})
        widget = SweetWidget(large_df, max_rows=5)
        html = widget._repr_html_()
        assert "95 more rows" in html

    def test_no_truncation_small_df(self):
        widget = SweetWidget(SAMPLE_DF, max_rows=10)
        html = widget._repr_html_()
        assert "more rows" not in html

    def test_to_workspace(self):
        from sweet.core.workspace import Workspace

        widget = SweetWidget(SAMPLE_DF, title="Test")
        ws = widget.to_workspace()
        assert isinstance(ws, Workspace)
        assert ws.shape == (4, 3)

    def test_to_workspace_custom_name(self):
        widget = SweetWidget(SAMPLE_DF)
        ws = widget.to_workspace(name="my_sheet")
        info = ws.inspect()
        assert info["name"] == "my_sheet"

    def test_to_great_table(self):
        from great_tables import GT

        widget = SweetWidget(SAMPLE_DF)
        gt = widget.to_great_table(title="Widget Export")
        assert isinstance(gt, GT)

    def test_inspect(self):
        widget = SweetWidget(SAMPLE_DF, title="Inspectable")
        info = widget.inspect()
        assert info["shape"] == (4, 3)

    def test_profile(self):
        widget = SweetWidget(SAMPLE_DF)
        profile = widget.profile()
        assert isinstance(profile, str)
        assert "4 rows" in profile


# ---------------------------------------------------------------------------
# _ensure_polars
# ---------------------------------------------------------------------------


class TestEnsurePolars:
    def test_polars_passthrough(self):
        result = _ensure_polars(SAMPLE_DF)
        assert result is SAMPLE_DF

    def test_from_dict(self):
        result = _ensure_polars({"a": [1, 2], "b": [3, 4]})
        assert isinstance(result, pl.DataFrame)
        assert result.height == 2

    def test_from_list_of_dicts(self):
        result = _ensure_polars([{"x": 1}, {"x": 2}])
        assert isinstance(result, pl.DataFrame)
        assert result.height == 2

    def test_invalid_type(self):
        with pytest.raises(TypeError, match="Cannot create SweetWidget"):
            _ensure_polars("not a dataframe")

    def test_pandas_conversion(self):
        try:
            import pandas as pd

            pdf = pd.DataFrame({"col": [1, 2, 3]})
            result = _ensure_polars(pdf)
            assert isinstance(result, pl.DataFrame)
            assert result.height == 3
        except ImportError:
            pytest.skip("pandas not installed")


# ---------------------------------------------------------------------------
# Magic argument parsing
# ---------------------------------------------------------------------------


class TestParseMagicArgs:
    def test_simple_variable(self):
        result = _parse_magic_args("df")
        assert result == {"var": "df"}

    def test_with_title(self):
        result = _parse_magic_args("df --title 'My Title'")
        assert result["var"] == "df"
        assert result["title"] == "My Title"

    def test_with_rows(self):
        result = _parse_magic_args("df --rows 20")
        assert result["var"] == "df"
        assert result["rows"] == 20

    def test_with_no_profile(self):
        result = _parse_magic_args("df --no-profile")
        assert result["var"] == "df"
        assert result["profile"] is False

    def test_combined_flags(self):
        result = _parse_magic_args("my_data --title 'Data' --rows 5 --no-profile")
        assert result["var"] == "my_data"
        assert result["title"] == "Data"
        assert result["rows"] == 5
        assert result["profile"] is False

    def test_empty_input(self):
        result = _parse_magic_args("")
        assert result == {}

    def test_invalid_rows_defaults(self):
        result = _parse_magic_args("df --rows abc")
        assert result["rows"] == 10


# ---------------------------------------------------------------------------
# IPython Magic (integration test)
# ---------------------------------------------------------------------------


ipython = pytest.importorskip("IPython")


class TestIPythonMagic:
    def test_magic_class_exists(self):
        from sweet.notebook import SweetMagics

        assert hasattr(SweetMagics, "sweet")

    def test_load_extension(self):
        from IPython.testing.globalipapp import get_ipython

        ip = get_ipython()
        from sweet.notebook import load_ipython_extension

        load_ipython_extension(ip)
        # Should register without error
        assert "SweetMagics" in [type(m).__name__ for m in ip.magics_manager.registry.values()]

    def test_magic_with_dataframe(self):
        from IPython.testing.globalipapp import get_ipython

        ip = get_ipython()
        from sweet.notebook import load_ipython_extension

        load_ipython_extension(ip)

        # Put a DataFrame in the namespace
        ip.user_ns["test_df"] = SAMPLE_DF

        # Run the magic (captures display output)
        ip.run_line_magic("sweet", "test_df")
        # If it doesn't raise, it worked


# ---------------------------------------------------------------------------
# Package-level access
# ---------------------------------------------------------------------------


class TestPackageAccess:
    def test_import_from_package(self):
        from sweet import SweetWidget as SW

        assert SW is SweetWidget

    def test_load_ext_entry_point(self):
        import sweet

        assert hasattr(sweet, "load_ipython_extension")
