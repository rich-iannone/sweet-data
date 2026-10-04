"""DataView: a virtualized grid over a `TableView`.

Only the visible rows are rendered, and rows are fetched in chunks on demand
(in worker threads for lazy data), so the size of the data doesn't matter.
The header shows each column's name, type, null share, and a sparkline of
its distribution.
"""

from __future__ import annotations

import datetime as dt
import math
from typing import Any

import polars as pl
from rich.segment import Segment
from rich.style import Style
from textual.binding import Binding
from textual.events import Click, Resize
from textual.geometry import Size
from textual.message import Message
from textual.reactive import reactive
from textual.scroll_view import ScrollView
from textual.strip import Strip

from ...core.diff import STATUS, changed_column, old_column
from ...core.stats import column_kind
from ...core.view import ROW_ID, TableView

HEADER_LINES = 4
MIN_WIDTH = 4
MAX_WIDTH = 32
SEPARATOR = " │ "

_SHORT_TYPES = {
    "Int8": "i8", "Int16": "i16", "Int32": "i32", "Int64": "i64",
    "UInt8": "u8", "UInt16": "u16", "UInt32": "u32", "UInt64": "u64",
    "Float32": "f32", "Float64": "f64", "String": "str", "Boolean": "bool",
    "Date": "date", "Time": "time", "Categorical": "cat", "Null": "null",
}  # fmt: skip


def short_type(dtype: pl.DataType) -> str:
    """Compact dtype label, e.g. Int64 -> i64, Datetime(us) -> datetime."""
    name = dtype.base_type().__name__ if hasattr(dtype, "base_type") else str(dtype)
    return _SHORT_TYPES.get(name, name.lower())


def format_value(value: Any) -> str:
    """Render a cell value as display text."""
    if value is None:
        return "∅"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float):
        if math.isnan(value):
            return "NaN"
        if math.isinf(value):
            return "∞" if value > 0 else "-∞"
        return format(value, ".10g")
    if isinstance(value, dt.datetime):
        return value.isoformat(sep=" ")
    if isinstance(value, str):
        return value.replace("\r\n", "↵").replace("\n", "↵").replace("\t", "→")
    return str(value)


def _fit(text: str, width: int, right: bool) -> str:
    if len(text) > width:
        return text[: max(width - 1, 0)] + "…"
    return text.rjust(width) if right else text.ljust(width)


class DataView(ScrollView, can_focus=True):
    """A keyboard- and mouse-driven virtual grid."""

    COMPONENT_CLASSES = {
        "dataview--header",
        "dataview--header-current",
        "dataview--type",
        "dataview--spark",
        "dataview--rule",
        "dataview--gutter",
        "dataview--cursor",
        "dataview--cursor-row",
        "dataview--null",
        "dataview--pending",
        "dataview--removed",
        "dataview--added",
        "dataview--changed",
        "dataview--header-added",
        "dataview--header-removed",
        "dataview--selection",
        "dataview--badge",
    }

    DEFAULT_CSS = """
    DataView {
        background: $surface;
        scrollbar-size: 1 1;
    }
    DataView > .dataview--header { text-style: bold; color: $text; }
    DataView > .dataview--header-current { text-style: bold reverse; color: $accent; }
    DataView > .dataview--type { color: $text-muted; }
    DataView > .dataview--spark { color: $accent; }
    DataView > .dataview--rule { color: $panel-lighten-2; }
    DataView > .dataview--gutter { color: $text-muted; }
    DataView > .dataview--cursor { background: $accent; color: $text; text-style: bold; }
    DataView > .dataview--cursor-row { background: $boost; }
    DataView > .dataview--null { color: $text-disabled; }
    DataView > .dataview--pending { color: $text-disabled; }
    DataView > .dataview--removed { color: $error; text-style: strike; }
    DataView > .dataview--added { color: $success; }
    DataView > .dataview--changed { background: $warning 35%; color: $text; text-style: bold; }
    DataView > .dataview--header-added { color: $success; text-style: bold underline; }
    DataView > .dataview--header-removed { color: $error; text-style: bold strike; }
    DataView > .dataview--selection { background: $accent 30%; }
    DataView > .dataview--badge { color: $warning; text-style: bold; }
    """

    BINDINGS = [
        Binding("up,k", "cursor(-1, 0)", "Up", show=False),
        Binding("down,j", "cursor(1, 0)", "Down", show=False),
        Binding("left,h", "cursor(0, -1)", "Left", show=False),
        Binding("right,l", "cursor(0, 1)", "Right", show=False),
        Binding("pageup", "page(-1)", "Page up", show=False),
        Binding("pagedown", "page(1)", "Page down", show=False),
        Binding("home", "first_column", "First column", show=False),
        Binding("end", "last_column", "Last column", show=False),
        Binding("ctrl+home,g", "top", "Top", show=False),
        Binding("ctrl+end,G", "bottom", "Bottom", show=False),
        Binding("shift+up", "extend(-1, 0)", "Select up", show=False),
        Binding("shift+down", "extend(1, 0)", "Select down", show=False),
        Binding("shift+left", "extend(0, -1)", "Select left", show=False),
        Binding("shift+right", "extend(0, 1)", "Select right", show=False),
    ]

    cursor_row: reactive[int] = reactive(0, always_update=True, repaint=False)
    cursor_column: reactive[int] = reactive(0, always_update=True, repaint=False)

    class CursorMoved(Message):
        """The cursor moved to (`row`, `column`) of the view."""

        def __init__(self, data_view: DataView, row: int, column: int) -> None:
            super().__init__()
            self.data_view = data_view
            self.row = row
            self.column = column

    class RowsLoaded(Message):
        """Rows arrived from a background fetch (anything showing them may update)."""

    class CellActivated(Message):
        """A cell was activated (double-clicked)."""

        def __init__(self, data_view: DataView, row: int, column: int) -> None:
            super().__init__()
            self.data_view = data_view
            self.row = row
            self.column = column

    #: Highlight colors agents may use, mapped to backgrounds
    HIGHLIGHT_COLORS = {
        "yellow": "#5c5000",
        "red": "#5c1a1a",
        "green": "#1a4d1a",
        "blue": "#1a2f5c",
        "magenta": "#4d1a4d",
        "cyan": "#0f4a4a",
    }

    def __init__(self, view: TableView | None = None, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        # Callables supplied by the app: highlights for (row id, column), and a
        # badge for a column header (e.g. "🔒" for columns masked from agents)
        self.highlight_lookup: Any = None
        self.column_badge: Any = None
        self.anchor: tuple[int, int] | None = None  # Selection anchor (row, column)
        self.view: TableView | None = None
        self._columns: list[str] = []
        self._dtypes: list[pl.DataType] = []
        self._widths: list[int] = []
        self._offsets: list[int] = []  # Start x of each column within the columns area
        self._pending: set[tuple[int, int]] = set()  # (version, chunk) being fetched
        self._rows_hint = 0  # Rows known to exist (before the full count is known)
        self._widths_version = -1
        if view is not None:
            self.set_view(view)

    # -- public API ----------------------------------------------------------------

    def set_view(self, view: TableView) -> None:
        """Show `view`, resetting the cursor and scroll position."""
        self.view = view
        self._rows_hint = 0
        self.cursor_row = 0
        self.cursor_column = 0
        self.scroll_to(0, 0, animate=False)
        self.reload()

    def reload(self) -> None:
        """Re-read the view's schema and size (call after the data or sort changes)."""
        if self.view is None:
            return
        schema = self.view.schema
        self._columns = [c for c in schema.names() if not c.startswith("__sweet_")]
        self._dtypes = [schema[c] for c in self._columns]
        self._pending.clear()
        if not self.view.is_lazy:
            self.view.row_count()
        self._compute_widths(sample=None)
        self._widths_version = -1
        self._update_virtual_size()
        self.cursor_row = min(self.cursor_row, max(self.total_rows - 1, 0))
        self.cursor_column = min(self.cursor_column, max(len(self._columns) - 1, 0))
        self.refresh()

    @property
    def columns(self) -> list[str]:
        return list(self._columns)

    @property
    def total_rows(self) -> int:
        """Rows in the view (a lower bound until the count is known)."""
        if self.view is None:
            return 0
        known = self.view.known_row_count
        return known if known is not None else self._rows_hint

    @property
    def current_column(self) -> str | None:
        return self._columns[self.cursor_column] if self._columns else None

    def current_row(self) -> dict[str, Any] | None:
        """The row under the cursor (from cache), including `ROW_ID`."""
        return self._row(self.cursor_row)

    def current_value(self) -> Any:
        row = self.current_row()
        column = self.current_column
        return None if row is None or column is None else row.get(column)

    def move_cursor(
        self, row: int | None = None, column: int | str | None = None, *, extend: bool = False
    ) -> None:
        """Move the cursor (column by index or name) and scroll it into view.

        With `extend`, grow the selection from its anchor instead of clearing it.
        """
        if extend and self.anchor is None:
            self.anchor = (self.cursor_row, self.cursor_column)
        elif not extend:
            self.anchor = None
        if isinstance(column, str):
            column = self._columns.index(column)
        if row is not None:
            limit = self.total_rows - 1 if self.total_rows else 0
            self.cursor_row = max(0, min(row, limit))
        if column is not None:
            self.cursor_column = max(0, min(column, len(self._columns) - 1))
        self._scroll_cursor_into_view()
        self.refresh()
        self.post_message(self.CursorMoved(self, self.cursor_row, self.cursor_column))

    @property
    def selection(self) -> tuple[int, int, int, int]:
        """(first row, last row, first column, last column), inclusive; the cursor if no range."""
        if self.anchor is None:
            return (self.cursor_row, self.cursor_row, self.cursor_column, self.cursor_column)
        r0, c0 = self.anchor
        return (
            min(r0, self.cursor_row),
            max(r0, self.cursor_row),
            min(c0, self.cursor_column),
            max(c0, self.cursor_column),
        )

    def _selected(self, row: int, column: int) -> bool:
        if self.anchor is None:
            return False
        r0, r1, c0, c1 = self.selection
        return r0 <= row <= r1 and c0 <= column <= c1

    def visible_window(self) -> list[dict[str, Any]]:
        """The rows currently on screen (from cache), for agents' screen view."""
        if self.view is None:
            return []
        first = int(self.scroll_y)
        count = max(self.size.height - HEADER_LINES, 0)
        rows = []
        for index in range(first, min(first + count, self.total_rows)):
            row = self.view.row(index)
            if row is not None:
                rows.append(
                    {k: v for k, v in row.items() if not k.startswith("__sweet_") or k == ROW_ID}
                )
        return rows

    def state(self) -> dict[str, Any]:
        """A structured description of what's on screen (for agents and tests)."""
        first = int(self.scroll_y)
        visible = max(self.size.height - HEADER_LINES, 0)
        return {
            "columns": self.columns,
            "total_rows": self.total_rows,
            "row_count_known": self.view is not None and self.view.known_row_count is not None,
            "cursor": {"row": self.cursor_row, "column": self.current_column},
            "visible_rows": [first, min(first + visible, self.total_rows)],
            "sort": [
                {"column": c, "descending": d} for c, d in (self.view.sort if self.view else [])
            ],
        }

    # -- geometry ----------------------------------------------------------------

    @property
    def gutter_width(self) -> int:
        return len(f"{max(self.total_rows, 1):,}") + 2

    def _compute_widths(self, sample: pl.DataFrame | None) -> None:
        widths = []
        for name, dtype in zip(self._columns, self._dtypes):
            width = max(len(name) + 2, len(short_type(dtype)) + 9, MIN_WIDTH)
            if sample is not None and name in sample.columns and sample.height:
                longest = max(len(format_value(v)) for v in sample[name].head(200).to_list())
                width = max(width, longest)
            widths.append(min(width, MAX_WIDTH))
        self._widths = widths
        offsets, x = [], 0
        for w in widths:
            offsets.append(x)
            x += w + len(SEPARATOR)
        self._offsets = offsets

    def _update_virtual_size(self) -> None:
        columns_width = (self._offsets[-1] + self._widths[-1]) if self._widths else 0
        self.virtual_size = Size(
            self.gutter_width + columns_width + 1, HEADER_LINES + max(self.total_rows, 1)
        )

    def _scroll_cursor_into_view(self) -> None:
        body = max(self.size.height - HEADER_LINES, 1)
        y = self.scroll_y
        if self.cursor_row < y:
            y = self.cursor_row
        elif self.cursor_row >= y + body:
            y = self.cursor_row - body + 1
        x = self.scroll_x
        if self._widths:
            area = max(self.size.width - self.gutter_width, 1)
            left = self._offsets[self.cursor_column]
            right = left + self._widths[self.cursor_column]
            if left < x:
                x = left
            elif right > x + area:
                x = right - area
        self.scroll_to(x, y, animate=False)

    # -- data access --------------------------------------------------------------

    def _row(self, index: int) -> dict[str, Any] | None:
        if self.view is None or index < 0:
            return None
        row = self.view.row(index)
        if row is not None:
            return row
        chunk = index // self.view.chunk_size
        if not self.view.is_lazy:
            try:
                self.view.load_chunk(chunk)
            except Exception:
                return None
            self._after_chunk(chunk, self.view.version, refresh=False)
            return self.view.row(index)
        key = (self.view.version, chunk)
        if key not in self._pending:
            self._pending.add(key)
            self.run_worker(
                lambda: self._fetch_chunk(*key), thread=True, group="chunks", exit_on_error=False
            )
        return None

    def _fetch_chunk(self, version: int, chunk: int) -> None:
        view = self.view
        if view is None or view.version != version:
            return
        try:
            view.load_chunk(chunk)
        except Exception as e:  # Reading data can fail (bad values, network)
            self.app.call_from_thread(self._fetch_failed, chunk, version, e)
        else:
            self.app.call_from_thread(self._after_chunk, chunk, version)

    def _fetch_failed(self, chunk: int, version: int, error: Exception) -> None:
        self._pending.discard((version, chunk))
        if self.view is not None and self.view.version == version:
            message = str(error).splitlines()[0][:200]
            self.notify(f"Couldn't read rows: {message}", severity="error", timeout=10)

    def _after_chunk(self, chunk: int, version: int, refresh: bool = True) -> None:
        self._pending.discard((version, chunk))
        view = self.view
        if view is None or view.version != version:
            return
        loaded = view.cached_chunk(chunk)
        if loaded is not None:
            end = chunk * view.chunk_size + loaded.height
            if loaded.height == view.chunk_size:
                end += 1  # There may be more
            if end > self._rows_hint:
                self._rows_hint = end
        if self._widths_version != version and chunk == 0 and loaded is not None:
            self._widths_version = version
            self._compute_widths(loaded)
        self._update_virtual_size()
        if refresh:
            self.refresh()
            self.post_message(self.RowsLoaded())

    # -- rendering ----------------------------------------------------------------

    def render_line(self, y: int) -> Strip:
        width = self.size.width
        if self.view is None or not self._columns:
            return Strip.blank(width, self.rich_style)
        gutter = self.gutter_width
        area = max(width - gutter, 0)
        x0 = int(self.scroll_x)

        if y < HEADER_LINES:
            gutter_strip = Strip(
                [Segment(" " * gutter, self.get_component_rich_style("dataview--gutter"))]
            )
            body = self._render_header(y, x0, area)
        else:
            index = int(self.scroll_y) + y - HEADER_LINES
            if index >= self.total_rows and self.view.known_row_count is not None:
                return Strip.blank(width, self.rich_style)
            gutter_text = f"{index + 1:,}".rjust(gutter - 1) + " "
            gutter_strip = Strip(
                [Segment(gutter_text, self.get_component_rich_style("dataview--gutter"))]
            )
            body = self._render_row(index, x0, area)
        return Strip.join([gutter_strip, body]).crop_extend(0, width, self.rich_style)

    def _visible_columns(self, x0: int, area: int) -> list[int]:
        return [
            i
            for i, (start, w) in enumerate(zip(self._offsets, self._widths))
            if start + w + len(SEPARATOR) > x0 and start < x0 + area
        ]

    def _assemble(
        self, cells: list[tuple[int, list[Segment]]], x0: int, area: int, separator: str = SEPARATOR
    ) -> Strip:
        if not cells:
            return Strip.blank(area, self.rich_style)
        rule = self.get_component_rich_style("dataview--rule")
        segments: list[Segment] = []
        for i, cell in cells:
            segments.extend(cell)
            segments.append(Segment(separator, rule))
        start = self._offsets[cells[0][0]]
        return Strip(segments).crop(x0 - start, x0 - start + area)

    def _render_header(self, line: int, x0: int, area: int) -> Strip:
        view = self.view
        sort_rank = {c: (n + 1, d) for n, (c, d) in enumerate(view.sort)}
        cells = []
        for i in self._visible_columns(x0, area):
            name, dtype, width = self._columns[i], self._dtypes[i], self._widths[i]
            if line == 0:
                marker = ""
                if name in sort_rank:
                    rank, desc = sort_rank[name]
                    marker = ("▼" if desc else "▲") + (str(rank) if len(sort_rank) > 1 else "")
                text = _fit(name, width - len(marker), False) + marker
                style = self.get_component_rich_style(
                    "dataview--header-current" if i == self.cursor_column else "dataview--header"
                )
                diff = view.diff if view.diff_rows else None
                if diff is not None and i != self.cursor_column:
                    if name in diff.added_columns:
                        style = self.get_component_rich_style("dataview--header-added")
                    elif name in diff.removed_columns:
                        style = self.get_component_rich_style("dataview--header-removed")
            elif line == 1:
                stats = view.cached_stats(name)
                label = short_type(dtype)
                badge = self.column_badge(name) if self.column_badge else ""
                if badge:
                    label = f"{badge} {label}"
                if stats is not None and stats.null_count:
                    label += f" · {stats.null_fraction:.0%} ∅"
                text = _fit(label, width, False)
                style = self.get_component_rich_style("dataview--type")
            elif line == 2:
                stats = view.cached_stats(name)
                spark = stats.sparkline(min(width, 12)) if stats is not None else "…"
                text = _fit(spark, width, False)
                style = self.get_component_rich_style("dataview--spark")
            else:
                text = "─" * width
                style = self.get_component_rich_style("dataview--rule")
            cells.append((i, [Segment(text, style)]))
        return self._assemble(cells, x0, area, "─┼─" if line == 3 else SEPARATOR)

    def _render_row(self, index: int, x0: int, area: int) -> Strip:
        row = self._row(index)
        base = self.rich_style
        is_cursor_row = index == self.cursor_row
        row_style = (
            base + self.get_component_rich_style("dataview--cursor-row") if is_cursor_row else base
        )
        cells = []
        for i in self._visible_columns(x0, area):
            name, width = self._columns[i], self._widths[i]
            if row is None:
                text, style = (
                    _fit("·", width, False),
                    self.get_component_rich_style("dataview--pending"),
                )
            else:
                value = row.get(name)
                right = column_kind(self._dtypes[i]) == "numeric"
                text = _fit(format_value(value), width, right)
                style = (
                    self.get_component_rich_style("dataview--null") if value is None else Style()
                )
                status = row.get(STATUS)
                if status == "-" or (status is not None and name in self._removed_columns()):
                    style = self.get_component_rich_style("dataview--removed")
                elif status == "+" or (status is not None and name in self._added_columns()):
                    style = style + self.get_component_rich_style("dataview--added")
                elif status == "~" and row.get(changed_column(name)):
                    style = self.get_component_rich_style("dataview--changed")
            if row is not None and self.highlight_lookup is not None:
                color = self.highlight_lookup(row.get(ROW_ID), name)
                if color:
                    style = style + Style(bgcolor=self.HIGHLIGHT_COLORS.get(color, color))
            if self._selected(index, i):
                style = style + self.get_component_rich_style("dataview--selection")
            style = row_style + style
            if is_cursor_row and i == self.cursor_column and self.has_focus:
                style = style + self.get_component_rich_style("dataview--cursor")
            elif is_cursor_row and i == self.cursor_column:
                style = style + Style(reverse=True)
            cells.append((i, [Segment(text, style)]))
        return self._assemble(cells, x0, area)

    def _added_columns(self) -> list[str]:
        return self.view.diff.added_columns if self.view and self.view.diff_rows else []

    def _removed_columns(self) -> list[str]:
        return self.view.diff.removed_columns if self.view and self.view.diff_rows else []

    def cell_change(self) -> tuple[str, Any] | None:
        """For a diff preview: ("changed", old value), ("removed", None), or ("added", None)."""
        row = self.current_row()
        column = self.current_column
        if row is None or column is None or STATUS not in row:
            return None
        status = row[STATUS]
        if status == "-":
            return ("removed", None)
        if status == "+":
            return ("added", None)
        if status == "~" and row.get(changed_column(column)):
            return ("changed", row.get(old_column(column)))
        return None

    # -- events & actions ------------------------------------------------------------

    def on_resize(self, event: Resize) -> None:
        self._update_virtual_size()

    def on_focus(self) -> None:
        self.refresh()

    def on_blur(self) -> None:
        self.refresh()

    def _cell_at(self, x: int, y: int) -> tuple[int | None, int | None]:
        column = None
        if x >= self.gutter_width:
            cx = int(self.scroll_x) + x - self.gutter_width
            for i, (start, w) in enumerate(zip(self._offsets, self._widths)):
                if start <= cx < start + w + len(SEPARATOR):
                    column = i
                    break
        row = int(self.scroll_y) + y - HEADER_LINES if y >= HEADER_LINES else None
        return row, column

    def on_click(self, event: Click) -> None:
        row, column = self._cell_at(event.x, event.y)
        if row is not None and row >= self.total_rows:
            return
        self.move_cursor(row=row, column=column)
        if event.chain == 2 and row is not None and column is not None:
            self.post_message(self.CellActivated(self, row, column))

    def action_cursor(self, rows: int, columns: int) -> None:
        self.move_cursor(row=self.cursor_row + rows, column=self.cursor_column + columns)

    def action_extend(self, rows: int, columns: int) -> None:
        self.move_cursor(
            row=self.cursor_row + rows, column=self.cursor_column + columns, extend=True
        )

    def action_page(self, direction: int) -> None:
        page = max(self.size.height - HEADER_LINES - 1, 1)
        self.move_cursor(row=self.cursor_row + direction * page)

    def action_first_column(self) -> None:
        self.move_cursor(column=0)

    def action_last_column(self) -> None:
        self.move_cursor(column=len(self._columns) - 1)

    def action_top(self) -> None:
        self.move_cursor(row=0)

    def action_bottom(self) -> None:
        if self.view is not None and self.view.known_row_count is None:
            self.view.row_count()  # Bottom needs the true size
            self._update_virtual_size()
        self.move_cursor(row=self.total_rows - 1)


__all__ = ["DataView", "HEADER_LINES", "ROW_ID", "format_value", "short_type"]
