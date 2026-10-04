"""Viewer panels: the column inspector, the dataset overview, and prompts."""

from __future__ import annotations

import math
from typing import Any

from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical, VerticalScroll
from textual.message import Message
from textual.screen import ModalScreen
from textual.widgets import DataTable, Input, Label, OptionList, Static
from textual.widgets.option_list import Option

from ...core.stats import ColumnStats
from .data_view import format_value

BAR_WIDTH = 18


def _num(value: Any) -> str:
    if value is None:
        return "–"
    if isinstance(value, float):
        if math.isnan(value):
            return "NaN"
        return format(value, ".6g")
    if isinstance(value, int):
        return f"{value:,}"
    return format_value(value)


def _bar(count: int, peak: int, width: int = BAR_WIDTH) -> str:
    if peak <= 0 or count <= 0:
        return ""
    full = count / peak * width
    whole = int(full)
    partial = " ▏▎▍▌▋▊▉"[int((full - whole) * 8)]
    return ("█" * whole + (partial if partial != " " else "")) or "▏"


class Inspector(VerticalScroll):
    """Statistics for one column. Selecting a top value requests a filter."""

    DEFAULT_CSS = """
    Inspector {
        width: 46;
        dock: right;
        border-left: tall $panel-lighten-1;
        padding: 0 1;
        background: $panel;
    }
    Inspector.hidden { display: none; }
    Inspector #inspector-title { text-style: bold; color: $accent; margin-bottom: 1; }
    Inspector .section { text-style: bold; margin-top: 1; }
    Inspector OptionList { height: auto; max-height: 14; border: none; padding: 0; }
    """

    class FilterRequested(Message):
        """The user picked a value to filter on."""

        def __init__(self, column: str, value: Any) -> None:
            super().__init__()
            self.column = column
            self.value = value

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._column: str | None = None
        self._values: list[Any] = []

    def compose(self) -> ComposeResult:
        yield Static("", id="inspector-title")
        yield Static("", id="inspector-summary")
        yield Static("", id="inspector-histogram")
        yield Label("Top values (⏎ to filter)", classes="section", id="inspector-top-label")
        yield OptionList(id="inspector-top")

    def show(self, column: str | None, stats: ColumnStats | None, dtype: str = "") -> None:
        self._column = column
        title = self.query_one("#inspector-title", Static)
        summary = self.query_one("#inspector-summary", Static)
        histogram = self.query_one("#inspector-histogram", Static)
        top_label = self.query_one("#inspector-top-label", Label)
        options = self.query_one("#inspector-top", OptionList)
        options.clear_options()
        self._values = []
        title.update(f"{column}  [dim]{dtype}[/dim]" if column else "")
        if stats is None:
            summary.update("[dim]Computing statistics…[/dim]" if column else "")
            histogram.update("")
            top_label.display = False
            options.display = False
            return

        rows = [
            ("Rows", _num(stats.count)),
            ("Nulls", f"{_num(stats.null_count)} ({stats.null_fraction:.1%})"),
            ("Distinct", f"~{_num(stats.n_unique)}" if stats.n_unique is not None else "–"),
        ]
        if stats.min is not None:
            rows += [("Min", _num(stats.min)), ("Max", _num(stats.max))]
        if stats.mean is not None:
            rows += [("Mean", _num(stats.mean)), ("Std dev", _num(stats.std))]
        for key, label in (("q25", "25%"), ("q50", "Median"), ("q75", "75%")):
            if key in stats.quantiles:
                rows.append((label, _num(stats.quantiles[key])))
        summary.update("\n".join(f"[dim]{k:<9}[/dim] {v}" for k, v in rows))

        if stats.histogram:
            peak = max(stats.histogram)
            edges = stats.bin_edges or []
            lines = ["[b]Distribution[/b]"]
            for i, count in enumerate(stats.histogram):
                low = _num(edges[i]) if i < len(edges) else ""
                lines.append(f"[dim]{low:>10}[/dim] [$accent]{_bar(count, peak)}[/] {count:,}")
            histogram.update("\n".join(lines))
        else:
            histogram.update("")

        top = stats.top_values or []
        top_label.display = options.display = bool(top)
        if top:
            peak = max(c for _, c in top)
            for value, count in top:
                self._values.append(value)
                label = Text.assemble(
                    (f"{format_value(value)[:16]:<16} ", "bold" if value is not None else "dim"),
                    (f"{_bar(count, peak, 12):<12}", "cyan"),
                    f" {count:,}",
                )
                options.add_option(Option(label))

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        event.stop()
        if self._column is not None and event.option_index < len(self._values):
            self.post_message(self.FilterRequested(self._column, self._values[event.option_index]))


class OverviewScreen(ModalScreen[str | None]):
    """Every column at a glance; ⏎ jumps to the column."""

    BINDINGS = [Binding("escape,o,q", "dismiss(None)", "Close")]

    DEFAULT_CSS = """
    OverviewScreen { align: center middle; }
    OverviewScreen > Vertical {
        width: 90%; height: 85%;
        border: thick $accent; background: $surface; padding: 0 1;
    }
    OverviewScreen #overview-title { text-style: bold; color: $accent; padding: 0 0 1 0; }
    OverviewScreen DataTable { height: 1fr; }
    """

    def __init__(self, title: str, columns: list[str], dtypes: list[str], stats: dict[str, ColumnStats]):
        super().__init__()
        self._title = title
        self._columns = columns
        self._dtypes = dtypes
        self._stats = stats

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Static(self._title, id="overview-title")
            yield DataTable(cursor_type="row", zebra_stripes=True)

    def on_mount(self) -> None:
        table = self.query_one(DataTable)
        table.add_columns("#", "Column", "Type", "Nulls", "Distinct", "Min", "Max", "Mean", "Distribution")
        for i, (name, dtype) in enumerate(zip(self._columns, self._dtypes)):
            st = self._stats.get(name)
            if st is None:
                table.add_row(str(i + 1), name, dtype, "…", "…", "", "", "", "", key=name)
                continue
            table.add_row(
                str(i + 1),
                Text(name, style="bold"),
                dtype,
                f"{st.null_fraction:.1%}",
                f"~{st.n_unique:,}" if st.n_unique is not None else "–",
                _num(st.min)[:19],
                _num(st.max)[:19],
                _num(st.mean)[:12] if st.mean is not None else "",
                Text(st.sparkline(16), style="cyan"),
                key=name,
            )
        table.focus()

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        self.dismiss(event.row_key.value)


class PromptScreen(ModalScreen[str | None]):
    """A one-line text prompt. ⏎ submits, Esc cancels."""

    BINDINGS = [Binding("escape", "dismiss(None)", "Cancel")]

    DEFAULT_CSS = """
    PromptScreen { align: center middle; }
    PromptScreen > Vertical {
        width: 70; height: auto; border: thick $accent; background: $surface; padding: 1 2;
    }
    PromptScreen Label { margin-bottom: 1; }
    """

    def __init__(self, prompt: str, value: str = "", placeholder: str = "") -> None:
        super().__init__()
        self._prompt = prompt
        self._value = value
        self._placeholder = placeholder

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Label(self._prompt)
            yield Input(value=self._value, placeholder=self._placeholder)

    def on_mount(self) -> None:
        self.query_one(Input).focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self.dismiss(event.value)
