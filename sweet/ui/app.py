"""Main Sweet application using Textual."""

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.widgets import Header, Input, Static

from .widgets import DrawerContainer, SweetFooter, CommandReferenceModal


class SweetApp(App):
    """Main Sweet application for data engineering."""

    CSS_PATH = "sweet.css"
    TITLE = "Sweet - Data Engineering CLI"
    SUB_TITLE = "Interactive data manipulation with Polars"

    BINDINGS = [
        ("f2", "toggle_script_panel", "Toggle Script Panel"),
        ("escape", "close_drawer", "Close Drawer"),
        ("colon", "enter_command_mode", "Command Mode"),
        Binding("f1", "show_command_reference", "Command Reference", show=True),
    ]

    def __init__(self, startup_file: str | None = None, **kwargs):
        """Initialize the app with optional startup file."""
        super().__init__(**kwargs)
        self.startup_file = startup_file
        self.command_mode = False

    def compose(self) -> ComposeResult:
        """Compose the application layout."""
        yield Header()
        yield DrawerContainer(id="main-container")
        yield SweetFooter()
        # Command input (initially hidden)
        with Horizontal(id="command-bar", classes="command-bar hidden"):
            yield Static(":", classes="command-prompt")
            yield Input(placeholder="Enter command (q to quit)", id="command-input", classes="command-input")

    def on_mount(self) -> None:
        """Initialize the application on mount."""
        self.log("Sweet application started")
        
        # If a startup file was provided, load it
        if self.startup_file:
            container = self.query_one("#main-container", DrawerContainer)
            data_grid = container.query_one("ExcelDataGrid")
            data_grid.load_file(self.startup_file)

    def action_toggle_script_panel(self) -> None:
        """Toggle the script panel drawer."""
        container = self.query_one("#main-container", DrawerContainer)
        container.action_toggle_drawer()

    def action_close_drawer(self) -> None:
        """Close the script panel drawer."""
        container = self.query_one("#main-container", DrawerContainer)
        container.show_drawer = False

    def action_enter_command_mode(self) -> None:
        """Enter command mode."""
        self.command_mode = True
        command_bar = self.query_one("#command-bar")
        command_input = self.query_one("#command-input", Input)
        
        # Show command bar
        command_bar.remove_class("hidden")
        command_bar.add_class("visible")
        
        # Focus the input
        command_input.focus()
        command_input.value = ""

    def action_exit_command_mode(self) -> None:
        """Exit command mode."""
        self.command_mode = False
        command_bar = self.query_one("#command-bar")
        
        # Hide command bar
        command_bar.remove_class("visible")
        command_bar.add_class("hidden")

    def on_input_submitted(self, event: Input.Submitted) -> None:
        """Handle command input submission."""
        if event.input.id == "command-input":
            command = event.value.strip().lower()
            self.execute_command(command)

    def execute_command(self, command: str) -> None:
        """Execute a command."""
        if command == "q" or command == "quit":
            self.exit()
        elif command == "help" or command == "h" or command == "ref":
            self.action_show_command_reference()
        else:
            self.log(f"Unknown command: {command}")
        
        # Exit command mode after executing
        self.action_exit_command_mode()

    def on_key(self, event) -> None:
        """Handle global key events."""
        if self.command_mode and event.key == "escape":
            self.action_exit_command_mode()
            return True
        return False

    def action_show_help(self) -> None:
        """Show help information (deprecated - use command reference)."""
        self.action_show_command_reference()

    def action_show_command_reference(self) -> None:
        """Show the command reference modal."""
        modal = CommandReferenceModal()
        self.push_screen(modal)

    def action_quit(self) -> None:
        """Quit the application."""
        self.exit()


def run_app(startup_file: str | None = None) -> None:
    """Run the Sweet application."""
    app = SweetApp(startup_file=startup_file)
    app.run()
