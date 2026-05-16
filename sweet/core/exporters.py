"""Export destinations — write DataFrames to files, databases, and cloud storage.

Provides a unified interface for exporting data to local files, remote databases,
and cloud storage. Mirrors the connector module for symmetric source/destination
handling.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import polars as pl

# =============================================================================
# Public API
# =============================================================================


def export_to(
    df: pl.DataFrame,
    dest: str,
    *,
    format: str | None = None,
    table: str | None = None,
    mode: str = "replace",
    name: str | None = None,
) -> dict[str, Any]:
    """Export a DataFrame to any supported destination.

    Detects the destination type (file, cloud, database) and dispatches
    to the appropriate exporter.

    Args:
        df: Polars DataFrame to export.
        dest: Destination — file path, URL, or connection string.
        format: Force a specific format (csv, parquet, json, etc.).
        table: Table name for database destinations.
        mode: Write mode for databases — 'replace', 'append', or 'fail'.
        name: Optional name/label for the export.

    Returns:
        Metadata dict with keys like 'dest_type', 'dest', 'format', 'rows'.

    Raises:
        ValueError: If destination type can't be determined or format unsupported.
        ConnectionError: If remote destination is unreachable.
    """
    dest_type = detect_dest_type(dest)

    if dest_type == "file":
        return _export_file(df, dest, format=format)
    elif dest_type == "cloud":
        return _export_cloud(df, dest, format=format)
    elif dest_type == "database":
        return _export_database(df, dest, table=table, mode=mode)
    else:
        raise ValueError(f"Cannot determine destination type for: {dest}")


def detect_dest_type(dest: str) -> str:
    """Detect the type of an export destination.

    Args:
        dest: Destination string to classify.

    Returns:
        One of: 'file', 'cloud', 'database'.
    """
    # Database connection strings
    db_schemes = ("postgresql://", "postgres://", "mysql://", "sqlite://", "duckdb://")
    if any(dest.startswith(s) for s in db_schemes):
        return "database"

    # Cloud storage
    if dest.startswith(("s3://", "gs://", "az://", "abfs://")):
        return "cloud"

    # HTTP(S) — not typically an export destination, treat as error
    if dest.startswith(("http://", "https://")):
        raise ValueError(
            "Cannot export to HTTP(S) URLs directly. "
            "Use s3://, gs://, or a database connection string."
        )

    # Default: local file
    return "file"


# =============================================================================
# Exporters
# =============================================================================


def _export_file(
    df: pl.DataFrame, dest: str, *, format: str | None = None
) -> dict[str, Any]:
    """Export to a local file."""
    path = Path(dest)

    if format is None:
        format = _detect_format(path)

    # Ensure parent directory exists
    path.parent.mkdir(parents=True, exist_ok=True)

    _write_file(df, path, format)

    return {
        "dest_type": "file",
        "dest": str(path.resolve()),
        "format": format,
        "rows": df.height,
        "cols": df.width,
    }


def _export_cloud(
    df: pl.DataFrame, dest: str, *, format: str | None = None
) -> dict[str, Any]:
    """Export to cloud storage (S3, GCS, Azure) via Polars native support."""
    if format is None:
        format = _format_from_path(dest) or "parquet"

    if format == "csv":
        df.write_csv(dest)
    elif format == "parquet":
        df.write_parquet(dest)
    elif format in ("jsonl", "ndjson"):
        df.write_ndjson(dest)
    elif format == "ipc":
        df.write_ipc(dest)
    else:
        raise ValueError(
            f"Unsupported format for cloud export: '{format}'. "
            "Supported: csv, parquet, ndjson, ipc."
        )

    return {
        "dest_type": "cloud",
        "dest": dest,
        "format": format,
        "rows": df.height,
        "cols": df.width,
    }


def _export_database(
    df: pl.DataFrame,
    dest: str,
    *,
    table: str | None = None,
    mode: str = "replace",
) -> dict[str, Any]:
    """Export to a database via DuckDB.

    Supports: PostgreSQL, MySQL, SQLite, DuckDB files.
    """
    import duckdb

    parsed = urlparse(dest)
    scheme = parsed.scheme.lower()

    if table is None:
        table = "exported_data"

    if mode not in ("replace", "append", "fail"):
        raise ValueError(f"Invalid mode: '{mode}'. Use 'replace', 'append', or 'fail'.")

    conn = duckdb.connect(":memory:")

    # Register the DataFrame for DuckDB access
    conn.register("export_df", df.to_arrow())

    try:
        if scheme in ("postgresql", "postgres"):
            conn.execute("INSTALL postgres")
            conn.execute("LOAD postgres")
            db_url = dest.replace("postgresql://", "postgres://")
            conn.execute(f"ATTACH '{db_url}' AS remote_db (TYPE postgres)")
            _write_to_attached_db(conn, "remote_db", table, mode)

        elif scheme == "mysql":
            conn.execute("INSTALL mysql")
            conn.execute("LOAD mysql")
            conn.execute(f"ATTACH '{dest}' AS remote_db (TYPE mysql)")
            _write_to_attached_db(conn, "remote_db", table, mode)

        elif scheme == "sqlite":
            db_path = parsed.path
            if db_path.startswith("///"):
                db_path = db_path[3:]
            elif db_path.startswith("/"):
                db_path = db_path[1:]
            conn.execute("INSTALL sqlite")
            conn.execute("LOAD sqlite")
            conn.execute(f"ATTACH '{db_path}' AS remote_db (TYPE sqlite)")
            _write_to_attached_db(conn, "remote_db", table, mode)

        elif scheme == "duckdb":
            db_path = parsed.path
            if db_path.startswith("///"):
                db_path = db_path[3:]
            elif db_path.startswith("/"):
                db_path = db_path[1:]
            conn.execute(f"ATTACH '{db_path}' AS remote_db")
            _write_to_attached_db(conn, "remote_db", table, mode)

        else:
            raise ValueError(f"Unsupported database scheme: '{scheme}'")

    finally:
        conn.close()

    return {
        "dest_type": "database",
        "dest": _redact_credentials(dest),
        "table": table,
        "mode": mode,
        "rows": df.height,
        "cols": df.width,
    }


def _write_to_attached_db(conn, db_name: str, table: str, mode: str) -> None:
    """Write export_df to a table in the attached database."""
    qualified = f"{db_name}.{table}"

    if mode == "replace":
        conn.execute(f"DROP TABLE IF EXISTS {qualified}")
        conn.execute(f"CREATE TABLE {qualified} AS SELECT * FROM export_df")
    elif mode == "append":
        # Create table if it doesn't exist, then insert
        conn.execute(
            f"CREATE TABLE IF NOT EXISTS {qualified} AS "
            f"SELECT * FROM export_df WHERE 1=0"
        )
        conn.execute(f"INSERT INTO {qualified} SELECT * FROM export_df")
    elif mode == "fail":
        # This will fail if table already exists
        conn.execute(f"CREATE TABLE {qualified} AS SELECT * FROM export_df")


# =============================================================================
# Helpers
# =============================================================================


def _write_file(df: pl.DataFrame, path: Path, format: str) -> None:
    """Write a DataFrame to a file in the given format."""
    writers = {
        "csv": lambda d, p: d.write_csv(p),
        "tsv": lambda d, p: d.write_csv(p, separator="\t"),
        "parquet": lambda d, p: d.write_parquet(p),
        "json": lambda d, p: d.write_json(p),
        "jsonl": lambda d, p: d.write_ndjson(p),
        "ndjson": lambda d, p: d.write_ndjson(p),
        "ipc": lambda d, p: d.write_ipc(p),
    }
    writer = writers.get(format)
    if writer is None:
        raise ValueError(
            f"Unsupported export format: '{format}'. "
            f"Supported: {', '.join(writers.keys())}"
        )
    writer(df, path)


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
    }
    fmt = format_map.get(path.suffix.lower())
    if fmt is None:
        raise ValueError(
            f"Cannot detect export format from extension '{path.suffix}'. "
            f"Supported: {', '.join(format_map.keys())}"
        )
    return fmt


def _format_from_path(path: str) -> str | None:
    """Extract format from a path string (best effort)."""
    ext_map = {
        ".csv": "csv",
        ".tsv": "tsv",
        ".parquet": "parquet",
        ".pq": "parquet",
        ".json": "json",
        ".jsonl": "jsonl",
        ".ndjson": "ndjson",
        ".ipc": "ipc",
    }
    for ext, fmt in ext_map.items():
        if path.endswith(ext):
            return fmt
    return None


def _redact_credentials(url: str) -> str:
    """Redact password from a connection string for safe logging."""
    return re.sub(r"://([^:]+):([^@]+)@", r"://\1:***@", url)
