"""Live data: stream sources and live tables.

A **stream source** yields batches of rows from an unbounded feed:

- `FileTailSource`: a growing file (like ``tail -f``), surviving truncation and rotation
- `StdinSource`: lines piped into Sweet (``tail -f app.log | sweet --follow``)
- `WebSocketSource`: ``ws://`` / ``wss://`` feeds (needs the ``stream`` extra)

Lines are parsed as NDJSON, CSV (with a header line), or plain text, detected from
the first line unless given. Plugins can register more sources (by URL scheme) in
`STREAM_SOURCES`.

A **live table** holds the most recent rows in a bounded ring buffer, evolving its
schema as new fields appear. Rows evicted from the buffer can spill to rolling
Parquet files, so the full history stays queryable. Each row carries an ingest
sequence number (`LIVE_SEQ`) and timestamp (`LIVE_TIME`).
"""

from __future__ import annotations

import asyncio
import csv
import io
import json
import os
import threading
import time
from collections.abc import AsyncIterator, Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import polars as pl

from .registry import Registry

LIVE_SEQ = "__sweet_seq"
LIVE_TIME = "__sweet_ingested"


# -----------------------------------------------------------------------------
# Parsing
# -----------------------------------------------------------------------------


class LineParser:
    """Turns text lines into row dicts. Formats: "ndjson", "csv", "text", or "auto"."""

    def __init__(self, format: str = "auto") -> None:
        if format not in ("auto", "ndjson", "json", "csv", "tsv", "text"):
            raise ValueError(f"Unknown stream format '{format}' (ndjson, csv, tsv, text)")
        self.format = {"json": "ndjson"}.get(format, format)
        self._header: list[str] | None = None
        self.errors = 0

    def _detect(self, line: str) -> None:
        stripped = line.lstrip()
        if stripped.startswith(("{", "[")):
            self.format = "ndjson"
        elif "\t" in line and line.count("\t") >= line.count(","):
            self.format = "tsv"
        elif "," in line:
            self.format = "csv"
        else:
            self.format = "text"

    def parse(self, line: str) -> list[dict[str, Any]]:
        line = line.rstrip("\r\n")
        if not line.strip():
            return []
        if self.format == "auto":
            self._detect(line)
        if self.format == "ndjson":
            try:
                value = json.loads(line)
            except ValueError:
                self.errors += 1
                return [{"_raw": line}]
            if isinstance(value, list):
                return [v if isinstance(v, dict) else {"value": v} for v in value]
            return [value if isinstance(value, dict) else {"value": value}]
        if self.format in ("csv", "tsv"):
            delimiter = "\t" if self.format == "tsv" else ","
            fields = next(csv.reader(io.StringIO(line), delimiter=delimiter))
            if self._header is None:
                self._header = [f.strip() for f in fields]
                return []
            row = dict(zip(self._header, fields))
            return [{k: _coerce(v) for k, v in row.items()}]
        return [{"line": line}]


def _coerce(value: str) -> Any:
    """Best-effort typing of CSV fields (ints, floats, booleans, empty -> null)."""
    text = value.strip()
    if text == "":
        return None
    lowered = text.lower()
    if lowered in ("true", "false"):
        return lowered == "true"
    try:
        return int(text)
    except ValueError:
        pass
    try:
        return float(text)
    except ValueError:
        return value


# -----------------------------------------------------------------------------
# Sources
# -----------------------------------------------------------------------------


class StreamSource:
    """Base class: `batches()` yields lists of row dicts until the feed ends (or forever)."""

    kind = "stream"

    def __init__(self, format: str = "auto") -> None:
        self.parser = LineParser(format)

    @property
    def name(self) -> str:
        return "stream"

    @property
    def source(self) -> dict[str, Any]:
        """Provenance for pipelines (``{"stream": kind, ...}``)."""
        return {"stream": self.kind, "format": self.parser.format}

    async def batches(self) -> AsyncIterator[list[dict[str, Any]]]:  # pragma: no cover
        raise NotImplementedError
        yield []


class FileTailSource(StreamSource):
    """Follow a growing file. Reads existing content first (unless `from_start=False`)."""

    kind = "file"

    def __init__(
        self,
        path: str | Path,
        format: str = "auto",
        *,
        from_start: bool = True,
        poll: float = 0.2,
        max_batch: int = 5000,
    ) -> None:
        super().__init__(format)
        self.path = Path(path)
        self.from_start = from_start
        self.poll = poll
        self.max_batch = max_batch

    @property
    def name(self) -> str:
        return self.path.stem

    @property
    def source(self) -> dict[str, Any]:
        return {**super().source, "path": str(self.path)}

    async def batches(self) -> AsyncIterator[list[dict[str, Any]]]:
        handle = None
        inode = None
        partial = ""
        while True:
            if handle is None:
                try:
                    handle = open(self.path, encoding="utf-8", errors="replace")  # noqa: SIM115
                    inode = os.fstat(handle.fileno()).st_ino
                    if not self.from_start:
                        handle.seek(0, os.SEEK_END)
                        self.from_start = True  # Later reopens (rotation) read from the start
                except FileNotFoundError:
                    await asyncio.sleep(self.poll)
                    continue
            rows: list[dict[str, Any]] = []
            while len(rows) < self.max_batch:
                chunk = handle.readline()
                if not chunk:
                    break
                if not chunk.endswith("\n"):
                    partial += chunk  # Incomplete last line: wait for the rest
                    break
                rows.extend(self.parser.parse(partial + chunk))
                partial = ""
            if rows:
                yield rows
                continue
            # Nothing new: detect truncation or rotation, then wait
            try:
                stat = os.stat(self.path)
                if stat.st_ino != inode or stat.st_size < handle.tell():
                    handle.close()
                    handle = None
                    partial = ""
                    continue
            except FileNotFoundError:
                handle.close()
                handle = None
            await asyncio.sleep(self.poll)


class StdinSource(StreamSource):
    """Follow lines from a binary stream or file descriptor (e.g. piped stdin)."""

    kind = "stdin"

    def __init__(self, stream: Any, format: str = "auto", *, max_batch: int = 2000) -> None:
        super().__init__(format)
        self.stream = os.fdopen(stream, "rb", buffering=0) if isinstance(stream, int) else stream
        self.max_batch = max_batch

    @property
    def name(self) -> str:
        return "stdin"

    async def batches(self) -> AsyncIterator[list[dict[str, Any]]]:
        loop = asyncio.get_running_loop()
        queue: asyncio.Queue[bytes | None] = asyncio.Queue()

        def reader() -> None:
            for line in iter(self.stream.readline, b""):
                loop.call_soon_threadsafe(queue.put_nowait, line)
            loop.call_soon_threadsafe(queue.put_nowait, None)

        threading.Thread(target=reader, daemon=True).start()
        done = False
        while not done:
            line = await queue.get()
            rows: list[dict[str, Any]] = []
            while True:
                if line is None:
                    done = True
                    break
                rows.extend(self.parser.parse(line.decode("utf-8", "replace")))
                if len(rows) >= self.max_batch or queue.empty():
                    break
                line = queue.get_nowait()
            if rows:
                yield rows


class WebSocketSource(StreamSource):
    """Follow a WebSocket feed. Each message is JSON (object or array) or a text line.

    Reconnects with backoff if the connection drops.
    """

    kind = "websocket"

    def __init__(self, url: str, format: str = "auto", *, reconnect: bool = True) -> None:
        super().__init__(format)
        self.url = url
        self.reconnect = reconnect
        self.connected = False

    @property
    def name(self) -> str:
        parsed = urlparse(self.url)
        tail = Path(parsed.path).name
        return tail or parsed.hostname or "websocket"

    @property
    def source(self) -> dict[str, Any]:
        return {**super().source, "url": self.url}

    async def batches(self) -> AsyncIterator[list[dict[str, Any]]]:
        try:
            from websockets.asyncio.client import connect
            from websockets.exceptions import ConnectionClosed
        except ImportError as e:
            raise RuntimeError(
                "WebSocket feeds need the 'websockets' package: pip install 'sweet-data[stream]'"
            ) from e
        delay = 0.5
        while True:
            try:
                async with connect(self.url, max_size=2**24) as ws:
                    self.connected = True
                    delay = 0.5
                    async for message in ws:
                        text = (
                            message.decode("utf-8", "replace")
                            if isinstance(message, bytes)
                            else message
                        )
                        rows = []
                        for line in text.splitlines() or [text]:
                            rows.extend(self.parser.parse(line))
                        if rows:
                            yield rows
            except (OSError, ConnectionClosed):
                pass
            finally:
                self.connected = False
            if not self.reconnect:
                return
            await asyncio.sleep(delay)
            delay = min(delay * 2, 10)


#: URL scheme -> factory(target, format) for network stream sources (plugins add more)
STREAM_SOURCES: Registry[Callable[..., StreamSource]] = Registry("stream source")
STREAM_SOURCES.register("ws", lambda target, format="auto": WebSocketSource(target, format))
STREAM_SOURCES.register("wss", lambda target, format="auto": WebSocketSource(target, format))


def is_stream_target(target: str) -> bool:
    """Whether `target` is always live (e.g. a WebSocket URL)."""
    return urlparse(target).scheme.lower() in STREAM_SOURCES


def stream_source(
    target: str, *, format: str = "auto", stdin: Any = None, from_start: bool = True
) -> StreamSource:
    """The stream source for a target: a URL scheme, "-" (stdin), or a file path."""
    scheme = urlparse(target).scheme.lower()
    if scheme in STREAM_SOURCES:
        return STREAM_SOURCES.get(scheme)(target, format)
    if target == "-":
        if stdin is None:
            raise ValueError("No stdin stream to follow")
        return StdinSource(stdin, format)
    return FileTailSource(target, format, from_start=from_start)


# -----------------------------------------------------------------------------
# Live tables
# -----------------------------------------------------------------------------


class LiveTable:
    """A bounded, thread-safe buffer of the most recent rows of a stream.

    Args:
        capacity: Rows kept in memory; older rows are evicted (and spilled, if set).
        spill_dir: Directory for rolling Parquet files of evicted rows.
    """

    def __init__(
        self, name: str, *, capacity: int = 1_000_000, spill_dir: str | Path | None = None
    ):
        self.name = name
        self.capacity = capacity
        self.spill_dir = Path(spill_dir) if spill_dir else None
        if self.spill_dir:
            self.spill_dir.mkdir(parents=True, exist_ok=True)
        self._chunks: list[pl.DataFrame] = []
        self._snapshot: pl.DataFrame | None = None
        self._lock = threading.RLock()
        self._listeners: list[Callable[[pl.DataFrame], None]] = []
        self.total = 0  # Rows ever ingested
        self.version = 0  # Bumped on every append
        self.spilled = 0
        self._spill_parts = 0
        self.started = time.monotonic()
        self.last_append: float | None = None
        self._rate = 0.0
        self.error: str | None = None
        self.running = False

    # -- writing ------------------------------------------------------------------

    def append(self, rows: list[dict[str, Any]] | pl.DataFrame) -> pl.DataFrame:
        """Add rows; returns them as a DataFrame (with sequence and ingest-time columns)."""
        batch = (
            rows if isinstance(rows, pl.DataFrame) else pl.DataFrame(rows, infer_schema_length=None)
        )
        if batch.height == 0:
            return batch
        now = datetime.now(timezone.utc)
        with self._lock:
            batch = batch.with_columns(
                pl.int_range(self.total, self.total + batch.height, dtype=pl.Int64).alias(LIVE_SEQ),
                pl.lit(now).alias(LIVE_TIME),
            )
            self._chunks.append(batch)
            self.total += batch.height
            self.version += 1
            self._snapshot = None
            self._evict()
            elapsed = time.monotonic() - (self.last_append or self.started)
            instant = batch.height / max(elapsed, 1e-3)
            self._rate = instant if self._rate == 0 else 0.7 * self._rate + 0.3 * instant
            self.last_append = time.monotonic()
        for listener in list(self._listeners):
            try:
                listener(batch)
            except Exception:
                pass
        return batch

    def _evict(self) -> None:
        rows = sum(c.height for c in self._chunks)
        evicted: list[pl.DataFrame] = []
        while rows > self.capacity and self._chunks:
            excess = rows - self.capacity
            first = self._chunks[0]
            if first.height <= excess:
                evicted.append(self._chunks.pop(0))
                rows -= first.height
            else:
                evicted.append(first.head(excess))
                self._chunks[0] = first.slice(excess)
                rows -= excess
        if evicted:
            self.spilled += sum(e.height for e in evicted)
            if self.spill_dir is not None:
                self._spill_parts += 1
                part = pl.concat(evicted, how="diagonal_relaxed")
                part.write_parquet(self.spill_dir / f"part-{self._spill_parts:06d}.parquet")

    def subscribe(self, listener: Callable[[pl.DataFrame], None]) -> Callable[[], None]:
        self._listeners.append(listener)
        return lambda: self._listeners.remove(listener) if listener in self._listeners else None

    # -- reading ------------------------------------------------------------------

    def snapshot(self) -> pl.DataFrame:
        """The buffered rows (oldest first), as one frame."""
        with self._lock:
            if self._snapshot is None:
                if not self._chunks:
                    self._snapshot = pl.DataFrame(
                        schema={LIVE_SEQ: pl.Int64, LIVE_TIME: pl.Datetime("us", "UTC")}
                    )
                else:
                    self._snapshot = pl.concat(self._chunks, how="diagonal_relaxed").rechunk()
                    self._chunks = [self._snapshot]
            return self._snapshot

    def history(self) -> pl.LazyFrame:
        """Spilled rows plus the buffer: everything ingested (if spilling is on)."""
        current = self.snapshot().lazy()
        if self.spill_dir is None or not any(self.spill_dir.glob("part-*.parquet")):
            return current
        spilled = pl.scan_parquet(str(self.spill_dir / "part-*.parquet"))
        return pl.concat([spilled, current], how="diagonal_relaxed")

    @property
    def rows(self) -> int:
        with self._lock:
            return sum(c.height for c in self._chunks)

    @property
    def rate(self) -> float:
        """Recent ingest rate in rows per second (decays when the feed goes quiet)."""
        if self.last_append is None:
            return 0.0
        idle = time.monotonic() - self.last_append
        return self._rate if idle < 2 else self._rate * (2 / idle)

    def status(self) -> dict[str, Any]:
        return {
            "rows_buffered": self.rows,
            "rows_ingested": self.total,
            "rows_spilled": self.spilled,
            "rate_per_second": round(self.rate, 1),
            "capacity": self.capacity,
            "running": self.running,
            "error": self.error,
        }


async def pump(source: StreamSource, table: LiveTable, *, max_rows: int | None = None) -> None:
    """Feed `table` from `source` until the source ends (or `max_rows` arrive)."""
    table.running = True
    try:
        async for rows in source.batches():
            table.append(rows)
            if max_rows is not None and table.total >= max_rows:
                return
    except asyncio.CancelledError:
        raise
    except Exception as e:  # Record the failure where the UI and agents can see it
        table.error = f"{type(e).__name__}: {e}"
    finally:
        table.running = False


# -----------------------------------------------------------------------------
# Streaming jobs: run a pipeline continuously over a stream
# -----------------------------------------------------------------------------


class StreamSink:
    """Appends result batches to a file (or stdout).

    - ``.ndjson`` / ``.jsonl``: appended lines
    - ``.csv``: appended rows (header written once)
    - ``.parquet``: rolling part files ``<stem>-000001.parquet``, ... flushed every
      `flush_rows` rows
    - None: NDJSON lines to stdout
    """

    def __init__(self, path: str | None, *, flush_rows: int = 10_000, stdout: Any = None) -> None:
        import sys

        self.path = Path(path) if path else None
        self.flush_rows = flush_rows
        self.stdout = stdout or sys.stdout
        self.rows = 0
        self.parts = 0
        self._pending: list[pl.DataFrame] = []
        suffix = self.path.suffix.lower() if self.path else ".ndjson"
        if suffix not in (".ndjson", ".jsonl", ".csv", ".parquet"):
            raise ValueError("Streaming output must be .ndjson, .jsonl, .csv, or .parquet")
        self.kind = suffix.lstrip(".").replace("jsonl", "ndjson")
        if self.path is not None:
            self.path.parent.mkdir(parents=True, exist_ok=True)

    def write(self, batch: pl.DataFrame) -> None:
        batch = batch.select([c for c in batch.columns if not c.startswith("__sweet_")])
        if batch.height == 0:
            return
        self.rows += batch.height
        if self.path is None:
            self.stdout.write(batch.write_ndjson())
            self.stdout.flush()
        elif self.kind == "ndjson":
            with self.path.open("a") as f:
                f.write(batch.write_ndjson())
        elif self.kind == "csv":
            header = not self.path.exists() or self.path.stat().st_size == 0
            with self.path.open("a") as f:
                f.write(batch.write_csv(include_header=header))
        else:
            self._pending.append(batch)
            if sum(b.height for b in self._pending) >= self.flush_rows:
                self.flush()

    def flush(self) -> None:
        if self.kind == "parquet" and self._pending:
            self.parts += 1
            part = self.path.with_name(f"{self.path.stem}-{self.parts:06d}.parquet")
            pl.concat(self._pending, how="diagonal_relaxed").write_parquet(part)
            self._pending = []


async def run_stream(
    steps: list[Any],
    source: StreamSource,
    sink: StreamSink,
    *,
    max_rows: int | None = None,
    duration: float | None = None,
    on_batch: Callable[[int, int], None] | None = None,
) -> dict[str, Any]:
    """Apply `steps` to each batch from `source`, writing results to `sink`.

    Only steps that work row by row can run on a stream.

    Raises:
        ValueError: If a step needs all rows at once (e.g. sort, window_agg, sql).
    """
    active = [s for s in steps if s.enabled]
    stateful = [s.label for s in active if s.stateful]
    if stateful:
        raise ValueError(
            "These steps need all rows at once, so they can't run on a stream: "
            + "; ".join(stateful)
            + ". Run the pipeline on a file instead, or remove them."
        )
    rows_in = 0
    started = time.monotonic()

    async def consume() -> None:
        nonlocal rows_in
        async for rows in source.batches():
            batch = pl.DataFrame(rows, infer_schema_length=None)
            rows_in += batch.height
            for step in active:
                batch = step.apply(batch)
            sink.write(batch)
            if on_batch is not None:
                on_batch(rows_in, sink.rows)
            if max_rows is not None and rows_in >= max_rows:
                return

    try:
        if duration is not None:
            await asyncio.wait_for(consume(), timeout=duration)
        else:
            await consume()
    except asyncio.TimeoutError:
        pass
    finally:
        sink.flush()
    return {
        "rows_in": rows_in,
        "rows_out": sink.rows,
        "parts": sink.parts,
        "seconds": round(time.monotonic() - started, 3),
    }
