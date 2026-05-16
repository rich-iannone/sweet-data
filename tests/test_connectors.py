"""Tests for sweet.core.connectors — source connectors."""

from __future__ import annotations

import json
import textwrap
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from threading import Thread

import polars as pl
import pytest

from sweet.core.connectors import (
    _parse_html_tables,
    _redact_credentials,
    detect_source_type,
    load_source,
)


# ---------------------------------------------------------------------------
# detect_source_type
# ---------------------------------------------------------------------------


class TestDetectSourceType:
    def test_local_file(self):
        assert detect_source_type("data.csv") == "file"
        assert detect_source_type("/tmp/data.parquet") == "file"
        assert detect_source_type("./path/to/file.json") == "file"

    def test_url_data_file(self):
        assert detect_source_type("https://example.com/data.csv") == "url"
        assert detect_source_type("http://example.com/path/file.parquet") == "url"
        assert detect_source_type("https://cdn.example.com/dataset.json") == "url"

    def test_url_web_page(self):
        assert detect_source_type("https://en.wikipedia.org/wiki/Countries") == "web_table"
        assert detect_source_type("https://example.com/page") == "web_table"
        assert detect_source_type("http://example.com/table.html") == "web_table"

    def test_cloud_storage(self):
        assert detect_source_type("s3://bucket/path/data.csv") == "url"
        assert detect_source_type("gs://bucket/data.parquet") == "url"
        assert detect_source_type("az://container/blob.csv") == "url"

    def test_database(self):
        assert detect_source_type("postgresql://user:pass@host/db") == "database"
        assert detect_source_type("postgres://user:pass@host/db") == "database"
        assert detect_source_type("mysql://user:pass@host/db") == "database"
        assert detect_source_type("sqlite:///path/to/file.db") == "database"
        assert detect_source_type("duckdb:///path/to/file.duckdb") == "database"


# ---------------------------------------------------------------------------
# load_source — file
# ---------------------------------------------------------------------------


class TestLoadFile:
    def test_load_csv(self, tmp_path):
        csv_file = tmp_path / "test.csv"
        pl.DataFrame({"a": [1, 2, 3], "b": ["x", "y", "z"]}).write_csv(csv_file)

        df, meta = load_source(str(csv_file))
        assert df.height == 3
        assert df.width == 2
        assert meta["source_type"] == "file"
        assert meta["format"] == "csv"

    def test_load_parquet(self, tmp_path):
        pq_file = tmp_path / "test.parquet"
        pl.DataFrame({"x": [10, 20]}).write_parquet(pq_file)

        df, meta = load_source(str(pq_file))
        assert df.height == 2
        assert meta["format"] == "parquet"

    def test_load_json(self, tmp_path):
        json_file = tmp_path / "test.json"
        pl.DataFrame({"col": ["a", "b"]}).write_json(json_file)

        df, meta = load_source(str(json_file))
        assert df.height == 2
        assert meta["format"] == "json"

    def test_file_not_found(self):
        with pytest.raises(FileNotFoundError):
            load_source("/nonexistent/path.csv")

    def test_unsupported_extension(self, tmp_path):
        bad_file = tmp_path / "data.xyz"
        bad_file.write_text("hello")
        with pytest.raises(ValueError, match="Cannot detect format"):
            load_source(str(bad_file))


# ---------------------------------------------------------------------------
# load_source — URL (with local HTTP server)
# ---------------------------------------------------------------------------


class _CSVHandler(BaseHTTPRequestHandler):
    """Simple handler that serves CSV data."""

    csv_data = "name,value\nAlice,1\nBob,2\n"
    html_data = textwrap.dedent("""\
        <html><body>
        <table>
          <tr><th>City</th><th>Pop</th></tr>
          <tr><td>NYC</td><td>8000000</td></tr>
          <tr><td>LA</td><td>4000000</td></tr>
        </table>
        </body></html>
    """)

    def do_GET(self):
        if self.path.endswith(".csv"):
            self.send_response(200)
            self.send_header("Content-Type", "text/csv")
            self.end_headers()
            self.wfile.write(self.csv_data.encode())
        elif self.path == "/page":
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(self.html_data.encode())
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, format, *args):
        pass  # Suppress output


@pytest.fixture()
def local_server():
    """Start a local HTTP server for testing URL loading."""
    server = HTTPServer(("127.0.0.1", 0), _CSVHandler)
    port = server.server_address[1]
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{port}"
    server.shutdown()


class TestLoadUrl:
    def test_load_csv_url(self, local_server):
        df, meta = load_source(f"{local_server}/data.csv")
        assert df.height == 2
        assert list(df.columns) == ["name", "value"]
        assert meta["source_type"] == "url"
        assert meta["format"] == "csv"

    def test_unreachable_url(self):
        with pytest.raises(ConnectionError):
            load_source("http://127.0.0.1:1/nonexistent.csv")


# ---------------------------------------------------------------------------
# load_source — web tables
# ---------------------------------------------------------------------------


class TestLoadWebTable:
    def test_load_web_table(self, local_server):
        df, meta = load_source(f"{local_server}/page")
        assert df.height == 2
        assert "City" in df.columns
        assert meta["source_type"] == "web_table"
        assert meta["tables_found"] == 1

    def test_no_tables_found(self, local_server):
        # 404 page has no content
        with pytest.raises((ValueError, ConnectionError)):
            load_source(f"{local_server}/empty")


# ---------------------------------------------------------------------------
# load_source — SQLite database
# ---------------------------------------------------------------------------


class TestLoadDatabase:
    def test_load_sqlite_table(self, tmp_path):
        import sqlite3

        db_path = tmp_path / "test.db"
        conn = sqlite3.connect(str(db_path))
        conn.execute("CREATE TABLE users (id INTEGER, name TEXT)")
        conn.execute("INSERT INTO users VALUES (1, 'Alice'), (2, 'Bob')")
        conn.commit()
        conn.close()

        df, meta = load_source(
            f"sqlite:///{db_path}",
            table="users",
        )
        assert df.height == 2
        assert "name" in df.columns
        assert meta["source_type"] == "database"

    def test_load_sqlite_query(self, tmp_path):
        import sqlite3

        db_path = tmp_path / "test.db"
        conn = sqlite3.connect(str(db_path))
        conn.execute("CREATE TABLE items (id INTEGER, price REAL)")
        conn.execute("INSERT INTO items VALUES (1, 9.99), (2, 19.99), (3, 29.99)")
        conn.commit()
        conn.close()

        df, meta = load_source(
            f"sqlite:///{db_path}",
            query="SELECT * FROM remote_db.items WHERE price > 10",
        )
        assert df.height == 2
        assert meta["query"] is not None


# ---------------------------------------------------------------------------
# HTML table parsing
# ---------------------------------------------------------------------------


class TestParseHtmlTables:
    def test_simple_table(self):
        html = """
        <table>
            <tr><th>Name</th><th>Age</th></tr>
            <tr><td>Alice</td><td>30</td></tr>
            <tr><td>Bob</td><td>25</td></tr>
        </table>
        """
        tables = _parse_html_tables(html)
        assert len(tables) == 1
        assert tables[0].height == 2
        assert list(tables[0].columns) == ["Name", "Age"]

    def test_multiple_tables(self):
        html = """
        <table>
            <tr><th>A</th></tr>
            <tr><td>1</td></tr>
        </table>
        <table>
            <tr><th>B</th><th>C</th></tr>
            <tr><td>x</td><td>y</td></tr>
        </table>
        """
        tables = _parse_html_tables(html)
        assert len(tables) == 2

    def test_no_tables(self):
        html = "<html><body><p>No tables here</p></body></html>"
        tables = _parse_html_tables(html)
        assert tables == []

    def test_table_with_nested_tags(self):
        html = """
        <table>
            <tr><th>Link</th><th>Value</th></tr>
            <tr><td><a href="#">Click</a></td><td><b>42</b></td></tr>
        </table>
        """
        tables = _parse_html_tables(html)
        assert len(tables) == 1
        assert tables[0]["Link"][0] == "Click"
        assert tables[0]["Value"][0] == "42"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class TestRedactCredentials:
    def test_redacts_password(self):
        url = "postgresql://user:secret123@host:5432/db"
        assert _redact_credentials(url) == "postgresql://user:***@host:5432/db"

    def test_no_credentials(self):
        url = "sqlite:///path/to/file.db"
        assert _redact_credentials(url) == url


# ---------------------------------------------------------------------------
# Integration: Workspace.load() with URLs and databases
# ---------------------------------------------------------------------------


class TestWorkspaceLoadIntegration:
    def test_load_url(self, local_server):
        from sweet.core.workspace import Workspace

        ws = Workspace()
        ws.load(f"{local_server}/data.csv")
        assert ws.shape == (2, 2)
        assert "name" in ws.df.columns

    def test_load_web_table(self, local_server):
        from sweet.core.workspace import Workspace

        ws = Workspace()
        ws.load(f"{local_server}/page")
        assert ws.shape == (2, 2)
        assert "City" in ws.df.columns

    def test_load_sqlite(self, tmp_path):
        import sqlite3

        from sweet.core.workspace import Workspace

        db_path = tmp_path / "test.db"
        conn = sqlite3.connect(str(db_path))
        conn.execute("CREATE TABLE products (id INTEGER, name TEXT, price REAL)")
        conn.execute("INSERT INTO products VALUES (1, 'Widget', 5.99), (2, 'Gadget', 12.99)")
        conn.commit()
        conn.close()

        ws = Workspace()
        ws.load(f"sqlite:///{db_path}", table="products")
        assert ws.shape == (2, 3)
        assert "price" in ws.df.columns

    def test_local_file_still_works(self, tmp_path):
        from sweet.core.workspace import Workspace

        csv_file = tmp_path / "simple.csv"
        pl.DataFrame({"x": [1, 2, 3]}).write_csv(csv_file)

        ws = Workspace()
        ws.load(str(csv_file))
        assert ws.shape == (3, 1)
