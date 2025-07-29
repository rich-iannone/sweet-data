"""Tests for Sweet workbook functionality."""

from unittest.mock import Mock

import pytest

from sweet.core.transforms import TransformStep
from sweet.core.workbook import Sheet, Workbook


def test_workbook_creation():
    """Test creating an empty workbook."""
    wb = Workbook()
    assert len(wb.sheets) == 0
    assert wb.current_sheet_name is None
    assert wb.current_sheet is None


def test_add_sheet():
    """Test adding a sheet to workbook."""
    wb = Workbook()

    # Mock DataFrame to avoid polars dependency
    mock_df = Mock()
    sheet = wb.add_sheet("test_sheet", mock_df)

    assert len(wb.sheets) == 1
    assert "test_sheet" in wb.sheets
    assert wb.current_sheet_name == "test_sheet"
    assert wb.current_sheet == sheet
    assert sheet.name == "test_sheet"
    assert sheet.df == mock_df


def test_add_duplicate_sheet():
    """Test adding a sheet with duplicate name raises error."""
    wb = Workbook()
    wb.add_sheet("test_sheet")

    with pytest.raises(ValueError, match="Sheet 'test_sheet' already exists"):
        wb.add_sheet("test_sheet")


def test_set_current_sheet():
    """Test setting current sheet."""
    wb = Workbook()
    wb.add_sheet("sheet1")
    wb.add_sheet("sheet2")

    assert wb.current_sheet_name == "sheet1"  # First sheet is current

    wb.set_current_sheet("sheet2")
    assert wb.current_sheet_name == "sheet2"


def test_set_nonexistent_current_sheet():
    """Test setting current sheet to nonexistent sheet raises error."""
    wb = Workbook()

    with pytest.raises(ValueError, match="Sheet 'nonexistent' not found"):
        wb.set_current_sheet("nonexistent")


def test_remove_sheet():
    """Test removing a sheet."""
    wb = Workbook()
    wb.add_sheet("sheet1")
    wb.add_sheet("sheet2")

    wb.remove_sheet("sheet1")

    assert len(wb.sheets) == 1
    assert "sheet1" not in wb.sheets
    assert "sheet2" in wb.sheets
    assert wb.current_sheet_name == "sheet2"  # Should update to remaining sheet


def test_remove_current_sheet():
    """Test removing the current sheet updates current_sheet_name."""
    wb = Workbook()
    wb.add_sheet("sheet1")
    wb.add_sheet("sheet2")
    wb.set_current_sheet("sheet1")

    wb.remove_sheet("sheet1")

    assert wb.current_sheet_name == "sheet2"


def test_remove_last_sheet():
    """Test removing the last sheet sets current_sheet_name to None."""
    wb = Workbook()
    wb.add_sheet("only_sheet")

    wb.remove_sheet("only_sheet")

    assert len(wb.sheets) == 0
    assert wb.current_sheet_name is None


def test_sheet_creation():
    """Test creating a sheet."""
    sheet = Sheet("test_sheet")

    assert sheet.name == "test_sheet"
    assert sheet.df is None
    assert len(sheet.transform_steps) == 0
    assert len(sheet.extra_cells) == 0
    assert len(sheet.branches) == 0
    assert sheet.parent is None


def test_sheet_get_schema_empty():
    """Test getting schema of empty sheet."""
    sheet = Sheet("test_sheet")
    schema = sheet.get_schema()

    assert schema == {}


def test_transform_step_creation():
    """Test creating a transform step."""
    step = TransformStep(
        expr="df.filter(pl.col('age') > 30)",
        input_hash="abc123",
        output_schema={"name": "string", "age": "int"},
    )

    assert step.expr == "df.filter(pl.col('age') > 30)"
    assert step.input_hash == "abc123"
    assert step.output_schema == {"name": "string", "age": "int"}
    assert step.metadata == {}


def test_transform_step_with_metadata():
    """Test creating a transform step with metadata."""
    metadata = {"description": "Filter adults"}
    step = TransformStep(
        expr="df.filter(pl.col('age') > 30)",
        input_hash="abc123",
        output_schema={"name": "string", "age": "int"},
        metadata=metadata,
    )

    assert step.metadata == metadata


def test_export_polars_empty_workbook():
    """Test exporting empty workbook to Polars code."""
    wb = Workbook()
    code = wb.export_polars()

    assert "No sheets in workbook" in code


def test_export_polars_with_sheets():
    """Test exporting workbook with sheets to Polars code."""
    wb = Workbook()
    wb.add_sheet("sheet1")
    wb.add_sheet("sheet2")

    code = wb.export_polars()

    assert "Sweet Workbook Export" in code
    assert "import polars as pl" in code
    assert "Sheet: sheet1" in code
    assert "Sheet: sheet2" in code


def test_get_sheet_names():
    """Test getting list of sheet names."""
    wb = Workbook()
    assert wb.get_sheet_names() == []

    wb.add_sheet("sheet1")
    wb.add_sheet("sheet2")

    names = wb.get_sheet_names()
    assert set(names) == {"sheet1", "sheet2"}
