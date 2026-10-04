"""The steps panel and the step composer.

The steps panel lists a sheet's source, its steps, and any pending proposals.
Selecting an entry shows the data as of that point (time travel); keys toggle,
remove, reorder, edit, and branch from steps. Each step is shown with its code
in Polars or SQL (press L to switch).
"""

from __future__ import annotations

from typing import Any

from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.message import Message
from textual.screen import ModalScreen
from textual.widgets import (
    Button,
    Input,
    Label,
    OptionList,
    RadioButton,
    RadioSet,
    Static,
    TextArea,
)
from textual.widgets.option_list import Option

from ...core.steps import NotExportable, Step, StepError


def step_code(step: Step, language: str) -> str:
    """A step's code in "polars" or "sql" (or a note if it has no such form)."""
    try:
        if language == "sql":
            return step.to_sql("input")
        return step.to_polars_statements()
    except NotExportable:
        return "(no SQL form)" if language == "sql" else "(no Polars form)"
    except Exception:
        return ""


class StepsPanel(Vertical):
    """Source, steps, and proposals of the current sheet."""

    DEFAULT_CSS = """
    StepsPanel {
        width: 48;
        dock: left;
        border-right: tall $panel-lighten-1;
        background: $panel;
        padding: 0 1;
    }
    StepsPanel.hidden { display: none; }
    StepsPanel #steps-title { text-style: bold; color: $accent; }
    StepsPanel #steps-help { color: $text-muted; margin-bottom: 1; }
    StepsPanel OptionList { height: 1fr; border: none; background: $panel; padding: 0; }
    """

    BINDINGS = [
        Binding("space", "toggle_step", "Toggle step"),
        Binding("x,delete,backspace", "remove_step", "Remove step"),
        Binding("K,shift+up", "move_step(-1)", "Move up"),
        Binding("J,shift+down", "move_step(1)", "Move down"),
        Binding("e", "edit_step", "Edit step"),
        Binding("b", "branch", "Branch from here"),
        Binding("L", "toggle_language", "Polars/SQL"),
    ]

    class Selected(Message):
        """Show the data after the first `index` steps (index == len(steps): live)."""

        def __init__(self, index: int) -> None:
            super().__init__()
            self.index = index

    class Action(Message):
        """A request to change the pipeline: kind is toggle/remove/move/edit/branch/accept/reject."""

        def __init__(self, kind: str, target: str | int, value: Any = None) -> None:
            super().__init__()
            self.kind = kind
            self.target = target
            self.value = value

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.language = "polars"
        self._entries: list[
            tuple[str, Any]
        ] = []  # ("source", None) / ("step", Step) / ("proposal", id)
        self._steps: list[Step] = []

    def compose(self) -> ComposeResult:
        yield Static("Steps", id="steps-title")
        yield Static(
            "⏎ view · space toggle · x remove · K/J move · e edit · b branch · L SQL",
            id="steps-help",
        )
        yield OptionList(id="steps-list")

    @property
    def option_list(self) -> OptionList:
        return self.query_one("#steps-list", OptionList)

    def show(
        self,
        source: str,
        steps: list[Step],
        proposals: list[tuple[str, Step, str]],
        *,
        at_step: int | None = None,
    ) -> None:
        """Render the source, steps, and proposals (id, step, author)."""
        self._steps = steps
        self._entries = [("source", None)]
        options = self.option_list
        highlighted = options.highlighted
        options.clear_options()
        options.add_option(
            Option(Text.assemble(("◆ ", "bold"), ("source ", "bold"), (source, "dim")))
        )
        for n, step in enumerate(steps, 1):
            self._entries.append(("step", step))
            mark = "●" if step.enabled else "○"
            label = Text.assemble(
                (f"{mark} {n}. ", "bold" if step.enabled else "dim"),
                (step.label, "" if step.enabled else "dim strike"),
            )
            if step.author != "human":
                label.append(f"  {step.author}", "italic dim")
            code = step_code(step, self.language).splitlines()
            if code:
                width = 40
                more = len(code) > 1 or len(code[0]) > width
                label.append("\n   " + code[0][:width] + ("…" if more else ""), "dim")
            options.add_option(Option(label))
        for proposal_id, step, author in proposals:
            self._entries.append(("proposal", proposal_id))
            label = Text.assemble(
                ("◐ proposed ", "bold yellow"), step.label, (f"  {author}", "italic dim")
            )
            label.append("\n   ⏎ preview · a accept · r reject", "dim")
            options.add_option(Option(label))
        title = self.query_one("#steps-title", Static)
        where = f" · viewing after step {at_step}" if at_step is not None else ""
        title.update(f"Steps ({len(steps)}) · {self.language}{where}")
        if highlighted is not None and highlighted < len(self._entries):
            options.highlighted = highlighted
        else:
            # Start on the entry matching what's shown (the latest step when live)
            options.highlighted = at_step if at_step is not None else len(steps)

    def _current(self) -> tuple[int, str, Any] | None:
        index = self.option_list.highlighted
        if index is None or index >= len(self._entries):
            return None
        kind, value = self._entries[index]
        return index, kind, value

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        event.stop()
        current = self._current()
        if current is None:
            return
        index, kind, value = current
        if kind == "proposal":
            self.post_message(self.Action("preview_proposal", value))
        else:
            self.post_message(self.Selected(index))  # source -> 0 steps, step n -> n steps

    def _step_action(self, kind: str, value: Any = None) -> None:
        current = self._current()
        if current is not None and current[1] == "step":
            self.post_message(self.Action(kind, current[2].id, value))

    def action_toggle_step(self) -> None:
        self._step_action("toggle")

    def action_remove_step(self) -> None:
        self._step_action("remove")

    def action_move_step(self, delta: int) -> None:
        current = self._current()
        if current is not None and current[1] == "step":
            self._step_action("move", delta)
            new_index = current[0] + delta
            if 1 <= new_index <= len(self._steps):
                self.call_after_refresh(setattr, self.option_list, "highlighted", new_index)

    def action_edit_step(self) -> None:
        self._step_action("edit")

    def action_branch(self) -> None:
        current = self._current()
        if current is not None and current[1] in ("source", "step"):
            self.post_message(self.Action("branch", current[0]))

    def action_toggle_language(self) -> None:
        self.language = "sql" if self.language == "polars" else "polars"
        self.post_message(self.Action("refresh", 0))


# -----------------------------------------------------------------------------
# Composer
# -----------------------------------------------------------------------------

KINDS = [
    ("filter", "Filter rows"),
    ("mutate", "Add or replace a column"),
    ("sql", "SQL query (table: df)"),
    ("polars", "Polars code (df → df)"),
]


class ComposerScreen(ModalScreen[Step | None]):
    """Write a step in SQL or Polars. Ctrl+S (or the button) previews it."""

    BINDINGS = [
        Binding("escape", "dismiss(None)", "Cancel"),
        Binding("ctrl+s", "submit", "Preview", priority=True),
    ]

    DEFAULT_CSS = """
    ComposerScreen { align: center middle; }
    ComposerScreen > Vertical {
        width: 90; height: auto; max-height: 90%;
        border: thick $accent; background: $surface; padding: 1 2;
    }
    ComposerScreen #composer-title { text-style: bold; color: $accent; margin-bottom: 1; }
    ComposerScreen RadioSet { width: 100%; margin-bottom: 1; }
    ComposerScreen .row { height: auto; margin-bottom: 1; }
    ComposerScreen #composer-code { height: 8; }
    ComposerScreen #composer-columns { color: $text-muted; margin: 1 0; }
    ComposerScreen #composer-error { color: $error; }
    ComposerScreen #composer-buttons { height: auto; align-horizontal: right; }
    ComposerScreen Button { margin-left: 1; }
    """

    def __init__(self, columns: list[str], step: Step | None = None) -> None:
        super().__init__()
        self._columns = columns
        self._editing = step
        self._kind, self._language, self._column, self._code = "filter", "sql", "", ""
        self._yaml_kind: str | None = None
        if step is not None:
            self._prefill(step)

    def _prefill(self, step: Step) -> None:
        params = step.params
        if step.kind in ("filter", "mutate") and ("sql" in params or "expr" in params):
            self._kind = step.kind
            self._language = "sql" if "sql" in params else "polars"
            self._code = params.get("sql") or params.get("expr", "")
            self._column = params.get("column", "")
        elif step.kind == "sql":
            self._kind, self._code = "sql", params.get("query", "")
        elif step.kind == "polars":
            self._kind, self._code = "polars", params.get("code", "")
        else:
            from yaml12 import format_yaml

            self._kind, self._yaml_kind = "yaml", step.kind
            self._code = format_yaml(params)

    def compose(self) -> ComposeResult:
        title = f"Edit step: {self._editing.label}" if self._editing else "New step"
        with Vertical():
            yield Static(title, id="composer-title")
            if self._kind == "yaml":
                yield Label(f"Parameters for '{self._yaml_kind}' (YAML)")
            else:
                with RadioSet(id="composer-kind"):
                    for kind, label in KINDS:
                        yield RadioButton(label, value=kind == self._kind, name=kind)
                with Horizontal(classes="row", id="composer-language-row"):
                    with RadioSet(id="composer-language"):
                        yield RadioButton(
                            "SQL expression", value=self._language == "sql", name="sql"
                        )
                        yield RadioButton(
                            "Polars expression", value=self._language == "polars", name="polars"
                        )
                yield Input(self._column, placeholder="Column name", id="composer-column")
            yield TextArea(self._code, id="composer-code", language=None, soft_wrap=True)
            yield Static(self._columns_hint(), id="composer-columns")
            yield Static("", id="composer-error")
            with Horizontal(id="composer-buttons"):
                yield Button("Cancel", id="composer-cancel")
                yield Button("Preview (ctrl+s)", variant="primary", id="composer-preview")

    def _columns_hint(self) -> str:
        shown = ", ".join(self._columns[:30]) + (" …" if len(self._columns) > 30 else "")
        return f"Columns: {shown}"

    def on_mount(self) -> None:
        self._sync_fields()
        self.query_one("#composer-code", TextArea).focus()

    def on_radio_set_changed(self, event: RadioSet.Changed) -> None:
        name = event.pressed.name or ""
        if event.radio_set.id == "composer-kind":
            self._kind = name
        else:
            self._language = name
        self._sync_fields()

    def _sync_fields(self) -> None:
        if self._kind == "yaml":
            return
        self.query_one("#composer-language-row").display = self._kind in ("filter", "mutate")
        self.query_one("#composer-column").display = self._kind == "mutate"

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "composer-cancel":
            self.dismiss(None)
        else:
            self.action_submit()

    def action_submit(self) -> None:
        code = self.query_one("#composer-code", TextArea).text.strip()
        try:
            step = self._build(code)
            step.validate()
        except (StepError, ValueError) as e:
            self.query_one("#composer-error", Static).update(str(e))
            return
        if self._editing is not None:
            step.id = self._editing.id
            step.enabled = self._editing.enabled
        self.dismiss(step)

    def _build(self, code: str) -> Step:
        if not code:
            raise StepError("Write some code first")
        if self._kind == "yaml":
            from yaml12 import parse_yaml

            params = parse_yaml(code)
            if not isinstance(params, dict):
                raise StepError("Parameters must be a YAML mapping")
            return Step(self._yaml_kind, params)
        key = "sql" if self._language == "sql" else "expr"
        if self._kind == "filter":
            return Step("filter", {key: code})
        if self._kind == "mutate":
            column = self.query_one("#composer-column", Input).value.strip()
            if not column:
                raise StepError("Give the column a name")
            return Step("mutate", {"column": column, key: code})
        if self._kind == "sql":
            return Step("sql", {"query": code, "table": "df"})
        return Step("polars", {"code": code})
