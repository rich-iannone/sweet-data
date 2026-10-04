"""Open data from anywhere: files, globs, directories, remote URLs, and stdin.

`open_source()` resolves a target string to a lazy scan where the format
supports it, so large and remote data can be explored without reading it all.
Small local files are read eagerly, since that's faster for everything after.

Examples of targets::

    data.parquet
    'logs/2026-*.csv'
    exports/                       # a directory (e.g. hive-partitioned Parquet)
    s3://bucket/events/*.parquet
    hf://datasets/org/name         # all Parquet files in a Hugging Face dataset
    https://example.com/data.csv
    -                              # stdin
"""

from __future__ import annotations

import io as _io
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, BinaryIO
from urllib.parse import urlparse

import polars as pl

from .io import READERS, format_for_path

REMOTE_SCHEMES = (
    "s3", "s3a", "gs", "gcs", "az", "abfs", "abfss", "azure", "hf", "http", "https",
)  # fmt: skip
GLOB_CHARS = frozenset("*?[")
DATABASE_EXTENSIONS = (".db", ".sqlite", ".sqlite3", ".duckdb", ".ddb")

#: Local files smaller than this are read into memory instead of scanned lazily.
EAGER_THRESHOLD_BYTES = 64 * 1024 * 1024

#: Compressed columnar formats expand a lot in memory, so they get a lower cutoff.
COMPRESSED_FORMATS = {"parquet": 8, "ipc": 4}  # format -> divisor of the threshold


@dataclass
class OpenedSource:
    """The result of opening a target.

    Attributes:
        frame: A LazyFrame (lazy) or DataFrame (eager).
        name: A sheet name derived from the target.
        source: Provenance, recorded in pipelines (``{"path": ..., "format": ...}``).
    """

    frame: pl.DataFrame | pl.LazyFrame
    name: str
    source: dict[str, Any]

    @property
    def lazy(self) -> bool:
        return isinstance(self.frame, pl.LazyFrame)


def is_remote(target: str) -> bool:
    scheme = urlparse(target).scheme.lower()
    return scheme in REMOTE_SCHEMES


def is_database_file(target: str) -> bool:
    return Path(target).suffix.lower() in DATABASE_EXTENSIONS


def open_source(
    target: str,
    *,
    format: str | None = None,
    lazy: bool | None = None,
    stdin: BinaryIO | None = None,
    eager_threshold: int = EAGER_THRESHOLD_BYTES,
) -> OpenedSource:
    """Open `target` as a frame.

    Args:
        target: Path, glob, directory, remote URL, or "-" for stdin.
        format: Registered reader name, to override detection by extension.
        lazy: Force lazy (True) or eager (False) loading. By default, remote,
            glob, and directory targets and large local files are lazy.
        stdin: Stream to read for "-" (defaults to `sys.stdin.buffer`).
        eager_threshold: Size below which local files are read eagerly.

    Raises:
        FileNotFoundError: If a local target doesn't exist.
        ValueError: If the format can't be determined or isn't supported.
    """
    if target == "-":
        return _open_stdin(stdin or sys.stdin.buffer, format)
    if is_remote(target):
        return _open_remote(target, format, lazy)

    path = Path(target).expanduser()
    if path.is_dir():
        return _open_directory(path, format)
    if GLOB_CHARS & set(target):
        fmt = format or format_for_path(target)
        name = path.parent.name if path.parent.name else "data"
        return OpenedSource(_scan(target, fmt), name, {"path": target, "format": fmt})
    if not path.exists():
        raise FileNotFoundError(f"File not found: {path}")
    if is_database_file(target):
        raise ValueError(
            f"{path.name} is a database file; open it with `sweet --classic {target}` for now"
        )

    fmt = format or _format_or_csv(path)
    reader = READERS.get(fmt)
    if lazy is None:
        threshold = eager_threshold // COMPRESSED_FORMATS.get(fmt, 1)
        lazy = path.stat().st_size >= threshold
    source = {"path": str(path), "format": fmt}
    if lazy and reader.scan is not None:
        return OpenedSource(reader.scan(str(path)), path.stem, source)
    return OpenedSource(reader.read(path), path.stem, source)


def _format_or_csv(path: Path) -> str:
    try:
        return format_for_path(path)
    except ValueError:
        return "csv"  # e.g. .txt, .dat, or no extension


def _scan(target: str, fmt: str) -> pl.LazyFrame:
    reader = READERS.get(fmt)
    if reader.scan is None:
        raise ValueError(f"Format '{fmt}' can't be scanned lazily; open a single file instead")
    return reader.scan(target)


def _open_remote(target: str, format: str | None, lazy: bool | None) -> OpenedSource:
    parsed = urlparse(target)
    path_part = parsed.path
    suffix = Path(path_part).suffix.lower()

    # A bare Hugging Face dataset (hf://datasets/org/name): scan all its Parquet files
    if parsed.scheme == "hf" and not suffix and not (GLOB_CHARS & set(target)):
        target = target.rstrip("/") + "/**/*.parquet"
        path_part = urlparse(target).path

    fmt = format or format_for_path(path_part)
    reader = READERS.get(fmt)
    name = Path(path_part.replace("*", "")).stem or Path(path_part).parent.name or "data"
    source = {"path": target, "format": fmt}
    if lazy is not False and reader.scan is not None:
        return OpenedSource(reader.scan(target), name, source)
    return OpenedSource(reader.read(target), name, source)


def _open_directory(path: Path, format: str | None) -> OpenedSource:
    """Scan every file of the directory's dominant (scannable) format."""
    counts: dict[str, int] = {}
    for name in READERS:
        reader = READERS.get(name)
        if reader.scan is None or (format and name != format):
            continue
        counts[name] = sum(1 for ext in reader.extensions for _ in path.rglob(f"*{ext}"))
    counts = {k: v for k, v in counts.items() if v}
    if not counts:
        raise ValueError(f"No readable data files found in {path}")
    fmt = max(counts, key=counts.get)
    ext = READERS.get(fmt).extensions[0]
    pattern = str(path / "**" / f"*{ext}")
    opts = {"hive_partitioning": True} if fmt == "parquet" else {}
    lf = READERS.get(fmt).scan(pattern, **opts)
    return OpenedSource(lf, path.name, {"path": pattern, "format": fmt})


def sniff_format(data: bytes) -> str:
    """Guess the format of raw bytes (stdin): json, ndjson, or csv."""
    head = data.lstrip()[:4096]
    if head.startswith(b"["):
        return "json"
    if head.startswith(b"{"):
        return "ndjson"
    return "csv"


def _open_stdin(stream: BinaryIO, format: str | None) -> OpenedSource:
    data = stream.read()
    if not data.strip():
        raise ValueError("No data on stdin")
    fmt = format or sniff_format(data)
    buffer = _io.BytesIO(data)
    if fmt == "csv":
        first_line = data.split(b"\n", 1)[0]
        separator = "\t" if first_line.count(b"\t") > first_line.count(b",") else ","
        df = pl.read_csv(buffer, separator=separator)
    else:
        df = READERS.get(fmt).read(buffer)
    return OpenedSource(df, "stdin", {"uri": "stdin", "format": fmt})
