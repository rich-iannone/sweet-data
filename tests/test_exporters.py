"""Tests for sweet.core.exporters — export destinations."""

from __future__ import annotations

from pathlib import Path

import polars as pl
import pytest

from sweet.core.exporters import (
    _redact_credentials,
    detect_dest_type,
    export_to,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

SAMPLE_DF = pl.DataFrame({"name": ["Alice", "Bob", "Carol"], "age": [30, 25, 35]})


# ---------------------------------------------------------------------------
# detect_dest_type
# ---------------------------------------------------------------------------


class TestDetectDestType:
    def test_local_file(self):
        assert detect_dest_type("output.csv") == "file"
        assert detect_dest_type("/tmp/data.parquet") == "file"
        assert detect_dest_type("./results/out.json") == "file"

    def test_cloud_storage(self):
        assert detect_dest_type("s3://bucket/path/data.parquet") == "cloud"
        assert detect_dest_type("gs://bucket/data.csv") == "cloud"
        assert detect_dest_type("az://container/blob.parquet") == "cloud"
        assert detect_dest_type("abfs://container/path.ipc") == "cloud"

    def test_database(self):
        assert detect_dest_type("postgresql://user:pass@host/db") == "database"
        assert detect_dest_type("postgres://user:pass@host/db") == "database"
        assert detect_dest_type("mysql://user:pass@host/db") == "database"
        assert detect_dest_type("sqlite:///path/to/file.db") == "database"
        assert detect_dest_type("duckdb:///path/to/file.duckdb") == "database"

    def test_http_raises(self):
        with pytest.raises(ValueError, match="Cannot export to HTTP"):
            detect_dest_type("https://example.com/upload")


# ---------------------------------------------------------------------------
# export_to — local files
# ---------------------------------------------------------------------------


class TestExportFile:
    def test_export_csv(self, tmp_path):
        dest = str(tmp_path / "out.csv")
        meta = export_to(SAMPLE_DF, dest)
        assert meta["dest_type"] == "file"
        assert meta["format"] == "csv"
        assert meta["rows"] == 3

        # Verify file was written
        loaded = pl.read_csv(dest)
        assert loaded.height == 3
        assert list(loaded.columns) == ["name", "age"]

    def test_export_parquet(self, tmp_path):
        dest = str(tmp_path / "out.parquet")
        meta = export_to(SAMPLE_DF, dest)
        assert meta["format"] == "parquet"

        loaded = pl.read_parquet(dest)
        assert loaded.height == 3

    def test_export_json(self, tmp_path):
        dest = str(tmp_path / "out.json")
        meta = export_to(SAMPLE_DF, dest)
        assert meta["format"] == "json"

        loaded = pl.read_json(dest)
        assert loaded.height == 3

    def test_export_ndjson(self, tmp_path):
        dest = str(tmp_path / "out.ndjson")
        meta = export_to(SAMPLE_DF, dest)
        assert meta["format"] == "ndjson"

        loaded = pl.read_ndjson(dest)
        assert loaded.height == 3

    def test_export_tsv(self, tmp_path):
        dest = str(tmp_path / "out.tsv")
        meta = export_to(SAMPLE_DF, dest)
        assert meta["format"] == "tsv"

        loaded = pl.read_csv(dest, separator="\t")
        assert loaded.height == 3

    def test_export_ipc(self, tmp_path):
        dest = str(tmp_path / "out.ipc")
        meta = export_to(SAMPLE_DF, dest)
        assert meta["format"] == "ipc"

        loaded = pl.read_ipc(dest)
        assert loaded.height == 3

    def test_forced_format(self, tmp_path):
        dest = str(tmp_path / "output.dat")
        meta = export_to(SAMPLE_DF, dest, format="csv")
        assert meta["format"] == "csv"

        loaded = pl.read_csv(dest)
        assert loaded.height == 3

    def test_creates_parent_dirs(self, tmp_path):
        dest = str(tmp_path / "deep" / "nested" / "out.csv")
        export_to(SAMPLE_DF, dest)
        assert Path(dest).exists()

    def test_unsupported_extension(self, tmp_path):
        with pytest.raises(ValueError, match="Cannot detect export format"):
            export_to(SAMPLE_DF, str(tmp_path / "out.xyz"))


# ---------------------------------------------------------------------------
# export_to — database (SQLite)
# ---------------------------------------------------------------------------


class TestExportDatabase:
    def test_export_to_sqlite_replace(self, tmp_path):
        import sqlite3

        db_path = tmp_path / "test.db"
        # Create the SQLite file
        sqlite3.connect(str(db_path)).close()

        meta = export_to(
            SAMPLE_DF,
            f"sqlite:///{db_path}",
            table="people",
        )
        assert meta["dest_type"] == "database"
        assert meta["table"] == "people"
        assert meta["mode"] == "replace"
        assert meta["rows"] == 3

        # Verify data was written
        conn = sqlite3.connect(str(db_path))
        rows = conn.execute("SELECT COUNT(*) FROM people").fetchone()[0]
        conn.close()
        assert rows == 3

    def test_export_to_sqlite_append(self, tmp_path):
        import sqlite3

        db_path = tmp_path / "test.db"
        sqlite3.connect(str(db_path)).close()

        # First write
        export_to(SAMPLE_DF, f"sqlite:///{db_path}", table="items")
        # Append
        export_to(SAMPLE_DF, f"sqlite:///{db_path}", table="items", mode="append")

        conn = sqlite3.connect(str(db_path))
        rows = conn.execute("SELECT COUNT(*) FROM items").fetchone()[0]
        conn.close()
        assert rows == 6

    def test_export_to_sqlite_fail_mode(self, tmp_path):
        import sqlite3

        db_path = tmp_path / "test.db"
        sqlite3.connect(str(db_path)).close()

        # First write succeeds
        export_to(SAMPLE_DF, f"sqlite:///{db_path}", table="unique_table", mode="fail")

        # Second write should fail
        with pytest.raises(Exception):
            export_to(SAMPLE_DF, f"sqlite:///{db_path}", table="unique_table", mode="fail")

    def test_default_table_name(self, tmp_path):
        import sqlite3

        db_path = tmp_path / "test.db"
        sqlite3.connect(str(db_path)).close()

        meta = export_to(SAMPLE_DF, f"sqlite:///{db_path}")
        assert meta["table"] == "exported_data"

    def test_invalid_mode(self, tmp_path):
        with pytest.raises(ValueError, match="Invalid mode"):
            export_to(SAMPLE_DF, "sqlite:///test.db", mode="bad")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class TestRedactCredentials:
    def test_redacts_password(self):
        url = "postgresql://user:secret@host:5432/db"
        assert _redact_credentials(url) == "postgresql://user:***@host:5432/db"

    def test_no_credentials(self):
        url = "sqlite:///path/to/file.db"
        assert _redact_credentials(url) == url


# ---------------------------------------------------------------------------
# Integration: Workspace.export()
# ---------------------------------------------------------------------------


class TestWorkspaceExportIntegration:
    def test_export_to_parquet(self, tmp_path):
        from sweet.core.workspace import Workspace

        csv_file = tmp_path / "input.csv"
        SAMPLE_DF.write_csv(csv_file)

        ws = Workspace()
        ws.load(str(csv_file))

        dest = str(tmp_path / "out.parquet")
        ws.export(dest)
        assert Path(dest).exists()
        assert pl.read_parquet(dest).height == 3

    def test_export_to_ndjson(self, tmp_path):
        from sweet.core.workspace import Workspace

        csv_file = tmp_path / "input.csv"
        SAMPLE_DF.write_csv(csv_file)

        ws = Workspace()
        ws.load(str(csv_file))

        dest = str(tmp_path / "out.ndjson")
        ws.export(dest)
        assert Path(dest).exists()
        assert pl.read_ndjson(dest).height == 3

    def test_export_to_sqlite(self, tmp_path):
        import sqlite3

        from sweet.core.workspace import Workspace

        csv_file = tmp_path / "input.csv"
        SAMPLE_DF.write_csv(csv_file)
        db_path = tmp_path / "out.db"
        sqlite3.connect(str(db_path)).close()

        ws = Workspace()
        ws.load(str(csv_file))
        ws.export(f"sqlite:///{db_path}", table="exported")

        conn = sqlite3.connect(str(db_path))
        rows = conn.execute("SELECT COUNT(*) FROM exported").fetchone()[0]
        conn.close()
        assert rows == 3

    def test_export_multiple_formats(self, tmp_path):
        from sweet.core.workspace import Workspace

        csv_file = tmp_path / "input.csv"
        SAMPLE_DF.write_csv(csv_file)

        ws = Workspace()
        ws.load(str(csv_file))

        for ext in ("csv", "parquet", "json", "ndjson", "tsv", "ipc"):
            dest = str(tmp_path / f"out.{ext}")
            ws.export(dest)
            assert Path(dest).exists(), f"Failed for format: {ext}"
