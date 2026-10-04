"""File readers and writers, registered by format name.

Each reader knows how to load a file *and* how to render the equivalent Polars
code, so pipelines can be exported as standalone scripts.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import polars as pl

from .registry import Registry


@dataclass(frozen=True)
class Reader:
    """A file format that Sweet can load."""

    name: str
    extensions: tuple[str, ...]
    read: Callable[..., pl.DataFrame]
    code: Callable[[str], str]  # path -> Polars source expression
    #: Lazy scan (path, glob, or remote URL -> LazyFrame), if the format supports it
    scan: Callable[..., pl.LazyFrame] | None = None


@dataclass(frozen=True)
class Writer:
    """A file format that Sweet can write."""

    name: str
    extensions: tuple[str, ...]
    write: Callable[[pl.DataFrame, Path], None]
    code: Callable[[str], str]  # path -> Polars statement writing `df`
    options: dict[str, Any] = field(default_factory=dict)


READERS: Registry[Reader] = Registry("reader")
WRITERS: Registry[Writer] = Registry("writer")


def _lit(path: str) -> str:
    return json.dumps(path)


# -----------------------------------------------------------------------------
# Readers
# -----------------------------------------------------------------------------


def _read_csv(path: Path, **opts: Any) -> pl.DataFrame:
    if Path(path).suffix.lower() == ".tsv":
        opts.setdefault("separator", "\t")
    try:
        return pl.read_csv(path, **opts)
    except Exception:
        # Retry with tolerance for ragged lines (messy CSVs)
        return pl.read_csv(path, truncate_ragged_lines=True, **opts)


def _scan_csv(path: str, **opts: Any) -> pl.LazyFrame:
    if str(path).lower().endswith(".tsv"):
        opts.setdefault("separator", "\t")
    opts.setdefault("infer_schema_length", 10_000)
    return pl.scan_csv(path, **opts)


def _csv_code(path: str) -> str:
    if path.lower().endswith(".tsv"):
        return f"pl.read_csv({_lit(path)}, separator='\\t')"
    return f"pl.read_csv({_lit(path)})"


READERS.register("csv", Reader("csv", (".csv", ".tsv"), _read_csv, _csv_code, _scan_csv))
READERS.register(
    "parquet",
    Reader(
        "parquet",
        (".parquet", ".pq"),
        lambda p, **o: pl.read_parquet(p, **o),
        lambda p: f"pl.read_parquet({_lit(p)})",
        lambda p, **o: pl.scan_parquet(p, **o),
    ),
)
READERS.register(
    "json",
    Reader(
        "json",
        (".json",),
        lambda p, **o: pl.read_json(p, **o),
        lambda p: f"pl.read_json({_lit(p)})",
    ),
)
READERS.register(
    "ndjson",
    Reader(
        "ndjson",
        (".jsonl", ".ndjson"),
        lambda p, **o: pl.read_ndjson(p, **o),
        lambda p: f"pl.read_ndjson({_lit(p)})",
        lambda p, **o: pl.scan_ndjson(p, **o),
    ),
)
READERS.register(
    "ipc",
    Reader(
        "ipc",
        (".arrow", ".feather", ".ipc"),
        lambda p, **o: pl.read_ipc(p, **o),
        lambda p: f"pl.read_ipc({_lit(p)})",
        lambda p, **o: pl.scan_ipc(p, **o),
    ),
)
READERS.register(
    "avro",
    Reader(
        "avro",
        (".avro",),
        lambda p, **o: pl.read_avro(p, **o),
        lambda p: f"pl.read_avro({_lit(p)})",
    ),
)
READERS.register(
    "excel",
    Reader(
        "excel",
        (".xlsx", ".xls"),
        lambda p, **o: pl.read_excel(p, **o),
        lambda p: f"pl.read_excel({_lit(p)})",
    ),
)


# -----------------------------------------------------------------------------
# Writers
# -----------------------------------------------------------------------------

WRITERS.register(
    "csv",
    Writer("csv", (".csv",), lambda df, p: df.write_csv(p), lambda p: f"df.write_csv({_lit(p)})"),
)
WRITERS.register(
    "parquet",
    Writer(
        "parquet",
        (".parquet", ".pq"),
        lambda df, p: df.write_parquet(p),
        lambda p: f"df.write_parquet({_lit(p)})",
    ),
)
WRITERS.register(
    "json",
    Writer(
        "json", (".json",), lambda df, p: df.write_json(p), lambda p: f"df.write_json({_lit(p)})"
    ),
)
WRITERS.register(
    "ndjson",
    Writer(
        "ndjson",
        (".jsonl", ".ndjson"),
        lambda df, p: df.write_ndjson(p),
        lambda p: f"df.write_ndjson({_lit(p)})",
    ),
)
WRITERS.register(
    "ipc",
    Writer(
        "ipc",
        (".arrow", ".feather", ".ipc"),
        lambda df, p: df.write_ipc(p),
        lambda p: f"df.write_ipc({_lit(p)})",
    ),
)
WRITERS.register(
    "excel",
    Writer(
        "excel",
        (".xlsx",),
        lambda df, p: df.write_excel(p),
        lambda p: f"df.write_excel({_lit(p)})",
    ),
)


# -----------------------------------------------------------------------------
# Lookup helpers
# -----------------------------------------------------------------------------


def format_for_path(path: str | Path, registry: Registry = READERS) -> str:
    """Return the registered format name for a file path's extension.

    Raises:
        ValueError: If no registered format handles the extension.
    """
    suffix = Path(path).suffix.lower()
    match = registry.find(lambda _name, item: suffix in item.extensions)
    if match is None:
        supported = sorted({ext for name in registry for ext in registry.get(name).extensions})
        raise ValueError(
            f"Cannot detect format from extension '{suffix}'. Supported: {', '.join(supported)}"
        )
    return match[0]


def read_file(path: str | Path, format: str | None = None, **opts: Any) -> pl.DataFrame:
    """Read a file using the registered reader for `format` (or its extension)."""
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"File not found: {path}")
    name = format.lower() if format else format_for_path(path)
    if name not in READERS:
        raise ValueError(f"Unsupported file format: {name}")
    return READERS.get(name).read(path, **opts)


def write_file(df: pl.DataFrame, path: str | Path, format: str | None = None) -> None:
    """Write `df` using the registered writer for `format` (or the path's extension)."""
    path = Path(path)
    name = format.lower() if format else format_for_path(path, WRITERS)
    if name not in WRITERS:
        raise ValueError(f"Unsupported file format: {name}")
    WRITERS.get(name).write(df, path)
