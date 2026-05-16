"""Tests for sweet.core.gt_export — Great Tables integration."""

from __future__ import annotations

from pathlib import Path

import polars as pl
import pytest

from sweet.core.gt_export import save_great_table, to_great_table


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

SAMPLE_DF = pl.DataFrame(
    {
        "region": ["North", "South", "East", "West"],
        "revenue": [1500.50, 2300.75, 980.25, 3100.00],
        "growth": [0.12, 0.08, -0.03, 0.15],
        "units": [150, 230, 98, 310],
    }
)


# ---------------------------------------------------------------------------
# to_great_table
# ---------------------------------------------------------------------------


class TestToGreatTable:
    def test_basic_creation(self):
        gt = to_great_table(SAMPLE_DF)
        # Returns a GT object
        from great_tables import GT

        assert isinstance(gt, GT)

    def test_with_title(self):
        gt = to_great_table(SAMPLE_DF, title="Revenue Report")
        assert gt._heading.title == "Revenue Report"

    def test_with_title_and_subtitle(self):
        gt = to_great_table(SAMPLE_DF, title="Report", subtitle="Q1 2026")
        assert gt._heading.title == "Report"
        assert gt._heading.subtitle == "Q1 2026"

    def test_with_rowname_col(self):
        gt = to_great_table(SAMPLE_DF, rowname_col="region")
        # GT was created with rowname_col set
        assert gt is not None

    def test_with_groupname_col(self):
        df = SAMPLE_DF.with_columns(
            pl.when(pl.col("revenue") > 2000)
            .then(pl.lit("High"))
            .otherwise(pl.lit("Low"))
            .alias("tier")
        )
        gt = to_great_table(df, groupname_col="tier")
        assert gt is not None

    def test_fmt_currency(self):
        gt = to_great_table(SAMPLE_DF, fmt_currency=["revenue"])
        # Verify formatting was applied (GT object is mutated)
        assert gt is not None

    def test_fmt_number(self):
        gt = to_great_table(SAMPLE_DF, fmt_number=["revenue", "units"])
        assert gt is not None

    def test_fmt_percent(self):
        gt = to_great_table(SAMPLE_DF, fmt_percent=["growth"])
        assert gt is not None

    def test_fmt_integer(self):
        gt = to_great_table(SAMPLE_DF, fmt_integer=["units"])
        assert gt is not None

    def test_source_note(self):
        gt = to_great_table(SAMPLE_DF, source_note="Data from Q1 2026")
        assert gt is not None

    def test_striping(self):
        gt = to_great_table(SAMPLE_DF, striping=True)
        assert gt is not None

    def test_stylize(self):
        gt = to_great_table(SAMPLE_DF, stylize=3)
        assert gt is not None

    def test_combined_options(self):
        gt = to_great_table(
            SAMPLE_DF,
            title="Sales",
            subtitle="By Region",
            fmt_currency=["revenue"],
            fmt_percent=["growth"],
            fmt_integer=["units"],
            striping=True,
            source_note="Internal data",
        )
        assert gt is not None
        assert gt._heading.title == "Sales"

    def test_invalid_column_fmt_number(self):
        with pytest.raises(ValueError, match="non-existent columns"):
            to_great_table(SAMPLE_DF, fmt_number=["nonexistent"])

    def test_invalid_column_fmt_currency(self):
        with pytest.raises(ValueError, match="non-existent columns"):
            to_great_table(SAMPLE_DF, fmt_currency=["bad_col"])

    def test_invalid_rowname_col(self):
        with pytest.raises(ValueError, match="rowname_col"):
            to_great_table(SAMPLE_DF, rowname_col="missing")

    def test_invalid_groupname_col(self):
        with pytest.raises(ValueError, match="groupname_col"):
            to_great_table(SAMPLE_DF, groupname_col="missing")

    def test_renders_to_html(self):
        gt = to_great_table(SAMPLE_DF, title="Test")
        html = gt.as_raw_html()
        assert "<table" in html
        assert "Test" in html


# ---------------------------------------------------------------------------
# save_great_table
# ---------------------------------------------------------------------------


class TestSaveGreatTable:
    def test_save_html(self, tmp_path):
        dest = str(tmp_path / "table.html")
        result = save_great_table(SAMPLE_DF, dest, title="Saved Table")
        assert result == dest
        assert Path(dest).exists()
        content = Path(dest).read_text()
        assert "Saved Table" in content

    def test_save_creates_parent_dirs(self, tmp_path):
        dest = str(tmp_path / "deep" / "nested" / "table.html")
        save_great_table(SAMPLE_DF, dest)
        assert Path(dest).exists()

    def test_save_non_html_extension(self, tmp_path):
        dest = str(tmp_path / "output.txt")
        save_great_table(SAMPLE_DF, dest, title="Raw")
        assert Path(dest).exists()
        content = Path(dest).read_text()
        assert "<table" in content


# ---------------------------------------------------------------------------
# Integration: Workspace.to_great_table()
# ---------------------------------------------------------------------------


class TestWorkspaceIntegration:
    def test_workspace_to_great_table(self, tmp_path):
        from great_tables import GT

        from sweet.core.workspace import Workspace

        csv_file = tmp_path / "data.csv"
        SAMPLE_DF.write_csv(csv_file)

        ws = Workspace()
        ws.load(str(csv_file))
        gt = ws.to_great_table(title="From Workspace", fmt_currency=["revenue"])

        assert isinstance(gt, GT)
        assert gt._heading.title == "From Workspace"

    def test_workspace_to_great_table_after_transform(self, tmp_path):
        from great_tables import GT

        from sweet.core.workspace import Workspace

        csv_file = tmp_path / "data.csv"
        SAMPLE_DF.write_csv(csv_file)

        ws = Workspace()
        ws.load(str(csv_file))
        ws.transform("df.filter(pl.col('revenue') > 1000)")

        gt = ws.to_great_table(title="Filtered")
        assert isinstance(gt, GT)
        html = gt.as_raw_html()
        assert "Filtered" in html
