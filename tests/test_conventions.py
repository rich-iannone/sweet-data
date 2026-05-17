"""Tests for sweet.core.conventions — Team conventions validation."""

from __future__ import annotations

from pathlib import Path

import polars as pl
import pytest

from sweet.core.conventions import (
    Conventions,
    NamingConventions,
    QualityConventions,
    TypeConventions,
    Violation,
    _check_naming,
    find_conventions_file,
    generate_default_yaml,
    load_conventions,
    validate,
)


# ---------------------------------------------------------------------------
# Naming validation
# ---------------------------------------------------------------------------


class TestCheckNaming:
    def test_snake_case_valid(self):
        assert _check_naming("user_name", "snake_case") is True
        assert _check_naming("id", "snake_case") is True
        assert _check_naming("total_amount_usd", "snake_case") is True

    def test_snake_case_invalid(self):
        assert _check_naming("userName", "snake_case") is False
        assert _check_naming("UserName", "snake_case") is False
        assert _check_naming("user-name", "snake_case") is False
        assert _check_naming("User_Name", "snake_case") is False

    def test_camel_case_valid(self):
        assert _check_naming("userName", "camelCase") is True
        assert _check_naming("id", "camelCase") is True
        assert _check_naming("totalAmountUsd", "camelCase") is True

    def test_camel_case_invalid(self):
        assert _check_naming("user_name", "camelCase") is False
        assert _check_naming("UserName", "camelCase") is False

    def test_pascal_case_valid(self):
        assert _check_naming("UserName", "PascalCase") is True
        assert _check_naming("Id", "PascalCase") is True

    def test_pascal_case_invalid(self):
        assert _check_naming("userName", "PascalCase") is False
        assert _check_naming("user_name", "PascalCase") is False

    def test_kebab_case_valid(self):
        assert _check_naming("user-name", "kebab-case") is True
        assert _check_naming("id", "kebab-case") is True

    def test_kebab_case_invalid(self):
        assert _check_naming("user_name", "kebab-case") is False
        assert _check_naming("userName", "kebab-case") is False

    def test_empty_convention(self):
        assert _check_naming("anything", "") is True

    def test_unknown_convention(self):
        assert _check_naming("anything", "UNKNOWN") is True


# ---------------------------------------------------------------------------
# Load conventions from YAML
# ---------------------------------------------------------------------------


class TestLoadConventions:
    def test_load_full_file(self, tmp_path):
        yaml_content = """\
naming:
  columns: snake_case
  sheets: kebab-case

types:
  dates: pl.Date
  money: pl.Int64
  ids: pl.Utf8

quality:
  max_null_pct: 5.0
  require_unique:
    - id
    - email
  banned_values:
    - "N/A"
    - "null"
    - ""
"""
        f = tmp_path / "conventions.yaml"
        f.write_text(yaml_content)

        conv = load_conventions(f)
        assert conv.naming.columns == "snake_case"
        assert conv.naming.sheets == "kebab-case"
        assert conv.types.dates == "pl.Date"
        assert conv.types.money == "pl.Int64"
        assert conv.types.ids == "pl.Utf8"
        assert conv.quality.max_null_pct == 5.0
        assert conv.quality.require_unique == ["id", "email"]
        assert "N/A" in conv.quality.banned_values
        assert "" in conv.quality.banned_values

    def test_load_minimal_file(self, tmp_path):
        f = tmp_path / "conventions.yaml"
        f.write_text("naming:\n  columns: snake_case\n")

        conv = load_conventions(f)
        assert conv.naming.columns == "snake_case"
        assert conv.naming.sheets == ""
        assert conv.quality.max_null_pct == 100.0

    def test_load_empty_file(self, tmp_path):
        f = tmp_path / "conventions.yaml"
        f.write_text("")

        conv = load_conventions(f)
        assert conv.naming.columns == ""

    def test_file_not_found(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            load_conventions(tmp_path / "missing.yaml")


# ---------------------------------------------------------------------------
# Find conventions file
# ---------------------------------------------------------------------------


class TestFindConventionsFile:
    def test_finds_in_current_dir(self, tmp_path):
        sweet_dir = tmp_path / ".sweet"
        sweet_dir.mkdir()
        conv_file = sweet_dir / "conventions.yaml"
        conv_file.write_text("naming:\n  columns: snake_case\n")

        result = find_conventions_file(tmp_path)
        assert result == conv_file

    def test_finds_in_parent(self, tmp_path):
        sweet_dir = tmp_path / ".sweet"
        sweet_dir.mkdir()
        conv_file = sweet_dir / "conventions.yaml"
        conv_file.write_text("naming:\n  columns: snake_case\n")

        child = tmp_path / "subdir" / "deep"
        child.mkdir(parents=True)

        result = find_conventions_file(child)
        assert result == conv_file

    def test_returns_none_when_missing(self, tmp_path):
        # Search in a tmp dir with no .sweet folder
        child = tmp_path / "empty"
        child.mkdir()
        result = find_conventions_file(child)
        assert result is None


# ---------------------------------------------------------------------------
# Validate
# ---------------------------------------------------------------------------


class TestValidate:
    @pytest.fixture
    def strict_conventions(self):
        return Conventions(
            naming=NamingConventions(columns="snake_case", sheets="snake_case"),
            types=TypeConventions(),
            quality=QualityConventions(
                max_null_pct=5.0,
                require_unique=["id"],
                banned_values=["N/A", "null", ""],
            ),
        )

    def test_all_pass(self, strict_conventions):
        df = pl.DataFrame({
            "id": [1, 2, 3],
            "user_name": ["Alice", "Bob", "Carol"],
            "score": [95.0, 87.0, 76.0],
        })
        violations = validate(df, strict_conventions, sheet_name="users")
        assert violations == []

    def test_column_naming_violation(self, strict_conventions):
        df = pl.DataFrame({
            "id": [1, 2, 3],
            "UserName": ["Alice", "Bob", "Carol"],
        })
        violations = validate(df, strict_conventions, sheet_name="users")
        naming_v = [v for v in violations if v.rule == "naming.columns"]
        assert len(naming_v) == 1
        assert "UserName" in naming_v[0].message

    def test_sheet_naming_violation(self, strict_conventions):
        df = pl.DataFrame({"id": [1, 2, 3]})
        violations = validate(df, strict_conventions, sheet_name="MySheet")
        naming_v = [v for v in violations if v.rule == "naming.sheets"]
        assert len(naming_v) == 1

    def test_null_percentage_violation(self, strict_conventions):
        df = pl.DataFrame({
            "id": [1, 2, 3, 4, 5, 6, 7, 8, 9, 10],
            "value": [1.0, None, None, None, None, None, None, None, None, None],
        })
        violations = validate(df, strict_conventions, sheet_name="data")
        null_v = [v for v in violations if v.rule == "quality.max_null_pct"]
        assert len(null_v) == 1
        assert null_v[0].severity == "error"
        assert "value" in null_v[0].message

    def test_require_unique_violation(self, strict_conventions):
        df = pl.DataFrame({
            "id": [1, 2, 2, 3],
            "name": ["a", "b", "c", "d"],
        })
        violations = validate(df, strict_conventions, sheet_name="data")
        unique_v = [v for v in violations if v.rule == "quality.require_unique"]
        assert len(unique_v) == 1
        assert "duplicate" in unique_v[0].message

    def test_banned_values_violation(self, strict_conventions):
        df = pl.DataFrame({
            "id": [1, 2, 3],
            "status": ["active", "N/A", "inactive"],
        })
        violations = validate(df, strict_conventions, sheet_name="data")
        banned_v = [v for v in violations if v.rule == "quality.banned_values"]
        assert len(banned_v) == 1
        assert "N/A" in banned_v[0].message

    def test_multiple_violations(self, strict_conventions):
        df = pl.DataFrame({
            "id": [1, 1, 3],  # duplicate
            "UserName": ["Alice", "N/A", "Carol"],  # naming + banned
        })
        violations = validate(df, strict_conventions, sheet_name="data")
        assert len(violations) >= 3  # naming + unique + banned

    def test_no_conventions(self):
        df = pl.DataFrame({"anything": [1, 2, 3]})
        conv = Conventions()
        violations = validate(df, conv)
        assert violations == []

    def test_empty_dataframe(self, strict_conventions):
        df = pl.DataFrame({"id": pl.Series([], dtype=pl.Int64)})
        violations = validate(df, strict_conventions, sheet_name="empty")
        # No null violations for empty df
        null_v = [v for v in violations if v.rule == "quality.max_null_pct"]
        assert len(null_v) == 0


# ---------------------------------------------------------------------------
# Generate default YAML
# ---------------------------------------------------------------------------


class TestGenerateDefaultYaml:
    def test_generates_valid_yaml(self, tmp_path):
        content = generate_default_yaml()
        f = tmp_path / "test.yaml"
        f.write_text(content)
        conv = load_conventions(f)
        assert conv.naming.columns == "snake_case"
        assert conv.quality.max_null_pct == 5.0

    def test_contains_expected_sections(self):
        content = generate_default_yaml()
        assert "naming:" in content
        assert "quality:" in content
        assert "snake_case" in content


# ---------------------------------------------------------------------------
# Workspace integration
# ---------------------------------------------------------------------------


class TestWorkspaceConventions:
    def test_load_and_check_pass(self, tmp_path):
        from sweet.core.workspace import Workspace

        sweet_dir = tmp_path / ".sweet"
        sweet_dir.mkdir()
        (sweet_dir / "conventions.yaml").write_text(
            "naming:\n  columns: snake_case\nquality:\n  max_null_pct: 10.0\n"
        )

        ws = Workspace()
        ws.load_df(pl.DataFrame({"user_id": [1, 2, 3], "name": ["a", "b", "c"]}), name="data")
        ws.load_conventions(sweet_dir / "conventions.yaml")
        violations = ws.check_conventions()
        assert violations == []

    def test_load_and_check_violations(self, tmp_path):
        from sweet.core.workspace import Workspace

        sweet_dir = tmp_path / ".sweet"
        sweet_dir.mkdir()
        (sweet_dir / "conventions.yaml").write_text(
            "naming:\n  columns: snake_case\nquality:\n  banned_values:\n    - 'N/A'\n"
        )

        ws = Workspace()
        ws.load_df(pl.DataFrame({"BadName": [1, 2], "status": ["ok", "N/A"]}), name="data")
        ws.load_conventions(sweet_dir / "conventions.yaml")
        violations = ws.check_conventions()
        assert len(violations) >= 2  # naming + banned

    def test_check_without_load_raises(self):
        from sweet.core.workspace import Workspace

        ws = Workspace()
        ws.load_df(pl.DataFrame({"x": [1]}), name="data")
        with pytest.raises(ValueError, match="No conventions loaded"):
            ws.check_conventions()
