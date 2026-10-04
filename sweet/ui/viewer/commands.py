"""The viewer's command registry.

Every user-facing action is a named `Command`. Key bindings, the command
palette (Ctrl+P), and, in time, agents all go through this one list, so
anything a person can do from the keyboard can be discovered and invoked by
name.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import partial
from typing import TYPE_CHECKING

from textual.binding import Binding
from textual.command import DiscoveryHit, Hit, Hits, Provider

if TYPE_CHECKING:
    from .app import ViewerApp


@dataclass(frozen=True)
class Command:
    """A named action.

    Attributes:
        id: Stable identifier, e.g. "view.sort_ascending".
        title: Palette title.
        action: Textual action string run on the app, e.g. "sort(False)".
        key: Key binding (Textual key names, comma-separated), if any.
        help: One-line description.
        footer: Show the key in the footer.
        key_display: How to show the key (defaults to Textual's rendering).
    """

    id: str
    title: str
    action: str
    key: str | None = None
    help: str = ""
    footer: bool = False
    key_display: str | None = None


COMMANDS: tuple[Command, ...] = (
    Command("edit.cell", "Edit cell", "edit_cell", "enter,e", "Edit the value under the cursor", True, "⏎"),
    Command("view.sort_ascending", "Sort ascending", "sort(False)", "s", "Sort by this column, ascending (again to remove)", True),
    Command("view.sort_descending", "Sort descending", "sort(True)", "S", "Sort by this column, descending (again to remove)"),
    Command("view.clear_sort", "Clear sort", "clear_sort", "c", "Remove all sorting from the view"),
    Command("filter.equal", "Filter to this value", "filter_value(False)", "f", "Keep rows equal to the value under the cursor", True),
    Command("filter.exclude", "Exclude this value", "filter_value(True)", "exclamation_mark", "Remove rows equal to the value under the cursor", key_display="!"),
    Command("column.drop", "Drop column", "drop_column", "d", "Remove the column under the cursor"),
    Command("panel.inspector", "Toggle column inspector", "toggle_inspector", "i", "Show statistics for the current column", True),
    Command("panel.overview", "Dataset overview", "overview", "o", "Summary of every column", True),
    Command("history.undo", "Undo", "undo", "u", "Undo the last change", True),
    Command("history.redo", "Redo", "redo", "U", "Redo the last undone change"),
    Command("file.open", "Open file…", "open_file", "ctrl+o", "Open another file as a new sheet"),
    Command("file.save", "Save data as…", "save_data", "w", "Write the current sheet to a file"),
    Command("file.pipeline", "Save pipeline as…", "save_pipeline", "p", "Save every step as a replayable .sweet.yaml file"),
    Command("sheet.next", "Next sheet", "sheet(1)", "right_square_bracket", "Switch to the next sheet", key_display="]"),
    Command("sheet.previous", "Previous sheet", "sheet(-1)", "left_square_bracket", "Switch to the previous sheet", key_display="["),
    Command("app.quit", "Quit", "quit", "q", "Exit Sweet", True),
)  # fmt: skip

COMMANDS_BY_ID = {c.id: c for c in COMMANDS}


def bindings() -> list[Binding]:
    """App key bindings derived from the registry."""
    return [
        Binding(c.key, c.action, c.title, show=c.footer, key_display=c.key_display)
        for c in COMMANDS
        if c.key
    ]


class SweetCommands(Provider):
    """Command palette provider: registry commands plus "go to column"."""

    @property
    def _app(self) -> ViewerApp:
        return self.app  # type: ignore[return-value]

    def _entries(self) -> list[tuple[str, str, object]]:
        entries = [
            (c.title, f"{c.help}  [{c.key_display or c.key or 'no key'}]", c.action)
            for c in COMMANDS
        ]
        grid = getattr(self._app, "grid", None)
        for column in grid.columns if grid is not None else []:
            entries.append((f"Go to column: {column}", "Move the cursor to this column", ("column", column)))
        return entries

    def _runner(self, target: object):
        if isinstance(target, tuple):
            return partial(self._app.goto_column, target[1])
        return partial(self._app.run_action, target)

    async def discover(self) -> Hits:
        for title, help_text, target in self._entries():
            if not title.startswith("Go to column"):
                yield DiscoveryHit(title, self._runner(target), help=help_text)

    async def search(self, query: str) -> Hits:
        matcher = self.matcher(query)
        for title, help_text, target in self._entries():
            score = matcher.match(title)
            if score > 0:
                yield Hit(score, matcher.highlight(title), self._runner(target), help=help_text)
