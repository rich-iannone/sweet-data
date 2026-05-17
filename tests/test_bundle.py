"""Tests for sweet.core.bundle — Shareable workspace bundles."""

from __future__ import annotations

import json
import zipfile
from pathlib import Path

import polars as pl
import pytest

from sweet.core.bundle import (
    BUNDLE_EXTENSION,
    BUNDLE_VERSION,
    inspect_bundle,
    load_bundle,
    save_bundle,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

SAMPLE_DF = pl.DataFrame(
    {
        "id": [1, 2, 3],
        "name": ["Alice", "Bob", "Carol"],
        "score": [95.5, 87.3, 76.0],
    }
)

SECOND_DF = pl.DataFrame(
    {
        "product": ["Widget", "Gadget"],
        "price": [9.99, 19.99],
    }
)


@pytest.fixture
def workspace():
    from sweet.core.workspace import Workspace

    ws = Workspace()
    ws.load_df(SAMPLE_DF, name="data")
    return ws


@pytest.fixture
def workspace_with_transforms():
    from sweet.core.workspace import Workspace

    ws = Workspace()
    ws.load_df(SAMPLE_DF, name="data")
    ws.transform("df.filter(pl.col('score') > 80)", description="filter high scores")
    return ws


@pytest.fixture
def multi_sheet_workspace():
    from sweet.core.workspace import Workspace

    ws = Workspace()
    ws.load_df(SAMPLE_DF, name="people")
    ws.load_df(SECOND_DF, name="products")
    ws.switch("people")
    return ws


# ---------------------------------------------------------------------------
# save_bundle
# ---------------------------------------------------------------------------


class TestSaveBundle:
    def test_creates_file(self, workspace, tmp_path):
        out = save_bundle(workspace, tmp_path / "test")
        assert out.exists()
        assert out.suffix == BUNDLE_EXTENSION

    def test_adds_extension(self, workspace, tmp_path):
        out = save_bundle(workspace, tmp_path / "test")
        assert out.name == "test.sweet"

    def test_keeps_extension(self, workspace, tmp_path):
        out = save_bundle(workspace, tmp_path / "test.sweet")
        assert out.name == "test.sweet"

    def test_is_valid_zip(self, workspace, tmp_path):
        out = save_bundle(workspace, tmp_path / "test")
        assert zipfile.is_zipfile(out)

    def test_contains_manifest(self, workspace, tmp_path):
        out = save_bundle(workspace, tmp_path / "test")
        with zipfile.ZipFile(out, "r") as zf:
            manifest = json.loads(zf.read("manifest.json"))
            assert manifest["bundle_version"] == BUNDLE_VERSION
            assert len(manifest["sheets"]) == 1
            assert manifest["sheets"][0]["name"] == "data"

    def test_contains_data(self, workspace, tmp_path):
        out = save_bundle(workspace, tmp_path / "test")
        with zipfile.ZipFile(out, "r") as zf:
            assert "data/data.parquet" in zf.namelist()

    def test_contains_transforms(self, workspace_with_transforms, tmp_path):
        out = save_bundle(workspace_with_transforms, tmp_path / "test")
        with zipfile.ZipFile(out, "r") as zf:
            transforms = json.loads(zf.read("transforms.json"))
            assert "data" in transforms
            assert len(transforms["data"]) > 0

    def test_contains_journal(self, workspace_with_transforms, tmp_path):
        out = save_bundle(workspace_with_transforms, tmp_path / "test")
        with zipfile.ZipFile(out, "r") as zf:
            journal = json.loads(zf.read("journal.json"))
            assert len(journal) > 0

    def test_no_journal_option(self, workspace_with_transforms, tmp_path):
        out = save_bundle(workspace_with_transforms, tmp_path / "test", include_journal=False)
        with zipfile.ZipFile(out, "r") as zf:
            journal = json.loads(zf.read("journal.json"))
            assert len(journal) == 0

    def test_description(self, workspace, tmp_path):
        out = save_bundle(workspace, tmp_path / "test", description="My analysis")
        with zipfile.ZipFile(out, "r") as zf:
            manifest = json.loads(zf.read("manifest.json"))
            assert manifest["description"] == "My analysis"

    def test_multi_sheet(self, multi_sheet_workspace, tmp_path):
        out = save_bundle(multi_sheet_workspace, tmp_path / "test")
        with zipfile.ZipFile(out, "r") as zf:
            manifest = json.loads(zf.read("manifest.json"))
            assert len(manifest["sheets"]) == 2
            names = {s["name"] for s in manifest["sheets"]}
            assert names == {"people", "products"}

    def test_empty_workspace_raises(self, tmp_path):
        from sweet.core.workspace import Workspace

        ws = Workspace()
        with pytest.raises(ValueError, match="no sheets"):
            save_bundle(ws, tmp_path / "test")


# ---------------------------------------------------------------------------
# load_bundle
# ---------------------------------------------------------------------------


class TestLoadBundle:
    def test_roundtrip(self, workspace, tmp_path):
        out = save_bundle(workspace, tmp_path / "test")
        bundle = load_bundle(out)
        assert "data" in bundle["sheets"]
        assert bundle["sheets"]["data"].equals(SAMPLE_DF)

    def test_roundtrip_with_transforms(self, workspace_with_transforms, tmp_path):
        out = save_bundle(workspace_with_transforms, tmp_path / "test")
        bundle = load_bundle(out)
        assert len(bundle["transforms"]["data"]) > 0

    def test_roundtrip_multi_sheet(self, multi_sheet_workspace, tmp_path):
        out = save_bundle(multi_sheet_workspace, tmp_path / "test")
        bundle = load_bundle(out)
        assert len(bundle["sheets"]) == 2
        assert "people" in bundle["sheets"]
        assert "products" in bundle["sheets"]

    def test_manifest_preserved(self, workspace, tmp_path):
        out = save_bundle(workspace, tmp_path / "test", description="Test bundle")
        bundle = load_bundle(out)
        assert bundle["manifest"]["description"] == "Test bundle"

    def test_file_not_found(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            load_bundle(tmp_path / "nonexistent.sweet")

    def test_invalid_zip(self, tmp_path):
        bad_file = tmp_path / "bad.sweet"
        bad_file.write_text("not a zip file")
        with pytest.raises(ValueError, match="Invalid"):
            load_bundle(bad_file)

    def test_journal_preserved(self, workspace_with_transforms, tmp_path):
        out = save_bundle(workspace_with_transforms, tmp_path / "test")
        bundle = load_bundle(out)
        assert len(bundle["journal"]) > 0
        assert "kind" in bundle["journal"][0]


# ---------------------------------------------------------------------------
# inspect_bundle
# ---------------------------------------------------------------------------


class TestInspectBundle:
    def test_inspect_metadata(self, workspace, tmp_path):
        out = save_bundle(workspace, tmp_path / "test", description="Inspectable")
        info = inspect_bundle(out)
        assert info["manifest"]["description"] == "Inspectable"
        assert info["file_size"] > 0

    def test_inspect_data_sizes(self, workspace, tmp_path):
        out = save_bundle(workspace, tmp_path / "test")
        info = inspect_bundle(out)
        assert "data" in info["data_sizes"]
        assert info["data_sizes"]["data"] > 0

    def test_inspect_not_found(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            inspect_bundle(tmp_path / "missing.sweet")


# ---------------------------------------------------------------------------
# Workspace integration
# ---------------------------------------------------------------------------


class TestWorkspaceBundle:
    def test_workspace_save(self, workspace, tmp_path):
        result = workspace.save(tmp_path / "ws-bundle")
        assert result.exists()
        assert result.suffix == ".sweet"

    def test_workspace_open(self, workspace, tmp_path):
        from sweet.core.workspace import Workspace

        workspace.save(tmp_path / "ws-bundle")
        restored = Workspace.open(tmp_path / "ws-bundle.sweet")
        assert restored.sheet_names == workspace.sheet_names
        assert restored.current_sheet_name == workspace.current_sheet_name
        assert restored.df.equals(workspace.df)

    def test_workspace_open_with_transforms(self, workspace_with_transforms, tmp_path):
        from sweet.core.workspace import Workspace

        workspace_with_transforms.save(tmp_path / "ws-bundle")
        restored = Workspace.open(tmp_path / "ws-bundle.sweet")
        # Transform steps should be restored
        sheet = restored._workbook.sheets["data"]
        assert len(sheet.transform_steps) > 0

    def test_workspace_open_multi_sheet(self, multi_sheet_workspace, tmp_path):
        from sweet.core.workspace import Workspace

        multi_sheet_workspace.save(tmp_path / "ws-bundle")
        restored = Workspace.open(tmp_path / "ws-bundle.sweet")
        assert set(restored.sheet_names) == {"people", "products"}
        assert restored.current_sheet_name == "people"

    def test_workspace_inspect_bundle(self, workspace, tmp_path):
        from sweet.core.workspace import Workspace

        workspace.save(tmp_path / "info-bundle")
        info = Workspace.inspect_bundle(tmp_path / "info-bundle.sweet")
        assert info["manifest"]["bundle_version"] == BUNDLE_VERSION
        assert info["file_size"] > 0

    def test_roundtrip_data_integrity(self, tmp_path):
        """Verify data survives save → open cycle exactly."""
        from sweet.core.workspace import Workspace

        original = Workspace()
        df = pl.DataFrame(
            {
                "int_col": [1, 2, None],
                "float_col": [1.5, None, 3.5],
                "str_col": ["hello", "world", None],
                "bool_col": [True, False, True],
            }
        )
        original.load_df(df, name="mixed_types")
        original.save(tmp_path / "integrity")

        restored = Workspace.open(tmp_path / "integrity.sweet")
        assert restored.df.equals(df)
