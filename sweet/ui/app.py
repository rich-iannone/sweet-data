"""Main Sweet application using Textual."""

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.widgets import Header

from .widgets import DrawerContainer, SweetFooter


class SweetApp(App):
    """Main Sweet application for data engineering."""

    CSS_PATH = "sweet.css"
    TITLE = "Sweet - Data Engineering CLI"
    SUB_TITLE = "Interactive data manipulation with Polars"

    BINDINGS = [
        ("f2", "toggle_script_panel", "Toggle Script Panel"),
        ("ctrl+q", "quit", "Quit"),
        ("escape", "close_drawer", "Close Drawer"),
        Binding("f1", "show_help", "Help", show=True),
    ]

    def compose(self) -> ComposeResult:
        """Compose the application layout."""
        yield Header()
        yield DrawerContainer(id="main-container")
        yield SweetFooter()

    def on_mount(self) -> None:
        """Initialize the application on mount."""
        self.log("Sweet application started")

    def action_toggle_script_panel(self) -> None:
        """Toggle the script panel drawer."""
        container = self.query_one("#main-container", DrawerContainer)
        container.action_toggle_drawer()

    def action_close_drawer(self) -> None:
        """Close the script panel drawer."""
        container = self.query_one("#main-container", DrawerContainer)
        container.show_drawer = False

    def action_show_help(self) -> None:
        """Show help information."""
        self.log("Help: F2 to toggle script panel, Arrow keys to navigate grid")

    def action_quit(self) -> None:
        """Quit the application."""
        self.exit()


def run_app() -> None:
    """Run the Sweet application."""
    app = SweetApp()
    app.run()
