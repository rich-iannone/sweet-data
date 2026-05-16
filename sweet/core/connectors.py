"""Source connectors — load data from URLs, databases, and web tables.

Provides a unified interface for ingesting data from remote sources
into Polars DataFrames. All connectors are lazy-imported to avoid
hard dependencies on optional packages.
"""

from __future__ import annotations

import re
import tempfile
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import polars as pl

# =============================================================================
# Public API
# =============================================================================


def load_source(
    source: str,
    *,
    name: str | None = None,
    format: str | None = None,
    query: str | None = None,
    table: str | None = None,
    selector: int = 0,
) -> tuple[pl.DataFrame, dict[str, Any]]:
    """Load data from any supported source.

    Detects the source type (file, URL, database, web table) and
    dispatches to the appropriate loader.

    Args:
        source: Source identifier — file path, URL, or connection string.
        name: Optional name hint (used as sheet name).
        format: Force a specific format (csv, parquet, json, etc.).
        query: SQL query for database sources.
        table: Table name for database sources.
        selector: Table index when source has multiple tables (web pages).

    Returns:
        Tuple of (DataFrame, metadata_dict). Metadata includes keys like
        'source_type', 'source', 'format', 'rows', 'cols'.

    Raises:
        ValueError: If source type can't be determined or format is unsupported.
        ConnectionError: If remote source is unreachable.
    """
    source_type = detect_source_type(source)

    if source_type == "file":
        return _load_file(source, format=format)
    elif source_type == "url":
        return _load_url(source, format=format)
    elif source_type == "database":
        return _load_database(source, query=query, table=table)
    elif source_type == "web_table":
        return _load_web_table(source, selector=selector)
    else:
        raise ValueError(f"Cannot determine source type for: {source}")


def detect_source_type(source: str) -> str:
    """Detect the type of a data source.

    Args:
        source: Source string to classify.

    Returns:
        One of: 'file', 'url', 'database', 'web_table'.
    """
    # Database connection strings
    db_schemes = ("postgresql://", "postgres://", "mysql://", "sqlite://", "duckdb://")
    if any(source.startswith(s) for s in db_schemes):
        return "database"

    # HTTP(S) URLs
    if source.startswith(("http://", "https://")):
        parsed = urlparse(source)
        path = parsed.path.lower()
        # Data file URLs (direct download)
        data_extensions = (
            ".csv", ".tsv", ".parquet", ".pq", ".json", ".jsonl",
            ".ndjson", ".xlsx", ".xls", ".ipc", ".avro",
        )
        if any(path.endswith(ext) for ext in data_extensions):
            return "url"
        # Otherwise treat as web page with tables
        return "web_table"

    # S3/GCS/Azure URLs
    if source.startswith(("s3://", "gs://", "az://", "abfs://")):
        return "url"

    # Default: local file
    return "file"


# =============================================================================
# Loaders
# =============================================================================


def _load_file(source: str, *, format: str | None = None) -> tuple[pl.DataFrame, dict[str, Any]]:
    """Load from a local file."""
    path = Path(source)
    if not path.exists():
        raise FileNotFoundError(f"File not found: {source}")

    if format is None:
        format = _detect_format(path)

    df = _read_file(path, format)
    metadata = {
        "source_type": "file",
        "source": str(path.resolve()),
        "format": format,
        "rows": df.height,
        "cols": df.width,
    }
    return df, metadata


def _load_url(source: str, *, format: str | None = None) -> tuple[pl.DataFrame, dict[str, Any]]:
    """Load data from a URL (direct file download or cloud storage)."""
    # Cloud storage — Polars handles natively
    if source.startswith(("s3://", "gs://", "az://", "abfs://")):
        return _load_cloud(source, format=format)

    # HTTP(S) — download to temp file, then read
    import urllib.request

    parsed = urlparse(source)
    path = parsed.path.lower()

    if format is None:
        format = _format_from_path(path) or "csv"

    suffix = f".{format}" if not format.startswith(".") else format

    try:
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
            tmp_path = Path(tmp.name)
            urllib.request.urlretrieve(source, tmp_path)  # noqa: S310
    except Exception as e:
        raise ConnectionError(f"Failed to download {source}: {e}") from e

    try:
        df = _read_file(tmp_path, format)
    finally:
        tmp_path.unlink(missing_ok=True)

    metadata = {
        "source_type": "url",
        "source": source,
        "format": format,
        "rows": df.height,
        "cols": df.width,
    }
    return df, metadata


def _load_cloud(source: str, *, format: str | None = None) -> tuple[pl.DataFrame, dict[str, Any]]:
    """Load from cloud storage (S3, GCS, Azure) via Polars native support."""
    if format is None:
        format = _format_from_path(source) or "parquet"

    if format == "csv":
        df = pl.read_csv(source)
    elif format == "parquet":
        df = pl.read_parquet(source)
    elif format == "json":
        df = pl.read_json(source)
    elif format in ("jsonl", "ndjson"):
        df = pl.read_ndjson(source)
    else:
        raise ValueError(f"Unsupported format for cloud source: {format}")

    metadata = {
        "source_type": "cloud",
        "source": source,
        "format": format,
        "rows": df.height,
        "cols": df.width,
    }
    return df, metadata


def _load_database(
    source: str,
    *,
    query: str | None = None,
    table: str | None = None,
) -> tuple[pl.DataFrame, dict[str, Any]]:
    """Load data from a database via DuckDB.

    Supports: PostgreSQL, MySQL, SQLite, DuckDB files.
    """
    import duckdb

    parsed = urlparse(source)
    scheme = parsed.scheme.lower()

    conn = duckdb.connect(":memory:")

    try:
        if scheme in ("postgresql", "postgres"):
            conn.execute("INSTALL postgres")
            conn.execute("LOAD postgres")
            # Normalize to postgres:// for DuckDB
            db_url = source.replace("postgresql://", "postgres://")
            conn.execute(f"ATTACH '{db_url}' AS remote_db (TYPE postgres, READ_ONLY)")
            sql = query or f"SELECT * FROM remote_db.{table or 'information_schema.tables'}"

        elif scheme == "mysql":
            conn.execute("INSTALL mysql")
            conn.execute("LOAD mysql")
            conn.execute(f"ATTACH '{source}' AS remote_db (TYPE mysql, READ_ONLY)")
            sql = query or f"SELECT * FROM remote_db.{table or 'information_schema.tables'}"

        elif scheme == "sqlite":
            db_path = parsed.path  # sqlite:///path/to/file.db
            if db_path.startswith("///"):
                db_path = db_path[3:]
            elif db_path.startswith("/"):
                db_path = db_path[1:]
            conn.execute("INSTALL sqlite")
            conn.execute("LOAD sqlite")
            conn.execute(f"ATTACH '{db_path}' AS remote_db (TYPE sqlite, READ_ONLY)")
            sql = query or f"SELECT * FROM remote_db.{table or 'sqlite_master'}"

        elif scheme == "duckdb":
            db_path = parsed.path
            if db_path.startswith("///"):
                db_path = db_path[3:]
            elif db_path.startswith("/"):
                db_path = db_path[1:]
            conn.execute(f"ATTACH '{db_path}' AS remote_db (READ_ONLY)")
            sql = query or f"SELECT * FROM remote_db.{table or 'information_schema.tables'}"

        else:
            raise ValueError(f"Unsupported database scheme: {scheme}")

        result = conn.execute(sql)
        df = pl.from_arrow(result.fetch_arrow_table())

    finally:
        conn.close()

    metadata = {
        "source_type": "database",
        "source": _redact_credentials(source),
        "query": sql,
        "rows": df.height,
        "cols": df.width,
    }
    return df, metadata


def _load_web_table(
    source: str, *, selector: int = 0
) -> tuple[pl.DataFrame, dict[str, Any]]:
    """Extract a table from a web page.

    Uses DuckDB's built-in HTML table reader or falls back to
    basic parsing.
    """
    import urllib.request

    try:
        req = urllib.request.Request(source, headers={"User-Agent": "Sweet/1.0"})
        with urllib.request.urlopen(req, timeout=30) as resp:  # noqa: S310
            html = resp.read().decode("utf-8", errors="replace")
    except Exception as e:
        raise ConnectionError(f"Failed to fetch {source}: {e}") from e

    tables = _parse_html_tables(html)

    if not tables:
        raise ValueError(f"No tables found at {source}")

    if selector >= len(tables):
        raise ValueError(
            f"Table index {selector} out of range. Found {len(tables)} tables."
        )

    df = tables[selector]
    metadata = {
        "source_type": "web_table",
        "source": source,
        "table_index": selector,
        "tables_found": len(tables),
        "rows": df.height,
        "cols": df.width,
    }
    return df, metadata


# =============================================================================
# Helpers
# =============================================================================


def _read_file(path: Path, format: str) -> pl.DataFrame:
    """Read a file into a DataFrame given its format."""
    readers = {
        "csv": pl.read_csv,
        "tsv": lambda p: pl.read_csv(p, separator="\t"),
        "parquet": pl.read_parquet,
        "json": pl.read_json,
        "jsonl": pl.read_ndjson,
        "ndjson": pl.read_ndjson,
        "ipc": pl.read_ipc,
        "avro": pl.read_avro,
    }
    reader = readers.get(format)
    if reader is None:
        raise ValueError(f"Unsupported format: {format}")
    return reader(path)


def _detect_format(path: Path) -> str:
    """Detect format from file extension."""
    format_map = {
        ".csv": "csv",
        ".tsv": "tsv",
        ".parquet": "parquet",
        ".pq": "parquet",
        ".json": "json",
        ".jsonl": "jsonl",
        ".ndjson": "ndjson",
        ".ipc": "ipc",
        ".avro": "avro",
        ".xlsx": "xlsx",
        ".xls": "xlsx",
    }
    fmt = format_map.get(path.suffix.lower())
    if fmt is None:
        raise ValueError(
            f"Cannot detect format from extension '{path.suffix}'. "
            f"Supported: {', '.join(format_map.keys())}"
        )
    return fmt


def _format_from_path(path: str) -> str | None:
    """Extract format from a URL path (best effort)."""
    ext_map = {
        ".csv": "csv",
        ".tsv": "tsv",
        ".parquet": "parquet",
        ".pq": "parquet",
        ".json": "json",
        ".jsonl": "jsonl",
        ".ndjson": "ndjson",
        ".ipc": "ipc",
        ".avro": "avro",
    }
    for ext, fmt in ext_map.items():
        if path.endswith(ext):
            return fmt
    return None


def _redact_credentials(url: str) -> str:
    """Redact password from a connection string for safe logging."""
    return re.sub(r"://([^:]+):([^@]+)@", r"://\1:***@", url)


def _parse_html_tables(html: str) -> list[pl.DataFrame]:
    """Parse HTML tables into DataFrames using regex (no lxml dependency).

    This is a lightweight parser — handles simple tables with <th>/<td>.
    """
    tables: list[pl.DataFrame] = []

    # Find all <table>...</table> blocks
    table_pattern = re.compile(r"<table[^>]*>(.*?)</table>", re.DOTALL | re.IGNORECASE)
    row_pattern = re.compile(r"<tr[^>]*>(.*?)</tr>", re.DOTALL | re.IGNORECASE)
    cell_pattern = re.compile(r"<t[hd][^>]*>(.*?)</t[hd]>", re.DOTALL | re.IGNORECASE)
    tag_strip = re.compile(r"<[^>]+>")

    for table_match in table_pattern.finditer(html):
        table_html = table_match.group(1)
        rows = row_pattern.findall(table_html)

        if not rows:
            continue

        parsed_rows: list[list[str]] = []
        for row in rows:
            cells = cell_pattern.findall(row)
            parsed_rows.append([tag_strip.sub("", c).strip() for c in cells])

        if len(parsed_rows) < 2:
            continue

        # First row as headers
        headers = parsed_rows[0]
        if not headers or all(h == "" for h in headers):
            continue

        # Data rows — skip rows with wrong column count
        n_cols = len(headers)
        data_rows = [r for r in parsed_rows[1:] if len(r) == n_cols]

        if not data_rows:
            continue

        # Build DataFrame
        col_data = {
            headers[i]: [row[i] for row in data_rows]
            for i in range(n_cols)
        }
        try:
            tables.append(pl.DataFrame(col_data))
        except Exception:
            continue

    return tables


def list_database_tables(source: str) -> list[str]:
    """List tables available in a database source.

    Args:
        source: Database connection string.

    Returns:
        List of table names.
    """
    import duckdb

    parsed = urlparse(source)
    scheme = parsed.scheme.lower()
    conn = duckdb.connect(":memory:")

    try:
        if scheme in ("postgresql", "postgres"):
            conn.execute("INSTALL postgres")
            conn.execute("LOAD postgres")
            db_url = source.replace("postgresql://", "postgres://")
            conn.execute(f"ATTACH '{db_url}' AS remote_db (TYPE postgres, READ_ONLY)")
            result = conn.execute(
                "SELECT table_name FROM remote_db.information_schema.tables "
                "WHERE table_schema = 'public'"
            ).fetchall()

        elif scheme == "mysql":
            conn.execute("INSTALL mysql")
            conn.execute("LOAD mysql")
            conn.execute(f"ATTACH '{source}' AS remote_db (TYPE mysql, READ_ONLY)")
            result = conn.execute(
                "SELECT table_name FROM remote_db.information_schema.tables "
                "WHERE table_schema NOT IN ('information_schema', 'mysql', 'performance_schema')"
            ).fetchall()

        elif scheme == "sqlite":
            db_path = parsed.path
            if db_path.startswith("///"):
                db_path = db_path[3:]
            elif db_path.startswith("/"):
                db_path = db_path[1:]
            conn.execute("INSTALL sqlite")
            conn.execute("LOAD sqlite")
            conn.execute(f"ATTACH '{db_path}' AS remote_db (TYPE sqlite, READ_ONLY)")
            result = conn.execute(
                "SELECT name FROM remote_db.sqlite_master WHERE type='table'"
            ).fetchall()

        elif scheme == "duckdb":
            db_path = parsed.path
            if db_path.startswith("///"):
                db_path = db_path[3:]
            elif db_path.startswith("/"):
                db_path = db_path[1:]
            conn.execute(f"ATTACH '{db_path}' AS remote_db (READ_ONLY)")
            result = conn.execute(
                "SELECT table_name FROM remote_db.information_schema.tables"
            ).fetchall()

        else:
            raise ValueError(f"Unsupported database scheme: {scheme}")

        return [row[0] for row in result]

    finally:
        conn.close()
