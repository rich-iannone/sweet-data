"""TableView: windowed, view-sorted access to a sheet, for humans and agents.

A view never changes data. It adds presentation state (currently a sort
order) on top of a sheet and serves *windows* of rows, so a viewer (or an
agent) only ever reads what it shows. Each fetched row carries its position
in the underlying data (`ROW_ID`), which is how edits made in a sorted view
target the right row.
"""

from __future__ import annotations

import threading
from collections import OrderedDict
from typing import TYPE_CHECKING, Any

import polars as pl

from .stats import ColumnStats, summarize

if TYPE_CHECKING:
    from .diff import TableDiff

if TYPE_CHECKING:
    from .workspace import Workspace

#: Column added to fetched windows: the row's 0-based position in the sheet's data.
ROW_ID = "__sweet_row"


class TableView:
    """A sorted, windowed view over one sheet of a `Workspace`.

    Thread-safe for concurrent `fetch()`/`row_count()`/`stats()` calls (viewers
    fetch from worker threads). Call `invalidate()` after the data changes.
    """

    def __init__(self, workspace: Workspace, sheet: str, *, chunk_size: int = 256) -> None:
        self.workspace = workspace
        self.sheet = sheet
        self.chunk_size = chunk_size
        self.sort: list[tuple[str, bool]] = []  # (column, descending)
        self._lock = threading.RLock()
        self._chunks: OrderedDict[int, pl.DataFrame] = OrderedDict()
        self._max_chunks = 64
        self._row_count: int | None = None
        self._stats: dict[str, ColumnStats] = {}
        self._sorted: pl.DataFrame | None = None  # Sorted result, for in-memory sheets
        self.version = 0  # Bumped on every invalidation
        # Modes (both read-only): a diff preview, or the data as of an earlier step
        self.diff: TableDiff | None = None
        self.at_step: int | None = None
        self._time_frame: pl.LazyFrame | None = None

    # -- data -------------------------------------------------------------------

    @property
    def base(self) -> pl.LazyFrame:
        """The data being viewed (a lazy plan, even for in-memory sheets).

        Normally the sheet's data; in a diff preview, the *after* data; when
        viewing an earlier step, the data as of that step.
        """
        if self.diff is not None and self.diff.after is not None:
            return self.diff.after
        if self.at_step is not None and self._time_frame is not None:
            return self._time_frame
        sheet = self.workspace._workbook.sheets[self.sheet]
        return sheet.lf

    @property
    def read_only(self) -> bool:
        """Whether the view shows something other than the sheet's current data."""
        return self.diff is not None or self.at_step is not None

    @property
    def diff_rows(self) -> bool:
        """Whether rows carry diff statuses (a lineage or keyed diff preview)."""
        return self.diff is not None and self.diff.frame is not None

    def set_mode(self, *, diff: TableDiff | None = None, at_step: int | None = None) -> None:
        """Show a diff preview, the data as of step `at_step`, or (neither) the live data."""
        self.diff = diff
        self.at_step = at_step
        self._time_frame = (
            self.workspace.frame_at(at_step, self.sheet).lazy() if at_step is not None else None
        )
        if diff is not None or at_step is not None:
            self.sort = []
        self.invalidate()

    @property
    def is_lazy(self) -> bool:
        return self.workspace._workbook.sheets[self.sheet].is_lazy

    @property
    def schema(self) -> pl.Schema:
        """Schema of what's displayed (diff previews include Sweet's diff columns)."""
        if self.diff_rows:
            return self.diff.frame.collect_schema()
        return self.base.collect_schema()

    @property
    def columns(self) -> list[str]:
        return self.schema.names()

    def frame(self) -> pl.LazyFrame:
        """The viewed data: the base plus `ROW_ID`, in view-sort order."""
        if self.diff_rows:
            lf = self.diff.frame
            if ROW_ID not in lf.collect_schema():
                lf = lf.with_row_index(ROW_ID)
            return lf
        lf = self.base.with_row_index(ROW_ID)
        if self.sort:
            cols = [c for c, _ in self.sort]
            desc = [d for _, d in self.sort]
            lf = lf.sort(cols, descending=desc, nulls_last=True, maintain_order=True)
        return lf

    def invalidate(self, *, stats: bool = True, count: bool | None = None) -> None:
        """Forget cached rows (and stats); call after the data or sort changes.

        `count` (default: same as `stats`) also forgets the row count; live data
        refreshes forget rows and the count but keep (slightly stale) stats.
        """
        with self._lock:
            self._chunks.clear()
            self._sorted = None
            if stats if count is None else count:
                self._row_count = None
            if stats:
                self._stats = {}
            self.version += 1

    def set_sort(self, sort: list[tuple[str, bool]]) -> None:
        unknown = [c for c, _ in sort if c not in self.columns]
        if unknown:
            raise ValueError(f"Column(s) not found: {', '.join(unknown)}")
        self.sort = list(sort)
        self.invalidate(stats=False)

    def toggle_sort(self, column: str, descending: bool = False) -> None:
        """Sort by `column` (as the primary key); the same request again removes it."""
        if self.sort and self.sort[0] == (column, descending):
            self.set_sort([s for s in self.sort if s[0] != column])
        else:
            self.set_sort([(column, descending)] + [s for s in self.sort if s[0] != column])

    # -- windows -----------------------------------------------------------------

    def cached_chunk(self, index: int) -> pl.DataFrame | None:
        with self._lock:
            chunk = self._chunks.get(index)
            if chunk is not None:
                self._chunks.move_to_end(index)
            return chunk

    def load_chunk(self, index: int) -> pl.DataFrame:
        """Fetch (and cache) chunk `index` of the viewed data."""
        chunk = self.cached_chunk(index)
        if chunk is not None:
            return chunk
        version = self.version
        if self.sort and not self.is_lazy:
            # In-memory data: sort once per version, then slice windows from it
            with self._lock:
                sorted_df = self._sorted
            if sorted_df is None:
                sorted_df = self.frame().collect()
                with self._lock:
                    if version == self.version:
                        self._sorted = sorted_df
            chunk = sorted_df.slice(index * self.chunk_size, self.chunk_size)
        else:
            chunk = (
                self.frame()
                .slice(index * self.chunk_size, self.chunk_size)
                .collect(engine="streaming")
            )
        with self._lock:
            if version == self.version:  # Don't cache results of a stale view
                self._chunks[index] = chunk
                while len(self._chunks) > self._max_chunks:
                    self._chunks.popitem(last=False)
                if chunk.height < self.chunk_size and self._row_count is None:
                    self._row_count = index * self.chunk_size + chunk.height
        return chunk

    def fetch(self, offset: int, limit: int) -> pl.DataFrame:
        """Rows [offset, offset + limit) of the view, with `ROW_ID`."""
        if limit <= 0:
            return self.frame().head(0).collect()
        first = offset // self.chunk_size
        last = (offset + limit - 1) // self.chunk_size
        parts = [self.load_chunk(i) for i in range(first, last + 1)]
        joined = pl.concat(parts) if len(parts) > 1 else parts[0]
        return joined.slice(offset - first * self.chunk_size, limit)

    def row(self, index: int) -> dict[str, Any] | None:
        """Row `index` of the view as a dict, from cache only (None if not loaded)."""
        chunk = self.cached_chunk(index // self.chunk_size)
        if chunk is None:
            return None
        offset = index % self.chunk_size
        return chunk.row(offset, named=True) if offset < chunk.height else None

    def row_count(self) -> int:
        with self._lock:
            if self._row_count is not None:
                return self._row_count
        version = self.version
        counted = self.diff.frame if self.diff_rows else self.base
        count = counted.select(pl.len()).collect(engine="streaming").item()
        with self._lock:
            if version == self.version:
                self._row_count = count
        return count

    @property
    def known_row_count(self) -> int | None:
        return self._row_count

    # -- statistics ---------------------------------------------------------------

    def stats(self, columns: list[str] | None = None) -> dict[str, ColumnStats]:
        """Column statistics over all rows (cached until `invalidate()`)."""
        wanted = columns or self.columns
        with self._lock:
            missing = [c for c in wanted if c not in self._stats]
        if missing:
            version = self.version
            computed = summarize(self.base, missing)
            with self._lock:
                if version == self.version:
                    self._stats.update(computed)
                else:
                    return {c: computed[c] for c in wanted if c in computed}
        with self._lock:
            return {c: self._stats[c] for c in wanted if c in self._stats}

    def cached_stats(self, column: str) -> ColumnStats | None:
        return self._stats.get(column)

    # -- text rendering (agents, CLI) ----------------------------------------------

    def to_markdown(self, offset: int = 0, limit: int = 20, *, max_width: int = 40) -> str:
        """A compact Markdown table of a window, for agents and logs."""
        window = self.fetch(offset, limit)
        cols = [c for c in window.columns if c != ROW_ID]
        header = "| # | " + " | ".join(cols) + " |"
        rule = "|---|" + "---|" * len(cols)
        lines = [header, rule]
        for row in window.iter_rows(named=True):
            cells = [_short(row[c], max_width) for c in cols]
            lines.append(f"| {row[ROW_ID]} | " + " | ".join(cells) + " |")
        return "\n".join(lines)


def _short(value: Any, width: int) -> str:
    text = "∅" if value is None else str(value).replace("|", "\\|").replace("\n", " ")
    return text if len(text) <= width else text[: width - 1] + "…"
