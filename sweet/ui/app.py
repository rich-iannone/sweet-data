from textual import events
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal
from textual.widgets import Header, TextArea

from .widgets import (
    CommandReferenceModal,
    DrawerContainer,
    InitConfirmationModal,
    QuitConfirmationModal,
    SweetFooter,
)


class CommandTextArea(TextArea):
    """A custom TextArea for command input that handles Enter key specially."""

    def _on_key(self, event: events.Key) -> None:
        """Handle key events for command input."""
        if event.key == "enter":
            # Post a custom message instead of handling enter normally
            self.post_message(CommandTextArea.CommandSubmitted(self))
            event.prevent_default()
        else:
            # Let the parent handle other keys normally
            super()._on_key(event)

    class CommandSubmitted(events.Message):
        """Posted when a command is submitted."""

        def __init__(self, text_area: "CommandTextArea") -> None:
            super().__init__()
            self.text_area = text_area


class SweetApp(App):
    """Main Sweet application for data engineering."""

    CSS_PATH = "sweet.tcss"
    TITLE = "Sweet // Data CLI"
    SUB_TITLE = ""

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
        self.current_filename = None
        self._update_title()

    def compose(self) -> ComposeResult:
        """Compose the application layout."""
        yield Header()
        yield DrawerContainer(id="main-container")
        yield SweetFooter()
        # Command input (initially hidden)
        with Horizontal(id="command-bar", classes="command-bar hidden"):
            yield CommandTextArea(
                id="command-input",
                classes="command-input",
                compact=True,
                show_line_numbers=False,
                tab_behavior="focus",
            )

    def on_mount(self) -> None:
        """Initialize the application on mount."""
        self.log("Sweet application started")

        # Store reference to data grid for command access
        container = self.query_one("#main-container", DrawerContainer)
        self._data_grid = container.query_one("ExcelDataGrid")

        # If a startup file was provided, load it
        if self.startup_file:
            self._data_grid.load_file(self.startup_file)
            # Set the current filename and update title
            self.set_current_filename(self.startup_file)

    def _update_title(self) -> None:
        """Update the window title with current filename."""
        base_title = "Sweet // Data CLI"
        if self.current_filename:
            # Extract just the filename from the full path
            from pathlib import Path

            filename = Path(self.current_filename).name
            self.title = f"{base_title} -- {filename}"
        else:
            self.title = base_title

    def set_current_filename(self, filename: str | None) -> None:
        """Set the current filename and update the title."""
        self.current_filename = filename
        self._update_title()

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
        command_input = self.query_one("#command-input", CommandTextArea)

        # Show command bar
        command_bar.remove_class("hidden")
        command_bar.add_class("visible")

        # Focus the input and pre-populate with ":"
        command_input.focus()
        command_input.text = ":"
        # Position cursor after the colon
        command_input.cursor_location = (0, 1)

    def action_exit_command_mode(self) -> None:
        """Exit command mode."""
        self.command_mode = False
        command_bar = self.query_one("#command-bar")

        # Hide command bar
        command_bar.remove_class("visible")
        command_bar.add_class("hidden")

    def on_command_text_area_command_submitted(
        self, message: CommandTextArea.CommandSubmitted
    ) -> None:
        """Handle command submission from the command text area."""
        if self.command_mode:
            command_text = message.text_area.text.strip()
            # Remove the leading colon if present
            if command_text.startswith(":"):
                command_text = command_text[1:]
            command = command_text.lower()
            message.text_area.clear()  # Clear the input
            self.execute_command(command)

    def on_key(self, event: events.Key) -> None:
        """Handle key events."""
        # Handle Escape key to exit command mode
        if self.command_mode and event.key == "escape":
            self.action_exit_command_mode()
            event.prevent_default()
            return

    def execute_command(self, command: str) -> None:
        """Execute a command."""
        if command == "q" or command == "quit":
            # Check for unsaved changes
            if hasattr(self, "_data_grid") and self._data_grid.has_changes:
                # For sample data, skip confirmation and just quit
                if hasattr(self._data_grid, "is_sample_data") and self._data_grid.is_sample_data:
                    self.exit()
                    return

                # Show quit confirmation modal for external files
                modal = QuitConfirmationModal()
                self.push_screen(modal, self._handle_quit_confirmation)
                # Exit command mode when showing modal
                self.action_exit_command_mode()
                return
            self.exit()
        elif command == "q!" or command == "quit!":
            # Force quit without saving
            self.exit()
        elif command == "wo" or command == "so":
            # Save and overwrite: for sample data, redirect to save-as
            if hasattr(self, "_data_grid") and self._data_grid.data is not None:
                # For sample data, redirect to save-as behavior
                if hasattr(self._data_grid, "is_sample_data") and self._data_grid.is_sample_data:
                    try:
                        self.log(f"Sample data detected: redirecting {command} to save-as")
                        self._data_grid.action_save_as()
                    except Exception as e:
                        self.log(f"Error in save-as command: {e}")
                        import traceback

                        self.log(f"Traceback: {traceback.format_exc()}")
                else:
                    # Regular save behavior for external files
                    if self._data_grid.action_save_original():
                        self.log("File saved successfully")
                    else:
                        self.log("No file to save to: use :wa for save as")
            else:
                self.log("No data to save")
        elif command == "wa" or command == "wq" or command == "sa":
            # Save as (new filename)
            if hasattr(self, "_data_grid") and self._data_grid.data is not None:
                try:
                    self.log(f"Executing save-as command for {command}")
                    self._data_grid.action_save_as()
                except Exception as e:
                    self.log(f"Error in save-as command: {e}")
                    import traceback

                    self.log(f"Traceback: {traceback.format_exc()}")
            else:
                if not hasattr(self, "_data_grid"):
                    self.log("Error: Data grid not found")
                elif self._data_grid.data is None:
                    self.log("No data to save: load a dataset first")
                else:
                    self.log("No data to save")
        elif command == "help" or command == "h" or command == "ref":
            self.action_show_command_reference()
        elif command == "init":
            # Return to welcome screen: check for unsaved changes
            if hasattr(self, "_data_grid") and self._data_grid.has_changes:
                # For sample data, skip confirmation and go to welcome screen
                if hasattr(self._data_grid, "is_sample_data") and self._data_grid.is_sample_data:
                    self._reset_to_welcome_screen()
                    return

                # Show confirmation modal for external files with unsaved changes
                modal = InitConfirmationModal()
                self.push_screen(modal, self._handle_init_confirmation)
                # Exit command mode when showing modal
                self.action_exit_command_mode()
                return
            # No unsaved changes: go directly to welcome screen
            self._reset_to_welcome_screen()
        elif command == "row" or command.startswith("row "):
            # Navigate to specific row: :row or :row <number>
            if hasattr(self, "_data_grid") and self._data_grid.data is not None:
                if command == "row":
                    # Show row navigation modal
                    from .widgets import RowNavigationModal

                    total_rows = len(self._data_grid.data)
                    current_row = 1

                    # Try to get current row position
                    try:
                        cursor_coordinate = self._data_grid._table.cursor_coordinate
                        if cursor_coordinate:
                            current_row = cursor_coordinate[0]
                            # Adjust for display offset if in truncated view
                            if hasattr(self._data_grid, "_display_offset"):
                                current_row += self._data_grid._display_offset
                    except Exception:
                        pass

                    modal = RowNavigationModal(total_rows, current_row)
                    self.push_screen(modal, self._handle_row_navigation)
                    # Exit command mode when showing modal
                    self.action_exit_command_mode()
                    return
                else:
                    # Direct row number: :row 1000 or :row -5
                    try:
                        row_number = int(command.split()[1])

                        # Handle special case: :row 0 should go to first row (same as :row 1)
                        if row_number == 0:
                            row_number = 1

                        # Handle negative indexing: count back from the end
                        elif row_number < 0:
                            total_rows = len(self._data_grid.data)
                            # Convert negative index to positive (e.g., -5 -> total_rows - 4)
                            # Note: -1 goes to last row, -2 to second-to-last, etc.
                            target_row = total_rows + row_number + 1
                            if target_row < 1:
                                # Schedule error message display after command mode exits
                                error_msg = f"[bold]ERROR[/bold]: Row index {row_number} is out of bounds. Dataset has {total_rows} rows (valid range: 1 to {total_rows})."
                                self.call_later(self._show_error_message, error_msg)
                                return
                            row_number = target_row

                        # Check bounds for positive row numbers
                        total_rows = len(self._data_grid.data)
                        if row_number > total_rows:
                            # Schedule error message display after command mode exits
                            error_msg = f"[bold]ERROR[/bold]: Row {row_number} is out of range. Dataset has {total_rows} rows (valid range: 1 to {total_rows})."
                            self.call_later(self._show_error_message, error_msg)
                            return

                        self._data_grid.navigate_to_row(row_number)
                    except (IndexError, ValueError):
                        # Schedule error message display after command mode exits
                        error_msg = "[bold]ERROR[/bold]: Invalid row number. Use :row <number> (e.g., :row 1000 or :row -5)"
                        self.call_later(self._show_error_message, error_msg)
            else:
                self.log("No data loaded. Load a dataset first.")
        else:
            self.log(f"Unknown command: {command}")

        # Exit command mode after executing
        self.action_exit_command_mode()

    def _show_error_message(self, message: str) -> None:
        """Show an error message in the status bar for a limited time."""
        try:
            # Get current cursor position to restore later
            current_pos = self._data_grid._table.cursor_coordinate
            
            # Directly update the status bar with just the error message
            status_bar = self._data_grid.query_one("#status-bar")
            status_bar.update(message)
            
            # Clear the error message after 4 seconds and restore normal display
            if current_pos:
                row, col = current_pos
                self.set_timer(4.0, lambda: self._clear_error_message(row, col))
        except Exception as e:
            self.log(f"Error showing error message: {e}")

    def _clear_error_message(self, row: int, col: int) -> None:
        """Clear the error message and restore normal status display."""
        try:
            # Restore normal address display without custom message
            self._data_grid.update_address_display(row, col)
        except Exception as e:
            self.log(f"Error clearing error message: {e}")

    def _handle_quit_confirmation(self, result: bool | None) -> None:
        """Handle the result from the quit confirmation modal."""
        if result is True:
            # User chose to quit without saving
            self.exit()
        # If result is False or None, user cancelled: do nothing

    def _handle_init_confirmation(self, result: bool | None) -> None:
        """Handle the result from the init confirmation modal."""
        if result is True:
            # User chose to return to welcome screen without saving
            self._reset_to_welcome_screen()
        # If result is False or None, user cancelled: do nothing

    def _handle_row_navigation(self, result: int | None) -> None:
        """Handle the result from the row navigation modal."""
        if result is not None and hasattr(self, "_data_grid"):
            self._data_grid.navigate_to_row(result)

    def action_show_help(self) -> None:
        """Show help information (deprecated: use command reference)."""
        self.action_show_command_reference()

    def action_show_command_reference(self) -> None:
        """Show the command reference modal."""
        modal = CommandReferenceModal()
        self.push_screen(modal)

    def _reset_to_welcome_screen(self) -> None:
        """Properly reset the application to the welcome screen."""
        try:
            self.log("Resetting to welcome screen...")

            # Clear the data grid completely
            if hasattr(self, "_data_grid"):
                # Clear all data and state
                self._data_grid.data = None
                self._data_grid.original_data = None
                self._data_grid.has_changes = False
                self._data_grid.is_sample_data = False
                self._data_grid.data_source_name = None

                # Clear filename
                self.current_filename = None

                # Remove the existing table if it exists
                if hasattr(self._data_grid, "_table") and self._data_grid._table:
                    try:
                        self._data_grid._table.remove()
                    except Exception as e:
                        self.log(f"Error removing old table: {e}")

                # Create a fresh welcome state
                self._data_grid._create_welcome_state()

                # Update display
                self._data_grid.update_title_change_indicator()
                self.log("Successfully reset to welcome screen")

        except Exception as e:
            self.log(f"Error resetting to welcome screen: {e}")
            # Fallback: just show the welcome overlay
            if hasattr(self, "_data_grid"):
                self._data_grid.show_welcome_overlay()

    def action_quit(self) -> None:
        """Quit the application."""
        self.exit()


def run_app(startup_file: str | None = None) -> None:
    """Run the Sweet application."""
    app = SweetApp(startup_file=startup_file)
    app.run()
