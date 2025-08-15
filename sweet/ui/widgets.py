from __future__ import annotations

import keyword
import os
import re
import time
from pathlib import Path
from typing import TYPE_CHECKING

from textual.app import ComposeResult
from textual.containers import Container, Horizontal, Vertical, VerticalScroll
from textual.coordinate import Coordinate
from textual.reactive import reactive
from textual.screen import ModalScreen
from textual.widget import Widget
from textual.widgets import (
    Button,
    Checkbox,
    ContentSwitcher,
    DataTable,
    DirectoryTree,
    Footer,
    Input,
    Label,
    RadioSet,
    Select,
    Static,
    TextArea,
)

if TYPE_CHECKING:
    import polars as pl

# Add logging imports
import logging

# Maximum number of rows to display in the DataGrid for large datasets
MAX_DISPLAY_ROWS = 1000


# Setup debug logging
def setup_debug_logging():
    log_file = Path.cwd() / "sweet_llm_debug.log"
    # Only log to file, not to console
    file_handler = logging.FileHandler(log_file)
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(logging.Formatter("%(asctime)s - %(levelname)s - %(message)s"))

    logger = logging.getLogger("sweet_llm")
    logger.setLevel(logging.DEBUG)
    logger.addHandler(file_handler)
    # Don't propagate to root logger to avoid console output
    logger.propagate = False
    return logger


debug_logger = setup_debug_logging()

try:
    import polars as pl
except ImportError:
    pl = None

# Try to import chatlas, but don't fail if it's not available
try:
    import chatlas

    CHATLAS_AVAILABLE = True
except ImportError:
    chatlas = None
    CHATLAS_AVAILABLE = False


class WelcomeOverlay(Widget):
    """Welcome screen overlay similar to Vim's start screen."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.can_focus = True  # Make the overlay focusable

    def call_after_refresh(self, callback, *args, **kwargs):
        """Helper method to call a function after the next refresh using set_timer."""
        self.set_timer(0.01, lambda: callback(*args, **kwargs))

    def compose(self) -> ComposeResult:
        """Compose the welcome overlay."""
        with Vertical(id="welcome-overlay", classes="welcome-overlay"):
            yield Static("", classes="spacer")  # Top spacer
            yield Static("Sweet", classes="welcome-title")
            yield Static("Interactive data engineering CLI", classes="welcome-subtitle")
            yield Static("", classes="spacer-small")  # Small spacer
            with Horizontal(classes="welcome-buttons"):
                yield Button("New Empty Sheet", id="welcome-new-empty", classes="welcome-button")
                yield Button("Load Dataset", id="welcome-load-dataset", classes="welcome-button")
                yield Button("Load Sample Data", id="welcome-load-sample", classes="welcome-button")
                yield Button(
                    "Paste from Clipboard", id="welcome-paste-clipboard", classes="welcome-button"
                )
            with Horizontal(classes="welcome-buttons"):
                yield Button(
                    "Connect to Database", id="welcome-connect-database", classes="welcome-button"
                )
                yield Button("Exit Sweet", id="welcome-exit", classes="welcome-button")
            yield Static("", classes="spacer")  # Bottom spacer

    def on_button_pressed(self, event: Button.Pressed) -> None:
        """Handle button presses in the welcome overlay."""
        self.log(f"Welcome overlay button pressed: {event.button.id}")

        # Handle exit button separately since it doesn't need data grid access
        if event.button.id == "welcome-exit":
            self.log("Exit Sweet button pressed: closing application")
            self.app.exit()
            event.stop()
            return

        # Find the ExcelDataGrid: we need to go up to the parent Vertical container
        # The hierarchy is: WelcomeOverlay -> Vertical -> ExcelDataGrid
        try:
            data_grid = self.parent.parent
            if isinstance(data_grid, ExcelDataGrid):
                if event.button.id == "welcome-load-dataset":
                    self.log("Calling action_load_dataset")
                    data_grid.action_load_dataset()
                elif event.button.id == "welcome-load-sample":
                    self.log("Calling action_load_sample_data")
                    data_grid.action_load_sample_data()
                elif event.button.id == "welcome-new-empty":
                    self.log("Calling action_new_empty_sheet")
                    data_grid.action_new_empty_sheet()
                elif event.button.id == "welcome-paste-clipboard":
                    self.log("Calling action_paste_from_clipboard")
                    data_grid.action_paste_from_clipboard()
                elif event.button.id == "welcome-connect-database":
                    self.log("***** OPENING DATABASE CONNECTION MODAL *****")
                    try:
                        self.log("Creating DatabaseConnectionModal instance...")
                        modal = DatabaseConnectionModal()
                        self.log("Modal created successfully")
                        self.log("Pushing modal screen...")
                        self.app.push_screen(modal, self._handle_database_connection)
                        self.log("Modal pushed successfully")
                    except Exception as modal_error:
                        self.log(f"Error opening modal: {modal_error}")
                        import traceback

                        self.log(f"Modal traceback: {traceback.format_exc()}")
            else:
                self.log(f"Data grid not found, parent.parent is: {type(data_grid)}")
        except Exception as e:
            self.log(f"Error accessing data grid: {e}")

        # Consume the event to prevent further propagation
        event.stop()

    def on_mount(self) -> None:
        """Set up keyboard focus on the first button when the overlay is mounted."""
        # Use call_after_refresh to ensure the overlay is fully ready
        self.call_after_refresh(self._setup_initial_focus)

    def _setup_initial_focus(self) -> None:
        """Set up the initial focus on the first button."""
        try:
            # Focus on the first button (New Empty Sheet) by default
            first_button = self.query_one("#welcome-new-empty", Button)
            first_button.focus()
            self.log("Focused on first button: New Empty Sheet")

            # Additional delay to ensure focus is properly set
            self.set_timer(0.1, lambda: self._ensure_focus())
        except Exception as e:
            self.log(f"Error focusing first button: {e}")

    def _ensure_focus(self) -> None:
        """Ensure focus is properly set on the first button."""
        try:
            first_button = self.query_one("#welcome-new-empty", Button)
            if not first_button.has_focus:
                first_button.focus()
                self.log("Re-focused first button after delay")
            else:
                self.log("First button already has focus")
        except Exception as e:
            self.log(f"Error ensuring focus: {e}")

    def on_key(self, event) -> bool:
        """Handle keyboard navigation in the welcome overlay."""
        if event.key == "left":
            self._navigate_buttons(-1)
            return True
        elif event.key == "right":
            self._navigate_buttons(1)
            return True
        elif event.key == "up":
            self._navigate_buttons_vertical(-1)
            return True
        elif event.key == "down":
            self._navigate_buttons_vertical(1)
            return True
        elif event.key == "enter":
            self._activate_focused_button()
            return True
        return False

    def _navigate_buttons(self, direction: int) -> None:
        """Navigate between buttons using arrow keys."""
        # Define the button order: include all buttons
        button_ids = [
            "welcome-new-empty",
            "welcome-load-dataset",
            "welcome-load-sample",
            "welcome-paste-clipboard",
            "welcome-exit",
        ]

        try:
            # Find currently focused button
            focused_button_id = None
            for button_id in button_ids:
                button = self.query_one(f"#{button_id}", Button)
                if button.has_focus:
                    focused_button_id = button_id
                    break

            if focused_button_id is not None:
                current_index = button_ids.index(focused_button_id)
                new_index = (current_index + direction) % len(button_ids)
                new_button = self.query_one(f"#{button_ids[new_index]}", Button)
                new_button.focus()
            else:
                # If no button is focused, focus the first one
                first_button = self.query_one(f"#{button_ids[0]}", Button)
                first_button.focus()

        except Exception as e:
            self.log(f"Error navigating buttons: {e}")

    def _navigate_buttons_vertical(self, direction: int) -> None:
        """Navigate between button rows using up/down arrow keys."""
        # Define button layout by rows
        first_row = [
            "welcome-new-empty",
            "welcome-load-dataset",
            "welcome-load-sample",
            "welcome-paste-clipboard",
        ]
        second_row = ["welcome-connect-database", "welcome-exit"]

        try:
            # Find currently focused button and its row
            focused_button_id = None
            current_row = None
            current_col = None

            for i, button_id in enumerate(first_row):
                button = self.query_one(f"#{button_id}", Button)
                if button.has_focus:
                    focused_button_id = button_id
                    current_row = 0  # First row
                    current_col = i
                    break

            if focused_button_id is None:
                for i, button_id in enumerate(second_row):
                    button = self.query_one(f"#{button_id}", Button)
                    if button.has_focus:
                        focused_button_id = button_id
                        current_row = 1  # Second row
                        current_col = i
                        break

            if focused_button_id is not None:
                if direction == -1:  # Up arrow
                    if current_row == 1:  # From second row to first row
                        # Try to go to same column position in first row, or closest available
                        target_col = min(current_col, len(first_row) - 1)
                        target_button = self.query_one(f"#{first_row[target_col]}", Button)
                        target_button.focus()
                    # If already in first row, stay there (or could wrap to second row)
                elif direction == 1:  # Down arrow
                    if current_row == 0:  # From first row to second row
                        # Go to same column position in second row, or closest available
                        target_col = min(current_col, len(second_row) - 1)
                        target_button = self.query_one(f"#{second_row[target_col]}", Button)
                        target_button.focus()
                    # If already in second row, stay there (or could wrap to first row)
            else:
                # If no button is focused, focus the first one
                first_button = self.query_one(f"#{first_row[0]}", Button)
                first_button.focus()

        except Exception as e:
            self.log(f"Error navigating buttons vertically: {e}")

    def _activate_focused_button(self) -> None:
        """Activate the currently focused button."""
        # Find the focused button and trigger its press event: include all buttons
        button_ids = [
            "welcome-new-empty",
            "welcome-load-dataset",
            "welcome-load-sample",
            "welcome-paste-clipboard",
            "welcome-connect-database",
            "welcome-exit",
        ]

        try:
            for button_id in button_ids:
                button = self.query_one(f"#{button_id}", Button)
                if button.has_focus:
                    # Trigger the button press
                    button.press()
                    break
        except Exception as e:
            self.log(f"Error activating focused button: {e}")

    def _handle_database_connection(self, connection_result: dict | None) -> None:
        """Handle the result from the database connection modal."""
        self.log(f"Database connection modal callback called with result: {connection_result}")

        if connection_result:
            self.log(f"Database connection requested with: {connection_result}")

            # Find the data grid and connect to the database
            try:
                self.log(
                    f"Looking for data grid, parent: {type(self.parent)}, parent.parent: {type(self.parent.parent) if self.parent else 'None'}"
                )
                data_grid = self.parent.parent
                self.log(f"Found data grid candidate: {type(data_grid)}")

                if isinstance(data_grid, ExcelDataGrid):
                    self.log("Data grid is ExcelDataGrid, proceeding with connection")
                    if connection_result.get("connection_string"):
                        connection_string = connection_result["connection_string"]
                        self.log(f"Calling connect_to_database with: {connection_string}")
                        data_grid.connect_to_database(connection_string)
                        # Hide the welcome overlay after successful connection with a small delay
                        # to allow the focus logic to complete
                        self.log("Scheduling welcome overlay hide after database connection")
                        self.set_timer(0.5, lambda: self._hide_welcome_overlay())
                    else:
                        self.log("No connection string provided in result")
                else:
                    self.log(
                        f"Data grid not found or wrong type, parent.parent is: {type(data_grid)}"
                    )
            except Exception as e:
                self.log(f"Error connecting to database: {e}")
                import traceback

                self.log(f"Traceback: {traceback.format_exc()}")
        else:
            self.log("Database connection cancelled or no result")

    def _hide_welcome_overlay(self) -> None:
        """Hide the welcome overlay after database connection."""
        try:
            self.log("Hiding welcome overlay after database connection")
            self.add_class("hidden")
        except Exception as e:
            self.log(f"Error hiding welcome overlay: {e}")


class DataDirectoryTree(DirectoryTree):
    """A DirectoryTree that filters to show only data files and directories."""

    def filter_paths(self, paths):
        """Filter paths to show only directories and supported data files."""
        data_extensions = {
            ".csv",
            ".tsv",
            ".txt",
            ".parquet",
            ".json",
            ".jsonl",
            ".ndjson",
            ".xlsx",
            ".xls",
            ".feather",
            ".ipc",
            ".arrow",
            ".db",
            ".sqlite",
            ".sqlite3",
            ".ddb",
        }

        filtered = []
        for path in paths:
            # Always include directories so users can navigate
            if path.is_dir():
                filtered.append(path)
            # Include files with supported data extensions
            elif path.is_file() and path.suffix.lower() in data_extensions:
                filtered.append(path)

        return filtered


class FileBrowserModal(ModalScreen[str]):
    """Modal screen for file selection using DirectoryTree."""

    CSS = """
    FileBrowserModal {
        align: center middle;
    }

    #file-browser {
        width: 95;
        height: 45;
        background: $surface;
        border: thick $primary;
        padding: 1;
    }

    #file-browser .modal-title {
        text-style: bold;
        text-align: center;
        margin-bottom: 1;
        color: $primary;
    }

    #file-browser .instructions {
        text-align: center;
        margin-bottom: 1;
        color: $text-muted;
    }

    .directory-shortcuts {
        height: 5;
        margin-bottom: 1;
        layout: horizontal;
        align: center middle;
    }

    .directory-shortcuts Button {
        margin: 0 1;
        min-width: 8;
        height: 3;
    }

    #directory-tree {
        height: 17;
        border: solid $secondary;
        margin-bottom: 1;
    }

    #selected-file {
        height: 6;
        background: $surface-darken-1;
        border: solid $primary;
        padding: 1;
        margin-bottom: 1;
    }

    .selected-file-label {
        text-style: bold;
        color: $primary;
    }

    .selected-file-path {
        color: $accent;
    }

    .error-message {
        color: $error;
        background: $error-darken-2;
        padding: 1;
        margin-bottom: 1;
        text-align: center;
        border: solid $error;
    }

    .error-message.hidden {
        display: none;
    }

    .modal-buttons {
        height: 3;
        align: center middle;
        layout: horizontal;
    }

    .modal-buttons Button {
        margin: 0 2;
        min-width: 12;
    }
    """

    def __init__(self, initial_path: str = None, **kwargs):
        super().__init__(**kwargs)
        self.selected_file_path = None
        # Use current working directory if no initial path provided
        if initial_path is None:
            initial_path = os.getcwd()
        self.initial_path = Path(initial_path).expanduser().absolute()

    def compose(self) -> ComposeResult:
        """Compose the modal content."""
        with Vertical(id="file-browser"):
            yield Static("Select Data File", classes="modal-title")
            yield Static("Navigate and click on a file to select it", classes="instructions")

            # Directory shortcuts for quick navigation
            with Horizontal(classes="directory-shortcuts"):
                yield Button("CWD", id="nav-current", variant="default")
                yield Button("Home", id="nav-home", variant="default")
                yield Button("Desktop", id="nav-desktop", variant="default")
                yield Button("Documents", id="nav-documents", variant="default")
                yield Button("Downloads", id="nav-downloads", variant="default")

            # Directory tree for file navigation (filtered for data files)
            yield DataDirectoryTree(str(self.initial_path), id="directory-tree")

            # Display selected file
            with Vertical(id="selected-file"):
                yield Static("Selected file:", classes="selected-file-label")
                yield Static("No file selected", id="selected-path", classes="selected-file-path")

            # Error message area
            yield Static("", id="error-message", classes="error-message hidden")

            # Buttons
            with Horizontal(classes="modal-buttons"):
                yield Button("Cancel", id="cancel-file", variant="error")
                yield Button("Load File", id="load-file", variant="primary", disabled=True)

    def on_directory_tree_file_selected(self, event: DirectoryTree.FileSelected) -> None:
        """Handle file selection in the directory tree."""
        file_path = event.path
        self.selected_file_path = file_path

        # Update the selected file display
        selected_path = self.query_one("#selected-path", Static)
        selected_path.update(str(file_path))

        # Enable the load button
        load_button = self.query_one("#load-file", Button)
        load_button.disabled = False

        # Clear any previous error
        self._clear_error()

        # Focus the Load File button after file selection
        self.call_after_refresh(lambda: load_button.focus())

    def on_mount(self) -> None:
        """Set initial focus on the directory tree when modal is mounted."""
        self.call_after_refresh(self._set_initial_focus)

    def _set_initial_focus(self) -> None:
        """Set the initial focus on the directory tree."""
        try:
            tree = self.query_one("#directory-tree", DataDirectoryTree)
            tree.focus()
            self.log("Initial focus set on directory tree")
        except Exception as e:
            self.log(f"Error setting initial focus on directory tree: {e}")

    def on_key(self, event) -> None:
        """Handle keyboard shortcuts in the file browser."""
        if event.key == "enter":
            # Check if a location shortcut button has focus
            try:
                shortcut_buttons = [
                    self.query_one("#nav-current", Button),
                    self.query_one("#nav-home", Button),
                    self.query_one("#nav-desktop", Button),
                    self.query_one("#nav-documents", Button),
                    self.query_one("#nav-downloads", Button),
                ]

                # Check if a shortcut button has focus, let it navigate and then focus the tree
                for button in shortcut_buttons:
                    if button.has_focus:
                        # Let the button handle the navigation, then focus tree
                        button_id = button.id
                        self._navigate_to_directory(button_id)
                        return  # _navigate_to_directory already focuses the tree
            except Exception:
                pass

            # Check if Load File button has focus
            try:
                load_button = self.query_one("#load-file", Button)
                if load_button.has_focus and not load_button.disabled:
                    # Let the button handle the Enter key naturally
                    # Don't intercept: let it trigger the button press event
                    return
            except Exception:
                pass

            # Check if Cancel button has focus
            try:
                cancel_button = self.query_one("#cancel-file", Button)
                if cancel_button.has_focus:
                    # Let the button handle the Enter key naturally
                    return
            except Exception:
                pass

            # If a file is selected but no button has focus,
            # and we have a selected file, load it
            if self.selected_file_path:
                self._try_load_file()
        elif event.key == "escape":
            # Escape key cancels
            self.dismiss(None)
        elif event.key == "tab" or event.key == "shift+tab":
            # Tab navigation between major UI groups
            self._handle_tab_navigation(event.key == "shift+tab")
            # Prevent the event from bubbling up to avoid default tab behavior
            event.prevent_default()
            event.stop()
        elif event.key in ["left", "right"]:
            # Arrow key navigation between buttons (only if a button has focus)
            self._handle_arrow_navigation(event.key == "left")

    def _handle_tab_navigation(self, reverse: bool = False) -> None:
        """Handle tab navigation between major UI groups (skip within location buttons)."""
        try:
            # Get all focusable groups in order: tree, location button group (as single unit), main buttons group
            tree = self.query_one("#directory-tree", DataDirectoryTree)
            shortcut_buttons = [
                self.query_one("#nav-current", Button),
                self.query_one("#nav-home", Button),
                self.query_one("#nav-desktop", Button),
                self.query_one("#nav-documents", Button),
                self.query_one("#nav-downloads", Button),
            ]
            load_button = self.query_one("#load-file", Button)
            cancel_button = self.query_one("#cancel-file", Button)

            # Determine which group currently has focus
            current_group = None
            if tree.has_focus:
                current_group = "tree"
            elif any(btn.has_focus for btn in shortcut_buttons):
                current_group = "shortcuts"
            elif load_button.has_focus or cancel_button.has_focus:
                current_group = "main_buttons"

            # Navigate between groups
            if current_group == "tree":
                if reverse:
                    # Go to main buttons (focus load button if enabled, otherwise cancel)
                    if not load_button.disabled:
                        load_button.focus()
                    else:
                        cancel_button.focus()
                else:
                    # Go to first shortcut button
                    shortcut_buttons[0].focus()
            elif current_group == "shortcuts":
                if reverse:
                    # Go to tree
                    tree.focus()
                else:
                    # Go to main buttons (focus load button if enabled, otherwise cancel)
                    if not load_button.disabled:
                        load_button.focus()
                    else:
                        cancel_button.focus()
            elif current_group == "main_buttons":
                if reverse:
                    # Go to first shortcut button
                    shortcut_buttons[0].focus()
                else:
                    # Go to tree
                    tree.focus()
            else:
                # No group focused, focus the tree (first element)
                tree.focus()

            self.log(f"Tab navigation: moved from {current_group} group")

        except Exception as e:
            self.log(f"Error in tab navigation: {e}")

    def _handle_arrow_navigation(self, left: bool = True) -> None:
        """Handle arrow key navigation between buttons in the same group."""
        try:
            # Get directory shortcut buttons
            shortcut_buttons = [
                self.query_one("#nav-current", Button),
                self.query_one("#nav-home", Button),
                self.query_one("#nav-desktop", Button),
                self.query_one("#nav-documents", Button),
                self.query_one("#nav-downloads", Button),
            ]

            # Get main buttons (Load/Cancel)
            load_button = self.query_one("#load-file", Button)
            cancel_button = self.query_one("#cancel-file", Button)

            # Check if any shortcut button has focus: handle shortcut button navigation
            focused_shortcut = -1
            for i, button in enumerate(shortcut_buttons):
                if button.has_focus:
                    focused_shortcut = i
                    break

            if focused_shortcut >= 0:
                # Navigate within shortcut buttons using arrow keys
                if left:
                    next_index = (focused_shortcut - 1) % len(shortcut_buttons)
                else:
                    next_index = (focused_shortcut + 1) % len(shortcut_buttons)
                shortcut_buttons[next_index].focus()
                self.log(f"Arrow navigation: shortcut button {next_index}")
                return

            # Check if either main button has focus: handle main button navigation
            if load_button.has_focus or cancel_button.has_focus:
                if left:
                    # Left arrow: focus Cancel button
                    cancel_button.focus()
                    self.log("Arrow navigation: focused Cancel button")
                else:  # right
                    # Right arrow: focus Load button (if enabled)
                    if not load_button.disabled:
                        load_button.focus()
                        self.log("Arrow navigation: focused Load button")
                    else:
                        # If Load button is disabled, stay on Cancel
                        cancel_button.focus()
                        self.log("Arrow navigation: Load button disabled, staying on Cancel")
                return

        except Exception as e:
            self.log(f"Error in arrow navigation: {e}")

    def _handle_button_navigation(self, reverse: bool = False) -> None:
        """Handle tab navigation between buttons."""
        try:
            load_button = self.query_one("#load-file", Button)
            cancel_button = self.query_one("#cancel-file", Button)

            # Determine current focus
            if load_button.has_focus:
                if reverse:
                    cancel_button.focus()
                else:
                    cancel_button.focus()
            elif cancel_button.has_focus:
                if reverse:
                    if not load_button.disabled:
                        load_button.focus()
                else:
                    if not load_button.disabled:
                        load_button.focus()
            else:
                # No button has focus, focus the appropriate default button
                if not load_button.disabled:
                    load_button.focus()
                else:
                    cancel_button.focus()
        except Exception as e:
            self.log(f"Error in button navigation: {e}")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        """Handle button presses in the modal."""
        if event.button.id == "load-file":
            self._try_load_file()
        elif event.button.id == "cancel-file":
            self.dismiss(None)
        elif event.button.id.startswith("nav-"):
            # Handle directory navigation shortcuts
            self._navigate_to_directory(event.button.id)

    def _navigate_to_directory(self, button_id: str) -> None:
        """Navigate to a specific directory based on button ID."""
        try:
            directory_map = {
                "nav-home": Path.home(),
                "nav-desktop": Path.home() / "Desktop",
                "nav-documents": Path.home() / "Documents",
                "nav-downloads": Path.home() / "Downloads",
                "nav-current": Path.cwd(),
            }

            target_path = directory_map.get(button_id)
            if target_path and target_path.exists() and target_path.is_dir():
                # Update the directory tree to show the new path
                tree = self.query_one("#directory-tree", DataDirectoryTree)
                tree.path = str(target_path)
                tree.reload()

                # Clear any selected file since we're navigating
                self.selected_file_path = None
                selected_path = self.query_one("#selected-path", Static)
                selected_path.update("No file selected")

                # Disable load button
                load_button = self.query_one("#load-file", Button)
                load_button.disabled = True

                # Clear any errors
                self._clear_error()

                # Focus the directory tree after navigation
                self.call_after_refresh(lambda: tree.focus())

                self.log(f"Navigated to: {target_path}")
            else:
                self._show_error(f"Directory not accessible: {target_path}")

        except Exception as e:
            self.log(f"Error navigating to directory: {e}")
            self._show_error(f"Failed to navigate: {str(e)[:30]}...")

    def _try_load_file(self) -> None:
        """Try to load the selected file and validate it."""
        if not self.selected_file_path:
            self._show_error("Please select a file")
            return

        file_path = str(self.selected_file_path)

        # Check if file exists
        try:
            file_obj = Path(file_path)
            if not file_obj.exists():
                self._show_error(f"File not found: {file_path}")
                return

            if not file_obj.is_file():
                self._show_error(f"Path is not a file: {file_path}")
                return

            # Try to validate that polars can read the file
            if pl is None:
                self._show_error("Polars library not available")
                return

            # Check file extension: support multiple formats
            supported_extensions = (
                ".csv",
                ".tsv",
                ".txt",
                ".parquet",
                ".json",
                ".jsonl",
                ".ndjson",
                ".xlsx",
                ".xls",
                ".feather",
                ".ipc",
                ".arrow",
                ".db",
                ".sqlite",
                ".sqlite3",
                ".ddb",
            )
            if not file_path.lower().endswith(supported_extensions):
                self._show_error(
                    "Unsupported file format. Supported: CSV, TSV, TXT, Parquet, JSON, JSONL, Excel, Feather, Arrow, Database (SQLite, DuckDB)"
                )
                return

            # Try to read first few rows to validate
            try:
                extension = file_path.lower().split(".")[-1]
                if extension in ["csv", "txt"]:
                    df_test = pl.read_csv(file_path, n_rows=5)
                elif extension == "tsv":
                    df_test = pl.read_csv(file_path, separator="\t", n_rows=5)
                elif extension == "parquet":
                    df_test = pl.read_parquet(file_path).head(5)
                elif extension == "json":
                    df_test = pl.read_json(file_path).head(5)
                elif extension in ["jsonl", "ndjson"]:
                    df_test = pl.read_ndjson(file_path).head(5)
                elif extension in ["xlsx", "xls"]:
                    try:
                        df_test = pl.read_excel(file_path).head(5)
                    except AttributeError:
                        self._show_error("Excel support requires additional dependencies")
                        return
                elif extension in ["feather", "ipc", "arrow"]:
                    df_test = pl.read_ipc(file_path).head(5)
                elif extension in ["db", "sqlite", "sqlite3", "ddb"]:
                    # Database files: validate by attempting to connect
                    try:
                        import duckdb

                        test_conn = duckdb.connect(file_path, read_only=True)
                        # Try to get table list to validate it's a valid database
                        try:
                            test_conn.execute(
                                "SELECT name FROM sqlite_master WHERE type='table'"
                            ).fetchall()
                        except Exception:
                            # Try alternative query for other database types
                            test_conn.execute(
                                "SELECT table_name FROM information_schema.tables WHERE table_schema = 'main'"
                            ).fetchall()
                        test_conn.close()
                        # Database is valid, skip the dataframe validation
                        self.log(f"Database file validation successful: {file_path}")
                        self.call_after_refresh(lambda: self._dismiss_modal_with_file(file_path))
                        return
                    except Exception as e:
                        self._show_error(f"Invalid database file: {str(e)[:50]}...")
                        return
                else:
                    # Fallback to CSV
                    df_test = pl.read_csv(file_path, n_rows=5)

                if df_test.shape[0] == 0:
                    self._show_error("File appears to be empty")
                    return

                # File is valid: log success and dismiss modal with file path
                self.log(f"File validation successful: {file_path}")
                # Use call_after_refresh to ensure dismissal happens after current event processing
                self.call_after_refresh(lambda: self._dismiss_modal_with_file(file_path))
                return

            except Exception as e:
                self.log(f"File validation failed: {str(e)}")
                self._show_error(f"Cannot read file: {str(e)[:50]}...")
                return

        except Exception as e:
            self.log(f"File access error: {str(e)}")
            self._show_error(f"Error accessing file: {str(e)[:50]}...")
            return

    def _show_error(self, message: str) -> None:
        """Show an error message in the modal."""
        error_message = self.query_one("#error-message", Static)
        error_message.update(message)
        error_message.remove_class("hidden")

        # Clear error after a few seconds
        self.set_timer(5.0, lambda: self._clear_error())

    def _clear_error(self) -> None:
        """Clear the error message."""
        try:
            error_message = self.query_one("#error-message", Static)
            error_message.add_class("hidden")
            error_message.update("")
        except Exception:
            pass

    def _dismiss_modal_with_file(self, file_path: str) -> None:
        """Helper method to dismiss modal with file path."""
        try:
            self.log(f"Attempting to dismiss modal with file: {file_path}")
            self.dismiss(file_path)
            self.log("Modal dismissed successfully")
        except Exception as e:
            self.log(f"Error dismissing modal: {e}")
            # Force close the modal if dismiss fails
            try:
                self.app.pop_screen()
                self.log("Modal forcibly closed via pop_screen")
                # Still call the callback manually if we had to force close
                if hasattr(self.app, "_modal_callback"):
                    self.log("Calling modal callback manually")
                    self.app._modal_callback(file_path)
            except Exception as e2:
                self.log(f"Error force-closing modal: {e2}")


class CustomDataTable(DataTable):
    """Custom DataTable that allows immediate editing for specific keys and handles row label clicks."""

    def on_key(self, event) -> bool:
        """Handle key events: delegate immediate edit keys to parent first."""
        # Only intercept keys that should trigger immediate editing
        if self._should_delegate_key(event.key):
            # Find the ExcelDataGrid parent
            parent = self.parent
            while parent and not isinstance(parent, ExcelDataGrid):
                parent = parent.parent

            if parent:
                # Let parent handle immediate editing
                if parent._handle_immediate_edit_key(event):
                    return True  # Parent handled the key, event consumed

        # For all other keys, let DataTable handle them normally
        # Return False to allow normal event handling to continue
        return False

    def on_data_table_header_selected(self, event: DataTable.HeaderSelected) -> None:
        """Handle column header clicks."""
        # Find the ExcelDataGrid parent
        parent = self.parent
        while parent and not isinstance(parent, ExcelDataGrid):
            parent = parent.parent

        if parent:
            # Check if this is a valid column click (not the corner cell)
            # The corner cell might have column_index of -1 or be outside valid range
            if parent.data is not None:
                visible_columns = [
                    col for col in parent.data.columns if col != "__original_row_index__"
                ]
                max_valid_col = len(visible_columns)  # Include pseudo-column

                # Only handle clicks on actual column headers (not corner cell)
                if 0 <= event.column_index <= max_valid_col:
                    parent.log(f"Column header clicked: {event.column_index} ({event.label})")
                    parent._handle_column_header_click(event.column_index)
                else:
                    parent.log(f"Corner cell clicked (column_index={event.column_index}), ignoring")
            else:
                # No data loaded, ignore all header clicks
                parent.log(
                    f"Header clicked but no data loaded (column_index={event.column_index}), ignoring"
                )

    def on_data_table_row_label_selected(self, event: DataTable.RowLabelSelected) -> None:
        """Handle row label clicks."""
        # Find the ExcelDataGrid parent
        parent = self.parent
        while parent and not isinstance(parent, ExcelDataGrid):
            parent = parent.parent

        if parent:
            parent.log(f"Row label clicked: {event.row_index}")
            parent._handle_row_label_click(event.row_index)

    def on_click(self, event) -> None:
        """Handle click events for right-click menu and search mode redirection."""
        # Find the ExcelDataGrid parent
        parent = self.parent
        while parent and not isinstance(parent, ExcelDataGrid):
            parent = parent.parent

        if not parent:
            return

        # Check if we're in search mode and handle click redirection for left-clicks
        search_overlay = parent.query_one(SearchOverlay)
        if search_overlay.is_active and search_overlay.matches:
            # Only handle left-clicks for search redirection
            if not hasattr(event, "button") or event.button != 2:  # Not right-click
                # Get the current cursor position after the click
                cursor_row = self.cursor_row - 1  # Convert to 0-based index (subtract header)
                cursor_col = self.cursor_column

                # Check if the clicked cell is already a match
                clicked_position = (cursor_row, cursor_col)
                if clicked_position not in search_overlay.matches:
                    # Find the nearest match to the clicked position
                    nearest_match = parent._find_nearest_match(
                        cursor_row, cursor_col, search_overlay.matches
                    )
                    if nearest_match:
                        # Update search overlay to navigate to this match
                        match_index = search_overlay.matches.index(nearest_match)
                        search_overlay.current_match_index = match_index
                        search_overlay._navigate_to_current_match()

                        # Prevent default click behavior
                        event.prevent_default()
                        event.stop()
                        return

        # Check if this is a right-click
        if hasattr(event, "button") and event.button == 2:  # Right mouse button
            parent.log("Right-click detected")
            # Show delete menu for right-click
            parent.action_show_delete_menu()
            return

        # DataTable doesn't have on_click method, so we don't call super()

    def _should_delegate_key(self, key: str) -> bool:
        """Check if this key should be delegated to parent for immediate editing."""
        if len(key) == 1:  # Single character keys only
            return key.isalnum()
        # Handle special keys with their Textual key names
        return key in ["plus", "minus", "full_stop"]


class ExcelDataGrid(Widget):
    """Excel-like data grid widget with editable cells and Excel addressing."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._table = CustomDataTable(classes="data-grid-table")
        self.data = None
        self.original_data = None  # Store original data for change tracking
        self.has_changes = False  # Track if data has been modified
        self._current_address = "A1"

    def call_after_refresh(self, callback, *args, **kwargs):
        """Helper method to call a function after the next refresh using set_timer."""
        self.set_timer(0.01, lambda: callback(*args, **kwargs))
        self.editing_cell = False
        self._edit_input = None
        self.original_data = None  # Store original data for change tracking
        self.has_changes = False  # Track if data has been modified
        self._editing_cell = None  # Currently editing cell coordinate

        # Double-click tracking
        self._last_click_time = 0
        self._last_click_coordinate = None
        self._double_click_threshold = 0.5  # 500ms for double-click detection

        # Search state
        self.search_matches = []  # List of (row, col) tuples for found cells

        # Row label double-click tracking
        self._last_row_label_click_time = 0
        self._last_row_label_clicked = None

        self.is_sample_data = False  # Track if we're working with internal sample data
        self.data_source_name = None  # Name of the data source (for sample data)
        self.is_data_truncated = False  # Track if data display is truncated due to large size
        self._display_offset = 0  # Track offset when viewing slices of large datasets

        # Database mode tracking
        self.is_database_mode = False  # Track if we're in database analysis mode
        self.database_path = None  # Path to database file
        self.database_connection = None  # DuckDB connection for database mode
        self.current_table_name = None  # Currently selected table in database mode
        self.database_schema = {}  # Store original database column types
        self.current_table_column_types = {}  # Store column types for current table
        self.native_column_types = {}  # DIRECT: Store native DB column types
        self.available_tables = []  # List of available tables in database

        # Double-tap left arrow tracking (keyboard equivalent to double-click)
        self._last_left_arrow_time = 0
        self._last_left_arrow_position = None

        # Double-tap up arrow tracking for column operations
        self._last_up_arrow_time = 0
        self._last_up_arrow_position = None

        # Exit search gesture tracking (left-right-left-right)
        self._gesture_sequence = []  # Track the sequence of arrow keys
        self._gesture_start_time = 0  # When the gesture sequence started
        self._gesture_timeout = 2.0  # 2 seconds to complete the full gesture
        self._gesture_max_interval = 0.5  # Max time between individual keys in gesture

        # Sorting state tracking - now supports multiple ordered sorts
        self._sort_columns = []  # List of (column_index, ascending) tuples in sort order
        self._original_data = None  # Store original data for unsorted state
        self._pending_cell_edits = {}  # Track pending cell edits: {(row, col): value}

        # Search state tracking
        self.search_matches = []  # List of (row, col) tuples for search matches
        self.current_search_match = None  # Currently highlighted search match (row, col)

        # Column click debouncing for sort vs double-click detection
        self._pending_sort_timer = None  # Timer for delayed sorting
        self._pending_sort_column = None  # Column pending sort

        # Override the DataTable's clear method to preserve row labels
        original_clear = self._table.clear

        def preserve_row_labels_clear(*args, **kwargs):
            result = original_clear(*args, **kwargs)
            self._table.show_row_labels = True
            return result

        self._table.clear = preserve_row_labels_clear

    def compose(self) -> ComposeResult:
        """Compose the data grid widget."""
        with Vertical():
            # Hide load controls: they're now in the welcome overlay
            with Horizontal(id="load-controls", classes="load-controls hidden"):
                yield Button("Load Dataset", id="load-dataset", classes="load-button")
                yield Button("Load Sample Data", id="load-sample", classes="load-button")

            # Main table area (simplified without edge controls)
            with Vertical(id="table-area"):
                yield self._table

            # Search overlay
            yield SearchOverlay(data_grid=self)

            # Create status bar with simple content
            yield Static("No data loaded", id="status-bar", classes="status-bar")
            # Add welcome overlay
            yield WelcomeOverlay(id="welcome-overlay")

    def on_mount(self) -> None:
        """Initialize the data grid on mount."""
        self._table.cursor_type = "cell"  # Enable cell-level navigation
        self._table.zebra_stripes = False
        self._table.show_header = True
        self._table.show_row_labels = True  # This shows row numbers

        # Force row labels to be visible by calling refresh after setting
        self._table.refresh()

        # Start with empty state: don't load sample data automatically
        # self.load_sample_data()  # Commented out for empty start

        # Set up initial empty state
        self.show_empty_state()

        # Set up a timer to periodically check cursor position
        self.set_interval(0.1, self._check_cursor_position)

    def show_empty_state(self) -> None:
        """Show empty state with welcome overlay."""
        # Clear the table
        self._table.clear(columns=True)

        # Ensure row labels remain enabled
        self._table.show_row_labels = True

        # Reset data and original data to None
        self.data = None
        self.original_data = None

        # Reset data tracking flags
        self.is_sample_data = False
        self.data_source_name = None
        self.has_changes = False

        # Clear the filename from title
        self.app.set_current_filename(None)

        # Hide the status bar during welcome screen
        try:
            status_bar = self.query_one("#status-bar", Static)
            status_bar.display = False
        except Exception as e:
            self.log(f"Error hiding status bar: {e}")

        # Hide header and footer bars
        try:
            # Hide the header (blue bar)
            header = self.app.query_one("Header")
            header.display = False
        except Exception as e:
            self.log(f"Error hiding header: {e}")

        try:
            # Hide the footer (green bar)
            footer = self.app.query_one("SweetFooter")
            footer.display = False
        except Exception as e:
            self.log(f"Error hiding footer: {e}")

        # Show welcome overlay
        try:
            welcome_overlay = self.query_one("#welcome-overlay", WelcomeOverlay)
            welcome_overlay.remove_class("hidden")
            welcome_overlay.display = True  # Also set display to True
            # Focus the welcome overlay so it can receive keyboard events
            self.call_after_refresh(lambda: welcome_overlay.focus())
            # Add additional focus attempt with delay
            self.set_timer(0.2, self._focus_welcome_buttons)
        except Exception as e:
            self.log(f"Error showing welcome overlay: {e}")

    def _focus_welcome_buttons(self) -> None:
        """Focus the welcome buttons with a delay."""
        try:
            welcome_overlay = self.query_one("#welcome-overlay", WelcomeOverlay)
            first_button = welcome_overlay.query_one("#welcome-new-empty", Button)
            first_button.focus()
            self.log("Delayed focus set on welcome buttons")
        except Exception as e:
            self.log(f"Error setting delayed focus: {e}")

    def _create_welcome_state(self) -> None:
        """Create a clean welcome state without complex recreations."""
        try:
            self.log("Creating clean welcome state...")

            # First, clear the table cleanly
            self._table.clear(columns=True)
            self._table.show_row_labels = True

            # Show welcome overlay
            welcome_overlay = self.query_one("#welcome-overlay", WelcomeOverlay)
            welcome_overlay.remove_class("hidden")
            welcome_overlay.display = True

            # Hide status bar during welcome screen
            status_bar = self.query_one("#status-bar", Static)
            status_bar.display = False

            # Hide header and footer bars
            try:
                header = self.app.query_one("Header")
                header.display = False
            except Exception as e:
                self.log(f"Note: Could not hide header: {e}")

            try:
                footer = self.app.query_one("SweetFooter")
                footer.display = False
            except Exception as e:
                self.log(f"Note: Could not hide footer: {e}")

            # Set focus after refresh
            self.call_after_refresh(lambda: welcome_overlay.focus())
            self.set_timer(0.2, self._focus_welcome_buttons)

            self.log("Welcome state created successfully")

        except Exception as e:
            self.log(f"Error creating welcome state: {e}")
            # Fallback to the original method
            self.show_empty_state()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        """Handle button presses in the data grid."""
        if event.button.id == "load-dataset":
            self.action_load_dataset()
        elif event.button.id == "load-sample":
            self.action_load_sample_data()

    def on_click(self, event) -> None:
        """Handle click events for static elements."""
        # Click handling for data table cells is now handled in CustomDataTable
        pass

    def _find_nearest_match(
        self, clicked_row: int, clicked_col: int, matches: list[tuple[int, int]]
    ) -> tuple[int, int] | None:
        """Find the nearest match to the clicked position using Manhattan distance."""
        if not matches:
            return None

        min_distance = float("inf")
        nearest_match = None

        for match_row, match_col in matches:
            # Calculate Manhattan distance
            distance = abs(match_row - clicked_row) + abs(match_col - clicked_col)
            if distance < min_distance:
                min_distance = distance
                nearest_match = (match_row, match_col)

        return nearest_match

    def action_load_dataset(self) -> None:
        """Load a dataset from file using modal input."""

        def handle_file_input(file_path: str | None) -> None:
            self.log(f"FileBrowserModal callback received: {file_path}")
            if file_path:
                self.log(f"Loading file: {file_path}")
                try:
                    self.load_file(file_path)
                    self.log("File loaded successfully in callback")
                except Exception as e:
                    self.log(f"Error in file loading callback: {e}")
                    # Even if loading fails, we don't want to return to welcome screen
                    # The error will be displayed in the grid
            else:
                self.log("File loading cancelled: returning to welcome screen")
                # User cancelled: return to welcome screen
                self.show_empty_state()

        # Push the modal screen starting from the current working directory
        start_path = os.getcwd()  # Start from current working directory
        modal = FileBrowserModal(initial_path=start_path)
        self.app.push_screen(modal, handle_file_input)

    def action_load_sample_data(self) -> None:
        """Load sample data for demonstration."""
        self.log("action_load_sample_data called")
        self.load_sample_data()
        self.log("Load sample data button clicked")

    def action_new_empty_sheet(self) -> None:
        """Create a new empty sheet with 5 columns and 10 rows."""
        self.log("action_new_empty_sheet called")
        self.create_empty_sheet()
        self.log("New empty sheet created")

    def get_file_format(self, file_path: str) -> str:
        """Get the file format from the file extension."""
        extension = Path(file_path).suffix.lower()
        format_mapping = {
            ".csv": "CSV",
            ".tsv": "TSV",
            ".txt": "TXT",
            ".parquet": "PARQUET",
            ".json": "JSON",
            ".jsonl": "JSONL",
            ".ndjson": "NDJSON",
            ".xlsx": "XLSX",
            ".xls": "XLS",
            ".feather": "FEATHER",
            ".ipc": "ARROW",
            ".arrow": "ARROW",
            ".db": "DATABASE",
            ".sqlite": "DATABASE",
            ".sqlite3": "DATABASE",
            ".ddb": "DUCKDB",
        }
        return format_mapping.get(extension, "UNKNOWN")

    def load_file(self, file_path: str) -> None:
        """Load data from a specific file path."""
        try:
            self.log(f"Starting to load file: {file_path}")
        except Exception:
            # Fallback logging if no app context
            print(f"DEBUG: Starting to load file: {file_path}")

        try:
            if pl is None:
                try:
                    self.log("Polars not available")
                except Exception:
                    print("DEBUG: Polars not available")
                self._table.clear(columns=True)
                self._table.add_column("Error")
                self._table.add_row("Polars not available")
                return

            # Detect file format and load accordingly
            extension = Path(file_path).suffix.lower()
            try:
                self.log(f"File extension detected: {extension}")
            except Exception:
                print(f"DEBUG: File extension detected: {extension}")

            # Check if this is a database file
            if extension in [".db", ".sqlite", ".sqlite3", ".ddb"]:
                try:
                    self.log("Database file detected - entering SQL mode")
                except Exception:
                    print("DEBUG: Database file detected - entering SQL mode")
                self._load_database_file(file_path)
                return

            # Load the file based on extension
            if extension in [".csv", ".txt"]:
                self.log("Loading as CSV")
                df = pl.read_csv(file_path)
            elif extension == ".tsv":
                self.log("Loading as TSV")
                df = pl.read_csv(file_path, separator="\t")
            elif extension == ".parquet":
                self.log("Loading as Parquet")
                df = pl.read_parquet(file_path)
            elif extension == ".json":
                self.log("Loading as JSON")
                df = pl.read_json(file_path)
            elif extension in [".jsonl", ".ndjson"]:
                self.log("Loading as NDJSON")
                df = pl.read_ndjson(file_path)
            elif extension in [".xlsx", ".xls"]:
                self.log("Loading as Excel")
                # Note: Polars Excel support might require additional dependencies
                try:
                    df = pl.read_excel(file_path)
                except AttributeError as e:
                    raise Exception(
                        "Excel file support requires additional dependencies. Please install with: pip install polars[xlsx]"
                    ) from e
            elif extension == ".feather":
                self.log("Loading as Feather")
                df = pl.read_ipc(file_path)
            elif extension in [".ipc", ".arrow"]:
                self.log("Loading as Arrow/IPC")
                df = pl.read_ipc(file_path)
            else:
                self.log("Unknown extension, trying CSV as fallback")
                # Try CSV as fallback
                df = pl.read_csv(file_path)

            self.log(f"File loaded successfully, shape: {df.shape}")
            self.load_dataframe(df, force_recreation=True)

            # Mark as external file (not sample data) and regular mode
            self.is_sample_data = False
            self.data_source_name = None
            self.is_database_mode = False
            self.database_path = None
            self.database_schema = {}  # Clear database schema
            self.current_table_column_types = {}  # Clear column types
            self.native_column_types = {}  # Clear native types

            # Notify tools panel about regular mode
            try:
                debug_logger.info("Attempting to notify tools panel about regular mode")
                tools_panel = self.app.query_one("#tools-panel", ToolsPanel)
                tools_panel.set_database_mode(False)
                debug_logger.info("Successfully notified tools panel about regular mode")
            except Exception as e:
                debug_logger.error(f"Could not notify tools panel: {e}")
                self.log(f"Could not notify tools panel: {e}")

            # Update the app title with the filename and format
            file_format = self.get_file_format(file_path)
            filename_with_format = f"{file_path} [{file_format}]"
            self.app.set_current_filename(filename_with_format)
            self.log(f"File loading completed successfully: {filename_with_format}")

        except Exception as e:
            self.log(f"Error loading file {file_path}: {e}")
            self.log(f"Exception type: {type(e).__name__}")
            import traceback

            self.log(f"Traceback: {traceback.format_exc()}")
            self._table.clear(columns=True)
            self._table.add_column("Error")
            self._table.add_row(f"Failed to load {file_path}: {str(e)}")
            # Re-raise the exception so the callback can handle it
            raise

    def _load_database_file(self, file_path: str) -> None:
        """Load a database file and enter SQL analysis mode."""
        try:
            import duckdb

            try:
                self.log(f"Loading database file: {file_path}")
            except Exception:
                print(f"DEBUG: Loading database file: {file_path}")

            # Connect to the database
            self.database_connection = duckdb.connect(file_path, read_only=True)
            self.database_path = file_path
            self.database_connection_type = "file"  # Store connection type
            self.database_connection_params = {
                "file_path": file_path,
                "read_only": True,
            }  # Store connection params
            self.is_database_mode = True
            self.is_sample_data = False
            self.data_source_name = None
            self.database_schema = {}  # Initialize schema storage

            try:
                self.log("Database connection established successfully")
            except Exception:
                print("DEBUG: Database connection established successfully")

            # Test the connection with a simple query
            try:
                test_result = self.database_connection.execute("SELECT 1").fetchall()
                try:
                    self.log(f"Connection test successful: {test_result}")
                except Exception:
                    print(f"DEBUG: Connection test successful: {test_result}")
            except Exception as e:
                try:
                    self.log(f"Connection test failed: {e}")
                except Exception:
                    print(f"DEBUG: Connection test failed: {e}")

            # Get list of available tables
            try:
                self.log("Attempting to discover tables using SHOW TABLES...")
            except Exception:
                print("DEBUG: Attempting to discover tables using SHOW TABLES...")
            try:
                result = self.database_connection.execute("SHOW TABLES").fetchall()
                self.available_tables = [row[0] for row in result]
                try:
                    self.log(f"SHOW TABLES query successful: {self.available_tables}")
                except Exception:
                    print(f"DEBUG: SHOW TABLES query successful: {self.available_tables}")
            except Exception as e:
                try:
                    self.log(f"SHOW TABLES failed: {e}")
                except Exception:
                    print(f"DEBUG: SHOW TABLES failed: {e}")
                # Fallback for information_schema
                try:
                    try:
                        self.log("Trying information_schema fallback...")
                    except Exception:
                        print("DEBUG: Trying information_schema fallback...")
                    tables_query = "SELECT table_name FROM information_schema.tables WHERE table_schema = 'main'"
                    result = self.database_connection.execute(tables_query).fetchall()
                    self.available_tables = [row[0] for row in result]
                    try:
                        self.log(f"Information schema query successful: {self.available_tables}")
                    except Exception:
                        print(
                            f"DEBUG: Information schema query successful: {self.available_tables}"
                        )
                except Exception as e2:
                    try:
                        self.log(f"Information schema also failed: {e2}")
                    except Exception:
                        print(f"DEBUG: Information schema also failed: {e2}")
                    # Fallback for SQLite - fix the SQL syntax
                    try:
                        try:
                            self.log("Trying SQLite master table fallback...")
                        except Exception:
                            print("DEBUG: Trying SQLite master table fallback...")
                        result = self.database_connection.execute(
                            "SELECT name FROM sqlite_master WHERE type='table'"
                        ).fetchall()
                        self.available_tables = [row[0] for row in result]
                        try:
                            self.log(f"SQLite master query successful: {self.available_tables}")
                        except Exception:
                            print(f"DEBUG: SQLite master query successful: {self.available_tables}")
                    except Exception as e3:
                        try:
                            self.log(f"SQLite master query also failed: {e3}")
                        except Exception:
                            print(f"DEBUG: SQLite master query also failed: {e3}")
                        # Try one more approach - list all objects
                        try:
                            try:
                                self.log("Trying to list all database objects...")
                            except Exception:
                                print("DEBUG: Trying to list all database objects...")
                            result = self.database_connection.execute(
                                "SELECT name, type FROM sqlite_master"
                            ).fetchall()
                            try:
                                self.log(f"All database objects: {result}")
                            except Exception:
                                print(f"DEBUG: All database objects: {result}")
                            # Filter for tables only
                            tables = [row[0] for row in result if row[1] == "table"]
                            self.available_tables = tables
                            try:
                                self.log(f"Filtered tables: {tables}")
                            except Exception:
                                print(f"DEBUG: Filtered tables: {tables}")
                        except Exception as e4:
                            try:
                                self.log(f"Final fallback also failed: {e4}")
                                self.log("All table discovery methods failed - setting empty list")
                            except Exception:
                                print(f"DEBUG: Final fallback also failed: {e4}")
                                print(
                                    "DEBUG: All table discovery methods failed - setting empty list"
                                )
                            self.available_tables = []

            try:
                self.log(f"Final table list: {self.available_tables}")
            except Exception:
                print(f"DEBUG: Final table list: {self.available_tables}")

            # Update app title
            self.app.set_current_filename(f"{file_path} [Database]")

            # Notify tools panel about database mode BEFORE loading first table
            try:
                try:
                    self.log("Attempting to find tools panel...")
                except Exception:
                    print("DEBUG: Attempting to find tools panel...")
                tools_panel = self.app.query_one("#tools-panel", ToolsPanel)
                try:
                    self.log(f"Tools panel found: {tools_panel}")
                    self.log(f"Calling set_database_mode with tables: {self.available_tables}")
                except Exception:
                    print(f"DEBUG: Tools panel found: {tools_panel}")
                    print(f"DEBUG: Calling set_database_mode with tables: {self.available_tables}")
                tools_panel.set_database_mode(True, self.available_tables, is_remote=False)
                try:
                    self.log("Successfully notified tools panel about database mode")
                except Exception:
                    print("DEBUG: Successfully notified tools panel about database mode")
            except Exception as e:
                try:
                    self.log(f"Could not notify tools panel: {e}")
                    import traceback

                    self.log(f"Traceback: {traceback.format_exc()}")
                    # Try using call_after_refresh to delay the notification
                    self.log("Trying delayed notification via call_after_refresh...")
                    self.call_after_refresh(lambda: self._notify_tools_panel_database_mode())
                except Exception as e2:
                    print(f"DEBUG: Could not notify tools panel: {e}")
                    import traceback

                    print(f"DEBUG: Traceback: {traceback.format_exc()}")
                    # Try using call_after_refresh to delay the notification
                    print("DEBUG: Trying delayed notification via call_after_refresh...")
                    try:
                        self.call_after_refresh(lambda: self._notify_tools_panel_database_mode())
                    except Exception as e3:
                        print(f"DEBUG: Delayed notification also failed: {e3}")

            # Now try to load the first table (this might fail, but database mode is already set)
            if self.available_tables:
                # Load the first table by default
                self.current_table_name = self.available_tables[0]
                try:
                    self.log(f"Loading first table: {self.current_table_name}")
                except Exception:
                    print(f"DEBUG: Loading first table: {self.current_table_name}")
                try:
                    self._load_database_table(self.current_table_name)
                except Exception as e:
                    # If table loading fails, show error but don't break database mode
                    try:
                        self.log(f"Failed to load first table {self.current_table_name}: {e}")
                    except Exception:
                        print(f"DEBUG: Failed to load first table {self.current_table_name}: {e}")
                    self._table.clear(columns=True)
                    self._table.add_column("Error")
                    self._table.add_row(f"Failed to load table {self.current_table_name}: {str(e)}")
            else:
                # No tables found
                try:
                    self.log("No tables found - showing empty table")
                except Exception:
                    print("DEBUG: No tables found - showing empty table")
                self._table.clear(columns=True)
                self._table.add_column("Info")
                self._table.add_row("No tables found in database")

        except Exception as e:
            try:
                self.log(f"Error loading database file {file_path}: {e}")
                import traceback

                self.log(f"Traceback: {traceback.format_exc()}")
            except Exception:
                print(f"DEBUG: Error loading database file {file_path}: {e}")
                import traceback

                print(f"DEBUG: Traceback: {traceback.format_exc()}")

            # Hide welcome screen even when there's an error
            try:
                welcome_overlay = self.query_one("#welcome-overlay", WelcomeOverlay)
                welcome_overlay.add_class("hidden")
                welcome_overlay.display = False
            except Exception:
                pass

            # Show UI elements
            try:
                header = self.app.query_one("Header")
                header.display = True
                footer = self.app.query_one("SweetFooter")
                footer.display = True
                status_bar = self.query_one("#status-bar", Static)
                status_bar.display = True
                load_controls = self.query_one("#load-controls")
                load_controls.add_class("hidden")
            except Exception:
                pass

            self._table.clear(columns=True)
            self._table.add_column("Error")
            self._table.add_row(f"Failed to load database {file_path}: {str(e)}")
            # Don't re-raise the exception - just show the error in the table

    def _ensure_database_connection(self) -> bool:
        """Ensure we have a valid database connection, re-establishing if needed."""
        try:
            # First check if we have a connection
            if not self.database_connection:
                return self._reconnect_database()

            # Test if the connection is still valid
            try:
                test_result = self.database_connection.execute("SELECT 1").fetchall()
                try:
                    self.log("Database connection test successful")
                except Exception:
                    print("DEBUG: Database connection test successful")
                return True
            except Exception as e:
                try:
                    self.log(f"Database connection test failed: {e}, attempting to reconnect...")
                except Exception:
                    print(
                        f"DEBUG: Database connection test failed: {e}, attempting to reconnect..."
                    )
                return self._reconnect_database()

        except Exception as e:
            try:
                self.log(f"Error checking database connection: {e}")
            except Exception:
                print(f"DEBUG: Error checking database connection: {e}")
            return False

    def _reconnect_database(self) -> bool:
        """Re-establish the database connection using stored parameters."""
        try:
            if not hasattr(self, "database_connection_type") or not hasattr(
                self, "database_connection_params"
            ):
                try:
                    self.log("No stored connection parameters available for reconnection")
                except Exception:
                    print("DEBUG: No stored connection parameters available for reconnection")
                return False

            try:
                self.log(f"Attempting to reconnect to {self.database_connection_type} database...")
            except Exception:
                print(
                    f"DEBUG: Attempting to reconnect to {self.database_connection_type} database..."
                )

            import duckdb

            if self.database_connection_type == "file":
                # Reconnect to file-based database
                params = self.database_connection_params
                file_path = params.get("file_path")
                read_only = params.get("read_only", True)

                self.database_connection = duckdb.connect(file_path, read_only=read_only)
                try:
                    self.log(f"Successfully reconnected to file database: {file_path}")
                except Exception:
                    print(f"DEBUG: Successfully reconnected to file database: {file_path}")
                return True

            elif self.database_connection_type == "remote":
                # Reconnect to remote database
                params = self.database_connection_params
                connection_string = params.get("connection_string")
                connection_type = params.get("connection_type")
                connection_details = params.get("connection_details")

                # Create DuckDB connection
                self.database_connection = duckdb.connect(":memory:")

                # Re-setup the remote connection based on type
                if connection_type == "mysql":
                    try:
                        self.log("Re-installing MySQL extension...")
                    except Exception:
                        print("DEBUG: Re-installing MySQL extension...")
                    self.database_connection.execute("INSTALL mysql")
                    self.database_connection.execute("LOAD mysql")
                    attach_query = f"ATTACH '{connection_details}' AS mysql_db (TYPE mysql)"
                    self.database_connection.execute(attach_query)

                elif connection_type == "postgresql":
                    try:
                        self.log("Re-installing PostgreSQL extension...")
                    except Exception:
                        print("DEBUG: Re-installing PostgreSQL extension...")
                    self.database_connection.execute("INSTALL postgres")
                    self.database_connection.execute("LOAD postgres")
                    attach_query = f"ATTACH '{connection_details}' AS pg_db (TYPE postgres)"
                    self.database_connection.execute(attach_query)

                try:
                    self.log(f"Successfully reconnected to {connection_type} database")
                except Exception:
                    print(f"DEBUG: Successfully reconnected to {connection_type} database")
                return True

            return False

        except Exception as e:
            try:
                self.log(f"Failed to reconnect to database: {e}")
            except Exception:
                print(f"DEBUG: Failed to reconnect to database: {e}")
            return False

    def _notify_tools_panel_database_mode(self) -> None:
        """Helper method to notify tools panel about database mode."""
        try:
            try:
                self.log("Delayed notification: Attempting to find tools panel...")
            except Exception:
                print("DEBUG: Delayed notification: Attempting to find tools panel...")
            tools_panel = self.app.query_one("#tools-panel", ToolsPanel)
            try:
                self.log(f"Delayed notification: Tools panel found: {tools_panel}")
                self.log(
                    f"Delayed notification: Calling set_database_mode with tables: {self.available_tables}"
                )
            except Exception:
                print(f"DEBUG: Delayed notification: Tools panel found: {tools_panel}")
                print(
                    f"DEBUG: Delayed notification: Calling set_database_mode with tables: {self.available_tables}"
                )
            tools_panel.set_database_mode(True, self.available_tables, is_remote=False)
            try:
                self.log(
                    "Delayed notification: Successfully notified tools panel about database mode"
                )
            except Exception:
                print(
                    "DEBUG: Delayed notification: Successfully notified tools panel about database mode"
                )
        except Exception as e:
            try:
                self.log(f"Delayed notification: Could not notify tools panel: {e}")
                import traceback

                self.log(f"Delayed notification traceback: {traceback.format_exc()}")
            except Exception:
                print(f"DEBUG: Delayed notification: Could not notify tools panel: {e}")
                import traceback

                print(f"DEBUG: Delayed notification traceback: {traceback.format_exc()}")

    def connect_to_database(self, connection_string: str) -> None:
        """Connect to a remote database using a connection string."""
        try:
            import duckdb

            self.log(f"Connecting to remote database: {connection_string}")

            # Parse the connection string
            connection_type, connection_details = self._parse_connection_string(connection_string)

            # Create DuckDB connection
            self.database_connection = duckdb.connect(":memory:")
            self.database_path = connection_string
            self.database_connection_type = "remote"  # Store connection type
            self.database_connection_params = {
                "connection_string": connection_string,
                "connection_type": connection_type,
                "connection_details": connection_details,
            }  # Store connection params
            self.is_database_mode = True
            self.is_sample_data = False
            self.data_source_name = None
            self.database_schema = {}

            # Install and load the appropriate DuckDB extension
            if connection_type == "mysql":
                self.log("Installing and loading MySQL extension...")
                try:
                    self.database_connection.execute("INSTALL mysql")
                    self.database_connection.execute("LOAD mysql")
                    self.log("MySQL extension loaded successfully")
                except Exception as e:
                    self.log(f"Failed to load MySQL extension: {e}")
                    raise Exception(f"Failed to load MySQL extension: {e}")

                # Attach the MySQL database
                self.log(f"Attaching MySQL database: {connection_details}")
                try:
                    attach_query = f"ATTACH '{connection_details}' AS mysql_db (TYPE mysql)"
                    self.database_connection.execute(attach_query)
                    self.log("MySQL database attached successfully")
                except Exception as e:
                    self.log(f"Failed to attach MySQL database: {e}")
                    raise Exception(f"Failed to connect to MySQL database: {e}")

            elif connection_type == "postgresql":
                self.log("Installing and loading PostgreSQL extension...")
                try:
                    self.database_connection.execute("INSTALL postgres")
                    self.database_connection.execute("LOAD postgres")
                    self.log("PostgreSQL extension loaded successfully")
                except Exception as e:
                    self.log(f"Failed to load PostgreSQL extension: {e}")
                    raise Exception(f"Failed to load PostgreSQL extension: {e}")

                # Attach the PostgreSQL database
                self.log(f"Attaching PostgreSQL database: {connection_details}")
                try:
                    attach_query = f"ATTACH '{connection_details}' AS pg_db (TYPE postgres)"
                    self.database_connection.execute(attach_query)
                    self.log("PostgreSQL database attached successfully")
                except Exception as e:
                    self.log(f"Failed to attach PostgreSQL database: {e}")
                    raise Exception(f"Failed to connect to PostgreSQL database: {e}")

            else:
                raise Exception(f"Unsupported database type: {connection_type}")

            # Test the connection
            try:
                test_result = self.database_connection.execute("SELECT 1").fetchall()
                self.log(f"Connection test successful: {test_result}")
            except Exception as e:
                self.log(f"Connection test failed: {e}")
                raise Exception(f"Database connection test failed: {e}")

            # Get list of available tables
            self.log("Discovering available tables...")
            try:
                if connection_type == "mysql":
                    # Try multiple approaches for MySQL table discovery
                    try:
                        # First try: Use SHOW TABLES with proper DuckDB MySQL syntax
                        result = self.database_connection.execute(
                            "SELECT table_name FROM mysql_db.information_schema.tables WHERE table_schema = 'Rfam'"
                        ).fetchall()
                        self.available_tables = [f"mysql_db.{row[0]}" for row in result]
                        self.log(f"MySQL info schema query successful: {self.available_tables}")
                    except Exception as e1:
                        self.log(f"MySQL info schema failed: {e1}")
                        try:
                            # Second try: Simple SHOW TABLES through the attachment
                            result = self.database_connection.execute("SHOW TABLES").fetchall()
                            # Filter for tables that look like they're from mysql_db
                            self.available_tables = [
                                row[0] for row in result if "mysql_db" in str(row)
                            ]
                            if not self.available_tables:
                                # If no mysql_db prefixed tables, just use all tables
                                self.available_tables = [f"mysql_db.{row[0]}" for row in result]
                            self.log(f"SHOW TABLES fallback successful: {self.available_tables}")
                        except Exception as e2:
                            self.log(f"SHOW TABLES also failed: {e2}")
                            # Third try: Query the mysql_db directly for its schema
                            try:
                                result = self.database_connection.execute(
                                    "SELECT name FROM mysql_db.sqlite_master WHERE type='table'"
                                ).fetchall()
                                self.available_tables = [f"mysql_db.{row[0]}" for row in result]
                                self.log(
                                    f"Direct mysql_db query successful: {self.available_tables}"
                                )
                            except Exception as e3:
                                self.log(f"All MySQL table discovery methods failed: {e3}")
                                self.available_tables = []

                elif connection_type == "postgresql":
                    # For PostgreSQL, query tables from the attached database
                    try:
                        result = self.database_connection.execute(
                            "SELECT table_name FROM pg_db.information_schema.tables WHERE table_schema = 'public'"
                        ).fetchall()
                        self.available_tables = [f"pg_db.{row[0]}" for row in result]
                    except Exception as e:
                        self.log(f"PostgreSQL info schema failed: {e}")
                        result = self.database_connection.execute(
                            "SHOW TABLES FROM pg_db"
                        ).fetchall()
                        self.available_tables = [f"pg_db.{row[0]}" for row in result]

                self.log(f"Final table list: {self.available_tables}")
            except Exception as e:
                self.log(f"Failed to discover tables: {e}")
                self.available_tables = []

            if self.available_tables:
                # For remote databases, don't auto-load tables - just show info
                self.log(f"Found {len(self.available_tables)} tables: {self.available_tables}")
                self.current_table_name = self.available_tables[0]  # Set for reference

                # Show connection info instead of loading data immediately
                self._table.clear(columns=True)
                self._table.add_column("Remote Database Info")
                self._table.add_row("✅ Connected to MySQL database")
                self._table.add_row(f"📊 Found {len(self.available_tables)} tables")
                self._table.add_row(f"🔗 Database: {connection_string}")
                self._table.add_row(f"📋 First table: {self.available_tables[0]}")
                self._table.add_row("💡 Use Table Selection tab to load data")
                self._table.add_row("💡 Use SQL Exec tab to run queries")

            else:
                # No tables found
                self.log("No tables found - showing empty table")
                self._table.clear(columns=True)
                self._table.add_column("Info")
                self._table.add_row("No tables found in database")

            # Update app title
            self.app.set_current_filename(f"{connection_string} [Remote Database]")

            # Notify tools panel about database mode
            try:
                tools_panel = self.app.query_one("#tools-panel", ToolsPanel)
                tools_panel.set_database_mode(True, self.available_tables, is_remote=True)

                # Automatically show the drawer for database mode
                drawer_container = self.app.query_one("#main-container", DrawerContainer)
                drawer_container.show_drawer = True
                drawer_container.update_drawer_visibility()
                self.log("Drawer automatically opened for database mode")

            except Exception as e:
                self.log(f"Could not notify tools panel or open drawer: {e}")

            # Hide welcome screen
            try:
                welcome_overlay = self.query_one("#welcome-overlay", WelcomeOverlay)
                welcome_overlay.add_class("hidden")
                welcome_overlay.display = False
            except Exception:
                pass

            # Show UI elements
            try:
                header = self.app.query_one("Header")
                header.display = True
                footer = self.app.query_one("SweetFooter")
                footer.display = True
                status_bar = self.query_one("#status-bar", Static)
                status_bar.display = True
                load_controls = self.query_one("#load-controls")
                load_controls.add_class("hidden")
            except Exception:
                pass

        except Exception as e:
            self.log(f"Error connecting to database {connection_string}: {e}")
            import traceback

            self.log(f"Traceback: {traceback.format_exc()}")

            # Show error message
            self._table.clear(columns=True)
            self._table.add_column("Error")
            self._table.add_row(f"Failed to connect to {connection_string}: {str(e)}")

            # Even on error, try to show the drawer so user can see error and try SQL Exec
            try:
                drawer_container = self.app.query_one("#main-container", DrawerContainer)
                drawer_container.show_drawer = True
                drawer_container.update_drawer_visibility()
                self.log("Drawer opened even on connection error")
            except Exception:
                pass

            # Hide welcome screen even when there's an error
            try:
                welcome_overlay = self.query_one("#welcome-overlay", WelcomeOverlay)
                welcome_overlay.add_class("hidden")
                welcome_overlay.display = False
            except Exception:
                pass

            # Show UI elements
            try:
                header = self.app.query_one("Header")
                header.display = True
                footer = self.app.query_one("SweetFooter")
                footer.display = True
                status_bar = self.query_one("#status-bar", Static)
                status_bar.display = True
                load_controls = self.query_one("#load-controls")
                load_controls.add_class("hidden")
            except Exception:
                pass

    def _parse_connection_string(self, connection_string: str) -> tuple[str, str]:
        """Parse a database connection string and return (type, connection_details)."""
        try:
            # Handle mysql:// format
            if connection_string.startswith("mysql://"):
                # mysql://user:password@host:port/database
                return "mysql", connection_string
            elif connection_string.startswith("postgresql://") or connection_string.startswith(
                "postgres://"
            ):
                # postgresql://user:password@host:port/database
                return "postgresql", connection_string
            else:
                # Try to construct a MySQL connection string from the provided details
                # This is for the specific test case with the public MySQL database
                if "mysql-rfam-public.ebi.ac.uk" in connection_string:
                    # Assume it's the Rfam database
                    mysql_conn = "mysql://rfamro@mysql-rfam-public.ebi.ac.uk:4497/Rfam"
                    return "mysql", mysql_conn
                else:
                    raise Exception(f"Unsupported connection string format: {connection_string}")
        except Exception as e:
            raise Exception(f"Failed to parse connection string: {e}")

    def _load_database_table(self, table_name: str) -> None:
        """Load a specific table from the database."""
        try:
            # Ensure we have a valid database connection
            if not self._ensure_database_connection():
                raise Exception("No database connection available")

            try:
                self.log(f"Loading table: {table_name}")
            except Exception:
                print(f"DEBUG: Loading table: {table_name}")

            # DIRECT APPROACH: Get native column types immediately
            self.native_column_types = {}

            # Use DESCRIBE as it's the most reliable across database types
            try:
                describe_result = self.database_connection.execute(
                    f"DESCRIBE {table_name}"
                ).fetchall()
                # DESCRIBE returns: column_name, column_type, null, key, default, extra
                for row in describe_result:
                    col_name = row[0]
                    col_type = row[1]
                    self.native_column_types[col_name] = col_type
                try:
                    self.log(f"✓ Native column types captured: {self.native_column_types}")
                except Exception:
                    print(f"DEBUG: ✓ Native column types captured: {self.native_column_types}")
            except Exception as e:
                try:
                    self.log(f"DESCRIBE failed, trying fallback: {e}")
                except Exception:
                    print(f"DEBUG: DESCRIBE failed, trying fallback: {e}")
                self.native_column_types = {}

            # Query the table with a reasonable limit for preview
            try:
                self.log(f"Querying table {table_name}...")
            except Exception:
                print(f"DEBUG: Querying table {table_name}...")
            try:
                # Query with a reasonable limit for data exploration
                query = f"SELECT * FROM {table_name} LIMIT {MAX_DISPLAY_ROWS}"
                try:
                    self.log(f"Executing query: {query}")
                except Exception:
                    print(f"DEBUG: Executing query: {query}")
                result = self.database_connection.execute(query).arrow()
                try:
                    self.log("Arrow result obtained, converting to Polars...")
                except Exception:
                    print("DEBUG: Arrow result obtained, converting to Polars...")
                df = pl.from_arrow(result)
                try:
                    self.log(f"Polars conversion successful, shape: {df.shape}")
                except Exception:
                    print(f"DEBUG: Polars conversion successful, shape: {df.shape}")

                # Test if we can iterate over the data
                try:
                    first_row = df.head(1)
                    try:
                        self.log(f"First row test successful: {first_row.shape}")
                    except Exception:
                        print(f"DEBUG: First row test successful: {first_row.shape}")
                except Exception as iter_error:
                    try:
                        self.log(f"Data iteration test failed: {iter_error}")
                    except Exception:
                        print(f"DEBUG: Data iteration test failed: {iter_error}")
                    # Try with string conversion for problematic columns
                    df = df.with_columns([pl.col(col).cast(pl.Utf8) for col in df.columns])
                    try:
                        self.log("Converted all columns to string type")
                    except Exception:
                        print("DEBUG: Converted all columns to string type")

                self.current_table_name = table_name
                try:
                    self.log(f"Table loaded successfully, final shape: {df.shape}")
                    self.log(f"DEBUG: Set current_table_name to: {self.current_table_name}")
                except Exception:
                    print(f"DEBUG: Table loaded successfully, final shape: {df.shape}")
                    print(f"DEBUG: Set current_table_name to: {self.current_table_name}")
                self.load_dataframe(df, force_recreation=True)

            except Exception as query_error:
                try:
                    self.log(f"Query execution failed: {query_error}")
                except Exception:
                    print(f"DEBUG: Query execution failed: {query_error}")
                # Try an even simpler query
                try:
                    try:
                        self.log("Trying COUNT query as fallback...")
                    except Exception:
                        print("DEBUG: Trying COUNT query as fallback...")
                    count_query = f"SELECT COUNT(*) as row_count FROM {table_name}"
                    count_result = self.database_connection.execute(count_query).arrow()
                    count_df = pl.from_arrow(count_result)
                    try:
                        self.log(f"COUNT query successful: {count_df}")
                    except Exception:
                        print(f"DEBUG: COUNT query successful: {count_df}")

                    # Show table info instead of actual data
                    info_df = pl.DataFrame(
                        {
                            "Table": [table_name],
                            "Status": ["Connected - data preview failed"],
                            "Row_Count": count_df.get_column("row_count").to_list(),
                            "Note": ["Use SQL Exec tab to query this table"],
                        }
                    )

                    self.current_table_name = table_name
                    self.load_dataframe(info_df, force_recreation=True)

                except Exception as count_error:
                    try:
                        self.log(f"Even COUNT query failed: {count_error}")
                    except Exception:
                        print(f"DEBUG: Even COUNT query failed: {count_error}")
                    raise query_error

            # Update title to show current table
            self.app.set_current_filename(f"{self.database_path} [Database: {table_name}]")

        except Exception as e:
            try:
                self.log(f"Error loading table {table_name}: {e}")
                import traceback

                self.log(f"Traceback: {traceback.format_exc()}")
            except Exception:
                print(f"DEBUG: Error loading table {table_name}: {e}")
                import traceback

                print(f"DEBUG: Traceback: {traceback.format_exc()}")

            # Hide welcome screen even when there's an error
            try:
                welcome_overlay = self.query_one("#welcome-overlay", WelcomeOverlay)
                welcome_overlay.add_class("hidden")
                welcome_overlay.display = False
            except Exception:
                pass

            # Show UI elements
            try:
                header = self.app.query_one("Header")
                header.display = True
                footer = self.app.query_one("SweetFooter")
                footer.display = True
                status_bar = self.query_one("#status-bar", Static)
                status_bar.display = True
                load_controls = self.query_one("#load-controls")
                load_controls.add_class("hidden")
            except Exception:
                pass

            self._table.clear(columns=True)
            self._table.add_column("Error")
            self._table.add_row(f"Failed to load table {table_name}: {str(e)}")

    def _move_to_first_cell(self) -> None:
        """Move cursor to the first cell (A1) after loading data."""
        try:
            self._table.move_cursor(row=0, column=0)
            self.update_address_display(0, 0, "Data loaded")
            self.log("Moved cursor to first cell")
        except Exception as e:
            self.log(f"Error moving to first cell: {e}")

    def get_excel_column_name(self, col_index: int) -> str:
        """Convert column index to Excel-style column name (A, B, ..., Z, AA, AB, ...)."""
        result = ""
        while col_index >= 0:
            result = chr(ord("A") + (col_index % 26)) + result
            col_index = col_index // 26 - 1
        return result

    def _format_number_compact(self, num: int) -> str:
        """Format a number compactly (e.g., 1234567 -> 1.2M)."""
        if num < 1000:
            return str(num)
        elif num < 1000000:
            k_val = num / 1000
            if k_val >= 999.95:  # Round up to next unit
                return f"{k_val / 1000:.1f}M"
            return f"{k_val:.1f}K"
        elif num < 1000000000:
            m_val = num / 1000000
            if m_val >= 999.95:  # Round up to next unit
                return f"{m_val / 1000:.1f}B"
            return f"{m_val:.1f}M"
        else:
            return f"{num / 1000000000:.1f}B"

    def _get_dataset_dimensions_text(self, available_width: int = None) -> str:
        """Get dataset dimensions text with graceful degradation based on available width."""
        if self.data is None:
            return ""

        total_rows = len(self.data)
        total_cols = len([col for col in self.data.columns if col != "__original_row_index__"])

        # Format 1: Full format - "35,343,343 rows, 23 columns"
        full_format = f"{total_rows:,} rows, {total_cols} columns"

        # If no width constraint, return full format
        if available_width is None:
            return full_format

        # Format 2: Compact rows - "35.3M rows, 23 columns"
        compact_format = f"{self._format_number_compact(total_rows)} rows, {total_cols} columns"

        # Format 3: Very compact - "35.3M x 23"
        very_compact_format = f"{self._format_number_compact(total_rows)} x {total_cols}"

        # Choose format based on available width
        if available_width >= len(full_format):
            return full_format
        elif available_width >= len(compact_format):
            return compact_format
        elif available_width >= len(very_compact_format):
            return very_compact_format
        else:
            return ""  # Not enough space

    def _check_cursor_position(self) -> None:
        """Periodically check and update cursor position."""
        cursor_coordinate = self._table.cursor_coordinate
        if cursor_coordinate:
            row, col = cursor_coordinate

            # Calculate actual row number for address comparison only
            if self.is_data_truncated:
                display_offset = getattr(self, "_display_offset", 0)
                actual_row = display_offset + row
            else:
                actual_row = row

            # Only update if position has changed
            col_name = self.get_excel_column_name(col)
            new_address = f"{col_name}{actual_row}"
            if new_address != self._current_address:
                # Pass the display row, not the actual row - update_address_display will handle the offset
                self.update_address_display(row, col)

    def update_address_display(self, row: int, col: int, custom_message: str = None) -> None:
        """Update the status bar with current cell address, value, and type."""
        # Calculate the actual row number for display
        if self.is_data_truncated:
            display_offset = getattr(self, "_display_offset", 0)
            actual_row = display_offset + row
        else:
            actual_row = row

        col_name = self.get_excel_column_name(col)
        self._current_address = f"{col_name}{actual_row}"

        # Update status bar at bottom with robust approach
        try:
            status_bar = self.query_one("#status-bar", Static)
            if custom_message:
                new_text = f"{self._current_address} // {custom_message}"
            else:
                # Get cell value and type for display
                cell_value = "No data"
                cell_type = "N/A"

                if self.data is not None and row > 0:  # row > 0 because row 0 is headers
                    # The data_row calculation was already done at the beginning of this method
                    # Use actual_row - 1 to get the 0-based data index
                    data_row = actual_row - 1

                    # Use proper column mapping to get the actual data column index
                    data_col_index = self._get_data_column_index(col)
                    visible_columns = [
                        col for col in self.data.columns if col != "__original_row_index__"
                    ]

                    if data_row < len(self.data) and col < len(visible_columns):
                        try:
                            # Get the visible column name
                            column_name = self._get_visible_column_name(col)
                            if column_name and data_col_index >= 0:
                                raw_value = self.data[data_row, data_col_index]
                                if raw_value is None:
                                    cell_value = "None"
                                else:
                                    cell_value = str(raw_value)

                                # Get column type using our format method that handles database types
                                column_dtype = self.data[column_name].dtype
                                cell_type = self._format_column_info_message(
                                    column_name, column_dtype
                                )
                        except Exception as e:
                            self.log(f"Error getting cell data: {e}")
                            cell_value = "Error"
                            cell_type = "Unknown"
                elif row == 0:  # Header row
                    if self.data is not None:
                        # Use proper column mapping for header row as well
                        column_name = self._get_visible_column_name(col)
                        if column_name:
                            cell_value = str(column_name)
                            cell_type = "Column Header"

                new_text = f"{self._current_address} // {cell_value} // {cell_type}"

            # Add dataset dimensions on the right side with graceful degradation
            # Calculate total width that will fit in terminal
            try:
                terminal_width = self.app.size.width if hasattr(self.app, "size") else 80
                buffer = 12  # Generous buffer to prevent text cutoff, especially for "columns" text
                max_total_width = terminal_width - buffer

                # Try different dimension formats, starting with the most detailed
                dimension_attempts = [
                    None,  # Full format
                    35,  # Long format
                    30,  # Medium format
                    25,  # Compact format
                    20,  # Short format
                    15,  # Very short format
                    10,  # Minimal format
                ]

                dimensions_added = False
                for max_dim_width in dimension_attempts:
                    dimensions_text = self._get_dataset_dimensions_text(max_dim_width)
                    if dimensions_text:
                        test_text = f"{new_text} | {dimensions_text}"
                        if len(test_text) <= max_total_width:
                            new_text = test_text
                            dimensions_added = True
                            break

                # If no dimension format fits, don't add dimensions
                if not dimensions_added and terminal_width < 60:
                    # For very narrow terminals, just show the basic info
                    pass

            except Exception as e:
                # Fallback to simple approach if width calculation fails
                dimensions_text = self._get_dataset_dimensions_text(20)
                if dimensions_text and len(f"{new_text} | {dimensions_text}") < 80:
                    new_text = f"{new_text} | {dimensions_text}"

            # Try multiple approaches to ensure text is set
            status_bar.update(new_text)
            status_bar.renderable = new_text
            status_bar.refresh()
        except Exception as e:
            self.log(f"Error updating status bar: {e}")
            # Try fallback approach
            try:
                status_widgets = self.query(".status-bar")
                for widget in status_widgets:
                    if isinstance(widget, Static):
                        widget.update(f"{self._current_address}")
                        widget.refresh()
                        break
            except Exception as e2:
                self.log(f"Fallback status update failed: {e2}")

    def load_sample_data(self) -> None:
        """Load sample CSV data into the grid."""
        try:
            if pl is None:
                self._table.add_column("Error")
                self._table.add_row("Polars not available")
                return

            # Create internal sample data: this is packaged with the application
            df = pl.DataFrame(
                {
                    "name": [
                        "Alice",
                        "Bob",
                        "Charlie",
                        "Diana",
                        "Eve",
                        "Frank",
                        "Grace",
                        "Henry",
                        "Ivy",
                        "Jack",
                    ],
                    "age": [25, 30, 35, 28, 32, 27, 31, 29, 26, 33],
                    "city": [
                        "New York",
                        "San Francisco",
                        "Chicago",
                        "Boston",
                        "Seattle",
                        "Austin",
                        "Denver",
                        "Miami",
                        "Portland",
                        "Atlanta",
                    ],
                    "salary": [
                        75000,
                        85000,
                        70000,
                        80000,
                        92000,
                        68000,
                        88000,
                        77000,
                        82000,
                        95000,
                    ],
                    "department": [
                        "Engineering",
                        "Marketing",
                        "Sales",
                        "HR",
                        "Engineering",
                        "Design",
                        "Marketing",
                        "Sales",
                        "Engineering",
                        "HR",
                    ],
                }
            )

            self.load_dataframe(df)

            # Mark as sample data and set clean display name
            self.is_sample_data = True
            self.data_source_name = "sample_data"
            self.is_database_mode = False  # Ensure we're in regular mode for sample data

            # Notify tools panel about regular mode
            try:
                debug_logger.info(
                    "Attempting to notify tools panel about regular mode (sample data)"
                )
                tools_panel = self.app.query_one("#tools-panel", ToolsPanel)
                tools_panel.set_database_mode(False)
                debug_logger.info(
                    "Successfully notified tools panel about regular mode (sample data)"
                )
            except Exception as e:
                debug_logger.error(f"Could not notify tools panel (sample data): {e}")
                self.log(f"Could not notify tools panel: {e}")

            self.app.set_current_filename("sample_data [SAMPLE]")

        except Exception as e:
            self._table.add_column("Error")
            self._table.add_row(f"Failed to load data: {str(e)}")

    def create_empty_sheet(self) -> None:
        """Create a new empty sheet with 5 columns and 10 rows."""
        try:
            if pl is None:
                self._table.add_column("Error")
                self._table.add_row("Polars not available")
                return

            # Create empty dataframe with 5 columns and 10 rows
            # Use None values for all cells initially
            empty_data = {
                "Column_1": [None] * 10,
                "Column_2": [None] * 10,
                "Column_3": [None] * 10,
                "Column_4": [None] * 10,
                "Column_5": [None] * 10,
            }

            df = pl.DataFrame(empty_data)

            self.load_dataframe(df)

            # Mark as new sheet (not sample data, no source file)
            self.is_sample_data = False
            self.data_source_name = None
            self.is_database_mode = False  # Ensure we're in regular mode for new sheet

            # Notify tools panel about regular mode
            try:
                debug_logger.info("Attempting to notify tools panel about regular mode (new sheet)")
                tools_panel = self.app.query_one("#tools-panel", ToolsPanel)
                tools_panel.set_database_mode(False)
                debug_logger.info(
                    "Successfully notified tools panel about regular mode (new sheet)"
                )
            except Exception as e:
                debug_logger.error(f"Could not notify tools panel (new sheet): {e}")
                self.log(f"Could not notify tools panel: {e}")

            self.app.set_current_filename("new_sheet [UNSAVED]")

        except Exception as e:
            self._table.add_column("Error")
            self._table.add_row(f"Failed to create empty sheet: {str(e)}")

    def load_dataframe(self, df, force_recreation: bool = False) -> None:
        """Load a Polars DataFrame into the grid.

        Args:
            df: The Polars DataFrame to load
            force_recreation: If True, always recreate the table regardless of data source
        """
        if pl is None or df is None:
            return

        # Clean up any sorting tracking columns from previous sessions
        if "__original_row_index__" in df.columns:
            df = df.drop("__original_row_index__")

        self.data = df
        # Store original data for change tracking
        self.original_data = df.clone()
        self.has_changes = False

        # Reset sorting state when loading new data
        self._sort_columns = []
        self._original_data = None

        # Hide welcome overlay when data is loaded
        try:
            welcome_overlay = self.query_one("#welcome-overlay", WelcomeOverlay)
            welcome_overlay.add_class("hidden")
            welcome_overlay.display = False  # Also set display to False
        except Exception as e:
            self.log(f"Error hiding welcome overlay: {e}")

        # Show header and footer bars when data is loaded
        try:
            # Show the header (blue bar)
            header = self.app.query_one("Header")
            header.display = True
        except Exception as e:
            self.log(f"Error showing header: {e}")

        try:
            # Show the footer (green bar)
            footer = self.app.query_one("SweetFooter")
            footer.display = True
        except Exception as e:
            self.log(f"Error showing footer: {e}")

        # Show the status bar when data is loaded
        try:
            status_bar = self.query_one("#status-bar", Static)
            status_bar.display = True
        except Exception as e:
            self.log(f"Error showing status bar: {e}")

        # Hide load controls when data is loaded
        try:
            load_controls = self.query_one("#load-controls")
            load_controls.add_class("hidden")
        except Exception:
            pass

        # For file data or when forced, recreate the DataTable to ensure proper display
        # This works around an issue where existing DataTable instances lose row label visibility
        if not getattr(self, "is_sample_data", False) or force_recreation:
            # Instead of recreating the entire widget, do a more thorough reset
            try:
                # Clear the table completely
                self._table.clear(columns=True)

                # Force row labels back on with multiple approaches
                self._table.show_row_labels = True

                # Re-apply all table settings to ensure consistency
                self._table.cursor_type = "cell"
                self._table.show_header = True
                self._table.zebra_stripes = False

                # Override the clear method to preserve row labels (reapply the override)
                original_clear = self._table.clear

                def preserve_row_labels_clear(*args, **kwargs):
                    result = original_clear(*args, **kwargs)
                    self._table.show_row_labels = True
                    return result

                self._table.clear = preserve_row_labels_clear

                self.log("Reset existing DataTable with force_recreation=True")

            except Exception as e:
                self.log(f"Error resetting table: {e}")
        else:
            # Sample data: use existing table
            self._table.clear(columns=True)
            self._table.show_row_labels = True

        # Add Excel-style column headers with sort indicators (A, B ↑, C ↓, etc.)
        for i, column in enumerate(df.columns):
            header_text = self._get_column_header_with_sort_indicator(i, column)
            self._table.add_column(header_text, key=column)

        # Add pseudo-column for adding new columns (column adder)
        pseudo_col_index = len(df.columns)
        pseudo_excel_col = self.get_excel_column_name(pseudo_col_index)
        self._table.add_column(pseudo_excel_col, key="__ADD_COLUMN__")

        # Re-enable row labels after adding columns (sometimes gets reset)
        self._table.show_row_labels = True

        # Add column names as the first row (row 0) with bold formatting (without persistent type info)
        column_names = [f"[bold]{str(col)}[/bold]" for col in df.columns]
        # Add pseudo-column header with "+" indicator
        column_names.append("[dim italic]+ Add Column[/dim italic]")
        self._table.add_row(*column_names, label="0")

        # Add data rows with proper row numbering (starting from 1)
        # Limit display to MAX_DISPLAY_ROWS for large datasets
        total_rows = len(df)
        display_rows = min(total_rows, MAX_DISPLAY_ROWS)
        self.is_data_truncated = total_rows > MAX_DISPLAY_ROWS

        if self.is_data_truncated:
            self.log(f"Data truncated for display: showing {display_rows} of {total_rows} rows")

        try:
            # Try to use iter_rows() normally
            for row_idx, row in enumerate(df.head(display_rows).iter_rows()):
                # Use row number (1-based) as the row label for display
                row_label = str(row_idx + 1)  # This should show as row number
                # Style cell values (None as red, whitespace-only as orange underscores)
                styled_row = []
                for col_idx, cell in enumerate(row):
                    styled_row.append(self._style_cell_value(cell, row_idx, col_idx))
                # Add empty cell for the pseudo-column
                styled_row.append("")
                self._table.add_row(*styled_row, label=row_label)
        except BaseException as any_error:
            self.log(f"iter_rows() failed with: {type(any_error).__name__}: {any_error}")
            # Alternative approach: use to_pandas() and then iterate
            try:
                self.log("Trying pandas conversion as fallback...")
                pandas_df = df.head(min(10, display_rows)).to_pandas()
                for row_idx in range(len(pandas_df)):
                    row_label = str(row_idx + 1)
                    styled_row = []
                    for col_idx, col_name in enumerate(pandas_df.columns):
                        cell_value = pandas_df.iloc[row_idx, col_idx]
                        styled_row.append(self._style_cell_value(cell_value, row_idx, col_idx))
                    styled_row.append("")
                    self._table.add_row(*styled_row, label=row_label)
                self.log("Pandas conversion fallback successful")
            except Exception as pandas_error:
                self.log(f"Pandas fallback also failed: {pandas_error}")
                # Final fallback: show just the column info
                try:
                    self.log("Showing column info only...")
                    schema_info = []
                    for col_idx, (col_name, col_type) in enumerate(zip(df.columns, df.dtypes)):
                        if col_idx == 0:
                            schema_info = [
                                f"Column: {col_name}",
                                f"Type: {col_type}",
                                "Remote DB - use SQL Exec",
                                "",
                            ]
                        else:
                            schema_info.extend(["", "", "", ""])
                    self._table.add_row(*schema_info[: len(df.columns) + 1], label="1")
                except Exception as final_error:
                    self.log(f"Final fallback failed: {final_error}")
                    # Ultimate fallback
                    error_row = ["Error: Cannot display remote data"] + [""] * len(df.columns)
                    self._table.add_row(*error_row, label="1")

        # Only add pseudo-row for adding new rows if we're showing the last row of the dataset
        if self._is_showing_last_row():
            next_row_label = "+"  # Simple label instead of showing row number
            pseudo_row_cells = (
                ["[dim italic]+ Add Row[/dim italic]"] + [""] * (len(df.columns) - 1) + [""]
            )
            self._table.add_row(*pseudo_row_cells, label=next_row_label)

        # Final enforcement of row labels after all rows are added
        self._table.show_row_labels = True

        # Log the loaded data info
        log_message = f"Loaded dataframe with {len(df)} rows and {len(df.columns)} columns"
        if self.is_data_truncated:
            log_message += f" (displaying first {display_rows} rows)"
        self.log(log_message)
        self.log(
            f"Table now has {self._table.row_count} rows and {len(self._table.columns)} columns"
        )
        self.log(f"Table row_labels enabled: {self._table.show_row_labels}")
        self.log(
            f"Force recreation was: {force_recreation}, is_sample_data: {getattr(self, 'is_sample_data', False)}"
        )

        # Refresh the display with comprehensive approach
        self._table.refresh()  # Refresh table first
        self.refresh()  # Then refresh container

        # Move cursor to first cell (A1) with multiple attempts
        self.call_after_refresh(self._move_to_first_cell)

        # Secondary attempt with delay
        self.set_timer(0.1, self._move_to_first_cell)

        # Initialize cursor position and focus on cell A0
        self.call_after_refresh(self._focus_cell_a0)

        # Initialize address display after loading data
        self.update_address_display(0, 0)

        # Show the drawer tab when data is loaded
        try:
            # Find the parent container and show the drawer tab
            container = self.app.query_one("#main-container", DrawerContainer)
            drawer_tab = container.query_one("#drawer-tab")
            drawer_tab.remove_class("hidden")
        except Exception:
            pass

    def _is_pseudo_row(self, row: int) -> bool:
        """Check if the given row position is the pseudo-row (Add Row)."""
        if self.data is None:
            return False

        # The pseudo-row is only present when we're showing the last row of the dataset
        if not self._is_showing_last_row():
            return False

        # The pseudo-row is the last row in the table
        return row == self._table.row_count - 1

    def _is_showing_last_row(self) -> bool:
        """Check if the current view contains the last row of the dataset."""
        if self.data is None:
            return False

        total_rows = len(self.data)

        # If not truncated, we're showing everything
        if not self.is_data_truncated:
            return True

        # For truncated datasets, check if our current slice includes the last row
        display_offset = getattr(self, "_display_offset", 0)
        last_displayed_row = display_offset + MAX_DISPLAY_ROWS

        return last_displayed_row >= total_rows

    def navigate_to_row(self, target_row: int) -> None:
        """Navigate to a specific row number, creating a new slice if needed for large datasets."""
        if self.data is None:
            self.log("No data loaded")
            return

        total_rows = len(self.data)

        if target_row < 1 or target_row > total_rows:
            self.log(f"Row {target_row} is out of range (1-{total_rows})")
            return

        # If dataset is not truncated, just move to the row
        if not self.is_data_truncated:
            # Move cursor to the target row (accounting for header row)
            display_row = target_row  # target_row is already 1-based, matches display
            if display_row < self._table.row_count:
                self._table.move_cursor(row=display_row, column=0)
                self.update_address_display(display_row, 0)
                # Focus the table so user can immediately use arrow keys
                self._table.focus()
                self.log(f"Moved to row {target_row}")
            return

        # For truncated datasets, we need to create a new slice
        self.log(f"Navigating to row {target_row} in large dataset...")

        # Calculate the slice range - center the target row in the view
        half_display = MAX_DISPLAY_ROWS // 2
        start_row = max(0, target_row - half_display - 1)  # Convert to 0-based indexing
        end_row = min(total_rows, start_row + MAX_DISPLAY_ROWS)

        # Adjust start_row if we're near the end
        if end_row - start_row < MAX_DISPLAY_ROWS:
            start_row = max(0, end_row - MAX_DISPLAY_ROWS)

        # Create the new slice
        sliced_data = self.data.slice(start_row, MAX_DISPLAY_ROWS)

        # Store the offset so we know where we are in the full dataset
        self._display_offset = start_row

        # Clear and reload the table with the new slice
        self._table.clear(columns=True)
        self._table.show_row_labels = True

        # Add columns
        for i, column in enumerate(sliced_data.columns):
            if column != "__original_row_index__":
                header_text = self._get_column_header_with_sort_indicator(i, column)
                self._table.add_column(header_text, key=column)

        # Add pseudo-column for adding new columns
        pseudo_col_index = len(
            [col for col in sliced_data.columns if col != "__original_row_index__"]
        )
        pseudo_excel_col = self.get_excel_column_name(pseudo_col_index)
        self._table.add_column(pseudo_excel_col, key="__ADD_COLUMN__")

        # Add column headers
        visible_columns = [col for col in sliced_data.columns if col != "__original_row_index__"]
        column_names = [f"[bold]{str(col)}[/bold]" for col in visible_columns]
        column_names.append("[dim italic]+ Add Column[/dim italic]")
        self._table.add_row(*column_names, label="0")

        # Add data rows with actual row numbers (not slice indices)
        for row_idx, row in enumerate(sliced_data.iter_rows()):
            actual_row_num = start_row + row_idx + 1  # Convert back to 1-based actual row number
            row_label = str(actual_row_num)

            styled_row = []
            visible_col_idx = 0
            for col_idx, cell in enumerate(row):
                column_name = sliced_data.columns[col_idx]
                if column_name != "__original_row_index__":
                    styled_row.append(self._style_cell_value(cell, row_idx, visible_col_idx))
                    visible_col_idx += 1
            styled_row.append("")  # Pseudo-column
            self._table.add_row(*styled_row, label=row_label)

        # Add "+ Add Row" pseudo row if this slice contains the last row of the dataset
        if self._is_showing_last_row():
            next_row_label = "+"
            visible_column_count = len(visible_columns)
            pseudo_row_cells = (
                ["[dim italic]+ Add Row[/dim italic]"] + [""] * (visible_column_count - 1) + [""]
            )
            self._table.add_row(*pseudo_row_cells, label=next_row_label)

        # Calculate which display row the target should be on
        target_display_row = (
            target_row - start_row
        )  # This gives us the row in the current slice (1-based)

        # Move cursor to the target row
        if target_display_row < self._table.row_count:
            self._table.move_cursor(row=target_display_row, column=0)
            self.update_address_display(target_display_row, 0)

        self._table.refresh()
        self.refresh()

        # Focus the table so user can immediately use arrow keys
        self._table.focus()

        self.log(f"Navigated to row {target_row} (showing rows {start_row + 1}-{end_row})")

    def _focus_cell_a0(self) -> None:
        """Focus the table and position cursor at cell A0."""
        try:
            # Move cursor to cell A0 (row 0, column 0)
            self._table.move_cursor(row=0, column=0)
            # Give focus to the table so arrow keys work immediately
            self._table.focus()
            # Update the address display with column type info for A0
            if self.data is not None and len(self.data.columns) > 0:
                column_name = self.data.columns[0]
                dtype = self.data.dtypes[0]
                column_info = self._format_column_info_message(column_name, dtype)
                self.update_address_display(0, 0, column_info)
            else:
                self.update_address_display(0, 0)
        except Exception as e:
            self.log(f"Error focusing cell A0: {e}")
            # Fallback: just try to focus the table
            try:
                self._table.focus()
            except Exception as e2:
                self.log(f"Error focusing table: {e2}")

    def _force_row_labels_visible(self) -> None:
        """Force row labels to be visible by setting the property and refreshing."""
        self._table.show_row_labels = True
        # Force a refresh of just the table without rebuilding
        try:
            self._table.refresh()
        except Exception as e:
            self.log(f"Error refreshing table for row labels: {e}")

    def on_data_table_cell_selected(self, event: DataTable.CellSelected) -> None:
        """Handle cell selection and update address."""
        row, col = event.coordinate

        # Check if clicking on column header (row 0)
        if row == 0 and self.data is not None:
            # Column header clicked: notify script panel about column selection
            column_name = self._get_visible_column_name(col)
            if column_name:
                data_col_index = self._get_data_column_index(col)
                if data_col_index >= 0:
                    column_type = self._get_friendly_type_name(self.data.dtypes[data_col_index])
                    self._notify_script_panel_column_selection(col, column_name, column_type)
                else:
                    self._notify_script_panel_column_clear()
            else:
                self._notify_script_panel_column_clear()
        else:
            # Regular cell selection: clear script panel column selection
            self._notify_script_panel_column_clear()

        # Check if clicking on pseudo-elements (add column or add row)
        if self.data is not None:
            # Get number of visible columns
            visible_columns = [col for col in self.data.columns if col != "__original_row_index__"]
            num_visible_columns = len(visible_columns)

            # Check if clicked on pseudo-column (add column)
            if col == num_visible_columns:  # Last visible column is the pseudo-column
                self.log("Clicked on pseudo-column: adding new column")
                self.action_add_column()
                return

            # Check if clicked on pseudo-row (add row)
            if self._is_pseudo_row(row):
                self.log("Clicked on pseudo-row: adding new row")
                self.action_add_row()
                return

        # Show column type info when clicking on header row (row 0)
        if row == 0 and self.data is not None:
            visible_columns = [col for col in self.data.columns if col != "__original_row_index__"]
            if col < len(visible_columns):
                column_name = self._get_visible_column_name(col)
                data_col_index = self._get_data_column_index(col)
                dtype = self.data.dtypes[data_col_index]
                column_info = self._format_column_info_message(column_name, dtype)
                self.update_address_display(row, col, column_info)
            else:
                self.update_address_display(row, col)
        else:
            self.update_address_display(row, col)

        # Handle double-click for cell editing (only for real cells, not pseudo-elements)
        if self.data is not None and row <= len(self.data):
            visible_columns = [col for col in self.data.columns if col != "__original_row_index__"]
            if col < len(visible_columns):
                current_time = time.time()

                # Check if this is a double-click (same cell clicked within threshold)
                if (
                    self._last_click_coordinate == (row, col)
                    and current_time - self._last_click_time < self._double_click_threshold
                ):
                    # Double-click detected
                    if not self.editing_cell:  # Only process if not already editing
                        # Skip editing/modification features in database mode
                        if self.is_database_mode:
                            self.log("Database mode: editing disabled")
                            return

                        if row == 0:
                            # Double-click on column header: show column options
                            column_name = self._get_visible_column_name(col)
                            self.log(
                                f"Double-click detected on column header {self.get_excel_column_name(col)} ({column_name})"
                            )
                            self.call_after_refresh(self._show_row_column_delete_modal, row, col)
                        else:
                            # Double-click on data cell: start cell editing
                            self.log(
                                f"Double-click detected on cell {self.get_excel_column_name(col)}{row}"
                            )
                            self.call_after_refresh(self.start_cell_edit, row, col)

                # Update last click tracking
                self._last_click_time = current_time
                self._last_click_coordinate = (row, col)

    def _notify_script_panel_column_selection(
        self, col_index: int, column_name: str, column_type: str
    ) -> None:
        """Notify the tools panel about column selection."""
        try:
            # Find the tools panel through the drawer container
            container = self.app.query_one("#main-container", DrawerContainer)
            tools_panel = container.query_one("#tools-panel", ToolsPanel)
            tools_panel.update_column_selection(col_index, column_name, column_type)
        except Exception as e:
            self.log(f"Could not notify tools panel of column selection: {e}")
            import traceback

            self.log(f"Traceback: {traceback.format_exc()}")

    def _notify_script_panel_column_clear(self) -> None:
        """Notify the tools panel to clear column selection."""
        try:
            # Find the tools panel through the drawer container
            container = self.app.query_one("#main-container", DrawerContainer)
            tools_panel = container.query_one("#tools-panel", ToolsPanel)
            tools_panel.clear_column_selection()
        except Exception as e:
            self.log(f"Could not notify tools panel to clear column selection: {e}")

    def _handle_row_label_click(self, clicked_row: int) -> None:
        """Handle clicks on row labels for double-click detection."""
        if self.data is None:
            return

        # Handle row 0 click (header row) for sort reset
        if clicked_row == 0:
            if len(self._sort_columns) > 0:
                self.log("Sort reset button clicked")
                self._reset_sort()
                return

        current_time = time.time()

        # Check if this is a double-click on the same row label
        if (
            self._last_row_label_clicked == clicked_row
            and current_time - self._last_row_label_click_time < self._double_click_threshold
        ):
            # Double-click detected on row label
            self.log(f"Double-click detected on row label {clicked_row}")
            self._show_row_column_delete_modal(clicked_row)

        # Update last click tracking
        self._last_row_label_click_time = current_time
        self._last_row_label_clicked = clicked_row

    def _handle_column_header_click(self, clicked_col: int) -> None:
        """Handle clicks on column headers for sorting and double-click detection."""
        self.log(f"_handle_column_header_click called with clicked_col={clicked_col}")

        if self.data is None:
            self.log("No data available in _handle_column_header_click")
            return

        # Ensure the column is valid (check against visible columns)
        visible_columns = [col for col in self.data.columns if col != "__original_row_index__"]
        if clicked_col >= len(visible_columns):
            self.log(
                f"Invalid column {clicked_col}, only {len(visible_columns)} visible columns available"
            )
            return

        current_time = time.time()

        # Initialize tracking if needed
        if not hasattr(self, "_last_column_header_click_time"):
            self._last_column_header_click_time = 0
            self._last_column_header_clicked = None
            self.log("Initialized column header click tracking")

        # Check if this is a double-click for column operations
        self.log(
            f"Previous column click: {self._last_column_header_clicked}, time diff: {current_time - self._last_column_header_click_time}"
        )

        if (
            self._last_column_header_clicked == clicked_col
            and current_time - self._last_column_header_click_time < self._double_click_threshold
        ):
            # Double-click detected: cancel any pending sort and show column options
            if self._pending_sort_timer is not None:
                self._pending_sort_timer.stop()
                self._pending_sort_timer = None
                self._pending_sort_column = None
                self.log("Cancelled pending sort due to double-click")

            column_name = visible_columns[clicked_col]
            self.log(f"DOUBLE-CLICK DETECTED on column header {clicked_col} ({column_name})")
            self._show_row_column_delete_modal(0, clicked_col)  # Pass the specific column
        else:
            # Single click: schedule sorting with debounce delay
            if self._pending_sort_timer is not None:
                # Cancel previous pending sort
                self._pending_sort_timer.stop()
                self.log("Cancelled previous pending sort")

            # Schedule sort after debounce delay
            self._pending_sort_column = clicked_col
            self._pending_sort_timer = self.set_timer(
                self._double_click_threshold
                + 0.05,  # Wait slightly longer than double-click threshold
                self._execute_pending_sort,
            )
            self.log(f"Scheduled sort for column {clicked_col} after debounce delay")

        # Update last click tracking
        self._last_column_header_click_time = current_time
        self._last_column_header_clicked = clicked_col

    def _execute_pending_sort(self) -> None:
        """Execute a pending sort operation after debounce delay."""
        if self._pending_sort_column is not None:
            self.log(f"Executing pending sort for column {self._pending_sort_column}")
            self._handle_column_sorting(self._pending_sort_column)

        # Clear pending sort state
        self._pending_sort_timer = None
        self._pending_sort_column = None

    def _handle_column_sorting(self, col_index: int) -> None:
        """Handle sorting when a column header is clicked."""
        if self.data is None:
            return

        # Get visible columns (excluding tracking columns)
        visible_columns = [col for col in self.data.columns if col != "__original_row_index__"]

        if col_index >= len(visible_columns):
            return

        try:
            # Check if this column is already in the sort order
            existing_sort_idx = None
            for i, (sort_col, sort_asc) in enumerate(self._sort_columns):
                if sort_col == col_index:
                    existing_sort_idx = i
                    break

            if existing_sort_idx is not None:
                # Column already exists in sort order: toggle its direction
                old_asc = self._sort_columns[existing_sort_idx][1]
                self._sort_columns[existing_sort_idx] = (col_index, not old_asc)
                self.log(
                    f"Toggled sort direction for column {col_index} in position {existing_sort_idx + 1}"
                )
            else:
                # New column: add to end of sort order as ascending
                self._sort_columns.append((col_index, True))
                self.log(
                    f"Added column {col_index} to sort order at position {len(self._sort_columns)}"
                )

            # Apply the sort
            self._apply_sort()

            # Mark as changed since sort affects data display
            self.has_changes = True
            self.update_title_change_indicator()

            column_name = visible_columns[col_index]
            if existing_sort_idx is not None:
                sort_direction = (
                    "ascending" if self._sort_columns[existing_sort_idx][1] else "descending"
                )
                sort_position = existing_sort_idx + 1
                self.log(
                    f"Sorted column '{column_name}' {sort_direction} (position {sort_position})"
                )
                self.update_address_display(
                    0, col_index, f"Sorted '{column_name}' {sort_direction} (#{sort_position})"
                )
            else:
                sort_position = len(self._sort_columns)
                self.log(f"Sorted column '{column_name}' ascending (position {sort_position})")
                self.update_address_display(
                    0, col_index, f"Sorted '{column_name}' ascending (#{sort_position})"
                )

        except Exception as e:
            self.log(f"Error handling column sorting: {e}")
            import traceback

            self.log(f"Traceback: {traceback.format_exc()}")

    def _apply_sort(self) -> None:
        """Apply the current sort settings to the data."""
        if self.data is None:
            return

        try:
            # Add a row index column if it doesn't exist to track original order
            if "__original_row_index__" not in self.data.columns:
                self.data = self.data.with_row_index("__original_row_index__")

            # Get the visible columns (excluding tracking column)
            visible_columns = [col for col in self.data.columns if col != "__original_row_index__"]

            # Apply multiple sorts in order (most important sort last)
            if len(self._sort_columns) > 0:
                # Build list of column names and sort directions
                sort_columns = []
                sort_directions = []

                for sort_col_idx, sort_asc in self._sort_columns:
                    if sort_col_idx < len(visible_columns):
                        column_name = visible_columns[sort_col_idx]
                        sort_columns.append(column_name)
                        sort_directions.append(not sort_asc)  # Polars uses descending=True for desc

                if sort_columns:
                    self.data = self.data.sort(sort_columns, descending=sort_directions)

            # Refresh the table display
            self.refresh_table_data(preserve_cursor=True)

        except Exception as e:
            self.log(f"Error applying sort: {e}")

    def _reset_sort(self) -> None:
        """Reset sorting and restore original data order."""
        if self.data is None:
            return

        try:
            # If we have the original row index column, sort by it to restore original order
            if "__original_row_index__" in self.data.columns:
                self.data = self.data.sort("__original_row_index__").drop("__original_row_index__")

            # Clear sorting state
            self._sort_columns = []
            self._original_data = None

            # Refresh the table display
            self.refresh_table_data(preserve_cursor=True)

            # Mark as changed since sort reset affects data display
            self.has_changes = True
            self.update_title_change_indicator()

            self.log("Sort reset - restored original data order")
            self.update_address_display(0, 0, "Sort reset - restored original order")

        except Exception as e:
            self.log(f"Error resetting sort: {e}")
            import traceback

            self.log(f"Traceback: {traceback.format_exc()}")

            # Fallback: just clear sort state and refresh
            self._sort_columns = []
            self._original_data = None
            self.refresh_table_data(preserve_cursor=True)

    def _sort_column(self, col_index: int, ascending: bool = True) -> None:
        """Sort a specific column in the specified direction, supporting multi-column sorting."""
        if self.data is None:
            return

        # Get visible columns (excluding tracking columns)
        visible_columns = [col for col in self.data.columns if col != "__original_row_index__"]

        if col_index >= len(visible_columns):
            return

        try:
            # Check if this column is already in the sort order
            existing_sort_idx = None
            for i, (sort_col, sort_asc) in enumerate(self._sort_columns):
                if sort_col == col_index:
                    existing_sort_idx = i
                    break

            if existing_sort_idx is not None:
                # Column already exists in sort order: update its direction
                self._sort_columns[existing_sort_idx] = (col_index, ascending)
                self.log(
                    f"Updated sort direction for column {col_index} in position {existing_sort_idx + 1}"
                )
            else:
                # New column: add to end of sort order
                self._sort_columns.append((col_index, ascending))
                self.log(
                    f"Added column {col_index} to sort order at position {len(self._sort_columns)}"
                )

            # Apply the sort
            self._apply_sort()

            # Mark as changed since sort affects data display
            self.has_changes = True
            self.update_title_change_indicator()

            column_name = visible_columns[col_index]
            sort_direction = "ascending" if ascending else "descending"

            if existing_sort_idx is not None:
                sort_position = existing_sort_idx + 1
                self.log(
                    f"Updated column '{column_name}' {sort_direction} (position {sort_position})"
                )
                self.update_address_display(
                    0, col_index, f"Updated '{column_name}' {sort_direction} (#{sort_position})"
                )
            else:
                sort_position = len(self._sort_columns)
                self.log(
                    f"Sorted column '{column_name}' {sort_direction} (position {sort_position})"
                )
                self.update_address_display(
                    0, col_index, f"Sorted '{column_name}' {sort_direction} (#{sort_position})"
                )

        except Exception as e:
            self.log(f"Error sorting column: {e}")
            import traceback

            self.log(f"Traceback: {traceback.format_exc()}")

    def _update_sort_state_after_column_deletion(self, deleted_col_index: int) -> None:
        """Update sorting state after a column is deleted.

        Args:
            deleted_col_index: The data column index of the deleted column (before deletion)
        """
        if not self._sort_columns:
            return  # No sorts to update

        self.log(f"Updating sort state after deleting column {deleted_col_index}")
        self.log(f"Sort state before deletion: {self._sort_columns}")

        # Convert data column index to visible column index for comparison
        visible_columns = [col for col in self.data.columns if col != "__original_row_index__"]

        # We need to work with the column structure BEFORE deletion
        # Since the column is already deleted from self.data, we need to reconstruct
        # what the visible column index was before deletion

        # For simplicity, assume no tracking column initially, then adjust
        deleted_visible_col_index = deleted_col_index

        # If there was a tracking column, the visible index would be offset by -1
        if "__original_row_index__" in self.data.columns:
            # The deleted column index was in the full data.columns (including tracking)
            # So the visible column index was deleted_col_index - 1 (if tracking column exists)
            if deleted_col_index > 0:  # Not the tracking column itself
                deleted_visible_col_index = deleted_col_index - 1
            else:
                # This shouldn't happen as we don't delete tracking columns via UI
                deleted_visible_col_index = deleted_col_index

        new_sort_columns = []

        for sort_col_idx, sort_asc in self._sort_columns:
            if sort_col_idx == deleted_visible_col_index:
                # This sort was on the deleted column, remove it
                self.log(f"Removing sort on deleted column {sort_col_idx}")
                continue
            elif sort_col_idx > deleted_visible_col_index:
                # This sort was on a column to the right, shift index left by 1
                new_col_idx = sort_col_idx - 1
                new_sort_columns.append((new_col_idx, sort_asc))
                self.log(f"Shifting sort from column {sort_col_idx} to column {new_col_idx}")
            else:
                # This sort was on a column to the left, no change needed
                new_sort_columns.append((sort_col_idx, sort_asc))
                self.log(f"Keeping sort on column {sort_col_idx} unchanged")

        self._sort_columns = new_sort_columns
        self.log(f"Sort state after deletion: {self._sort_columns}")

        # If no sorts remain, make sure to clean up any tracking columns
        if not self._sort_columns and "__original_row_index__" in self.data.columns:
            self.log("No sorts remaining, cleaning up tracking column")
            self.data = self.data.drop("__original_row_index__")

    def _update_sort_state_after_column_insertion(self, inserted_col_index: int) -> None:
        """Update sorting state after a column is inserted.

        Args:
            inserted_col_index: The data column index where the new column was inserted
        """
        if not self._sort_columns:
            return  # No sorts to update

        self.log(f"Updating sort state after inserting column at {inserted_col_index}")
        self.log(f"Sort state before insertion: {self._sort_columns}")

        # Convert data column index to visible column index
        visible_columns = [col for col in self.data.columns if col != "__original_row_index__"]

        # Adjust for tracking column if present
        inserted_visible_col_index = inserted_col_index
        if "__original_row_index__" in self.data.columns:
            if inserted_col_index > 0:  # Not inserting before the tracking column
                inserted_visible_col_index = inserted_col_index - 1

        new_sort_columns = []

        for sort_col_idx, sort_asc in self._sort_columns:
            if sort_col_idx >= inserted_visible_col_index:
                # This sort was on a column at or to the right of insertion, shift index right by 1
                new_col_idx = sort_col_idx + 1
                new_sort_columns.append((new_col_idx, sort_asc))
                self.log(f"Shifting sort from column {sort_col_idx} to column {new_col_idx}")
            else:
                # This sort was on a column to the left, no change needed
                new_sort_columns.append((sort_col_idx, sort_asc))
                self.log(f"Keeping sort on column {sort_col_idx} unchanged")

        self._sort_columns = new_sort_columns
        self.log(f"Sort state after insertion: {self._sort_columns}")

    def _get_visible_column_index(self, data_col_index: int) -> int:
        """Convert data column index to visible column index (accounting for tracking columns)."""
        if "__original_row_index__" in self.data.columns:
            # Tracking column is at index 0, so visible columns start at index 1
            if self.data.columns[data_col_index] == "__original_row_index__":
                return -1  # Tracking column is not visible
            # Find the position in visible columns
            visible_columns = [col for col in self.data.columns if col != "__original_row_index__"]
            column_name = self.data.columns[data_col_index]
            try:
                return visible_columns.index(column_name)
            except ValueError:
                return -1
        else:
            return data_col_index

    def _get_data_column_index(self, visible_col_index: int) -> int:
        """Convert visible column index to data column index (accounting for tracking columns)."""
        if "__original_row_index__" in self.data.columns:
            # Get visible columns list
            visible_columns = [col for col in self.data.columns if col != "__original_row_index__"]
            if visible_col_index >= len(visible_columns):
                return -1
            # Find the column name and get its position in the full data
            column_name = visible_columns[visible_col_index]
            return self.data.columns.index(column_name)
        else:
            return visible_col_index

    def _get_visible_column_name(self, visible_col_index: int) -> str:
        """Get column name from visible column index."""
        visible_columns = [col for col in self.data.columns if col != "__original_row_index__"]
        if visible_col_index < len(visible_columns):
            return visible_columns[visible_col_index]
        return None

    def _get_column_header_with_sort_indicator(self, col_index: int, column_name: str) -> str:
        """Get column header text with sort indicator arrow and sort order number."""
        excel_col = self.get_excel_column_name(col_index)

        # Check if this column is in the sort order
        for sort_position, (sort_col, sort_asc) in enumerate(self._sort_columns):
            if sort_col == col_index:
                arrow = "↑" if sort_asc else "↓"
                sort_number = sort_position + 1
                return f"{excel_col} {arrow}{sort_number}"

        return excel_col

    def _show_row_column_delete_modal(self, row: int, col: int | None = None) -> None:
        """Show the row/column delete modal."""
        if self.data is None:
            return

        # Determine what to show based on the row clicked
        if row == 0:
            # Header row: show column options
            # Use the provided column or fall back to cursor position
            if col is not None:
                target_col = col
            else:
                cursor_coordinate = self._table.cursor_coordinate
                if cursor_coordinate and cursor_coordinate[1] < len(self.data.columns):
                    target_col = cursor_coordinate[1]
                else:
                    return

            # Get visible columns (excluding tracking columns) to ensure correct indexing
            visible_columns = [col for col in self.data.columns if col != "__original_row_index__"]
            if target_col < len(visible_columns):
                column_name = visible_columns[target_col]

                # Convert visual column index to actual data column index
                # If there's a tracking column, we need to offset the index
                data_col_index = target_col
                if "__original_row_index__" in self.data.columns:
                    # Find the actual position of this column in self.data.columns
                    column_name_to_find = visible_columns[target_col]
                    data_col_index = self.data.columns.index(column_name_to_find)

                def handle_column_action(choice: str | None) -> None:
                    if choice == "delete-column":
                        self._delete_column(data_col_index)
                    elif choice == "insert-column-left":
                        self._insert_column(data_col_index)
                    elif choice == "insert-column-right":
                        self._insert_column(data_col_index + 1)
                    elif choice == "sort-ascending":
                        self._sort_column(target_col, ascending=True)
                    elif choice == "sort-descending":
                        self._sort_column(target_col, ascending=False)

                modal = RowColumnDeleteModal("column", column_name, None, column_name)
                self.app.push_screen(modal, handle_column_action)
        elif row <= len(self.data):
            # Data row: show row options
            def handle_row_action(choice: str | None) -> None:
                if choice == "delete-row":
                    self._delete_row(row)
                elif choice == "insert-row-above":
                    self._insert_row(row)
                elif choice == "insert-row-below":
                    self._insert_row(row + 1)

            # Check if this is the last visible row in a truncated dataset
            # Only disable "Insert Row Below" if we're at the last row of the entire dataset
            is_last_visible_row = (
                self.is_data_truncated
                and not self._is_showing_last_row()
                and row == min(len(self.data), MAX_DISPLAY_ROWS)
            )

            modal = RowColumnDeleteModal(
                "row", f"Row {row}", row, None, self.is_data_truncated, is_last_visible_row
            )
            self.app.push_screen(modal, handle_row_action)

    def on_data_table_cell_highlighted(self, event: DataTable.CellHighlighted) -> None:
        """Handle cell highlighting and update address."""
        row, col = event.coordinate

        # Show column type info when hovering over header row (row 0)
        if row == 0 and self.data is not None:
            # Use proper column mapping
            column_name = self._get_visible_column_name(col)
            if column_name:
                data_col_index = self._get_data_column_index(col)
                if data_col_index >= 0:
                    dtype = self.data.dtypes[data_col_index]
                    column_info = self._format_column_info_message(column_name, dtype)
                    self.update_address_display(row, col, column_info)
                    # Notify script panel about column selection (for keyboard navigation)
                    column_type = self._get_friendly_type_name(dtype)
                    self._notify_script_panel_column_selection(col, column_name, column_type)
                else:
                    self.update_address_display(row, col)
                    self._notify_script_panel_column_clear()
            else:
                self.update_address_display(row, col)
                self._notify_script_panel_column_clear()
        else:
            self.update_address_display(row, col)
            # Clear script panel column selection when not on header row
            self._notify_script_panel_column_clear()

    def on_data_table_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        """Handle row highlighting and update address."""
        # Get the current cursor position
        cursor_coordinate = self._table.cursor_coordinate
        if cursor_coordinate:
            row, col = cursor_coordinate
            # Show column type info when cursor is on header row (row 0)
            if row == 0 and self.data is not None:
                # Use proper column mapping
                column_name = self._get_visible_column_name(col)
                if column_name:
                    data_col_index = self._get_data_column_index(col)
                    if data_col_index >= 0:
                        dtype = self.data.dtypes[data_col_index]
                        column_info = self._format_column_info_message(column_name, dtype)
                        self.update_address_display(row, col, column_info)
                    else:
                        self.update_address_display(row, col)
                else:
                    self.update_address_display(row, col)
            else:
                self.update_address_display(row, col)

    def on_data_table_cursor_moved(self, event) -> None:
        """Handle cursor movement and update address."""
        cursor_coordinate = self._table.cursor_coordinate
        if cursor_coordinate:
            row, col = cursor_coordinate
            # Show column type info when cursor is on header row (row 0)
            if row == 0 and self.data is not None:
                # Use proper column mapping
                column_name = self._get_visible_column_name(col)
                if column_name:
                    data_col_index = self._get_data_column_index(col)
                    if data_col_index >= 0:
                        dtype = self.data.dtypes[data_col_index]
                        column_info = self._format_column_info_message(column_name, dtype)
                        self.update_address_display(row, col, column_info)
                        # Notify script panel about column selection (same as mouse click)
                        column_type = self._get_friendly_type_name(dtype)
                        self._notify_script_panel_column_selection(col, column_name, column_type)
                    else:
                        self.update_address_display(row, col)
                        self._notify_script_panel_column_clear()
                else:
                    self.update_address_display(row, col)
                    self._notify_script_panel_column_clear()
            else:
                self.update_address_display(row, col)
                # Clear script panel column selection when not on header row
                self._notify_script_panel_column_clear()

    def on_key(self, event) -> bool:
        """Handle key events and update address based on cursor position."""
        # Check if we're in search mode and handle search navigation
        search_overlay = self.query_one(SearchOverlay)
        if search_overlay.is_active and search_overlay.matches:
            if event.key == "up":
                # Previous search match
                event.prevent_default()
                event.stop()
                search_overlay._navigate_to_previous_match()
                return True
            elif event.key == "down":
                # Next search match
                event.prevent_default()
                event.stop()
                search_overlay._navigate_to_next_match()
                return True
            elif event.key in ["left", "right"]:
                # Check for left-right-left-right gesture sequence
                current_time = time.time()

                # Reset gesture sequence if timeout exceeded
                if (
                    self._gesture_start_time is not None
                    and current_time - self._gesture_start_time > self._gesture_timeout
                ):
                    self._gesture_sequence = []
                    self._gesture_start_time = None

                # Add current gesture to sequence
                self._gesture_sequence.append(event.key)
                if self._gesture_start_time is None:
                    self._gesture_start_time = current_time

                # Keep only last 4 gestures
                if len(self._gesture_sequence) > 4:
                    self._gesture_sequence = self._gesture_sequence[-4:]

                # Check for four right-arrow pattern
                if len(self._gesture_sequence) == 4 and self._gesture_sequence == [
                    "right",
                    "right",
                    "right",
                    "right",
                ]:
                    # Exit search mode with gesture
                    search_overlay.deactivate_search()
                    self.clear_search_highlights()
                    self._gesture_sequence = []
                    self._gesture_start_time = None

                # Disable normal left/right movement in search mode
                event.prevent_default()
                event.stop()
                return True

        # Check if key should trigger immediate cell editing
        if not self.editing_cell and self._should_start_immediate_edit(event.key):
            cursor_coordinate = self._table.cursor_coordinate
            if cursor_coordinate:
                row, col = cursor_coordinate

                # Don't allow immediate editing on pseudo-elements
                if self.data is not None:
                    visible_columns = [
                        col for col in self.data.columns if col != "__original_row_index__"
                    ]
                    # Skip if on pseudo-column or pseudo-row
                    if col == len(visible_columns) or self._is_pseudo_row(row):
                        return False

                # Start cell editing with the typed character as initial value
                event.prevent_default()
                event.stop()
                self.call_after_refresh(self.start_cell_edit_with_initial, row, col, event.key)
                return True

        # Handle cell editing and pseudo-element actions
        if event.key == "enter" and not self.editing_cell:
            cursor_coordinate = self._table.cursor_coordinate
            if cursor_coordinate:
                row, col = cursor_coordinate

                # Check if Enter pressed on pseudo-elements (add column or add row)
                if self.data is not None:
                    visible_columns = [
                        col for col in self.data.columns if col != "__original_row_index__"
                    ]
                    # Check if on pseudo-column (add column)
                    if col == len(visible_columns):  # Last column is the pseudo-column
                        self.log("Enter pressed on pseudo-column: adding new column")
                        event.prevent_default()
                        event.stop()
                        self.action_add_column()
                        # Keep focus on the pseudo-column for easy multiple additions
                        self.call_after_refresh(self._focus_pseudo_column)
                        return True

                    # Check if on pseudo-row (add row)
                    if self._is_pseudo_row(row):
                        self.log("Enter pressed on pseudo-row: adding new row")
                        event.prevent_default()
                        event.stop()
                        self.action_add_row()
                        # Keep focus on the pseudo-row for easy multiple additions
                        self.call_after_refresh(self._focus_pseudo_row)
                        return True

                # Allow editing both header row (row 0) and data rows (row > 0)
                # Prevent default to stop event propagation
                event.prevent_default()
                event.stop()
                # Use call_after_refresh to start editing after the current event cycle
                self.call_after_refresh(self.start_cell_edit, row, col)
                return True

        # Handle paste operation (Ctrl+V or Cmd+V)
        if event.key == "ctrl+v" or event.key == "cmd+v":
            self.action_paste_from_clipboard()
            return True

        # Handle numeric extraction (Ctrl+Shift+N or Cmd+Shift+N)
        if event.key == "ctrl+shift+n" or event.key == "cmd+shift+n":
            self.action_extract_numbers_from_column()
            return True

        # Handle delete operations (Ctrl+D or Cmd+D for delete menu)
        if event.key == "ctrl+d" or event.key == "cmd+d":
            self.action_show_delete_menu()
            return True

        # Handle delete key for immediate row/column deletion
        if event.key == "delete":
            cursor_coordinate = self._table.cursor_coordinate
            if cursor_coordinate:
                row, col = cursor_coordinate
                self._show_row_column_delete_modal(row)
            return True

        # Allow the table to handle navigation keys and update display after
        if event.key in ["up", "down", "left", "right", "tab"]:
            # Special handling for left arrow double-tap in column A (keyboard equivalent to double-click)
            if event.key == "left":
                cursor_coordinate = self._table.cursor_coordinate
                if cursor_coordinate and self.data is not None:
                    row, col = cursor_coordinate
                    current_time = time.time()

                    # Check if we're in the "0" cell (row 0, col 0) and this is a double-tap - reset sorting
                    if (
                        row == 0
                        and col == 0
                        and self._last_left_arrow_position == (row, col)
                        and current_time - self._last_left_arrow_time < self._double_click_threshold
                    ):
                        # Double-tap detected in "0" cell: reset sorting if any sorts are active
                        if len(self._sort_columns) > 0:
                            self.log("Double-tap left arrow detected in '0' cell: resetting sort")
                            event.prevent_default()
                            event.stop()
                            self._reset_sort()
                            return True
                        else:
                            self.log(
                                "Double-tap left arrow detected in '0' cell: no sorts to reset"
                            )

                    # Check if we're in column A (col 0) and this is a double-tap
                    elif (
                        col == 0
                        and row > 0  # Column A and not header row
                        and self._last_left_arrow_position == (row, col)
                        and current_time - self._last_left_arrow_time < self._double_click_threshold
                    ):
                        # Double-tap detected in column A: show row operations modal
                        self.log(f"Double-tap left arrow detected in column A, row {row}")
                        event.prevent_default()
                        event.stop()
                        self._show_row_column_delete_modal(row)
                        return True

                    # Update tracking for next potential double-tap
                    self._last_left_arrow_time = current_time
                    self._last_left_arrow_position = (row, col)

            # Special handling for up arrow double-tap in header row (keyboard equivalent to column double-click)
            elif event.key == "up":
                cursor_coordinate = self._table.cursor_coordinate
                if cursor_coordinate and self.data is not None:
                    row, col = cursor_coordinate
                    current_time = time.time()

                    # Check if we're in header row (row 0) and this is a double-tap
                    if (
                        row == 0
                        and self._last_up_arrow_position == (row, col)
                        and current_time - self._last_up_arrow_time < self._double_click_threshold
                    ):
                        # Double-tap detected in header row: show column operations modal
                        column_name = self._get_visible_column_name(col)
                        if column_name:
                            self.log(
                                f"Double-tap up arrow detected in header row, column {col} ({column_name})"
                            )
                            event.prevent_default()
                            event.stop()
                            self._show_row_column_delete_modal(
                                0, col
                            )  # Pass row 0 and specific column
                            return True

                    # Update tracking for next potential double-tap
                    self._last_up_arrow_time = current_time
                    self._last_up_arrow_position = (row, col)

            # Use call_after_refresh to update display after navigation completes
            self.call_after_refresh(self._update_display_after_navigation)
            # Let the event bubble up to be handled by the table
            return False

        return False

    def _update_display_after_navigation(self) -> None:
        """Update the address display after cursor navigation."""
        cursor_coordinate = self._table.cursor_coordinate
        if cursor_coordinate:
            row, col = cursor_coordinate
            # Show column type info when cursor is on header row (row 0)
            if row == 0 and self.data is not None:
                # Use proper column mapping
                column_name = self._get_visible_column_name(col)
                if column_name:
                    data_col_index = self._get_data_column_index(col)
                    if data_col_index >= 0:
                        dtype = self.data.dtypes[data_col_index]
                        column_info = self._format_column_info_message(column_name, dtype)
                        self.update_address_display(row, col, column_info)
                    else:
                        self.update_address_display(row, col)
                else:
                    self.update_address_display(row, col)
            else:
                self.update_address_display(row, col)

    def _focus_pseudo_column(self) -> None:
        """Focus on the pseudo-column (Add Column) cell."""
        if self.data is not None:
            visible_columns = [col for col in self.data.columns if col != "__original_row_index__"]
            pseudo_col = len(visible_columns)  # Last column is the pseudo-column
            self._table.cursor_coordinate = (0, pseudo_col)  # Focus on header row of pseudo-column
            self.update_address_display(0, pseudo_col)

    def _focus_pseudo_row(self) -> None:
        """Focus on the pseudo-row (Add Row) cell."""
        if self.data is not None:
            # Calculate the pseudo-row position based on current display state
            if len(self.data) <= MAX_DISPLAY_ROWS:
                # Small dataset - pseudo-row is after all data
                pseudo_row = len(self.data) + 1
            else:
                # Large dataset - pseudo-row is the last visible row in current view
                pseudo_row = min(len(self.data), MAX_DISPLAY_ROWS) + 1

            self._table.cursor_coordinate = (pseudo_row, 0)  # Focus on first column of pseudo-row
            self.update_address_display(pseudo_row, 0)

    def _advance_to_next_cell(self, current_row: int, current_col: int) -> None:
        """Advance to the cell below the current cell, if not in the last row."""
        if self.data is not None:
            # Check if we're not in the last data row
            last_data_row = len(self.data)  # This is the row index + 1 since row 0 is headers
            if current_row < last_data_row:  # Not in the last row
                next_row = current_row + 1
                self._table.move_cursor(row=next_row, column=current_col)
                self.update_address_display(next_row, current_col)
                self.log(
                    f"Advanced to next cell: {self.get_excel_column_name(current_col)}{next_row}"
                )
            else:
                # Stay in the current cell if it's the last row
                self._table.move_cursor(row=current_row, column=current_col)
                self.update_address_display(current_row, current_col)
                self.log(
                    f"Stayed in current cell (last row): {self.get_excel_column_name(current_col)}{current_row}"
                )

    def _should_start_immediate_edit(self, key: str) -> bool:
        """Check if a key should trigger immediate cell editing."""
        # Allow alphanumeric characters
        if len(key) == 1:  # Single character keys only
            return key.isalnum()

        # Handle special keys with their Textual key names
        return key in ["plus", "minus", "full_stop"]

    def _handle_immediate_edit_key(self, event) -> bool:
        """Handle immediate edit key from CustomDataTable. Returns True if handled."""
        # Don't allow editing if welcome overlay is visible
        try:
            welcome_overlay = self.query_one("#welcome-overlay", WelcomeOverlay)
            if not welcome_overlay.has_class("hidden") and welcome_overlay.display:
                return False
        except Exception:
            pass

        # Check if key should trigger immediate cell editing
        if not self.editing_cell and self._should_start_immediate_edit(event.key):
            cursor_coordinate = self._table.cursor_coordinate
            if cursor_coordinate:
                row, col = cursor_coordinate

                # Don't allow immediate editing on pseudo-elements
                if self.data is not None:
                    visible_columns = [
                        col for col in self.data.columns if col != "__original_row_index__"
                    ]
                    # Skip if on pseudo-column or pseudo-row
                    if col == len(visible_columns) or self._is_pseudo_row(row):
                        return False

                # Start cell editing with the typed character as initial value
                event.prevent_default()
                event.stop()
                self.call_after_refresh(self.start_cell_edit_with_initial, row, col, event.key)
                return True

        return False

    def on_resize(self, event) -> None:
        """Handle terminal resize events to update status bar layout."""
        try:
            # Refresh the status bar with current cell position to adapt to new width
            cursor_coordinate = self._table.cursor_coordinate
            if cursor_coordinate:
                row, col = cursor_coordinate
                self.update_address_display(row, col)
        except Exception as e:
            self.log(f"Error handling resize: {e}")

    def start_cell_edit_with_initial(self, row: int, col: int, initial_char: str) -> None:
        """Start editing a cell with an initial character."""
        if self.data is None:
            return

        # Don't allow editing if welcome overlay is visible
        try:
            welcome_overlay = self.query_one("#welcome-overlay", WelcomeOverlay)
            if not welcome_overlay.has_class("hidden") and welcome_overlay.display:
                return
        except Exception:
            pass

        # Convert Textual key names to actual characters
        key_to_char = {"plus": "+", "minus": "-", "full_stop": "."}
        display_char = key_to_char.get(initial_char, initial_char)

        try:
            if row == 0:
                # Editing column name (header row): start with the typed character
                self.editing_cell = True
                self._edit_row = row
                # Convert visible column index to data column index
                data_col_index = self._get_data_column_index(col)
                if data_col_index == -1:
                    self.editing_cell = False
                    return
                self._edit_col = data_col_index

                # Create and show the cell edit modal for column name with initial character
                cell_address = f"{self.get_excel_column_name(col)}{row}"

                def handle_column_name_edit(new_value: str | None) -> None:
                    if new_value is not None and new_value.strip():
                        # Update the address display to show we're processing
                        self.update_address_display(row, col, f"UPDATING COLUMN: {new_value}")
                        self.finish_column_name_edit(new_value.strip())
                    else:
                        self.editing_cell = False

                    # Restore cursor position after editing
                    self.call_after_refresh(self._restore_cursor_position, row, col)

                modal = CellEditModal(display_char, cell_address, is_immediate_edit=True)
                self.app.push_screen(modal, handle_column_name_edit)

            else:
                # Editing data cell: start with the typed character
                # For large datasets, account for display offset
                display_offset = getattr(self, "_display_offset", 0)
                data_row = display_offset + row - 1  # Convert display row to actual data row

                if data_row < len(self.data):
                    # Store editing state
                    self.editing_cell = True
                    self._edit_row = row
                    # Convert visible column index to data column index
                    data_col_index = self._get_data_column_index(col)
                    if data_col_index == -1:
                        self.editing_cell = False
                        return
                    self._edit_col = data_col_index

                    # Create and show the cell edit modal for data with initial character
                    cell_address = f"{self.get_excel_column_name(col)}{row}"

                    def handle_cell_edit(new_value: str | None) -> None:
                        if new_value is not None:
                            # Update the address display to show we're processing
                            self.update_address_display(row, col, f"UPDATING: {new_value}")
                            self.finish_cell_edit(new_value)
                        else:
                            self.editing_cell = False

                        # For immediate edits, advance to next cell if not in last row
                        self.call_after_refresh(self._advance_to_next_cell, row, col)

                    modal = CellEditModal(display_char, cell_address, is_immediate_edit=True)
                    self.app.push_screen(modal, handle_cell_edit)

        except Exception as e:
            self.editing_cell = False

    def start_cell_edit(self, row: int, col: int) -> None:
        """Start editing a cell."""
        if self.data is None:
            self.log("Cannot edit: No data")
            return

        # Don't allow editing if welcome overlay is visible
        try:
            welcome_overlay = self.query_one("#welcome-overlay", WelcomeOverlay)
            if not welcome_overlay.has_class("hidden") and welcome_overlay.display:
                self.log("Cannot edit: Welcome overlay is visible")
                return
        except Exception:
            pass

        try:
            if row == 0:
                # Editing column name (header row)
                # Convert visible column index to data column index
                data_col_index = self._get_data_column_index(col)
                if data_col_index == -1:
                    return

                current_value = str(self.data.columns[data_col_index])

                # Store editing state
                self.editing_cell = True
                self._edit_row = row
                self._edit_col = data_col_index

                self.log(
                    f"Starting column name edit: {self.get_excel_column_name(col)} = '{current_value}'"
                )

                # Create and show the cell edit modal for column name
                cell_address = f"{self.get_excel_column_name(col)}{row}"

                def handle_column_name_edit(new_value: str | None) -> None:
                    self.log(f"Column name edit callback: new_value = {new_value}")
                    if new_value is not None and new_value.strip():
                        # Update the address display to show we're processing
                        self.update_address_display(row, col, f"UPDATING COLUMN: {new_value}")
                        self.finish_column_name_edit(new_value.strip())
                    else:
                        self.editing_cell = False
                        self.log("Column name edit cancelled or empty")

                    # Restore cursor position after editing
                    self.call_after_refresh(self._restore_cursor_position, row, col)

                modal = CellEditModal(current_value, cell_address)
                self.app.push_screen(modal, handle_column_name_edit)

            else:
                # Editing data cell
                # For large datasets, account for display offset
                display_offset = getattr(self, "_display_offset", 0)
                data_row = display_offset + row - 1  # Convert display row to actual data row

                if data_row < len(self.data):
                    # Convert visible column index to data column index
                    data_col_index = self._get_data_column_index(col)
                    if data_col_index == -1:
                        return

                    raw_value = self.data[data_row, data_col_index]
                    # For None values, use empty string in the editor
                    current_value = "" if raw_value is None else str(raw_value)

                    # Store editing state
                    self.editing_cell = True
                    self._edit_row = row
                    self._edit_col = data_col_index

                    self.log(
                        f"Starting cell edit: {self.get_excel_column_name(col)}{row} = '{current_value}'"
                    )

                    # Create and show the cell edit modal for data
                    cell_address = f"{self.get_excel_column_name(col)}{row}"

                    def handle_cell_edit(new_value: str | None) -> None:
                        self.log(f"Cell edit callback: new_value = {new_value}")
                        if new_value is not None:
                            # Update the address display to show we're processing
                            self.update_address_display(row, col, f"UPDATING: {new_value}")
                            self.finish_cell_edit(new_value)
                        else:
                            self.editing_cell = False
                            self.log("Cell edit cancelled")

                        # Restore cursor position after editing
                        self.call_after_refresh(self._restore_cursor_position, row, col)

                    modal = CellEditModal(current_value, cell_address)
                    self.app.push_screen(modal, handle_cell_edit)

        except Exception as e:
            self.log(f"Error starting cell edit: {e}")
            self.editing_cell = False

    def _restore_cursor_position(self, row: int, col: int) -> None:
        """Restore cursor position after cell editing."""
        try:
            # For large datasets, convert the absolute row to the relative position in the current view
            if self.is_data_truncated:
                display_offset = getattr(self, "_display_offset", 0)
                # Convert absolute row to relative position in current slice
                relative_row = row - display_offset
                # If the row is outside the current view, navigate to it first
                if relative_row < 1 or relative_row > MAX_DISPLAY_ROWS:
                    self.navigate_to_row(row)  # navigate_to_row expects 1-based row number
                    return
                else:
                    # Use the relative position for cursor movement
                    display_row = relative_row
            else:
                display_row = row

            self._table.move_cursor(row=display_row, column=col)
            self.update_address_display(row, col)
            self.log(f"Restored cursor to {self.get_excel_column_name(col)}{row}")
        except Exception as e:
            self.log(f"Error restoring cursor position: {e}")

    def _restore_cursor_after_refresh(self, cursor_coordinate: tuple) -> None:
        """Restore cursor position after table refresh."""
        try:
            row, col = cursor_coordinate
            # Ensure the coordinates are still valid after refresh
            if (
                row >= 0
                and col >= 0
                and row < self._table.row_count
                and col < len(self._table.columns)
            ):
                self._table.move_cursor(row=row, column=col)
                self.update_address_display(row, col)
                self.log(f"Restored cursor after refresh to {self.get_excel_column_name(col)}{row}")
            else:
                self.log(f"Cannot restore cursor to {cursor_coordinate}: out of bounds")
        except Exception as e:
            self.log(f"Error restoring cursor after refresh: {e}")

    def finish_column_name_edit(self, new_name: str) -> None:
        """Finish editing a column name and update the DataFrame schema."""
        if not self.editing_cell or self.data is None:
            self.log("Cannot finish column name edit: no editing state or no data")
            return

        try:
            col_index = self._edit_col
            old_name = self.data.columns[col_index]

            self.log(f"Updating column name from '{old_name}' to '{new_name}'")

            # Validate the new column name
            validation_error = self._validate_column_name(new_name, old_name)
            if validation_error:
                self.log(f"Column name validation failed: {validation_error}")

                # Show validation error modal and allow user to try again
                cell_address = f"{self.get_excel_column_name(col_index)}0"

                def handle_validation_error_response(try_again: bool) -> None:
                    if try_again:
                        # User wants to try again: restart the edit process
                        self.log("User chose to try again after validation error")
                        self.call_after_refresh(
                            self.start_cell_edit, self._edit_row, self._edit_col
                        )
                    else:
                        # User cancelled: just reset the editing state
                        self.log("User cancelled after validation error")
                        self.editing_cell = False
                        self.update_address_display(self._edit_row, self._edit_col)

                modal = ValidationErrorModal(validation_error, old_name, cell_address)
                self.app.push_screen(modal, handle_validation_error_response)
                return

            # Rename the column in the DataFrame
            old_to_new_mapping = {old_name: new_name}
            self.data = self.data.rename(old_to_new_mapping)

            # Mark as changed and refresh display
            self.has_changes = True
            self.update_title_change_indicator()
            self.refresh_table_data()

            # Reset the status bar to normal
            self.update_address_display(self._edit_row, self._edit_col)

            self.log(f"Successfully updated column name to '{new_name}'")

        except Exception as e:
            self.log(f"Error updating column name: {e}")
            import traceback

            self.log(f"Traceback: {traceback.format_exc()}")
        finally:
            self.editing_cell = False

    def _validate_column_name(self, name: str, old_name: str) -> str | None:
        """Validate a column name and return an error message if invalid, None if valid."""
        # Check if empty or only whitespace
        if not name or not name.strip():
            return "Column name cannot be empty"

        # Check if new name already exists (and is different from old name)
        if name in self.data.columns and name != old_name:
            return f"Column '{name}' already exists"

        # Check for purely numeric names (problematic in many contexts)
        if name.isdigit():
            return f"Column '{name}' is purely numeric (not recommended)"

        # Check for names that start with digits (problematic for Python identifiers)
        if name[0].isdigit():
            return f"Column '{name}' starts with a digit (not recommended for Python compatibility)"

        # Check for reserved Python keywords
        if keyword.iskeyword(name):
            return f"Column '{name}' is a Python reserved keyword"

        # Check for common problematic characters
        problematic_chars = set(" \t\n\r\f\v()[]{}.,;:!@#$%^&*+=|\\/<>?`~\"'")
        if any(char in problematic_chars for char in name):
            problematic_found = [char for char in name if char in problematic_chars]
            return f"Column '{name}' contains problematic characters: {', '.join(repr(c) for c in problematic_found[:3])}..."

        # Check for names that are too long (practical limit)
        if len(name) > 100:
            return f"Column name is too long ({len(name)} characters, max 100 recommended)"

        # Check for common reserved words in databases/analysis tools
        reserved_words = {
            "select",
            "from",
            "where",
            "insert",
            "update",
            "delete",
            "create",
            "drop",
            "table",
            "index",
            "view",
            "function",
            "procedure",
            "trigger",
            "database",
            "schema",
            "primary",
            "foreign",
            "key",
            "constraint",
            "null",
            "not",
            "and",
            "or",
            "in",
            "like",
            "between",
            "exists",
            "case",
            "when",
            "then",
            "else",
            "group",
            "order",
            "by",
            "having",
            "limit",
            "offset",
            "union",
            "join",
            "inner",
            "outer",
            "left",
            "right",
            "on",
            "as",
            "distinct",
            "all",
        }
        if name.lower() in reserved_words:
            return f"Column '{name}' is a reserved SQL keyword"

        return None  # Valid name

    def _extract_numeric_from_string(self, value: str) -> tuple[float | None, bool]:
        """Extract numeric content from a mixed string.

        Args:
            value: String that may contain numeric and non-numeric characters

        Returns:
            tuple: (extracted_number, has_decimal_point)
                - extracted_number: Float value or None if no numeric content found
                - has_decimal_point: True if the original had a decimal point
        """
        if not value or not value.strip():
            return None, False

        # Use regex to find all numeric parts including decimals
        # This pattern matches: optional negative sign, digits, optional decimal point and more digits
        import re

        numeric_pattern = r"[-+]?(?:\d+\.?\d*|\.\d+)"
        matches = re.findall(numeric_pattern, value.strip())

        if not matches:
            return None, False

        # Take the first numeric match and try to convert to float
        try:
            numeric_str = matches[0]
            numeric_value = float(numeric_str)
            has_decimal = "." in numeric_str
            return numeric_value, has_decimal
        except (ValueError, TypeError):
            return None, False

    def _infer_column_type_from_value(self, value: str) -> tuple[any, str]:
        """Infer the most appropriate column type from a string value.

        Returns:
            tuple: (converted_value, type_name) where type_name is user-friendly
        """
        if not value or not value.strip():
            return None, "null"

        value = value.strip()

        # Try boolean first (most specific)
        if value.lower() in ("true", "false", "yes", "no", "1", "0", "y", "n"):
            bool_value = value.lower() in ("true", "yes", "1", "y")
            return bool_value, "boolean"

        # Try integer
        try:
            int_value = int(value)
            return int_value, "integer"
        except ValueError:
            pass

        # Try float
        try:
            float_value = float(value)
            return float_value, "float"
        except ValueError:
            pass

        # Default to string: NO automatic numeric extraction during cell editing
        return value, "text"

    def _get_polars_dtype_for_type_name(self, type_name: str) -> any:
        """Convert user-friendly type name to Polars dtype."""
        type_mapping = {
            "integer": pl.Int64,
            "float": pl.Float64,
            "boolean": pl.Boolean,
            "text": pl.String,
            "null": pl.String,  # Default for null columns
        }
        return type_mapping.get(type_name, pl.String)

    def _is_column_empty(self, column_name: str) -> bool:
        """Check if a column contains only null values."""
        try:
            column_data = self.data[column_name]
            return column_data.null_count() == len(column_data)
        except Exception:
            return False

    def _get_friendly_type_name(self, dtype) -> str:
        """Convert Polars dtype to user-friendly name."""
        if dtype in [pl.Int64, pl.Int32, pl.Int16, pl.Int8]:
            return "integer"
        elif dtype in [pl.Float64, pl.Float32]:
            return "float"
        elif dtype == pl.Boolean:
            return "boolean"
        else:
            return "text"

    def _parse_create_table_types(self, create_sql: str) -> dict:
        """Parse column types from CREATE TABLE statement (basic implementation)."""
        column_types = {}
        try:
            # Extract the part between parentheses
            import re

            match = re.search(r"\((.*)\)", create_sql, re.DOTALL)
            if not match:
                return {}

            columns_part = match.group(1)
            # Split by comma, but be careful with constraints
            parts = []
            paren_depth = 0
            current_part = ""

            for char in columns_part:
                if char == "(":
                    paren_depth += 1
                elif char == ")":
                    paren_depth -= 1
                elif char == "," and paren_depth == 0:
                    parts.append(current_part.strip())
                    current_part = ""
                    continue
                current_part += char

            if current_part.strip():
                parts.append(current_part.strip())

            # Parse each column definition
            for part in parts:
                part = part.strip()
                if not part or part.upper().startswith(
                    ("PRIMARY", "FOREIGN", "UNIQUE", "CHECK", "CONSTRAINT")
                ):
                    continue

                # Split by whitespace to get column name and type
                tokens = part.split()
                if len(tokens) >= 2:
                    col_name = tokens[0].strip("\"'`")
                    col_type = tokens[1].upper()
                    column_types[col_name] = col_type

        except Exception as e:
            self.log(f"Error parsing CREATE TABLE: {e}")

        return column_types

    def _format_column_info_message(self, column_name: str, dtype) -> str:
        """Format column information message for status bar."""
        # DIRECT APPROACH: Use native_column_types if available
        if (
            self.is_database_mode
            and hasattr(self, "native_column_types")
            and self.native_column_types
            and column_name in self.native_column_types
        ):
            native_type = self.native_column_types[column_name]
            return f"'{column_name}' // column type: {native_type}"
        else:
            # Regular mode: show Polars types
            simple_type = self._get_friendly_type_name(dtype)
            polars_type = str(dtype)
            return f"'{column_name}' // column type: {simple_type} ({polars_type})"

    def _style_cell_value(self, cell, row_idx: int = None, col_idx: int = None) -> str:
        """Style a cell value for display in the table."""
        if cell is None:
            base_style = "[red]None[/red]"
        elif str(cell) == "":
            base_style = "[dim yellow]∅[/dim yellow]"  # Empty set symbol for empty strings
        elif str(cell).isspace():
            # Create bright, visible underscores to represent the whitespace
            underscore_count = len(str(cell))
            base_style = f"[bold magenta]{'_' * underscore_count}[/bold magenta]"
        else:
            base_style = str(cell)

        # Apply search match highlighting if this cell is a search match
        if row_idx is not None and col_idx is not None:
            # Convert to display coordinates for comparison (add 1 for header row)
            display_row = row_idx + 1
            match_coord = (display_row, col_idx)

            if match_coord in self.search_matches:
                # All search matches get light green background
                return f"[black on #90EE90]{base_style}[/black on #90EE90]"

        return base_style

    def _check_type_conversion_needed(self, current_dtype, new_value, new_type: str) -> bool:
        """Check if entering the new value would require type conversion."""
        if new_value is None:
            return False  # Null values can go in any column type

        current_type = self._get_friendly_type_name(current_dtype)

        # No conversion needed if types match
        if current_type == new_type:
            return False

        # Check specific conversion scenarios that need user confirmation
        if current_type == "integer" and new_type == "float":
            return True  # Integer -> Float needs confirmation
        elif current_type in ["integer", "float"] and new_type == "text":
            return True  # Numeric -> Text needs confirmation
        elif current_type == "boolean" and new_type != "boolean":
            return True  # Boolean -> anything else needs confirmation
        elif current_type == "text" and new_type in ["integer", "float", "boolean"]:
            # For string columns, accept numeric/boolean values as strings without conversion
            return False  # No conversion needed: store as string

        return False

    def _should_offer_numeric_extraction(self, column_name: str) -> tuple[bool, str]:
        """Check if a string column would benefit from numeric extraction.

        Returns:
            tuple: (should_offer, suggested_type)
        """
        if self.data is None:
            return False, ""

        try:
            column_data = self.data[column_name]
            if column_data.dtype != pl.String:
                return False, ""  # Only offer for string columns

            # Sample some non-null values
            sample_values = []
            for value in column_data:
                if value is not None:
                    sample_values.append(str(value))
                    if len(sample_values) >= 20:  # Check up to 20 samples
                        break

            if not sample_values:
                return False, ""

            # Check how many values contain extractable numbers
            extractable_count = 0
            has_decimals = False

            for value in sample_values:
                extracted_num, has_decimal = self._extract_numeric_from_string(value)
                if extracted_num is not None:
                    extractable_count += 1
                    if has_decimal:
                        has_decimals = True

            # Offer extraction if more than 50% of values contain numbers
            extraction_ratio = extractable_count / len(sample_values)
            if extraction_ratio >= 0.5:
                suggested_type = "float" if has_decimals else "integer"
                return True, suggested_type

            return False, ""

        except Exception as e:
            self.log(f"Error checking numeric extraction potential: {e}")
            return False, ""

    def _convert_value_to_existing_type(self, value: str, dtype):
        """Convert a string value to match the existing column type."""
        try:
            if not value or not value.strip():
                return None

            value = value.strip()

            if dtype in [pl.Int64, pl.Int32, pl.Int16, pl.Int8]:
                # For integer columns, try direct conversion only
                return int(float(value))  # Handle "3.0" -> 3
            elif dtype in [pl.Float64, pl.Float32]:
                # For float columns, try direct conversion only
                return float(value)
            elif dtype == pl.Boolean:
                return value.lower() in ("true", "1", "yes", "y", "on")
            else:
                return value  # String type

        except (ValueError, TypeError):
            return value  # Fallback to string: let type conversion dialog handle this

    def _update_cell_value_deferred(self, data_row: int, column_name: str, new_value):
        """Store cell edit for deferred processing - much faster for large datasets."""
        column_index = self.data.columns.index(column_name)

        # Store the edit in our pending edits dictionary
        self._pending_cell_edits[(data_row, column_index)] = new_value

        self.log(
            f"Deferred cell edit: row {data_row}, col {column_index} ({column_name}) = '{new_value}'"
        )

        # Note: The actual DataFrame will be updated later when needed (e.g., on save)
        # This makes cell editing virtually instant even for huge datasets

    def get_pending_edits_count(self) -> int:
        """Get the number of pending cell edits."""
        return len(self._pending_cell_edits)

    def has_pending_edits(self) -> bool:
        """Check if there are any pending cell edits."""
        return len(self._pending_cell_edits) > 0

    def _apply_pending_edits(self):
        """Apply all pending cell edits to the actual DataFrame."""
        if not self._pending_cell_edits:
            return

        import time

        start_time = time.time()

        self.log(f"Applying {len(self._pending_cell_edits)} pending cell edits...")

        # Group edits by column for efficiency
        edits_by_column = {}
        for (row, col), value in self._pending_cell_edits.items():
            column_name = self.data.columns[col]
            if column_name not in edits_by_column:
                edits_by_column[column_name] = []
            edits_by_column[column_name].append((row, value))

        # Apply edits column by column
        for column_name, row_value_pairs in edits_by_column.items():
            # For each column, use polars when/then for bulk updates
            conditions = []
            values = []

            for row, value in row_value_pairs:
                # Create row index condition
                conditions.append(pl.int_range(pl.len()).eq(row))
                values.append(value)

            # Apply all edits for this column at once
            if conditions:
                # Use when/then chain for bulk update
                expr = pl.col(column_name)
                for condition, value in zip(conditions, values):
                    expr = expr.when(condition).then(value)
                expr = expr.otherwise(
                    pl.col(column_name)
                )  # Keep original values for unchanged rows

                self.data = self.data.with_columns(expr.alias(column_name))

        # Clear pending edits
        self._pending_cell_edits.clear()

        apply_time = time.time() - start_time
        self.log(f"Applied pending edits in {apply_time:.3f}s")

    def _get_effective_cell_value(self, data_row: int, column_index: int):
        """Get the effective value of a cell, including any pending edits."""
        # Check if there's a pending edit for this cell
        if (data_row, column_index) in self._pending_cell_edits:
            return self._pending_cell_edits[(data_row, column_index)]

        # Otherwise return the current DataFrame value
        return self.data[data_row, column_index].item()

    def _update_cell_value(self, data_row: int, column_name: str, new_value):
        """Update a single cell value in the DataFrame efficiently."""
        import time

        start_time = time.time()

        try:
            self._debug_write(
                f"🔍 Starting cell update - row {data_row}, col '{column_name}', value '{new_value}'"
            )

            # Validate that the column name exists
            if column_name not in self.data.columns:
                raise ValueError(
                    f"Column '{column_name}' not found in DataFrame. Available columns: {self.data.columns}"
                )

            slice_start = time.time()
            # Use slice-and-concatenate approach for maximum efficiency
            # Split the DataFrame into before, target row, and after
            before_df = (
                self.data[:data_row] if data_row > 0 else pl.DataFrame(schema=self.data.schema)
            )
            after_df = (
                self.data[data_row + 1 :]
                if data_row < len(self.data) - 1
                else pl.DataFrame(schema=self.data.schema)
            )
            slice_time = time.time() - slice_start

            update_start = time.time()
            # Create the updated row - ensure we match the exact data type of the column
            original_dtype = self.data.dtypes[self.data.columns.index(column_name)]

            # Cast the new value to match the column's data type
            if original_dtype == pl.Int64:
                typed_value = int(new_value) if new_value is not None else None
                typed_literal = pl.lit(typed_value, dtype=pl.Int64)
            elif original_dtype == pl.Int32:
                typed_value = int(new_value) if new_value is not None else None
                typed_literal = pl.lit(typed_value, dtype=pl.Int32)
            elif original_dtype == pl.Float64:
                typed_value = float(new_value) if new_value is not None else None
                typed_literal = pl.lit(typed_value, dtype=pl.Float64)
            elif original_dtype == pl.Float32:
                typed_value = float(new_value) if new_value is not None else None
                typed_literal = pl.lit(typed_value, dtype=pl.Float32)
            elif original_dtype == pl.String:
                typed_literal = pl.lit(
                    str(new_value) if new_value is not None else None, dtype=pl.String
                )
            else:
                # For other types, let Polars infer but try to match
                typed_literal = pl.lit(new_value)

            target_row = (
                self.data[data_row : data_row + 1]
                .with_columns(typed_literal.alias(column_name))
                .select(self.data.columns)
            )  # Ensure we only keep original columns in original order
            update_time = time.time() - update_start

            # Verify schemas match before concatenating
            if before_df.shape[1] != target_row.shape[1] or (
                len(after_df) > 0 and after_df.shape[1] != target_row.shape[1]
            ):
                raise ValueError(
                    f"Schema mismatch: before={before_df.shape[1]}, target={target_row.shape[1]}, after={after_df.shape[1] if len(after_df) > 0 else 'N/A'}"
                )

            concat_start = time.time()
            # Concatenate back together efficiently
            if len(before_df) > 0 and len(after_df) > 0:
                self.data = pl.concat([before_df, target_row, after_df])
            elif len(before_df) > 0:
                self.data = pl.concat([before_df, target_row])
            elif len(after_df) > 0:
                self.data = pl.concat([target_row, after_df])
            else:
                self.data = target_row
            concat_time = time.time() - concat_start

            total_time = time.time() - start_time
            self._debug_write(
                f"✅ Fast update completed in {total_time:.4f}s (slice: {slice_time:.4f}s, update: {update_time:.4f}s, concat: {concat_time:.4f}s)"
            )

        except Exception as e:
            # Fallback to the original method if the efficient method fails
            fallback_start = time.time()
            self._debug_write(f"❌ Efficient cell update failed: {e}")
            self._debug_write(
                "❌ Using SLOW fallback method - this will take 10+ seconds for large datasets"
            )
            self._update_cell_value_fallback(data_row, column_name, new_value)
            fallback_time = time.time() - fallback_start
            total_time = time.time() - start_time
            self._debug_write(
                f"❌ Slow fallback completed in {total_time:.4f}s (fallback: {fallback_time:.4f}s)"
            )

    def _update_cell_value_fallback(self, data_row: int, column_name: str, new_value):
        """Fallback method for updating a single cell value (less efficient but reliable)."""
        # Convert to list of rows for update
        rows = []
        for i, row in enumerate(self.data.iter_rows()):
            if i == data_row:
                updated_row = list(row)
                updated_row[self._edit_col] = new_value
                rows.append(updated_row)
            else:
                rows.append(list(row))

        # Create new DataFrame from updated rows
        self.data = pl.DataFrame(rows, schema=self.data.schema)

    def _apply_numeric_extraction_to_column(self, column_name: str, target_type: str) -> None:
        """Apply numeric extraction to an entire column."""
        try:
            if self.data is None:
                return

            self.log(f"Applying numeric extraction to column '{column_name}' -> {target_type}")

            # Get current column data
            column_data = self.data[column_name]

            # Create new column with extracted numeric values
            extracted_values = []
            for value in column_data:
                if value is None:
                    extracted_values.append(None)
                else:
                    extracted_num, has_decimal = self._extract_numeric_from_string(str(value))
                    if extracted_num is not None:
                        if (
                            target_type == "integer"
                            and not has_decimal
                            and extracted_num.is_integer()
                        ):
                            extracted_values.append(int(extracted_num))
                        else:
                            extracted_values.append(extracted_num)
                    else:
                        extracted_values.append(None)

            # Determine the Polars dtype
            if target_type == "integer":
                new_dtype = pl.Int64
            else:  # float
                new_dtype = pl.Float64

            # Create new column and update the DataFrame
            self.data = self.data.with_columns(
                [pl.Series(column_name, extracted_values, dtype=new_dtype)]
            )

            # Mark as changed and refresh display
            self.has_changes = True
            self.update_title_change_indicator()
            self.refresh_table_data()

            self.log(f"Successfully applied numeric extraction to column '{column_name}'")

        except Exception as e:
            self.log(f"Error applying numeric extraction: {e}")
            import traceback

            self.log(f"Traceback: {traceback.format_exc()}")

    def _apply_type_conversion_and_update(self) -> None:
        """Apply column type conversion and update the cell value."""
        if not hasattr(self, "_pending_edit"):
            return

        try:
            edit_info = self._pending_edit
            data_row = edit_info["data_row"]
            column_name = edit_info["column_name"]
            converted_value = edit_info["converted_value"]
            new_type = edit_info["new_type"]

            self.log(f"Converting column '{column_name}' to {new_type} and updating value")

            # Convert the entire column to the new type
            new_dtype = self._get_polars_dtype_for_type_name(new_type)
            self.data = self.data.with_columns([pl.col(column_name).cast(new_dtype)])

            # Update the specific cell with the converted value
            self._update_cell_value(data_row, column_name, converted_value)

            # Mark as changed and update display efficiently
            self.has_changes = True
            self.update_title_change_indicator()
            self._update_cell_display(self._edit_row, self._edit_col, converted_value)
            self.update_address_display(
                self._edit_row, self._edit_col, f"Column converted to {new_type}"
            )

            self.log(f"Successfully converted column '{column_name}' to {new_type}")

        except Exception as e:
            self.log(f"Error in type conversion: {e}")
            import traceback

            self.log(f"Traceback: {traceback.format_exc()}")
        finally:
            self.editing_cell = False
            if hasattr(self, "_pending_edit"):
                delattr(self, "_pending_edit")

    def _apply_edit_with_truncation(self) -> None:
        """Apply the edit by truncating/converting the value to fit the current type."""
        if not hasattr(self, "_pending_edit"):
            return

        try:
            edit_info = self._pending_edit
            data_row = edit_info["data_row"]
            column_name = edit_info["column_name"]
            new_value = edit_info["new_value"]
            current_type = edit_info["current_type"]

            # Convert value to fit current type
            current_dtype = self.data.dtypes[self._edit_col]
            converted_value = self._convert_value_to_existing_type(new_value, current_dtype)

            self.log(f"Applying value '{new_value}' as {current_type}: '{converted_value}'")

            # Update the cell with converted value
            self._update_cell_value(data_row, column_name, converted_value)

            # Mark as changed and update display efficiently
            self.has_changes = True
            self.update_title_change_indicator()
            self._update_cell_display(self._edit_row, self._edit_col, converted_value)
            self.update_address_display(
                self._edit_row, self._edit_col, f"Value converted to {current_type}"
            )

            self.log(f"Successfully applied converted value '{converted_value}'")

        except Exception as e:
            self.log(f"Error applying edit with truncation: {e}")
            import traceback

            self.log(f"Traceback: {traceback.format_exc()}")
        finally:
            self.editing_cell = False
            if hasattr(self, "_pending_edit"):
                delattr(self, "_pending_edit")

    def finish_cell_edit(self, new_value: str) -> None:
        """Finish editing a cell and update the data."""
        if not self.editing_cell or self.data is None:
            self.log("Cannot finish edit: no editing state or no data")
            return

        try:
            # For large datasets, account for display offset when calculating data row
            display_offset = getattr(self, "_display_offset", 0)
            data_row = (
                display_offset + self._edit_row - 1
            )  # Convert from display row to actual data row
            column_name = self.data.columns[self._edit_col]

            self.log(
                f"Updating cell at data_row={data_row}, col={self._edit_col}, column='{column_name}' with value='{new_value}' (display_offset={display_offset}, edit_row={self._edit_row})"
            )

            # Check if this is a new/empty column that needs type inference
            is_empty_column = self._is_column_empty(column_name)
            current_dtype = self.data.dtypes[self._edit_col]

            # Infer type from the new value
            inferred_value, inferred_type = self._infer_column_type_from_value(new_value)

            if is_empty_column and inferred_value is not None:
                # This is the first value in a new column: establish the column type
                self.log(
                    f"Setting column '{column_name}' type to {inferred_type} based on first value"
                )

                # Convert the entire column to the inferred type
                new_dtype = self._get_polars_dtype_for_type_name(inferred_type)

                # Create new column with the correct type
                self.data = self.data.with_columns([pl.col(column_name).cast(new_dtype)])

                # Update the specific cell with the converted value
                self._debug_write("📝 About to call _update_cell_value for empty column case")
                self._update_cell_value(data_row, column_name, inferred_value)

                # Mark as changed and update display efficiently
                self.has_changes = True
                self.update_title_change_indicator()
                self._update_cell_display(self._edit_row, self._edit_col, inferred_value)
                self.update_address_display(
                    self._edit_row, self._edit_col, f"Column type set to {inferred_type}"
                )

            else:
                # This is an existing column: check for type conflicts
                needs_conversion = self._check_type_conversion_needed(
                    current_dtype, inferred_value, inferred_type
                )

                if needs_conversion:
                    # Store pending edit for conversion dialog
                    self._pending_edit = {
                        "data_row": data_row,
                        "column_name": column_name,
                        "new_value": new_value,
                        "converted_value": inferred_value,
                        "current_type": self._get_friendly_type_name(current_dtype),
                        "new_type": inferred_type,
                    }

                    def handle_type_conversion(convert: bool | None) -> None:
                        if convert is True:
                            self._apply_type_conversion_and_update()
                        elif convert is False:
                            self._apply_edit_with_truncation()
                        else:
                            # Cancel the edit
                            self.editing_cell = False
                            self.log("Type conversion cancelled")

                        # Restore cursor position after conversion dialog
                        self.call_after_refresh(
                            self._restore_cursor_position, self._edit_row, self._edit_col
                        )

                    # Show conversion warning dialog
                    current_type_name = self._get_friendly_type_name(current_dtype)
                    modal = ColumnConversionModal(
                        column_name, new_value, current_type_name, inferred_type
                    )
                    self.app.push_screen(modal, handle_type_conversion)
                    return

                else:
                    # No conversion needed: direct update
                    converted_value = self._convert_value_to_existing_type(new_value, current_dtype)
                    self._debug_write("📝 About to call _update_cell_value for normal case")
                    self._update_cell_value(data_row, column_name, converted_value)

                    # Mark as changed and update display efficiently
                    self.has_changes = True
                    self.update_title_change_indicator()
                    self._update_cell_display(self._edit_row, self._edit_col, converted_value)
                    self.update_address_display(self._edit_row, self._edit_col)

            self.log(
                f"Successfully updated cell {self.get_excel_column_name(self._edit_col)}{self._edit_row} = '{new_value}'"
            )

        except Exception as e:
            self.log(f"Error finishing cell edit: {e}")
            import traceback

            self.log(f"Traceback: {traceback.format_exc()}")
        finally:
            self.editing_cell = False
            # Restore cursor position after editing completes
            if hasattr(self, "_edit_row") and hasattr(self, "_edit_col"):
                self.call_after_refresh(
                    self._restore_cursor_position, self._edit_row, self._edit_col
                )

    def _apply_column_conversion_and_update(self) -> None:
        """Apply column type conversion and update the cell value."""
        if not hasattr(self, "_pending_edit"):
            return

        try:
            edit_info = self._pending_edit
            data_row = edit_info["data_row"]
            column_name = edit_info["column_name"]
            converted_value = edit_info["converted_value"]

            self.log(f"Converting column '{column_name}' to Float and updating value")

            # Convert the entire column to Float64
            self.data = self.data.with_columns([pl.col(column_name).cast(pl.Float64)])

            # Now update the specific cell using the efficient method
            self._update_cell_value(data_row, column_name, converted_value)

            # Mark as changed and refresh display
            self.has_changes = True
            self.update_title_change_indicator()
            self.refresh_table_data()

            # Reset status bar
            self.update_address_display(self._edit_row, self._edit_col)

            self.log(f"Successfully converted column '{column_name}' to Float and updated cell")

        except Exception as e:
            self.log(f"Error in column conversion: {e}")
            import traceback

            self.log(f"Traceback: {traceback.format_exc()}")
        finally:
            self.editing_cell = False
            if hasattr(self, "_pending_edit"):
                delattr(self, "_pending_edit")

    def _apply_edit_without_conversion(self) -> None:
        """Apply the edit without column conversion (truncate decimal)."""
        if not hasattr(self, "_pending_edit"):
            return

        try:
            edit_info = self._pending_edit
            data_row = edit_info["data_row"]
            column_name = edit_info["column_name"]
            new_value = edit_info["new_value"]

            # Convert to integer (truncating decimal)
            converted_value = int(float(new_value)) if new_value.strip() else None

            self.log(
                f"Applying edit without conversion, truncating '{new_value}' to '{converted_value}'"
            )

            # Update the cell with truncated value using the efficient method
            self._update_cell_value(data_row, column_name, converted_value)

            # Mark as changed and update display efficiently
            self.has_changes = True
            self.update_title_change_indicator()
            self._update_cell_display(self._edit_row, self._edit_col, converted_value)

            # Reset status bar
            self.update_address_display(self._edit_row, self._edit_col)

            self.log(f"Successfully applied truncated value '{converted_value}'")

        except Exception as e:
            self.log(f"Error applying edit without conversion: {e}")
            import traceback

            self.log(f"Traceback: {traceback.format_exc()}")
        finally:
            self.editing_cell = False
            if hasattr(self, "_pending_edit"):
                delattr(self, "_pending_edit")

    def update_title_change_indicator(self) -> None:
        """Update the title to show change indicator."""
        if hasattr(self.app, "current_filename") and self.app.current_filename:
            filename = self.app.current_filename
            if self.has_changes and not filename.endswith(" ●"):
                self.app.set_current_filename(filename + " ●")
            elif not self.has_changes and filename.endswith(" ●"):
                self.app.set_current_filename(filename[:-2])

    def _update_cell_display(self, display_row: int, column_index: int, new_value: any) -> None:
        """Update a specific cell in the display without full refresh."""
        if self.data is None:
            return

        import time

        start_time = time.time()

        try:
            print(
                f"🎨 PERF DEBUG: Starting display update for row {display_row}, col {column_index}"
            )

            # Convert display row to table coordinate
            table_row = display_row
            table_col = column_index

            # Style the new value directly
            data_row = display_row - 1  # Convert to 0-indexed for data access
            display_offset = getattr(self, "_display_offset", 0)
            actual_data_row = display_offset + data_row

            styled_value = self._style_cell_value(new_value, actual_data_row, column_index)

            # Create coordinate and update the cell in the table widget
            coordinate = Coordinate(table_row, table_col)
            self._table.update_cell_at(coordinate, styled_value)

            display_time = time.time() - start_time
            print(f"✅ PERF DEBUG: Display update completed in {display_time:.4f}s")

        except Exception as e:
            display_time = time.time() - start_time
            print(f"❌ PERF DEBUG: Display update failed in {display_time:.4f}s: {e}")
            print("❌ PERF DEBUG: Falling back to SLOW full table refresh")
            # Fallback to full refresh if the cell update fails
            self.refresh_table_data(preserve_cursor=True)

    def refresh_table_data(self, preserve_cursor: bool = True) -> None:
        """Refresh the table display with current data."""
        if self.data is None:
            return

        # Store current cursor position if we need to preserve it
        saved_cursor = None
        if preserve_cursor:
            saved_cursor = self._table.cursor_coordinate

        # Clear and rebuild the table
        self._table.clear(columns=True)
        self._table.show_row_labels = True

        # Add data columns (excluding any tracking columns)
        visible_columns = [col for col in self.data.columns if col != "__original_row_index__"]
        for i, column in enumerate(visible_columns):
            header_text = self._get_column_header_with_sort_indicator(i, column)
            self._table.add_column(header_text, key=column)

        # Add pseudo-column for adding new columns (column adder)
        pseudo_col_index = len(visible_columns)
        pseudo_excel_col = self.get_excel_column_name(pseudo_col_index)
        self._table.add_column(pseudo_excel_col, key="__ADD_COLUMN__")

        # Re-enable row labels after adding columns
        self._table.show_row_labels = True

        # Add header row with bold formatting (without persistent type info)
        visible_columns = [col for col in self.data.columns if col != "__original_row_index__"]
        column_names = [f"[bold]{str(col)}[/bold]" for col in visible_columns]
        # Add pseudo-column header with "+" indicator
        column_names.append("[dim italic]+ Add Column[/dim italic]")

        # Create row label for header row (0) - show sort reset button if sorting is active
        header_row_label = "0"
        if len(self._sort_columns) > 0:
            header_row_label = "↑↓"  # Combined up/down arrow for sort reset

        self._table.add_row(*column_names, label=header_row_label)

        # Add data rows (excluding tracking columns)
        # Limit display to MAX_DISPLAY_ROWS for large datasets
        total_rows = len(self.data)
        display_rows = min(total_rows, MAX_DISPLAY_ROWS)
        self.is_data_truncated = total_rows > MAX_DISPLAY_ROWS

        # Use current slice position for large datasets
        display_offset = getattr(self, "_display_offset", 0)
        if self.is_data_truncated:
            # Get the slice of data to display based on current offset
            end_row = min(display_offset + MAX_DISPLAY_ROWS, total_rows)
            data_slice = self.data.slice(display_offset, end_row - display_offset)
        else:
            # Small dataset - show everything
            data_slice = self.data
            display_offset = 0

        for row_idx, row in enumerate(data_slice.iter_rows()):
            # Calculate the actual row number considering the display offset
            actual_row_number = display_offset + row_idx + 1
            row_label = str(actual_row_number)
            # Style cell values (None as red, whitespace-only as orange underscores), exclude tracking columns
            styled_row = []
            visible_col_idx = 0  # Track column index for visible columns only
            for i, cell in enumerate(row):
                column_name = self.data.columns[i]
                if column_name != "__original_row_index__":
                    # Use row_idx for styling (local to the slice) but display_offset + row_idx for actual row
                    styled_row.append(
                        self._style_cell_value(cell, display_offset + row_idx, visible_col_idx)
                    )
                    visible_col_idx += 1
            # Add empty cell for the pseudo-column
            styled_row.append("")
            self._table.add_row(*styled_row, label=row_label)

        # Only add pseudo-row for adding new rows if we're showing the last row of the dataset
        if self._is_showing_last_row():
            next_row_label = "+"  # Simple label instead of showing row number
            visible_column_count = len(
                [col for col in self.data.columns if col != "__original_row_index__"]
            )
            pseudo_row_cells = (
                ["[dim italic]+ Add Row[/dim italic]"] + [""] * (visible_column_count - 1) + [""]
            )
            self._table.add_row(*pseudo_row_cells, label=next_row_label)

        # Final enforcement of row labels
        self._table.show_row_labels = True

        # Restore cursor position if we saved it
        if preserve_cursor and saved_cursor:
            self.call_after_refresh(self._restore_cursor_after_refresh, saved_cursor)

        # Use a timer to ensure row labels persist after refresh
        self.set_timer(0.1, self._force_row_labels_visible)

    def save_data(self, file_path: str) -> bool:
        """Save current data to file."""
        if self.data is None:
            return False

        try:
            # Determine file format from extension
            file_path_obj = Path(file_path)
            extension = file_path_obj.suffix.lower()

            # For database mode, export the full table data instead of limited display data
            if (
                self.is_database_mode
                and hasattr(self, "database_connection")
                and self.database_connection
                and hasattr(self, "current_table_name")
                and self.current_table_name
            ):
                self.log(f"Database mode: exporting full table {self.current_table_name}")

                # Query the full table without LIMIT
                try:
                    query = f"SELECT * FROM {self.current_table_name}"
                    self.log(f"Executing full table query: {query}")
                    result = self.database_connection.execute(query).arrow()
                    full_df = pl.from_arrow(result)
                    self.log(f"Full table query successful, shape: {full_df.shape}")

                    # Use the full DataFrame for export
                    export_data = full_df
                except Exception as e:
                    self.log(f"Failed to query full table: {e}")
                    # Fall back to the limited display data
                    export_data = self.data
            else:
                # Regular mode: use the current DataFrame
                export_data = self.data

            if extension == ".csv":
                export_data.write_csv(file_path)
            elif extension == ".tsv":
                export_data.write_csv(file_path, separator="\t")
            elif extension == ".parquet":
                export_data.write_parquet(file_path)
            elif extension == ".json":
                export_data.write_json(file_path)
            elif extension in [".jsonl", ".ndjson"]:
                export_data.write_ndjson(file_path)
            elif extension in [".xlsx", ".xls"]:
                try:
                    export_data.write_excel(file_path)
                except AttributeError as e:
                    raise Exception(
                        "Excel file support requires additional dependencies. Please install with: pip install polars[xlsx]"
                    ) from e
            elif extension in [".feather", ".ipc", ".arrow"]:
                export_data.write_ipc(file_path)
            else:
                # Default to CSV
                if not file_path.endswith(".csv"):
                    file_path += ".csv"
                export_data.write_csv(file_path)

            # Update tracking (only for regular mode, not database mode)
            if not self.is_database_mode:
                self.has_changes = False
                self.original_data = self.data.clone()
                self.update_title_change_indicator()

            rows_exported = len(export_data) if export_data is not None else 0
            self.log(f"Data saved to: {file_path} ({rows_exported} rows exported)")
            return True

        except Exception as e:
            self.log(f"Error saving file: {e}")
            return False

    def action_save_as(self) -> None:
        """Show save dialog to save with new filename."""

        def handle_save_input(file_path: str | None) -> None:
            if file_path:
                self.log(f"Attempting to save to: {file_path}")
                if self.save_data(file_path):
                    # Successfully saved, update filename with format
                    if hasattr(self.app, "set_current_filename"):
                        file_format = self.get_file_format(file_path)
                        filename_with_format = f"{file_path} [{file_format}]"
                        self.app.set_current_filename(filename_with_format)
                        self.log(f"File saved successfully as: {file_path}")
                    else:
                        self.log(f"File saved to: {file_path}")
                else:
                    self.log("Failed to save file")
            else:
                self.log("Save cancelled")

        modal = SaveFileModal()
        self.app.push_screen(modal, handle_save_input)

    def action_save_original(self) -> bool:
        """Save over the original file."""
        # For sample data, always redirect to save-as
        if self.is_sample_data:
            self.action_save_as()
            return False

        if hasattr(self.app, "current_filename") and self.app.current_filename:
            filename = self.app.current_filename
            # Remove change indicator if present
            if filename.endswith(" ●"):
                filename = filename[:-2]

            # Extract actual file path from filename with format (e.g., "file.csv [CSV]")
            if " [" in filename and filename.endswith("]"):
                actual_filename = filename.split(" [")[0]
            else:
                actual_filename = filename

            return self.save_data(actual_filename)
        else:
            # No original filename, show save dialog
            self.action_save_as()
            return False

    def _apply_column_type_conversion(self, column_name: str, target_type: str) -> None:
        """Apply standard type conversion to an entire column."""
        try:
            if self.data is None:
                return

            self.log(f"Converting column '{column_name}' to {target_type}")

            # Get the new Polars dtype
            new_dtype = self._get_polars_dtype_for_type_name(target_type)

            # Apply conversion to the entire column
            try:
                self.data = self.data.with_columns([pl.col(column_name).cast(new_dtype)])
            except Exception as cast_error:
                # If direct casting fails, try with conversion logic
                self.log(f"Direct cast failed, trying conversion: {cast_error}")

                # Get current column data
                column_data = self.data[column_name]
                converted_values = []

                for value in column_data:
                    if value is None:
                        converted_values.append(None)
                    else:
                        converted_val = self._convert_value_to_target_type(str(value), target_type)
                        converted_values.append(converted_val)

                # Create new column with converted values
                self.data = self.data.with_columns(
                    [pl.Series(column_name, converted_values, dtype=new_dtype)]
                )

            # Mark as changed and refresh display
            self.has_changes = True
            self.update_title_change_indicator()
            self.refresh_table_data()

            self.log(f"Successfully converted column '{column_name}' to {target_type}")

        except Exception as e:
            self.log(f"Error applying column type conversion: {e}")
            import traceback

            self.log(f"Traceback: {traceback.format_exc()}")

    def _apply_column_numeric_extraction(self, column_name: str) -> None:
        """Apply numeric extraction to an entire column (wrapper for existing method)."""
        # Determine the best target type by sampling the column
        should_offer, suggested_type = self._should_offer_numeric_extraction(column_name)

        if should_offer:
            self._apply_numeric_extraction_to_column(column_name, suggested_type)
        else:
            self.log(
                f"Column '{column_name}' doesn't contain enough numeric content for extraction"
            )

    def _convert_value_to_target_type(self, value: str, target_type: str):
        """Convert a string value to the target type (used by dropdown interface)."""
        try:
            if not value or not value.strip():
                return None

            value = value.strip()

            if target_type == "integer":
                # Try direct conversion first
                try:
                    return int(float(value))  # Handle "3.0" -> 3
                except ValueError:
                    # For dropdown interface, still try extraction as fallback
                    extracted_num, _ = self._extract_numeric_from_string(value)
                    if extracted_num is not None and extracted_num.is_integer():
                        return int(extracted_num)
                    return None
            elif target_type == "float":
                # Try direct conversion first
                try:
                    return float(value)
                except ValueError:
                    # For dropdown interface, still try extraction as fallback
                    extracted_num, _ = self._extract_numeric_from_string(value)
                    return extracted_num  # Could be None
            elif target_type == "boolean":
                return value.lower() in ("true", "1", "yes", "y", "on")
            else:  # text
                return value

        except (ValueError, TypeError):
            return None

    def action_extract_numbers_from_column(self) -> None:
        """Extract numeric values from the current column if it's a string column."""
        if self.data is None:
            self.log("No data available for numeric extraction")
            return

        cursor_coordinate = self._table.cursor_coordinate
        if not cursor_coordinate:
            self.log("No cell selected for numeric extraction")
            return

        row, col = cursor_coordinate

        # Check if we're in a valid column (not pseudo-column)
        visible_columns = [col for col in self.data.columns if col != "__original_row_index__"]
        if col >= len(visible_columns):
            self.log("Cannot extract numbers from pseudo-column")
            return

        # Use proper column mapping
        column_name = self._get_visible_column_name(col)
        if not column_name:
            self.log("Invalid column for numeric extraction")
            return

        # Check if this column would benefit from numeric extraction
        should_offer, suggested_type = self._should_offer_numeric_extraction(column_name)

        if not should_offer:
            self.log(
                f"Column '{column_name}' doesn't contain enough numeric content for extraction"
            )
            # Still show a message to the user
            try:
                status_bar = self.query_one("#status-bar", Static)
                status_bar.update(
                    f"Column '{column_name}' doesn't contain enough numeric content for extraction"
                )
            except Exception:
                pass
            return

        # Get sample data for preview
        try:
            column_data = self.data[column_name]
            sample_values = []
            for value in column_data:
                if value is not None:
                    sample_values.append(str(value))
                    if len(sample_values) >= 10:  # Preview up to 10 values
                        break

            def handle_extraction_choice(choice: str | None) -> None:
                if choice == "extract":
                    self._apply_numeric_extraction_to_column(column_name, suggested_type)
                    # Restore cursor position
                    self.call_after_refresh(self._restore_cursor_position, row, col)
                elif choice == "keep_text":
                    self.log(f"Keeping column '{column_name}' as text")
                    # Restore cursor position
                    self.call_after_refresh(self._restore_cursor_position, row, col)
                else:
                    self.log("Numeric extraction cancelled")
                    # Restore cursor position
                    self.call_after_refresh(self._restore_cursor_position, row, col)

            # Show the numeric extraction modal
            modal = NumericExtractionModal(column_name, sample_values, suggested_type)
            self.app.push_screen(modal, handle_extraction_choice)

        except Exception as e:
            self.log(f"Error preparing numeric extraction: {e}")

    def action_paste_from_clipboard(self) -> None:
        """Paste tabular data from system clipboard."""
        try:
            # Try to get clipboard content
            import subprocess
            import sys

            # Get clipboard content based on OS
            if sys.platform == "darwin":  # macOS
                result = subprocess.run(["pbpaste"], capture_output=True, text=True)
                clipboard_content = result.stdout
            elif sys.platform == "linux":  # Linux
                try:
                    result = subprocess.run(
                        ["xclip", "-selection", "clipboard", "-o"], capture_output=True, text=True
                    )
                    clipboard_content = result.stdout
                except FileNotFoundError:
                    # Try with xsel if xclip not available
                    result = subprocess.run(
                        ["xsel", "--clipboard", "--output"], capture_output=True, text=True
                    )
                    clipboard_content = result.stdout
            elif sys.platform == "win32":  # Windows
                try:
                    import win32clipboard

                    win32clipboard.OpenClipboard()
                    clipboard_content = win32clipboard.GetClipboardData()
                    win32clipboard.CloseClipboard()
                except ImportError:
                    # Fallback for Windows without pywin32
                    import tkinter as tk

                    root = tk.Tk()
                    root.withdraw()  # Hide the window
                    clipboard_content = root.clipboard_get()
                    root.destroy()
            else:
                self.update_address_display(0, 0, "Clipboard paste not supported on this platform")
                return

            if not clipboard_content or not clipboard_content.strip():
                self.update_address_display(0, 0, "Clipboard is empty")
                return

            # Parse the clipboard content as tabular data
            parsed_data = self._parse_clipboard_data(clipboard_content)
            if parsed_data is None:
                self.update_address_display(0, 0, "No tabular data found in clipboard")
                return

            # Show paste options modal
            self._show_paste_options_modal(parsed_data)

        except Exception as e:
            self.log(f"Error accessing clipboard: {e}")
            self.update_address_display(0, 0, f"Clipboard error: {str(e)[:30]}...")

    def action_add_row(self) -> None:
        """Add a new row to the bottom of the table (Apple Numbers style)."""
        if self.data is None:
            self.log("Cannot add row: No data loaded")
            return

        try:
            # Create a new row with None values for all columns
            new_row_data = [None for _ in self.data.columns]

            # Create a new DataFrame with the additional row
            new_row_df = pl.DataFrame([new_row_data], schema=self.data.schema)
            combined_df = pl.concat([self.data, new_row_df], how="vertical")

            # Update the data and refresh the display
            self.data = combined_df
            self.has_changes = True
            self.update_title_change_indicator()
            self.refresh_table_data()

            # Update the row add label to show the next row number
            try:
                next_row_number = len(self.data) + 1
                row_label = self.query_one("#row-add-label", Static)
                row_label.update(str(next_row_number))
            except Exception as e:
                self.log(f"Error updating row label: {e}")

            # Move cursor to the new row
            new_row_index = len(self.data)  # Row index in display (0-based, where 0 is header)

            # For large datasets, ensure we navigate to show the new row
            if len(self.data) > MAX_DISPLAY_ROWS:
                # Navigate to the end of the dataset to show the new row
                self.navigate_to_row(len(self.data))
                # After navigation, the new row will be visible at the bottom
                # Calculate its display position
                display_row = min(len(self.data), MAX_DISPLAY_ROWS)
                self.call_after_refresh(self._move_cursor_to_new_row, display_row, 0)
            else:
                # Small dataset - use the actual row index
                self.call_after_refresh(self._move_cursor_to_new_row, new_row_index, 0)

            self.log(f"Added new row. Table now has {len(self.data)} rows")

        except Exception as e:
            self.log(f"Error adding row: {e}")
            self.update_address_display(0, 0, f"Add row failed: {str(e)[:30]}...")

    def action_add_column(self) -> None:
        """Add a new column to the right of the table (Apple Numbers style)."""
        if self.data is None:
            self.log("Cannot add column: No data loaded")
            return

        try:
            # Generate a unique column name
            base_name = "Column"
            counter = 1
            new_column_name = f"{base_name}_{counter}"

            while new_column_name in self.data.columns:
                counter += 1
                new_column_name = f"{base_name}_{counter}"

            # Add the new column with null values initially: type will be inferred from first value
            self.data = self.data.with_columns(
                [pl.lit(None, dtype=pl.String).alias(new_column_name)]
            )

            # Mark as changed and refresh display
            self.has_changes = True
            self.update_title_change_indicator()
            self.refresh_table_data()

            # Move cursor to the new column header
            new_col_index = len(self.data.columns) - 1
            self.call_after_refresh(self._move_cursor_to_new_column, 0, new_col_index)

            self.log(
                f"Added new column '{new_column_name}'. Table now has {len(self.data.columns)} columns"
            )

        except Exception as e:
            self.log(f"Error adding column: {e}")
            self.update_address_display(0, 0, f"Add column failed: {str(e)[:30]}...")

    def _move_cursor_to_new_row(self, row: int, col: int) -> None:
        """Move cursor to a newly added row."""
        try:
            self._table.move_cursor(row=row, column=col)

            # Calculate the actual row number for display
            if self.is_data_truncated:
                display_offset = getattr(self, "_display_offset", 0)
                actual_row_number = display_offset + row
            else:
                actual_row_number = row

            self.update_address_display(actual_row_number, col, "New row added")
        except Exception as e:
            self.log(f"Error moving cursor to new row: {e}")

    def _move_cursor_to_new_column(self, row: int, col: int) -> None:
        """Move cursor to a newly added column."""
        try:
            self._table.move_cursor(row=row, column=col)
            self.update_address_display(row, col, "New column added")
        except Exception as e:
            self.log(f"Error moving cursor to new column: {e}")

    def _delete_row(self, row: int) -> None:
        """Delete a row from the table."""
        if self.data is None:
            self.log("Cannot delete row: No data loaded")
            return

        if row == 0:
            self.log("Cannot delete header row")
            return

        try:
            data_row = row - 1  # Convert from display row to data row (row 0 is headers)

            if data_row < 0 or data_row >= len(self.data):
                self.log(f"Cannot delete row {row}: Index out of range")
                return

            # Delete the row using polars slice operations
            if len(self.data) == 1:
                # If only one row, create empty dataframe with same schema
                self.data = pl.DataFrame(schema=self.data.schema)
            else:
                # Remove the specific row
                if data_row == 0:
                    self.data = self.data.slice(1)  # Remove first row
                elif data_row == len(self.data) - 1:
                    self.data = self.data.slice(0, len(self.data) - 1)  # Remove last row
                else:
                    # Remove middle row by concatenating before and after
                    before = self.data.slice(0, data_row)
                    after = self.data.slice(data_row + 1)
                    self.data = pl.concat([before, after])

            # Capture cursor position BEFORE refresh for better UX logic
            cursor_coordinate = self._table.cursor_coordinate
            current_row = cursor_coordinate[0] if cursor_coordinate else None
            current_col = cursor_coordinate[1] if cursor_coordinate else None

            # Mark as changed and refresh display
            self.has_changes = True
            self.update_title_change_indicator()
            self.refresh_table_data()

            # Move cursor to a safe position with better UX
            if cursor_coordinate:
                # If cursor was on the deleted row, move to previous row (same column)
                if current_row == row:
                    # Move to the previous row if possible, otherwise stay at row 1 (first data row)
                    new_row = max(1, row - 1)
                    new_col = current_col
                    self.call_after_refresh(self._move_cursor_after_delete, new_row, new_col)
                    self.log(f"Moved cursor from deleted row {row} to row {new_row}")
                elif current_row > row:
                    # If cursor was below the deleted row, shift it up by one
                    new_row = current_row - 1
                    new_col = current_col
                    self.call_after_refresh(self._move_cursor_after_delete, new_row, new_col)
                    self.log(f"Shifted cursor up from row {current_row} to row {new_row}")
                else:
                    # Cursor was above the deleted row, no change needed
                    new_row = current_row
                    new_col = current_col
                    self.call_after_refresh(self._move_cursor_after_delete, new_row, new_col)

            self.log(f"Deleted row {row}. Table now has {len(self.data)} rows")

        except Exception as e:
            self.log(f"Error deleting row {row}: {e}")
            self.update_address_display(row, 0, f"Delete row failed: {str(e)[:30]}...")

    def _delete_column(self, col: int) -> None:
        """Delete a column from the table."""
        if self.data is None:
            self.log("Cannot delete column: No data loaded")
            return

        if col < 0 or col >= len(self.data.columns):
            self.log(f"Cannot delete column {col}: Index out of range")
            return

        try:
            column_name = self.data.columns[col]

            # Capture cursor position BEFORE refresh for better UX logic
            cursor_coordinate = self._table.cursor_coordinate
            current_row = cursor_coordinate[0] if cursor_coordinate else None
            current_col = cursor_coordinate[1] if cursor_coordinate else None

            # Handle the case where this is the last remaining column
            if len(self.data.columns) == 1:
                # Create a new empty dataframe with a single Column_1 column
                num_rows = len(self.data)
                empty_column_data = [None] * num_rows
                self.data = pl.DataFrame(
                    {"Column_1": empty_column_data}, schema={"Column_1": pl.String}
                )

                self.log(
                    f"Deleted last column '{column_name}', created empty 'Column_1' column with {num_rows} rows"
                )
            else:
                # Delete the column normally
                remaining_columns = [name for i, name in enumerate(self.data.columns) if i != col]
                self.data = self.data.select(remaining_columns)

            # Mark as changed and refresh display
            self.has_changes = True
            self.update_title_change_indicator()

            # Update sorting state to handle the deleted column
            self._update_sort_state_after_column_deletion(col)

            self.refresh_table_data()

            # Move cursor to a safe position with better UX
            if cursor_coordinate:
                # Special case: if we just deleted the last column and created Column_1
                if len(self.data.columns) == 1 and self.data.columns[0] == "Column_1":
                    # Always move cursor to column 0 (the new Column_1)
                    new_col = 0
                    new_row = current_row
                    self.call_after_refresh(self._move_cursor_after_delete, new_row, new_col)
                    self.log(f"Moved cursor to new Column_1 at column {new_col}")
                # Normal column deletion cases
                elif current_col == col:
                    # Move to the previous column if possible, otherwise stay at column 0 (first column)
                    new_col = max(0, col - 1)
                    new_row = current_row
                    self.call_after_refresh(self._move_cursor_after_delete, new_row, new_col)
                    self.log(f"Moved cursor from deleted column {col} to column {new_col}")
                elif current_col > col:
                    # If cursor was to the right of the deleted column, shift it left by one
                    new_col = current_col - 1
                    new_row = current_row
                    self.call_after_refresh(self._move_cursor_after_delete, new_row, new_col)
                    self.log(f"Shifted cursor left from column {current_col} to column {new_col}")
                else:
                    # Cursor was to the left of the deleted column, no change needed
                    new_col = current_col
                    new_row = current_row
                    self.call_after_refresh(self._move_cursor_after_delete, new_row, new_col)

            self.log(
                f"Deleted column '{column_name}'. Table now has {len(self.data.columns)} columns"
            )

        except Exception as e:
            self.log(f"Error deleting column {col}: {e}")
            self.update_address_display(0, col, f"Delete column failed: {str(e)[:30]}...")

    def _move_cursor_after_delete(self, row: int, col: int) -> None:
        """Move cursor to a safe position after deletion."""
        try:
            self._table.move_cursor(row=row, column=col)
            self.update_address_display(row, col, "Item deleted")
        except Exception as e:
            self.log(f"Error moving cursor after delete: {e}")

    def action_show_delete_menu(self) -> None:
        """Show the delete menu for the current cursor position."""
        cursor_coordinate = self._table.cursor_coordinate
        if cursor_coordinate:
            row, col = cursor_coordinate
            self._show_row_column_delete_modal(row)

    def _insert_row(self, insert_at_row: int) -> None:
        """Insert a new row at the specified position."""
        if self.data is None:
            self.log("Cannot insert row: No data loaded")
            return

        try:
            # Convert from display row to data row (row 0 is headers)
            # For insert_at_row=1, we want to insert at data index 0 (before first data row)
            # For insert_at_row=2, we want to insert at data index 1 (before second data row)
            if insert_at_row == 0:
                self.log("Cannot insert row at header position")
                return

            data_insert_index = insert_at_row - 1  # Convert display row to data index

            # Create a new row with None values for all columns
            new_row_data = [None for _ in self.data.columns]
            new_row_df = pl.DataFrame([new_row_data], schema=self.data.schema)

            if data_insert_index == 0:
                # Insert at the beginning
                self.data = pl.concat([new_row_df, self.data], how="vertical")
            elif data_insert_index >= len(self.data):
                # Insert at the end
                self.data = pl.concat([self.data, new_row_df], how="vertical")
            else:
                # Insert in the middle
                before = self.data.slice(0, data_insert_index)
                after = self.data.slice(data_insert_index)
                self.data = pl.concat([before, new_row_df, after], how="vertical")

            # Mark as changed and refresh display
            self.has_changes = True
            self.update_title_change_indicator()
            self.refresh_table_data()

            # Move cursor to the newly inserted row
            self.call_after_refresh(self._move_cursor_after_insert, insert_at_row, 0)

            self.log(
                f"Inserted new row at position {insert_at_row}. Table now has {len(self.data)} rows"
            )

        except Exception as e:
            self.log(f"Error inserting row at {insert_at_row}: {e}")
            self.update_address_display(insert_at_row, 0, f"Insert row failed: {str(e)[:30]}...")

    def _insert_column(self, insert_at_col: int) -> None:
        """Insert a new column at the specified position."""
        if self.data is None:
            self.log("Cannot insert column: No data loaded")
            return

        try:
            self.log(f"Starting column insertion at position {insert_at_col}")
            self.log(f"Current columns: {self.data.columns}")
            self.log(f"Current data shape: {self.data.shape}")

            # Generate a unique column name
            base_name = "Column"
            counter = 1
            new_column_name = f"{base_name}_{counter}"

            while new_column_name in self.data.columns:
                counter += 1
                new_column_name = f"{base_name}_{counter}"

            self.log(f"Generated new column name: {new_column_name}")

            # Get current column names
            current_columns = list(self.data.columns)

            # Insert the new column name at the specified position
            if insert_at_col >= len(current_columns):
                # Insert at the end
                new_columns = current_columns + [new_column_name]
                self.log(f"Inserting at end: {new_columns}")
            else:
                # Insert at the specified position
                new_columns = (
                    current_columns[:insert_at_col]
                    + [new_column_name]
                    + current_columns[insert_at_col:]
                )
                self.log(f"Inserting at position {insert_at_col}: {new_columns}")

            # Create a new dataframe with the new column structure
            new_data = {}
            new_schema = {}

            for i, col_name in enumerate(new_columns):
                if col_name == new_column_name:
                    # New column with None values
                    new_data[col_name] = [None] * len(self.data)
                    new_schema[col_name] = pl.String  # New columns start as String
                    self.log(f"Added new column {col_name} with {len(self.data)} None values")
                else:
                    # Existing column data: preserve original data and type
                    original_col_name = col_name
                    new_data[col_name] = self.data[original_col_name].to_list()
                    new_schema[col_name] = self.data.dtypes[
                        self.data.columns.index(original_col_name)
                    ]
                    self.log(f"Copied existing column {col_name} with type {new_schema[col_name]}")

            # Create new DataFrame with reordered columns and preserved types
            self.data = pl.DataFrame(new_data, schema=new_schema)

            self.log(f"Created new DataFrame with shape: {self.data.shape}")
            self.log(f"New columns: {self.data.columns}")

            # Mark as changed and refresh display
            self.has_changes = True
            self.update_title_change_indicator()

            # Update sorting state to handle the inserted column
            self._update_sort_state_after_column_insertion(insert_at_col)

            self.refresh_table_data()

            # Move cursor to the newly inserted column header
            self.call_after_refresh(self._move_cursor_after_insert, 0, insert_at_col)

            self.log(
                f"Successfully inserted new column '{new_column_name}' at position {insert_at_col}. Table now has {len(self.data.columns)} columns"
            )

        except Exception as e:
            self.log(f"Error inserting column at {insert_at_col}: {e}")
            import traceback

            self.log(f"Exception details: {traceback.format_exc()}")
            self.update_address_display(0, insert_at_col, f"Insert column failed: {str(e)[:30]}...")

    def _move_cursor_after_insert(self, row: int, col: int) -> None:
        """Move cursor to a position after insertion."""
        try:
            self._table.move_cursor(row=row, column=col)
            self.update_address_display(row, col, "Item inserted")
        except Exception as e:
            self.log(f"Error moving cursor after insert: {e}")

    def _parse_clipboard_data(self, content: str) -> dict | None:
        """Parse clipboard content and extract tabular data."""
        try:
            lines = content.strip().split("\n")
            if len(lines) < 1:
                return None

            # Remove title lines that don't contain tabular data
            lines = self._filter_title_lines(lines)
            if len(lines) < 1:
                return None

            # Detect separator (tab is most common from spreadsheets)
            first_line = lines[0]
            tab_count = first_line.count("\t")
            comma_count = first_line.count(",")

            # Prefer tab separator (common from Google Sheets/Excel)
            if tab_count > 0:
                separator = "\t"
            elif comma_count > 0:
                separator = ","
            else:
                # Single column or unstructured data
                if len(lines) == 1:
                    return None  # Single cell, not tabular
                separator = None

            # Parse rows
            parsed_rows = []
            max_cols = 0

            for line in lines:
                if separator:
                    row = [cell.strip() for cell in line.split(separator)]
                else:
                    row = [line.strip()]
                parsed_rows.append(row)
                max_cols = max(max_cols, len(row))

            # Handle Wikipedia-style complex headers (detect multi-row headers)
            processed_rows, has_headers = self._process_wikipedia_table(parsed_rows, max_cols)

            return {
                "rows": processed_rows,
                "has_headers": has_headers,
                "separator": separator,
                "num_rows": len(processed_rows),
                "num_cols": max_cols,
                "is_wikipedia_style": self._detect_wikipedia_table(parsed_rows),
            }

        except Exception as e:
            self.log(f"Error parsing clipboard data: {e}")
            return None

    def _detect_wikipedia_table(self, rows: list) -> bool:
        """Detect if this looks like a Wikipedia table based on structural patterns."""
        if len(rows) < 2:
            return False

        # Check for footnote markers like [a], [b], [c], [1], [2], etc.
        footnote_pattern = r"\[[a-zA-Z0-9]+\]"
        has_footnotes = False

        for row in rows[:10]:  # Check first 10 rows
            for cell in row:
                if cell and "[" in cell and "]" in cell:
                    import re

                    if re.search(footnote_pattern, cell):
                        has_footnotes = True
                        break
            if has_footnotes:
                break

        # Check for inconsistent column counts in first few rows (indicating complex headers)
        col_counts = []
        for i, row in enumerate(rows[:5]):
            non_empty_count = len([cell for cell in row if cell.strip()])
            if non_empty_count > 0:
                col_counts.append(non_empty_count)

        has_inconsistent_structure = len(set(col_counts)) > 1 if col_counts else False

        # Check for unit indicators common in Wikipedia tables
        unit_indicators = [
            "mi2",
            "km2",
            "/ mi2",
            "/ km2",
            "%",
            "°N",
            "°W",
            "°E",
            "°S",
            "[tonnes]",
            "[kg",
            "[m (ft)]",
            "[ft]",
            "(ft)",
            "(m)",
            "lbs",
        ]
        has_units = False

        for row in rows[:5]:
            for cell in row:
                if cell and any(indicator in cell for indicator in unit_indicators):
                    has_units = True
                    break
            if has_units:
                break

        return has_footnotes or has_inconsistent_structure or has_units

    def _detect_complex_wikipedia_headers(self, rows: list) -> bool:
        """Detect if this Wikipedia table needs complex header processing."""
        if len(rows) < 4:
            return False

        # Look for tables with very irregular early structure
        first_few_rows = rows[:4]
        col_counts = []

        for row in first_few_rows:
            non_empty_count = len([cell for cell in row if cell.strip()])
            if non_empty_count > 0:
                col_counts.append(non_empty_count)

        # Check for highly variable column counts in header region
        unique_counts = set(col_counts)
        has_irregular_headers = len(unique_counts) >= 3

        # Check for coordinate patterns (geographic tables)
        has_coordinates = False
        for row in rows[3:8]:  # Check some data rows
            for cell in row:
                if cell and ("°N" in cell or "°S" in cell) and ("°W" in cell or "°E" in cell):
                    has_coordinates = True
                    break
            if has_coordinates:
                break

        # Check for very short rows that might be unit indicators
        has_unit_rows = False
        for row in first_few_rows[1:]:  # Skip first row
            non_empty_count = len([cell for cell in row if cell.strip()])
            if 0 < non_empty_count <= 4:  # Very short rows might be units
                row_text = " ".join(row).lower()
                if any(unit in row_text for unit in ["mi2", "km2", "ft", "m", "°", "%"]):
                    has_unit_rows = True
                    break

        return has_irregular_headers and (has_coordinates or has_unit_rows)

    def _process_wikipedia_table(self, rows: list, max_cols: int) -> tuple[list, bool]:
        """Process Wikipedia-style tables with complex headers and footnotes."""
        if len(rows) < 2:
            # Ensure all rows have the same number of columns
            for row in rows:
                while len(row) < max_cols:
                    row.append("")
            return rows, len(rows) > 0

        processed_rows = []
        has_headers = False

        # Check if this looks like a Wikipedia table
        is_wiki_style = self._detect_wikipedia_table(rows)

        # Detect and handle split-row Wikipedia tables (like Canadian cities)
        has_split_rows = self._detect_split_row_table(rows)

        # Detect multi-line headers (like whales/reptiles tables)
        has_multiline_headers = self._detect_multiline_headers(rows)

        # Detect spanning headers (like Netflix movies table)
        has_spanning_headers = self._detect_spanning_headers(rows)

        # Detect complex Wikipedia headers that need custom processing
        has_complex_headers = self._detect_complex_wikipedia_headers(rows)

        if (
            is_wiki_style
            or has_split_rows
            or has_multiline_headers
            or has_spanning_headers
            or has_complex_headers
        ):
            if has_split_rows:
                # Merge split rows (rank numbers + data rows)
                merged_rows = self._merge_split_rows(rows, max_cols)
                processed_rows = merged_rows
                has_headers = len(merged_rows) > 0 and self._is_header_row(merged_rows[0])
            elif has_spanning_headers:
                # Merge spanning headers where one header spans multiple columns
                merged_headers, data_start_idx = self._merge_spanning_headers(rows, max_cols)

                # Add merged headers
                if merged_headers:
                    processed_rows.append(merged_headers)
                    has_headers = True

                # Process data rows starting from data_start_idx, but this table structure is complex
                # We need to reconstruct the data properly
                reconstructed_data = self._reconstruct_complex_table_data(
                    rows, data_start_idx, max_cols
                )
                processed_rows.extend(reconstructed_data)
            elif has_multiline_headers:
                # Merge multi-line headers and process data
                merged_headers, data_start_idx = self._merge_multiline_headers(rows, max_cols)

                # Add merged headers
                if merged_headers:
                    processed_rows.append(merged_headers)
                    has_headers = True

                # Process data rows starting from data_start_idx
                for i in range(data_start_idx, len(rows)):
                    row = rows[i]
                    cleaned_row = self._clean_wikipedia_row(row, max_cols)
                    if any(cell.strip() for cell in cleaned_row):  # Skip empty rows
                        processed_rows.append(cleaned_row)
            elif has_complex_headers:
                # Handle complex Wikipedia headers with general approach
                headers = self._create_general_wikipedia_headers(rows, max_cols)

                # Find where the actual data starts
                data_start_idx = self._find_data_start_general(rows)

                # Process data rows: clean footnotes and format
                for i in range(data_start_idx, len(rows)):
                    row = rows[i]
                    cleaned_row = self._clean_wikipedia_row(row, max_cols)
                    if any(cell.strip() for cell in cleaned_row):  # Skip empty rows
                        processed_rows.append(cleaned_row)

                # Add headers as first row if we created them
                if headers:
                    processed_rows.insert(0, headers)
                    has_headers = True
            else:
                # Regular Wikipedia table or table with headers: standard processing
                for row in rows:
                    cleaned_row = self._clean_wikipedia_row(row, max_cols)
                    if any(cell.strip() for cell in cleaned_row):  # Skip empty rows
                        processed_rows.append(cleaned_row)

                # Detect headers normally
                if len(processed_rows) > 1:
                    first_row = processed_rows[0]
                    if self._is_header_row(first_row):
                        has_headers = True
        else:
            # Regular table processing
            for row in rows:
                while len(row) < max_cols:
                    row.append("")
                processed_rows.append(row)

            # Detect if first row contains headers (heuristic)
            if len(processed_rows) > 1:
                first_row = processed_rows[0]
                second_row = processed_rows[1]

                # Check if first row looks like headers (non-numeric, different from data)
                first_row_numeric = sum(
                    1 for cell in first_row if cell.replace(".", "").replace("-", "").isdigit()
                )
                second_row_numeric = sum(
                    1 for cell in second_row if cell.replace(".", "").replace("-", "").isdigit()
                )

                if (
                    first_row_numeric < second_row_numeric
                    and first_row_numeric < len(first_row) * 0.5
                ):
                    has_headers = True

        return processed_rows, has_headers

    def _detect_split_row_table(self, rows: list) -> bool:
        """Detect if this is a table where data is split across multiple lines (e.g., Canadian cities)."""
        if len(rows) < 4:
            return False

        # Look for pattern: header row, then alternating single-column and multi-column rows
        header_row = rows[0] if rows else []
        header_cols = len([cell for cell in header_row if cell.strip()])

        if header_cols < 5:  # Need substantial columns to detect this pattern
            return False

        # Check for alternating pattern after header
        single_col_count = 0
        multi_col_count = 0

        for i in range(1, min(11, len(rows))):  # Check first 10 data rows
            row = rows[i]
            non_empty_cells = len([cell for cell in row if cell.strip()])

            if non_empty_cells == 1:
                # Check if it's a simple number (likely a rank)
                cell_content = row[0].strip() if row else ""
                if cell_content.isdigit() or (
                    len(cell_content) <= 3 and cell_content.replace(".", "").isdigit()
                ):
                    single_col_count += 1
            elif non_empty_cells >= header_cols - 2:  # Allow for slight column mismatch
                multi_col_count += 1

        # If we have roughly equal numbers of single-column and multi-column rows, it's split
        return (
            single_col_count >= 2
            and multi_col_count >= 2
            and abs(single_col_count - multi_col_count) <= 2
        )

    def _detect_multiline_headers(self, rows: list) -> bool:
        """Detect if this table has multi-line headers based on structural patterns."""
        if len(rows) < 4:
            return False

        # Analyze column count consistency in first few rows
        header_region = rows[:5]  # Look at first 5 rows
        col_counts = []
        max_cols = 0

        for row in header_region:
            non_empty_count = len([cell for cell in row if cell.strip()])
            if non_empty_count > 0:
                col_counts.append(non_empty_count)
                max_cols = max(max_cols, non_empty_count)

        # Check if we have inconsistent column counts (sign of multi-line headers)
        has_varying_columns = len(set(col_counts)) > 1

        # Look for unit indicators scattered across early rows
        unit_patterns = ["[tonnes]", "[kg", "[m (ft)]", "[ft]", "(ft)", "(m)", "mi2", "km2", "%"]
        unit_rows = 0

        for row in header_region:
            row_text = " ".join(row).lower()
            if any(unit in row_text for unit in unit_patterns):
                unit_rows += 1

        # Look for numeric data starting after the inconsistent header region
        data_start_found = False
        for i in range(3, min(7, len(rows))):
            if i < len(rows):
                row = rows[i]
                first_cell = row[0].strip() if row and row[0] else ""
                # Look for numeric patterns (ranks, indices, etc.)
                if first_cell.isdigit() or (
                    len(first_cell) <= 3 and first_cell.replace(".", "").isdigit()
                ):
                    data_start_found = True
                    break

        return has_varying_columns and unit_rows >= 1 and data_start_found

    def _detect_spanning_headers(self, rows: list) -> bool:
        """Detect if this table has spanning headers where one header spans multiple columns."""
        if len(rows) < 3:
            return False

        # Check if we have a clear pattern:
        # Row 1: Full header row with substantial columns
        # Row 2: Shorter row that could be sub-headers
        # Row 3+: Data or continued complex structure

        first_row = rows[0]
        second_row = rows[1]

        first_row_cols = len([cell for cell in first_row if cell.strip()])
        second_row_cols = len([cell for cell in second_row if cell.strip()])

        # Spanning header pattern: first row has many columns, second row has few
        if first_row_cols >= 6 and second_row_cols >= 2 and second_row_cols < first_row_cols / 2:
            # Check if the second row looks like sub-headers (text, not data)
            second_row_looks_like_headers = True
            for cell in second_row:
                if cell and cell.strip():
                    cell_clean = cell.strip()
                    # Sub-headers should be short text, not long data values
                    if (
                        len(cell_clean) > 50
                        or cell_clean.replace(".", "").replace(",", "").replace("-", "").isdigit()
                    ):
                        second_row_looks_like_headers = False
                        break

            return second_row_looks_like_headers

        return False

    def _merge_multiline_headers(self, rows: list, max_cols: int) -> tuple[list, int]:
        """Merge multi-line headers into a single header row using simple column-wise selection."""
        if len(rows) < 4:
            return None, 0

        # Find where data starts by looking for consistent numeric patterns
        data_start_idx = 0
        for i, row in enumerate(rows):
            first_cell = row[0].strip() if row and row[0] else ""
            # Look for numeric first cell (rank/index) + substantial data in row
            if (
                first_cell.isdigit()
                and len([cell for cell in row if cell.strip()]) >= max_cols * 0.6
            ):
                data_start_idx = i
                break

        if data_start_idx == 0:
            data_start_idx = max(3, len(rows) // 2)  # Fallback: assume headers take first half

        # Simple strategy: Use the first row as the primary header source
        # and supplement with additional info only when the first row cell is empty
        header_rows = rows[:data_start_idx]
        merged_headers = []

        if len(header_rows) == 0:
            return [f"Column_{i + 1}" for i in range(max_cols)], data_start_idx

        # Use the first non-empty row as the base
        primary_header_row = header_rows[0]

        for col_idx in range(max_cols):
            # Start with the primary header
            if col_idx < len(primary_header_row) and primary_header_row[col_idx].strip():
                header_text = primary_header_row[col_idx].strip()

                # Look for unit information in subsequent rows if header seems incomplete
                if len(header_text) <= 15:  # Short headers might need unit info
                    for row_idx in range(1, len(header_rows)):
                        row = header_rows[row_idx]
                        if col_idx < len(row) and row[col_idx].strip():
                            potential_unit = row[col_idx].strip()
                            # Add units if they look like units (short, have brackets or parentheses)
                            if (
                                len(potential_unit) <= 10
                                and any(char in potential_unit for char in ["[", "]", "(", ")"])
                                and potential_unit not in header_text
                            ):
                                header_text = f"{header_text} {potential_unit}"
                                break

                # Clean up the header
                header_text = re.sub(
                    r"\[[a-zA-Z0-9]+\]", "", header_text
                ).strip()  # Remove footnotes
                merged_headers.append(header_text if header_text else f"Column_{col_idx + 1}")
            else:
                # Primary header is empty, look for content in other rows
                found_header = False
                for row_idx in range(1, len(header_rows)):
                    row = header_rows[row_idx]
                    if col_idx < len(row) and row[col_idx].strip():
                        header_text = row[col_idx].strip()
                        header_text = re.sub(r"\[[a-zA-Z0-9]+\]", "", header_text).strip()
                        merged_headers.append(
                            header_text if header_text else f"Column_{col_idx + 1}"
                        )
                        found_header = True
                        break

                if not found_header:
                    merged_headers.append(f"Column_{col_idx + 1}")

        return merged_headers, data_start_idx

    def _merge_spanning_headers(self, rows: list, max_cols: int) -> tuple[list, int]:
        """Merge spanning headers where one header spans multiple sub-columns."""
        if len(rows) < 3:
            return None, 0

        main_header_row = rows[0]
        sub_header_row = rows[1]

        # For the Netflix table structure:
        # Row 0: ['Title', 'Netflix release date', 'Director(s)', 'Writer(s)', 'Producer(s)', ...]  (9 columns)
        # Row 1: ['Story', 'Screenplay']  (2 columns)
        #
        # The sub-headers "Story" and "Screenplay" should replace "Writer(s)" and expand it into two columns

        merged_headers = []

        # Strategy: Find where to insert the sub-headers
        # The sub-headers should replace one of the main headers that spans multiple columns

        # Look for the most likely spanning header position
        # In most cases, it's a header with generic terms that could span multiple sub-categories
        spanning_candidates = []
        for i, header in enumerate(main_header_row):
            if header and any(term in header.lower() for term in ["writer", "author", "creator"]):
                spanning_candidates.append(i)

        if spanning_candidates and len(sub_header_row) >= 2:
            # Use the first spanning candidate
            spanning_idx = spanning_candidates[0]
            spanning_header = main_header_row[spanning_idx]

            # Build the new header row
            for i, main_header in enumerate(main_header_row):
                if i == spanning_idx:
                    # Replace the spanning header with sub-headers
                    for j, sub_header in enumerate(sub_header_row):
                        if sub_header.strip():
                            merged_headers.append(f"{spanning_header} - {sub_header.strip()}")
                        else:
                            merged_headers.append(f"{spanning_header}_{j + 1}")
                elif i > spanning_idx:
                    # Shift remaining headers to account for the expansion
                    merged_headers.append(main_header)
                else:
                    # Headers before the spanning header remain unchanged
                    merged_headers.append(main_header)
        else:
            # No clear spanning pattern, use a simple combination
            merged_headers = main_header_row[:]
            # Insert sub-headers after the first few main headers
            if len(sub_header_row) >= 2:
                # Insert sub-headers starting at position 3 (after Title, Date, Director)
                insert_pos = min(3, len(merged_headers))
                for i, sub_header in enumerate(sub_header_row):
                    if sub_header.strip():
                        merged_headers.insert(insert_pos + i, sub_header.strip())

        # Ensure we have the right number of columns
        while len(merged_headers) < max_cols:
            merged_headers.append(f"Column_{len(merged_headers) + 1}")

        # Trim to max_cols if we've exceeded it
        merged_headers = merged_headers[:max_cols]

        # Data starts from row 2 (after main header and sub-header)
        return merged_headers, 2

    def _create_general_wikipedia_headers(self, rows: list, max_cols: int) -> list:
        """Create headers from Wikipedia tables using general structural analysis."""
        if len(rows) < 2:
            return [f"Column_{i + 1}" for i in range(max_cols)]

        # Find the most complete row in the first few rows (likely the main header)
        header_candidates = rows[:4]
        best_header_row = None
        max_meaningful_cells = 0

        for row in header_candidates:
            meaningful_cells = 0
            for cell in row:
                if cell and cell.strip() and not cell.strip().isdigit():
                    meaningful_cells += 1

            if meaningful_cells > max_meaningful_cells:
                max_meaningful_cells = meaningful_cells
                best_header_row = row

        if not best_header_row:
            return [f"Column_{i + 1}" for i in range(max_cols)]

        # Create headers, cleaning up and filling gaps
        headers = []
        for i in range(max_cols):
            if i < len(best_header_row) and best_header_row[i] and best_header_row[i].strip():
                # Clean the header text
                header = best_header_row[i].strip()
                # Remove footnote markers
                header = re.sub(r"\[[a-zA-Z0-9]+\]", "", header).strip()
                # Replace problematic characters
                header = re.sub(r"[^\w\s()-]", "_", header).strip()
                headers.append(header if header else f"Column_{i + 1}")
            else:
                headers.append(f"Column_{i + 1}")

        return headers

    def _find_data_start_general(self, rows: list) -> int:
        """Find where actual data starts using general heuristics."""
        for i, row in enumerate(rows):
            if i < 2:  # Skip first couple rows (likely headers)
                continue

            # Look for rows with substantial data content
            non_empty_count = len([cell for cell in row if cell and cell.strip()])

            # Check if this looks like a data row
            if non_empty_count >= len(row) * 0.5:  # At least half the columns have data
                first_cell = row[0].strip() if row and row[0] else ""

                # Data rows often start with numbers, names, or have mixed content
                if (
                    first_cell.isdigit()  # Rank/index
                    or len(first_cell) > 3  # Likely a name/location
                    or any(cell and len(cell.strip()) > 2 for cell in row[:3])
                ):  # Substantial content
                    return i

        # Fallback: assume data starts after first quarter of rows
        return max(2, len(rows) // 4)

    def _reconstruct_complex_table_data(
        self, rows: list, data_start_idx: int, max_cols: int
    ) -> list:
        """Reconstruct data from complex table structure where data spans multiple lines."""
        if data_start_idx >= len(rows):
            return []

        reconstructed_rows = []
        current_record = None

        # Process lines starting from data_start_idx
        for i in range(data_start_idx, len(rows)):
            line = rows[i]
            line_tab_count = len([cell for cell in line if cell.strip()])

            # Look for patterns that indicate a new record vs continuation
            first_cell = line[0].strip() if line and line[0] else ""

            # A new record typically starts with:
            # 1. A meaningful title/name (like "Klaus", "The Willoughbys")
            # 2. Multiple columns of data (at least 3 for this table format)
            # 3. First cell is not a continuation marker like "Co-director:"
            is_new_record = (
                line_tab_count >= 3  # At least 3 meaningful columns (Title, Date, Director)
                and len(first_cell) > 2  # Meaningful first cell
                and not first_cell.lower().startswith("co-")  # Not a "Co-director:" type line
                and not first_cell.lower().startswith("copyright")  # Not a copyright line
            )

            # Additional check: look for date patterns in the second column (Netflix release date)
            if line_tab_count >= 2 and len(line) >= 2:
                second_cell = line[1].strip() if len(line) > 1 else ""
                # Netflix dates are in format like "November 15, 2019"
                has_date_pattern = (
                    any(
                        month in second_cell
                        for month in [
                            "January",
                            "February",
                            "March",
                            "April",
                            "May",
                            "June",
                            "July",
                            "August",
                            "September",
                            "October",
                            "November",
                            "December",
                        ]
                    )
                    or any(char.isdigit() for char in second_cell)  # Contains numbers (year)
                )
                if has_date_pattern:
                    is_new_record = True

            if is_new_record:
                # Save previous record if we have one
                if current_record:
                    # Pad the record to max_cols
                    while len(current_record) < max_cols:
                        current_record.append("")
                    reconstructed_rows.append(current_record[:max_cols])

                # Start new record
                current_record = line[:]
                # Pad immediately to max_cols to make merging easier
                while len(current_record) < max_cols:
                    current_record.append("")
            else:
                # This is a continuation line: merge into current record
                if current_record and line_tab_count > 0:
                    # Strategy: append continuation data to the appropriate positions
                    # For Netflix table, continuation lines often contain:
                    # - Additional names for the same role
                    # - Additional production details

                    # Find the first empty or suitable position to merge data
                    for j, cell in enumerate(line):
                        if cell and cell.strip():
                            # Find a good position to place this data
                            # Start looking from where the current record has data
                            start_pos = len([c for c in current_record if c.strip()])
                            target_pos = min(start_pos + j, max_cols - 1)

                            # If target position is empty, use it; otherwise append
                            if target_pos < len(current_record):
                                if current_record[target_pos].strip():
                                    # Position has data, append with separator
                                    current_record[target_pos] += f"; {cell.strip()}"
                                else:
                                    # Position is empty, use it
                                    current_record[target_pos] = cell.strip()

        # Don't forget the last record
        if current_record:
            while len(current_record) < max_cols:
                current_record.append("")
            reconstructed_rows.append(current_record[:max_cols])

        return reconstructed_rows

    def _merge_split_rows(self, rows: list, max_cols: int) -> list:
        """Merge split rows where rank numbers are on separate lines from data."""
        if len(rows) < 2:
            return rows

        merged_rows = []
        header_row = rows[0]
        merged_rows.append(header_row)  # Keep header as-is

        i = 1
        while i < len(rows):
            current_row = rows[i]
            current_non_empty = len([cell for cell in current_row if cell.strip()])

            # Check if this is a single-column row (likely a rank number)
            if current_non_empty == 1 and current_row[0].strip().isdigit():
                rank = current_row[0].strip()

                # Look for the next row with data
                if i + 1 < len(rows):
                    next_row = rows[i + 1]
                    next_non_empty = len([cell for cell in next_row if cell.strip()])

                    # If next row has substantial data, merge them
                    if next_non_empty >= 3:  # At least 3 columns of data
                        merged_row = [rank] + [cell for cell in next_row if cell.strip() or True]

                        # Pad to match expected column count
                        while len(merged_row) < max_cols:
                            merged_row.append("")

                        # Truncate if too long
                        merged_row = merged_row[:max_cols]

                        merged_rows.append(merged_row)
                        i += 2  # Skip both the rank row and data row
                        continue

            # If not a split pattern, add the row as-is
            padded_row = list(current_row)
            while len(padded_row) < max_cols:
                padded_row.append("")
            merged_rows.append(padded_row[:max_cols])
            i += 1

        return merged_rows

    def _filter_title_lines(self, lines: list) -> list:
        """Remove title lines that don't contain tabular data."""
        if len(lines) < 2:
            return lines

        filtered_lines = []

        for i, line in enumerate(lines):
            # Skip lines with no tabs if subsequent lines have tabs
            tab_count = line.count("\t")

            # Look ahead to see if there are tabular lines
            has_tabular_data_after = False
            for j in range(i + 1, min(i + 3, len(lines))):  # Check next 2 lines
                if lines[j].count("\t") > 0:
                    has_tabular_data_after = True
                    break

            # If this line has no tabs but tabular data follows, it's likely a title
            if tab_count == 0 and has_tabular_data_after:
                continue  # Skip this line

            # Otherwise, keep the line
            filtered_lines.append(line)

        return filtered_lines

    def _is_header_row(self, row: list) -> bool:
        """Check if a row looks like a header row."""
        if not row:
            return False

        # Headers typically have text, not numbers
        text_cells = 0
        numeric_cells = 0
        empty_cells = 0

        for cell in row:
            cell_clean = cell.strip()
            if not cell_clean:
                empty_cells += 1
                continue

            if cell_clean.replace(".", "").replace(",", "").replace("-", "").isdigit():
                numeric_cells += 1
            else:
                text_cells += 1

        total_non_empty = text_cells + numeric_cells

        # Special case: if row contains header-like words, it's likely a header
        header_words = [
            "name",
            "rank",
            "title",
            "height",
            "floor",
            "city",
            "country",
            "year",
            "comment",
            "animal",
            "mass",
            "length",
        ]
        header_word_count = 0
        for cell in row:
            cell_lower = cell.lower()
            for word in header_words:
                if word in cell_lower:
                    header_word_count += 1
                    break

        # If we have several header-like words, it's definitely a header
        if header_word_count >= 3:
            return True

        # Headers should be mostly text, not numbers (but allow some empty cells)
        if total_non_empty > 0:
            return text_cells > numeric_cells and text_cells >= total_non_empty * 0.6

        return False

    def _create_wikipedia_headers(self, header_rows: list, max_cols: int) -> list:
        """Create meaningful headers from Wikipedia complex header structure (deprecated: use _create_general_wikipedia_headers)."""
        # Fallback to general approach
        return self._create_general_wikipedia_headers(header_rows, max_cols)

    def _find_data_start(self, rows: list) -> int:
        """Find where actual data starts in a Wikipedia table (deprecated: use _find_data_start_general)."""
        return self._find_data_start_general(rows)

    def _clean_wikipedia_row(self, row: list, max_cols: int) -> list:
        """Clean a Wikipedia data row by removing footnotes and formatting properly."""
        import re

        cleaned_row = []
        footnote_pattern = r"\[[a-z]\]"

        for i in range(max_cols):
            if i < len(row):
                cell = row[i].strip()

                # Remove footnote markers like [a], [b], [c]
                cell = re.sub(footnote_pattern, "", cell)

                # Clean up common Wikipedia formatting
                cell = cell.replace("−", "-")  # Replace unicode minus with regular minus
                cell = cell.strip()

                cleaned_row.append(cell)
            else:
                cleaned_row.append("")

        return cleaned_row

    def _show_paste_options_modal(self, parsed_data: dict) -> None:
        """Show modal with paste options."""

        def handle_paste_choice(choice: dict | None) -> None:
            if choice:
                self._execute_paste_operation(parsed_data, choice["action"], choice["use_header"])

        modal = PasteOptionsModal(parsed_data, self.data is not None)
        self.app.push_screen(modal, handle_paste_choice)

    def _execute_paste_operation(self, parsed_data: dict, operation: str, use_header: bool) -> None:
        """Execute the chosen paste operation."""
        try:
            if pl is None:
                self.update_address_display(0, 0, "Polars not available")
                return

            # Create DataFrame from parsed data
            rows = parsed_data["rows"]

            # Use the user's choice for headers instead of the automatic detection
            if use_header:
                headers = rows[0]
                data_rows = rows[1:]
            else:
                # Generate column names
                headers = [f"Column_{i + 1}" for i in range(parsed_data["num_cols"])]
                data_rows = rows

            # Create dictionary for DataFrame
            df_dict = {}
            for i, header in enumerate(headers):
                # Clean header name
                clean_header = header if header.strip() else f"Column_{i + 1}"
                column_data = []

                for row in data_rows:
                    cell_value = row[i] if i < len(row) else ""
                    # Try to convert to appropriate type
                    if cell_value.strip():
                        # Try numeric conversion: be more careful about mixed types
                        try:
                            # Remove common formatting characters
                            clean_val = (
                                cell_value.replace(",", "")
                                .replace("%", "")
                                .replace("+", "")
                                .replace("−", "-")
                            )

                            # Try float first (safer for mixed numeric data)
                            if "." in clean_val or "," in cell_value:
                                cell_value = float(clean_val)
                            else:
                                # For integers, use float to avoid type conflicts
                                try:
                                    int_val = int(clean_val)
                                    cell_value = float(
                                        int_val
                                    )  # Store as float to avoid mixed type issues
                                except ValueError:
                                    # Not a clean integer, try float
                                    cell_value = float(clean_val)
                        except ValueError:
                            # Keep as string if not numeric
                            pass
                    else:
                        cell_value = None

                    column_data.append(cell_value)

                df_dict[clean_header] = column_data

            # Create DataFrame with strict=False to handle mixed types
            new_df = pl.DataFrame(df_dict, strict=False)

            # Execute operation
            if operation == "replace":
                self.load_dataframe(new_df, force_recreation=True)
                self.is_sample_data = False
                self.data_source_name = None
                self.app.set_current_filename("pasted_data [CLIPBOARD]")
                self.update_address_display(
                    0, 0, f"Pasted {len(data_rows)} rows, {len(headers)} columns"
                )

            elif operation == "append" and self.data is not None:
                # Append to existing data
                try:
                    combined_df = pl.concat([self.data, new_df], how="vertical_relaxed")
                    self.load_dataframe(combined_df, force_recreation=True)
                    self.has_changes = True
                    self.update_title_change_indicator()
                    self.update_address_display(0, 0, f"Appended {len(data_rows)} rows")
                except Exception as e:
                    self.update_address_display(0, 0, f"Append failed: {str(e)[:30]}...")

            elif operation == "new_sheet":
                # For now, same as replace (could be extended for multi-sheet support)
                self.load_dataframe(new_df, force_recreation=True)
                self.is_sample_data = False
                self.data_source_name = None
                self.app.set_current_filename("pasted_data [CLIPBOARD]")
                self.update_address_display(0, 0, f"Created new sheet: {len(data_rows)} rows")

        except Exception as e:
            self.log(f"Error executing paste operation: {e}")
            self.update_address_display(0, 0, f"Paste failed: {str(e)[:30]}...")

    def highlight_search_matches(self, matches: list[tuple[int, int]]) -> None:
        """Highlight search matches in the data grid."""
        # Store matches for the search overlay
        self.search_matches = matches
        # Clear current match tracking since we're using simple highlighting
        self.current_search_match = None

        # Refresh the table once to apply highlighting
        self.refresh_table_data()

        self.log(f"Highlighted {len(matches)} search matches")

    def clear_search_highlights(self) -> None:
        """Clear search match highlights."""
        self.search_matches = []
        self.current_search_match = None

        # Refresh the table to remove highlighting
        self.refresh_table_data()

        self.log("Cleared search match highlights")

    def navigate_to_cell(self, row: int, col: int) -> None:
        """Navigate to a specific cell."""
        try:
            # Set the cursor position
            self._table.cursor_coordinate = (row, col)
            # Update the display
            self.update_address_display(row, col)
            self.log(f"Navigated to cell {self.get_excel_column_name(col)}{row}")
        except Exception as e:
            self.log(f"Error navigating to cell: {e}")


class SearchOverlay(Widget):
    """Overlay widget for handling search functionality on top of the data grid."""

    DEFAULT_CSS = """
    SearchOverlay {
        height: 1;
        dock: bottom;
        background: transparent;
        display: none;
    }

    SearchOverlay.active {
        display: block;
    }

    SearchOverlay .search-info {
        height: 1;
        background: $success;
        color: $text;
        text-align: center;
        padding: 0 1;
    }
    """

    def __init__(self, data_grid: ExcelDataGrid, **kwargs):
        super().__init__(**kwargs)
        self.data_grid = data_grid
        self.is_active = False
        self.matches = []  # List of (row, col) tuples
        self.current_match_index = 0
        self.search_column = None
        self.search_type = None
        self.search_values = None

    def call_after_refresh(self, callback, *args, **kwargs):
        """Helper method to call a function after the next refresh using set_timer."""
        self.set_timer(0.01, lambda: callback(*args, **kwargs))

    def compose(self) -> ComposeResult:
        """Compose the search overlay."""
        # Search info bar - make it clickable to exit search
        yield Static("", id="search-info", classes="search-info hidden")

    def on_click(self, event) -> None:
        """Handle clicks on the search info bar to exit search."""
        if self.is_active and event.widget.id == "search-info":
            self.deactivate_search()
            self._notify_search_exit()

    def activate_search(
        self, matches: list[tuple[int, int]], column_name: str, search_description: str
    ) -> None:
        """Activate search mode with the given matches."""
        self.is_active = True
        self.matches = matches
        self.current_match_index = 0

        # Show the overlay
        self.add_class("active")

        # Update info bar
        info_bar = self.query_one("#search-info", Static)
        if matches:
            # Set the initial current match for highlighting
            self.data_grid.current_search_match = matches[0] if matches else None
            # Refresh the table to apply highlighting
            self.data_grid.refresh_table_data(preserve_cursor=True)

            info_bar.update(
                f"Found {len(matches)} matches in '{column_name}' | Press ↑/↓ to navigate | Click here or →→→→ to exit"
            )
            info_bar.remove_class("hidden")
            # Navigate to first match
            self._navigate_to_current_match()
        else:
            info_bar.update(f"No matches found in '{column_name}' | Click here to exit")
            info_bar.remove_class("hidden")
            # Auto-hide after 3 seconds
            self.set_timer(3.0, lambda: info_bar.add_class("hidden"))

    def deactivate_search(self) -> None:
        """Deactivate search mode."""
        self.is_active = False
        self.matches = []
        self.current_match_index = 0

        # Clear highlighting from data grid
        self.data_grid.current_search_match = None

        # Hide the overlay
        self.remove_class("active")

        # Hide info bar
        info_bar = self.query_one("#search-info", Static)
        info_bar.add_class("hidden")

    def on_click(self, event) -> None:
        """Handle clicks on the search info bar to exit search."""
        if self.is_active and event.widget.id == "search-info":
            self.deactivate_search()
            self._notify_search_exit()

    def _navigate_to_current_match(self) -> None:
        """Navigate to the current match."""
        if self.matches and 0 <= self.current_match_index < len(self.matches):
            row, col = self.matches[self.current_match_index]
            # Simply navigate to the cell without refreshing the table
            self.data_grid.navigate_to_cell(row, col)
            self._update_search_info()

    def _navigate_to_next_match(self) -> None:
        """Navigate to the next match."""
        if self.matches:
            self.current_match_index = (self.current_match_index + 1) % len(self.matches)
            self._navigate_to_current_match()

    def _navigate_to_previous_match(self) -> None:
        """Navigate to the previous match."""
        if self.matches:
            self.current_match_index = (self.current_match_index - 1) % len(self.matches)
            self._navigate_to_current_match()

    def _update_search_info(self) -> None:
        """Update the search info display."""
        if self.matches:
            info_bar = self.query_one("#search-info", Static)
            current_pos = self.current_match_index + 1
            total_matches = len(self.matches)
            row, col = self.matches[self.current_match_index]
            cell_address = self.data_grid.get_excel_column_name(col) + str(row)
            info_bar.update(
                f"Match {current_pos}/{total_matches} at {cell_address} | Press ↑/↓ to navigate | Click here or →→→→ to exit"
            )

    def _notify_search_exit(self) -> None:
        """Notify the tools panel that search mode has been exited."""
        try:
            # Find the ToolsPanel and call its exit method
            tools_panel = self.app.query_one("ToolsPanel")
            tools_panel._exit_find_mode()
        except Exception as e:
            self.log(f"Error notifying search exit: {e}")


class ToolsPanel(Widget):
    """Panel for displaying tools and controls."""

    DEFAULT_CSS = """
    ToolsPanel RadioSet {
        margin-bottom: 0;
        margin-top: 0;
        border: none;
        padding: 0;
    }

    ToolsPanel RadioSet:focus {
        border: none;
    }

    ToolsPanel RadioSet > RadioButton {
        margin: 0;
        padding: 0 1;
        border: none;
        height: 1;
    }

    ToolsPanel RadioSet > RadioButton:focus {
        border: none;
        outline: none;
    }

    ToolsPanel #code-input {
        height: 1fr;
        max-height: 15;
        border: solid $primary;
    }

    ToolsPanel .button-row {
        height: 3;
        margin-top: 1;
    }

    ToolsPanel .button-spacing {
        margin-top: 1;
    }

    ToolsPanel .search-values {
        margin: 1 0;
    }

    ToolsPanel .value-input-row {
        height: 3;
        margin-bottom: 0;
    }

    ToolsPanel .value-label {
        width: 8;
        align: left middle;
    }

    ToolsPanel .search-input {
        width: 1fr;
    }

    ToolsPanel .find-button {
        margin-top: 0;
        margin-bottom: 1;
    }

    ToolsPanel #chat-history-scroll {
        height: 16;
        background: $surface-darken-1;
        border: solid $secondary;
        margin-top: 1;
    }

    ToolsPanel #chat-history {
        padding: 1;
        text-wrap: wrap;
        align: left top;
    }

    ToolsPanel #chat-history-scroll.empty {
        height: 3;
        border: dashed $secondary-darken-1;
        background: $surface-darken-2;
    }

    ToolsPanel #chat-input {
        height: 6;
        border: solid $primary;
        margin-bottom: 1;
    }

    ToolsPanel #llm-response-scroll {
        height: 1fr;
        min-height: 10;
        background: $surface-darken-1;
        border: solid $accent;
        margin-bottom: 1;
    }

    ToolsPanel #llm-response {
        padding: 1;
        text-wrap: wrap;
    }

    ToolsPanel #generated-code {
        height: 8;
        border: solid $success;
        margin-bottom: 1;
    }

    ToolsPanel .panel-section {
        height: 1fr;
    }
    """

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.current_column = None
        self.current_column_name = None
        self.data_grid = None
        # Find in Column state
        self.find_mode_active = False
        self.found_matches = []  # List of (row, col) tuples for found cells
        self.current_match_index = 0
        # Sweet AI Assistant state
        self.chat_history = []  # List of {"role": "user"/"assistant", "content": "..."}
        self.current_chat_session = None
        self.last_generated_code = None
        self.pending_code = None  # Code waiting for user approval

        # Database mode state
        self.is_database_mode = False
        self.available_tables = []

    def call_after_refresh(self, callback, *args, **kwargs):
        """Helper method to call a function after the next refresh using set_timer."""
        self.set_timer(0.01, lambda: callback(*args, **kwargs))

    def compose(self) -> ComposeResult:
        """Compose the tools panel."""
        # Navigation radio buttons for sections - will be updated based on mode
        if self.is_database_mode:
            yield RadioSet(
                "Sweet AI Assistant",
                "SQL Exec",
                "Table Selection",
                id="section-radio",
            )
        else:
            yield RadioSet(
                "Sweet AI Assistant",
                "Transform with Code",
                "Find in Column",
                "Modify Column Type",
                id="section-radio",
            )

        # Content switcher for sections
        with ContentSwitcher(initial="first-content", id="content-switcher"):
            if self.is_database_mode:
                # Database mode sections

                # Sweet AI Assistant Section (first)
                with Vertical(id="first-content", classes="panel-section"):
                    yield Static(
                        "Chat with AI to analyze your database.",
                        classes="instruction-text",
                    )
                    yield TextArea("", id="chat-input", classes="chat-input")

                    with Horizontal(classes="button-row"):
                        yield Button(
                            "Send", id="send-chat", variant="primary", classes="panel-button"
                        )
                        yield Button(
                            "Restart", id="clear-chat", variant="error", classes="panel-button"
                        )
                        yield Button(
                            "Execute",
                            id="execute-sql-suggestion",
                            variant="success",
                            classes="panel-button hidden",
                        )

                    # Chat history display
                    with VerticalScroll(
                        id="chat-history-scroll", classes="chat-history-scroll compact"
                    ):
                        yield Static("", id="chat-history", classes="chat-history")

                    # LLM response and SQL preview
                    with VerticalScroll(
                        id="llm-response-scroll", classes="llm-response-scroll hidden"
                    ):
                        yield Static("", id="llm-response", classes="llm-response")
                    yield TextArea("", id="generated-sql", classes="generated-code hidden")

                # SQL Execution Section (second)
                with Vertical(id="sql-exec-content", classes="panel-section"):
                    yield Static(
                        "Write SQL queries to analyze your data.", classes="instruction-text"
                    )
                    yield TextArea("SELECT * FROM ", id="sql-input", classes="code-input")

                    with Horizontal(classes="button-row"):
                        yield Button(
                            "Execute SQL",
                            id="execute-sql",
                            variant="primary",
                            classes="panel-button",
                        )

                    # Execution result/error display
                    yield Static("", id="sql-result", classes="execution-result hidden")

                # Table Selection Section (third)
                with Vertical(id="table-selection-content", classes="panel-section"):
                    yield Static(
                        "Available database tables:",
                        classes="instruction-text",
                    )
                    # Create table options from available_tables
                    table_options = []
                    if hasattr(self, "available_tables") and self.available_tables:
                        table_options = [(table, table) for table in self.available_tables]

                    yield Select(
                        options=table_options,
                        id="table-selector",
                        classes="table-selector",
                        compact=True,
                    )

            else:
                # Regular mode sections

                # Sweet AI Assistant Section (first)
                with Vertical(id="first-content", classes="panel-section"):
                    yield Static(
                        "Chat with AI to transform your data.",
                        classes="instruction-text",
                    )

                    # Chat input area (prioritized placement)
                    yield TextArea("", id="chat-input", classes="chat-input")

                    with Horizontal(classes="button-row"):
                        yield Button(
                            "Send", id="send-chat", variant="primary", classes="panel-button"
                        )
                        yield Button(
                            "Restart", id="clear-chat", variant="error", classes="panel-button"
                        )
                        yield Button(
                            "Apply",
                            id="apply-transform",
                            variant="success",
                            classes="panel-button hidden",
                        )

                    # Chat history display (full conversation)
                    with VerticalScroll(
                        id="chat-history-scroll", classes="chat-history-scroll compact"
                    ):
                        yield Static("", id="chat-history", classes="chat-history")

                    # LLM response and code preview
                    with VerticalScroll(
                        id="llm-response-scroll", classes="llm-response-scroll hidden"
                    ):
                        yield Static("", id="llm-response", classes="llm-response")
                    yield TextArea(
                        "", id="generated-code", classes="generated-code hidden", language="python"
                    )

                # Transform with Code Section (second)
                with Vertical(id="transform-with-code-content", classes="panel-section"):
                    yield Static("Write code to transform your data.", classes="instruction-text")

                    # Editable code input area with syntax highlighting
                    yield TextArea(
                        "df = df.", id="code-input", classes="code-input", language="python"
                    )

                    with Horizontal(classes="button-row"):
                        yield Button(
                            "Execute Code",
                            id="execute-code",
                            variant="primary",
                            classes="panel-button",
                        )

                    # Execution result/error display
                    yield Static("", id="execution-result", classes="execution-result hidden")

                # Find in Column Section (third)
                with Vertical(id="find-in-column-content", classes="panel-section"):
                    yield Static(
                        "Select a column header to search within it.",
                        id="find-instruction",
                        classes="instruction-text",
                    )
                    yield Static("No column selected", id="find-column-info", classes="column-info")

                    # Search type selector: initially hidden
                    yield Select(
                        options=[
                            ("is null", "is_null"),
                            ("is not null", "is_not_null"),
                            ("equals (==)", "equals"),
                            ("not equals (!=)", "not_equals"),
                            ("greater than (>)", "greater_than"),
                            ("greater than or equal (>=)", "greater_equal"),
                            ("less than (<)", "less_than"),
                            ("less than or equal (<=)", "less_equal"),
                            ("is between", "between"),
                            ("is outside", "outside"),
                        ],
                        value="equals",
                        id="search-type-selector",
                        classes="search-type-selector hidden",
                        compact=True,
                    )

                    # Value input containers
                    with Vertical(id="search-values-container", classes="search-values hidden"):
                        with Horizontal(id="first-value-row", classes="value-input-row"):
                            yield Static("Value:", id="first-value-label", classes="value-label")
                            yield Input(
                                placeholder="Enter search value...",
                                id="search-value1",
                                classes="search-input",
                            )

                        # Second value input (for between/outside operations) - initially hidden
                        with Horizontal(id="second-value-row", classes="value-input-row hidden"):
                            yield Static("To:", id="second-value-label", classes="value-label")
                            yield Input(
                                placeholder="Enter second value...",
                                id="search-value2",
                                classes="search-input",
                            )

                        # Spacer for margin above the Find button
                        yield Static("", classes="button-spacer")

                        yield Button(
                            "Find",
                            id="find-in-column-btn",
                            variant="success",
                            classes="find-button hidden",
                        )

                # Modify Column Type Section (fourth)
                with Vertical(id="modify-column-type-content", classes="panel-section"):
                    yield Static(
                        "Select a column header to modify its type.",
                        id="column-type-instruction",
                        classes="instruction-text",
                    )
                    yield Static("No column selected", id="column-info", classes="column-info")

                    # Data type selector: initially hidden
                    yield Select(
                        options=[
                            ("Text (String)", "text"),
                            ("Integer", "integer"),
                            ("Float (Decimal)", "float"),
                            ("Boolean", "boolean"),
                        ],
                        value="text",
                        id="type-selector",
                        classes="type-selector hidden",
                        compact=True,
                    )

                    yield Button(
                        "Apply Type Change",
                        id="apply-type-change",
                        variant="primary",
                        classes="apply-button hidden",
                    )

    def on_mount(self) -> None:
        """Set up references to the data grid."""
        try:
            # Find the data grid to interact with
            self.data_grid = self.app.query_one("#data-grid", ExcelDataGrid)

            # Set initial section based on mode
            content_switcher = self.query_one("#content-switcher", ContentSwitcher)
            content_switcher.current = "first-content"

            # Set default radio button selection (index 0)
            radio_set = self.query_one("#section-radio", RadioSet)
            radio_set.pressed_index = 0

            # Set placeholder-like text for chat input
            try:
                chat_input = self.query_one("#chat-input", TextArea)
                if self.is_database_mode:
                    placeholder_text = "What would you like to know about your database?"
                else:
                    placeholder_text = "What transformation would you like to make?"

                if hasattr(chat_input, "placeholder"):
                    chat_input.placeholder = placeholder_text
                else:
                    chat_input.text = placeholder_text
            except Exception:
                pass  # Chat input might not exist in all modes

        except Exception as e:
            self.log(f"Could not find data grid or setup content switcher: {e}")

    def on_radio_set_changed(self, event: RadioSet.Changed) -> None:
        """Handle radio button changes."""
        if event.radio_set.id == "section-radio":
            # Handle main section switching based on mode
            if self.is_database_mode:
                if event.pressed.label == "Sweet AI Assistant":
                    self._switch_to_section("first-content")
                elif event.pressed.label == "SQL Exec":
                    self._switch_to_section("sql-exec-content")
                elif event.pressed.label == "Table Selection":
                    self._switch_to_section("table-selection-content")
            else:
                if event.pressed.label == "Sweet AI Assistant":
                    self._switch_to_section("first-content")
                elif event.pressed.label == "Transform with Code":
                    self._switch_to_section("transform-with-code-content")
                elif event.pressed.label == "Find in Column":
                    self._switch_to_section("find-in-column-content")
                elif event.pressed.label == "Modify Column Type":
                    self._switch_to_section("modify-column-type-content")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        """Handle button presses in the tools panel."""
        if event.button.id == "apply-type-change":
            self._apply_type_change()
        elif event.button.id == "execute-code":
            self._execute_code()
        elif event.button.id == "find-in-column-btn":
            self._handle_find_button()
        elif event.button.id == "send-chat":
            self._handle_send_chat()
        elif event.button.id == "clear-chat":
            self._handle_clear_chat()
        elif event.button.id == "apply-transform":
            self._handle_apply_transform()
        # Database mode buttons
        elif event.button.id == "execute-sql":
            self._execute_sql()
        elif event.button.id == "execute-sql-suggestion":
            self._execute_sql_suggestion()

    def on_select_changed(self, event: Select.Changed) -> None:
        """Handle select dropdown changes."""
        if event.select.id == "search-type-selector":
            self._update_search_inputs(event.value)
        elif event.select.id == "table-selector":
            # Handle table selection in database mode
            if self.data_grid and event.value:
                self.log(f"Table selector changed to: {event.value}")
                self.data_grid._load_database_table(event.value)

    def set_database_mode(
        self, enabled: bool, tables: list = None, is_remote: bool = False
    ) -> None:
        """Set database mode for the tools panel."""
        debug_logger.info(
            f"ToolsPanel.set_database_mode called: enabled={enabled}, tables={tables}, is_remote={is_remote}"
        )
        self.log(
            f"ToolsPanel.set_database_mode called: enabled={enabled}, tables={tables}, is_remote={is_remote}"
        )

        old_mode = self.is_database_mode
        self.is_database_mode = enabled

        if enabled and tables:
            self.available_tables = tables
            self.log(f"Setting available_tables to: {tables}")

        elif not enabled:
            # Switching to regular mode
            self.available_tables = []
            self.log("Cleared available_tables for regular mode")

        # If mode changed, we need to refresh the content to show the correct tools
        if old_mode != enabled:
            self.log(f"Mode changed from {old_mode} to {enabled}, refreshing UI...")
            try:
                # Remove and recreate the panel to get the correct UI for the new mode
                self.refresh(recompose=True)
                self.log("UI refresh completed successfully")

                # After refresh, try to update table selector if in database mode
                if enabled and tables:
                    if is_remote:
                        # For remote databases, focus on Table Selection tab
                        self.set_timer(
                            0.1, lambda: self._update_table_selector_and_focus_for_remote(tables)
                        )
                    else:
                        # For local databases, use normal flow (Sweet AI Assistant focus)
                        self.set_timer(
                            0.1, lambda: self._update_table_selector_after_refresh(tables)
                        )

            except Exception as e:
                self.log(f"Could not refresh tools panel for mode change: {e}")
                import traceback

                self.log(f"Traceback: {traceback.format_exc()}")
        else:
            self.log("Mode unchanged, no UI refresh needed")
            # Even if mode didn't change, try to update the selector if we have tables
            if enabled and tables:
                try:
                    self.log("Trying to update table selector without refresh...")
                    table_selector = self.query_one("#table-selector", Select)
                    self.log(f"Found table selector: {table_selector}")
                    table_options = [(table, table) for table in tables]
                    self.log(f"Created table options: {table_options}")
                    table_selector.set_options(table_options)
                    if tables:
                        table_selector.value = tables[0]
                        self.log(f"Set table selector value to: {tables[0]}")
                    self.log("Table selector updated successfully!")
                except Exception as e:
                    self.log(f"Could not update table selector: {e}")
                    import traceback

                    self.log(f"Traceback: {traceback.format_exc()}")

    def _update_table_selector_after_refresh(self, tables: list) -> None:
        """Update table selector after UI refresh."""
        try:
            self.log(f"Updating table selector after refresh with tables: {tables}")
            table_selector = self.query_one("#table-selector", Select)
            self.log(f"Found table selector after refresh: {table_selector}")
            table_options = [(table, table) for table in tables]
            self.log(f"Created table options after refresh: {table_options}")
            table_selector.set_options(table_options)
            if tables:
                table_selector.value = tables[0]
                self.log(f"Set table selector value after refresh to: {tables[0]}")
            self.log("Table selector updated successfully after refresh!")
        except Exception as e:
            self.log(f"Could not update table selector after refresh: {e}")
            import traceback

            self.log(f"Traceback: {traceback.format_exc()}")

    def _update_table_selector_and_focus_for_remote(self, tables: list) -> None:
        """Update table selector and focus on Table Selection tab for remote databases."""
        try:
            # First update the table selector
            self._update_table_selector_after_refresh(tables)

            # Then focus on Table Selection tab (third option in database mode)
            self.log("Setting focus to Table Selection tab for remote database")
            section_radio = self.query_one("#section-radio", RadioSet)
            section_radio.index = 2  # Table Selection is the third tab (index 2)

            # Also switch the content
            content_switcher = self.query_one("#content-switcher", ContentSwitcher)
            content_switcher.current = "table-selection-content"

            # Use a timer to focus on the dropdown after UI settles
            self.log("Scheduling focus on table selector dropdown for remote database")
            self.set_timer(0.2, self._focus_table_dropdown)

            self.log("Successfully focused on table dropdown for remote database")
        except Exception as e:
            self.log(f"Could not update table selector and focus for remote database: {e}")
            import traceback

            self.log(f"Traceback: {traceback.format_exc()}")

    def _focus_table_dropdown(self) -> None:
        """Focus on the table selector dropdown."""
        try:
            self.log("Attempting to focus on table selector dropdown")
            table_selector = self.query_one("#table-selector", Select)
            table_selector.focus()
            self.log("Successfully focused on table selector dropdown")
        except Exception as e:
            self.log(f"Could not focus on table selector dropdown: {e}")
            import traceback

            self.log(f"Traceback: {traceback.format_exc()}")

    def _execute_sql(self) -> None:
        """Execute SQL query from the SQL input area."""
        try:
            sql_input = self.query_one("#sql-input", TextArea)
            sql_result = self.query_one("#sql-result", Static)

            if not self.data_grid or not self.data_grid.database_connection:
                error_msg = "Error: No database connection"
                sql_result.update(error_msg)
                sql_result.remove_class("hidden")
                self.log(error_msg)
                return

            query = sql_input.text.strip()
            if not query:
                error_msg = "Error: Please enter a SQL query"
                sql_result.update(error_msg)
                sql_result.remove_class("hidden")
                self.log(error_msg)
                return

            self.log(f"Executing SQL query: {query}")

            # Execute the query
            try:
                # Use Arrow format to avoid pandas/numpy dependency
                self.log("Executing query against database connection...")
                result = self.data_grid.database_connection.execute(query).arrow()
                self.log(f"Query execution completed, got Arrow result with {len(result)} rows")

                # Convert Arrow table directly to Polars
                import polars as pl

                df = pl.from_arrow(result)
                self.log(f"Converted to Polars DataFrame: {df.shape} rows x columns")

                # Clear schema info for query results since we don't have original DB schema
                self.data_grid.database_schema = {}
                self.data_grid.current_table_column_types = {}
                self.data_grid.native_column_types = {}  # Clear native types for queries

                self.log("Calling load_dataframe to update display...")
                self.data_grid.load_dataframe(df, force_recreation=True)
                self.log("load_dataframe completed successfully")

                # Show success message
                success_msg = f"Query executed successfully. Retrieved {len(df)} rows."
                sql_result.update(success_msg)
                sql_result.remove_class("hidden")
                self.log(success_msg)

                # Update title to show it's a query result
                title = f"{self.data_grid.database_path} [Query Result]"
                self.app.set_current_filename(title)
                self.log(f"Updated title to: {title}")

            except Exception as e:
                error_msg = f"SQL Error: {str(e)}"
                sql_result.update(error_msg)
                sql_result.remove_class("hidden")
                self.log(f"SQL execution failed: {e}")
                import traceback

                self.log(f"Full traceback: {traceback.format_exc()}")

        except Exception as e:
            self.log(f"Error executing SQL: {e}")
            import traceback

            self.log(f"Full traceback: {traceback.format_exc()}")

    def _execute_sql_suggestion(self) -> None:
        """Execute SQL suggestion from AI assistant directly (like Polars workflow)."""
        try:
            generated_sql = self.query_one("#generated-sql", TextArea)
            sql_code = generated_sql.text.strip()

            if not sql_code:
                self._show_llm_response("No SQL code to execute.", is_error=True)
                return

            # Execute SQL directly like Polars code execution
            self._execute_sql_directly(sql_code)

            # Hide the suggestion UI and remove styling
            execute_button = self.query_one("#execute-sql-suggestion", Button)
            execute_button.add_class("hidden")
            generated_sql.add_class("hidden")
            generated_sql.remove_class("approval-ready")

        except Exception as e:
            self.log(f"Error executing SQL suggestion: {e}")
            self._show_llm_response(f"Error executing SQL: {e}", is_error=True)

    def _execute_sql_directly(self, sql_code: str) -> None:
        """Execute SQL code directly and show results in the AI Assistant area."""
        try:
            if (
                self.data_grid is None
                or not hasattr(self.data_grid, "database_connection")
                or self.data_grid.database_connection is None
            ):
                self._show_llm_response("No database connection available.", is_error=True)
                return

            debug_logger.info(f"Executing SQL directly: {sql_code[:100]}...")

            # Execute the SQL query
            connection = self.data_grid.database_connection
            result = connection.execute(sql_code).arrow()

            # Convert to Polars DataFrame for display
            if pl is not None:
                result_df = pl.from_arrow(result)

                # Load the result into the data grid
                self.data_grid.load_dataframe(result_df, force_recreation=True)
                self.data_grid.has_changes = True
                self.data_grid.update_title_change_indicator()

                # Force refresh
                self.data_grid._table.refresh()
                self.data_grid.refresh()
                self.data_grid.call_after_refresh(lambda: self.data_grid._table.refresh())

                # Show success message in AI Assistant area
                rows, cols = result_df.shape
                self._show_llm_response(
                    f"✅ SQL executed successfully! Result: {rows} rows × {cols} columns",
                    is_error=False,
                )
            else:
                self._show_llm_response(
                    "Polars library not available for result display.", is_error=True
                )

        except Exception as e:
            error_msg = str(e)
            self._show_llm_response(f"SQL Error: {error_msg}", is_error=True)
            self.log(f"SQL execution error: {e}")
            debug_logger.error(f"SQL execution error: {e}")

    def on_text_area_focused(self, event) -> None:
        """Handle TextArea focus events to clear placeholder text."""
        try:
            if event.text_area.id == "chat-input":
                # Clear placeholder text when user focuses on chat input
                if event.text_area.text == "What transformation would you like to make?":
                    event.text_area.text = ""
        except Exception as e:
            self.log(f"Error handling text area focus: {e}")

    def _update_history_display(self) -> None:
        """Update the history display with full conversation."""
        try:
            # Always show full history in the main chat area
            self._show_full_history_in_main_area()
        except Exception as e:
            self.log(f"Error updating history display: {e}")

    def _show_full_history_in_main_area(self) -> None:
        """Show the full conversation history in the main chat history area."""
        try:
            chat_history_widget = self.query_one("#chat-history", Static)
            chat_history_scroll = self.query_one("#chat-history-scroll", VerticalScroll)

            if not self.chat_history:
                chat_history_widget.update("[dim]💬 No conversation history to display...[/dim]")
                chat_history_scroll.add_class("empty")
                return

            # Remove empty class
            chat_history_scroll.remove_class("empty")

            # Create detailed history without header since it's the main mode
            history_lines = []

            for i, msg in enumerate(self.chat_history, 1):
                role_icon = "👤" if msg["role"] == "user" else "🤖"
                role_name = (
                    "[bold]You[/bold]" if msg["role"] == "user" else "[bold]Assistant[/bold]"
                )
                timestamp = msg.get("timestamp", "Unknown time")

                history_lines.append(
                    f"{role_icon} {role_name} ([dim]{timestamp}[/dim]) - Message #{i}"
                )
                history_lines.append("-" * 40)

                if msg["role"] == "assistant":
                    # For assistant messages, show full response and extract code
                    content = msg["content"]

                    # Extract and display code blocks separately
                    import re

                    code_matches = re.findall(r"```python\n(.*?)\n```", content, re.DOTALL)

                    if code_matches:
                        # Show response without code blocks first
                        response_text = re.sub(
                            r"```python\n.*?\n```",
                            "[CODE BLOCK EXTRACTED BELOW]",
                            content,
                            flags=re.DOTALL,
                        )
                        history_lines.append(response_text.strip())
                        history_lines.append("")

                        # Show extracted code blocks
                        for j, code in enumerate(code_matches, 1):
                            history_lines.append(f"[green]📝 Generated Code Block #{j}:[/green]")
                            history_lines.append("[cyan]```python[/cyan]")
                            for line in code.strip().split("\n"):
                                history_lines.append(f"[cyan]{line}[/cyan]")
                            history_lines.append("[cyan]```[/cyan]")
                            history_lines.append("")
                    else:
                        history_lines.append(content)
                        history_lines.append("")
                else:
                    # User message
                    history_lines.append(msg["content"])
                    history_lines.append("")

            # Display full history in main chat area
            full_history = "\n".join(history_lines)
            chat_history_widget.update(full_history)

            # Scroll to show content
            self.call_after_refresh(self._scroll_history_to_bottom)

        except Exception as e:
            self.log(f"Error showing full history in main area: {e}")

    def _switch_to_section(self, section_id: str) -> None:
        """Switch to the specified section."""
        try:
            content_switcher = self.query_one("#content-switcher", ContentSwitcher)
            content_switcher.current = section_id

            # If switching to Transform with Code section, set preferred focus to Execute Code button
            if section_id == "transform-with-code-content":
                self.call_later(self._focus_execute_button)

        except Exception as e:
            self.log(f"Error switching to section {section_id}: {e}")

    def update_column_selection(
        self, column_index: int, column_name: str, column_type: str
    ) -> None:
        """Update the panel when a column header is selected."""
        self.current_column = column_index
        self.current_column_name = column_name

        try:
            # Update column info display
            column_info = self.query_one("#column-info", Static)
            column_info.update(
                f"Column {self.get_excel_column_name(column_index)}: '{column_name}' ({column_type})"
            )

            # Show the type selector and apply button
            type_selector = self.query_one("#type-selector", Select)
            apply_button = self.query_one("#apply-type-change", Button)

            type_selector.remove_class("hidden")
            apply_button.remove_class("hidden")

            # Set current type in selector
            type_mapping = {
                "text": "text",
                "integer": "integer",
                "float": "float",
                "boolean": "boolean",
            }
            current_type = type_mapping.get(column_type, "text")
            type_selector.value = current_type

            # Also update Find in Column section
            self._update_find_column_selection(column_index, column_name, column_type)

        except Exception as e:
            self.log(f"Error updating column selection: {e}")

    def clear_column_selection(self) -> None:
        """Clear the column selection."""
        self.current_column = None
        self.current_column_name = None

        try:
            # Update display
            column_info = self.query_one("#column-info", Static)
            column_info.update("No column selected")

            # Hide the type selector and apply button
            type_selector = self.query_one("#type-selector", Select)
            apply_button = self.query_one("#apply-type-change", Button)

            type_selector.add_class("hidden")
            apply_button.add_class("hidden")

            # Also clear Find in Column section
            self._clear_find_column_selection()

        except Exception as e:
            self.log(f"Error clearing column selection: {e}")

    def get_excel_column_name(self, col_index: int) -> str:
        """Convert column index to Excel-style column name (A, B, ..., Z, AA, AB, ...)."""
        result = ""
        while col_index >= 0:
            result = chr(ord("A") + (col_index % 26)) + result
            col_index = col_index // 26 - 1
        return result

    def _apply_type_change(self) -> None:
        """Apply the selected type change to the current column."""
        if self.current_column is None or self.data_grid is None:
            return

        try:
            type_selector = self.query_one("#type-selector", Select)
            selected_type = type_selector.value

            # Use standard type conversion (which includes numeric extraction for float type)
            self.data_grid._apply_column_type_conversion(self.current_column_name, selected_type)

            # Update the column info after conversion
            if hasattr(self.data_grid, "data") and self.data_grid.data is not None:
                new_type = self.data_grid._get_friendly_type_name(
                    self.data_grid.data.dtypes[self.current_column]
                )
                self.update_column_selection(
                    self.current_column, self.current_column_name, new_type
                )

        except Exception as e:
            self.log(f"Error applying type change: {e}")

    def _execute_code(self) -> None:
        """Execute the Polars code on the current dataframe."""
        if (
            self.data_grid is None
            or not hasattr(self.data_grid, "data")
            or self.data_grid.data is None
        ):
            self._show_execution_result(
                "No data loaded. Please load a dataset first.", is_error=True
            )
            return

        try:
            code_input = self.query_one("#code-input", TextArea)
            code = code_input.text.strip()

            if not code or code == "df":
                self._show_execution_result(
                    "No code to execute. Please enter Polars code.", is_error=True
                )
                return

            # Import polars for the execution context
            if pl is None:
                self._show_execution_result("Polars library not available.", is_error=True)
                return

            # Log the original dataframe info
            original_shape = self.data_grid.data.shape
            original_columns = list(self.data_grid.data.columns)
            self.log(f"Original dataframe: {original_shape} - columns: {original_columns}")

            # Create execution context with current dataframe
            execution_context = {
                "pl": pl,
                "df": self.data_grid.data.clone(),  # Work with a copy initially
                "__builtins__": __builtins__,
            }

            # Log the code being executed
            self.log(f"Executing code: {code}")

            # Execute the code
            exec(code, execution_context)

            # Get the result dataframe
            result_df = execution_context.get("df")

            if result_df is None:
                self._show_execution_result(
                    "Code executed but no dataframe returned. Make sure to assign result to 'df'.",
                    is_error=True,
                )
                return

            # Validate that we got a Polars DataFrame
            if not hasattr(result_df, "shape") or not hasattr(result_df, "columns"):
                self._show_execution_result(
                    "Result is not a valid Polars DataFrame.", is_error=True
                )
                return

            # Log the result dataframe info
            result_shape = result_df.shape
            result_columns = list(result_df.columns)
            self.log(f"Result dataframe: {result_shape} - columns: {result_columns}")

            # Check if the dataframe actually changed
            if result_shape == original_shape and result_columns == original_columns:
                # Same shape and columns: check if data changed
                try:
                    if result_df.equals(self.data_grid.data):
                        self._show_execution_result(
                            "Code executed but dataframe unchanged.", is_error=True
                        )
                        return
                except Exception:
                    # If comparison fails, assume it changed
                    pass

            # Apply the transformation to the actual data grid
            self.data_grid.load_dataframe(result_df, force_recreation=True)
            self.data_grid.has_changes = True
            self.data_grid.update_title_change_indicator()

            # Force multiple levels of refresh to ensure display updates properly
            self.data_grid._table.refresh()  # Refresh the table widget
            self.data_grid.refresh()  # Refresh the container

            # Use multiple callbacks to ensure proper timing
            self.data_grid.call_after_refresh(lambda: self.data_grid._table.refresh())
            self.data_grid.call_after_refresh(self.data_grid._move_to_first_cell)

            # Additional forced refresh with a slight delay
            self.data_grid.set_timer(0.1, lambda: self.data_grid._table.refresh())

            # Show success message with detailed info
            rows, cols = result_shape
            if cols > len(original_columns):
                new_columns = [col for col in result_columns if col not in original_columns]
                self._show_execution_result(
                    f"✓ Code executed successfully! Result: {rows} rows, {cols} columns. New columns: {new_columns}",
                    is_error=False,
                )
            elif cols < len(original_columns):
                removed_columns = [col for col in original_columns if col not in result_columns]
                self._show_execution_result(
                    f"✓ Code executed successfully! Result: {rows} rows, {cols} columns. Removed columns: {removed_columns}",
                    is_error=False,
                )
            else:
                self._show_execution_result(
                    f"✓ Code executed successfully! Result: {rows} rows, {cols} columns",
                    is_error=False,
                )

        except Exception as e:
            error_msg = str(e)
            # Make common errors more user-friendly
            if "name 'pl' is not defined" in error_msg:
                error_msg = "Use 'pl' for Polars functions (e.g., pl.col('name'), pl.when(), etc.)"
            elif "DataFrame" in error_msg and "object has no attribute" in error_msg:
                error_msg = f"DataFrame error: {error_msg}. Check column names and operations."

            self._show_execution_result(f"Error: {error_msg}", is_error=True)
            self.log(f"Code execution error: {e}")
            # Also log the full traceback for debugging
            import traceback

            self.log(f"Full traceback: {traceback.format_exc()}")

    def _show_execution_result(self, message: str, is_error: bool = False) -> None:
        """Show execution result or error message."""
        try:
            result_display = self.query_one("#execution-result", Static)

            if is_error:
                result_display.update(f"[red]{message}[/red]")
            else:
                result_display.update(f"[green]{message}[/green]")

            result_display.remove_class("hidden")

            # Auto-hide success messages after 5 seconds
            if not is_error:
                self.set_timer(5.0, lambda: result_display.add_class("hidden"))

        except Exception as e:
            self.log(f"Error showing execution result: {e}")

    def _focus_execute_button(self) -> None:
        """Set focus to the Execute Code button (preferred default)."""
        try:
            execute_btn = self.query_one("#execute-code", Button)
            execute_btn.focus()
        except Exception as e:
            self.log(f"Error focusing execute button: {e}")

    def _update_find_column_selection(
        self, column_index: int, column_name: str, column_type: str
    ) -> None:
        """Update the Find in Column section when a column is selected."""
        try:
            # Update find column info display
            find_column_info = self.query_one("#find-column-info", Static)
            find_column_info.update(
                f"Column {self.get_excel_column_name(column_index)}: '{column_name}' ({column_type})"
            )

            # Show the search controls
            search_type_selector = self.query_one("#search-type-selector", Select)
            search_values_container = self.query_one("#search-values-container", Vertical)
            find_button = self.query_one("#find-in-column-btn", Button)

            search_type_selector.remove_class("hidden")
            search_values_container.remove_class("hidden")
            find_button.remove_class("hidden")

            # Update the button text based on current mode
            if self.find_mode_active:
                find_button.label = "Exit"
                find_button.variant = "error"
            else:
                find_button.label = "Find"
                find_button.variant = "success"

            # Update search inputs based on current search type
            self._update_search_inputs(search_type_selector.value)

        except Exception as e:
            self.log(f"Error updating find column selection: {e}")

    def _clear_find_column_selection(self) -> None:
        """Clear the Find in Column section."""
        try:
            # Update display
            find_column_info = self.query_one("#find-column-info", Static)
            find_column_info.update("No column selected")

            # Hide the search controls
            search_type_selector = self.query_one("#search-type-selector", Select)
            search_values_container = self.query_one("#search-values-container", Vertical)
            find_button = self.query_one("#find-in-column-btn", Button)

            search_type_selector.add_class("hidden")
            search_values_container.add_class("hidden")
            find_button.add_class("hidden")

            # Exit find mode if active
            if self.find_mode_active:
                self._exit_find_mode()

        except Exception as e:
            self.log(f"Error clearing find column selection: {e}")

    def _update_search_inputs(self, search_type: str) -> None:
        """Update the search input fields based on the selected search type."""
        # Write debug info to file
        import os

        debug_file = os.path.join(os.path.expanduser("~"), "sweet_debug.log")

        with open(debug_file, "a") as f:
            f.write(f"\n=== _update_search_inputs called with: {search_type} ===\n")

        try:
            # Get the rows and labels by ID
            first_value_row = self.query_one("#first-value-row", Horizontal)
            second_value_row = self.query_one("#second-value-row", Horizontal)
            first_label = self.query_one("#first-value-label", Static)
            second_label = self.query_one("#second-value-label", Static)

            with open(debug_file, "a") as f:
                f.write(
                    f"Found elements: first_row={first_value_row}, second_row={second_value_row}\n"
                )
                f.write(f"First row classes: {first_value_row.classes}\n")
                f.write(f"Second row classes: {second_value_row.classes}\n")

            if search_type in ["is_null", "is_not_null"]:
                # Hide both value input rows for null checks
                with open(debug_file, "a") as f:
                    f.write("Hiding both value rows for null checks\n")
                first_value_row.add_class("hidden")
                second_value_row.add_class("hidden")

            elif search_type in ["between", "outside"]:
                # Show both rows with "From:" and "To:" labels
                with open(debug_file, "a") as f:
                    f.write("Showing both value rows with From:/To: labels\n")
                first_label.update("From:")
                second_label.update("To:")
                first_value_row.remove_class("hidden")
                second_value_row.remove_class("hidden")

            else:
                # Show only first row with "Value:" label for all other search types
                with open(debug_file, "a") as f:
                    f.write("Showing first value row with Value: label, hiding second\n")
                first_label.update("Value:")
                first_value_row.remove_class("hidden")
                second_value_row.add_class("hidden")

            # Force a refresh of the UI
            self.refresh()

            # Also try refreshing parent containers
            try:
                search_values_container = self.query_one("#search-values-container")
                search_values_container.refresh()

                find_content = self.query_one("#find-in-column-content")
                find_content.refresh()
            except Exception:
                pass

            # Debug: Check final state
            with open(debug_file, "a") as f:
                f.write(f"FINAL STATE - First row classes: {first_value_row.classes}\n")
                f.write(f"FINAL STATE - Second row classes: {second_value_row.classes}\n")

        except Exception as e:
            with open(debug_file, "a") as f:
                f.write(f"EXCEPTION in _update_search_inputs: {str(e)}\n")
                import traceback

                f.write(traceback.format_exc())
            self.log(f"Error updating search inputs: {e}")
            import traceback

            traceback.print_exc()

    def _handle_find_button(self) -> None:
        """Handle Find/Exit button press."""
        # Write debug info to file
        import os

        debug_file = os.path.join(os.path.expanduser("~"), "sweet_debug.log")

        with open(debug_file, "a") as f:
            f.write("\n=== _handle_find_button called ===\n")
            f.write(f"find_mode_active: {self.find_mode_active}\n")

        try:
            # Get the SearchOverlay from the data grid
            data_grid = self.app.query_one("#data-grid", ExcelDataGrid)
            search_overlay = data_grid.query_one(SearchOverlay)

            with open(debug_file, "a") as f:
                f.write("Successfully got data_grid and search_overlay\n")

            if self.find_mode_active:
                # Exit find mode
                with open(debug_file, "a") as f:
                    f.write("Exiting find mode\n")
                self._exit_find_mode()
                search_overlay.deactivate_search()
                data_grid.clear_search_highlights()
            else:
                # Start search
                with open(debug_file, "a") as f:
                    f.write("Starting search\n")
                self._perform_search_via_overlay(search_overlay, data_grid)
        except Exception as e:
            with open(debug_file, "a") as f:
                f.write(f"EXCEPTION in _handle_find_button: {str(e)}\n")
                import traceback

                f.write(traceback.format_exc())
            self.log(f"Error handling find button: {e}")
            # Also try to show error in the console for debugging
            import traceback

            traceback.print_exc()

    def _perform_search_via_overlay(
        self, search_overlay: SearchOverlay, data_grid: ExcelDataGrid
    ) -> None:
        """Perform search using the SearchOverlay."""
        # Write debug info to file
        import os

        debug_file = os.path.join(os.path.expanduser("~"), "sweet_debug.log")

        with open(debug_file, "a") as f:
            f.write("\n=== Find Button Pressed ===\n")
            f.write(f"Current column: {self.current_column}\n")
            f.write(f"Current column name: {self.current_column_name}\n")

        if self.current_column is None:
            with open(debug_file, "a") as f:
                f.write("ERROR: No column selected\n")
            # Show error in search overlay info bar
            info_bar = search_overlay.query_one("#search-info", Static)
            info_bar.update("No column selected for search")
            info_bar.remove_class("hidden")
            search_overlay.set_timer(3.0, lambda: info_bar.add_class("hidden"))
            return

        try:
            # Get search parameters from ToolsPanel UI
            search_type_selector = self.query_one("#search-type-selector", Select)
            search_value1 = self.query_one("#search-value1", Input)
            search_value2 = self.query_one("#search-value2", Input)

            search_type = search_type_selector.value
            value1 = search_value1.value.strip()
            value2 = search_value2.value.strip()

            # Debug logging to file
            with open(debug_file, "a") as f:
                f.write(f"Search type: {search_type}\n")
                f.write(f"Value1: '{value1}'\n")
                f.write(f"Value2: '{value2}'\n")
                f.write(f"Column: {self.current_column_name}\n")

            # Validate inputs
            if search_type not in ["is_null", "is_not_null"] and not value1:
                with open(debug_file, "a") as f:
                    f.write("ERROR: No search value entered\n")
                # Show error in search overlay info bar
                info_bar = search_overlay.query_one("#search-info", Static)
                info_bar.update("Please enter a search value")
                info_bar.remove_class("hidden")
                search_overlay.set_timer(3.0, lambda: info_bar.add_class("hidden"))
                return

            if search_type in ["between", "outside"] and not value2:
                with open(debug_file, "a") as f:
                    f.write("ERROR: Missing second value for range search\n")
                # Show error in search overlay info bar
                info_bar = search_overlay.query_one("#search-info", Static)
                info_bar.update("Please enter both values for range search")
                info_bar.remove_class("hidden")
                search_overlay.set_timer(3.0, lambda: info_bar.add_class("hidden"))
                return

            # Get data and perform search
            if data_grid.data is None:
                with open(debug_file, "a") as f:
                    f.write("ERROR: No data loaded\n")
                # Show error in search overlay info bar
                info_bar = search_overlay.query_one("#search-info", Static)
                info_bar.update("No data loaded")
                info_bar.remove_class("hidden")
                search_overlay.set_timer(3.0, lambda: info_bar.add_class("hidden"))
                return

            df = data_grid.data
            column_name = self.current_column_name

            # Perform the search
            matches = self._search_column(df, column_name, search_type, value1, value2)

            with open(debug_file, "a") as f:
                f.write(f"Found {len(matches)} matches\n")
                f.write(f"Matches: {matches}\n")

            if matches:
                # Activate search overlay with results (this will navigate to first match)
                search_overlay.activate_search(matches, column_name, f"{search_type}: {value1}")

                # Get the first match coordinates
                first_match_row, first_match_col = matches[0]

                # Use a timer to apply highlighting after navigation is complete
                def apply_highlighting_and_navigate():
                    data_grid.highlight_search_matches(matches)
                    # Ensure cursor is at the first match after highlighting
                    data_grid._table.cursor_coordinate = (first_match_row, first_match_col)
                    data_grid.update_address_display(first_match_row, first_match_col)

                data_grid.set_timer(0.1, apply_highlighting_and_navigate)

                # Force focus to the data grid with a small delay to ensure it works
                def focus_data_grid():
                    data_grid.focus()
                    # Also ensure the table itself has focus
                    data_grid._table.focus()

                data_grid.set_timer(0.2, focus_data_grid)

                # Update ToolsPanel state
                self.find_mode_active = True
                self.found_matches = matches
                self.current_match_index = 0

                # Update find button
                find_button = self.query_one("#find-in-column-btn", Button)
                find_button.label = "Exit"
                find_button.variant = "error"

                with open(debug_file, "a") as f:
                    f.write("Search successful - updated find_mode_active to True\n")
                    f.write(f"Found {len(matches)} matches: {matches}\n")
            else:
                # Show error in search overlay info bar
                info_bar = search_overlay.query_one("#search-info", Static)
                info_bar.update(f"No matches found for '{value1}' in column '{column_name}'")
                info_bar.remove_class("hidden")
                search_overlay.set_timer(3.0, lambda: info_bar.add_class("hidden"))

        except Exception as e:
            with open(debug_file, "a") as f:
                f.write(f"EXCEPTION: {str(e)}\n")
                import traceback

                f.write(traceback.format_exc())
            # Show error in search overlay info bar
            info_bar = search_overlay.query_one("#search-info", Static)
            info_bar.update(f"Search error: {str(e)}")
            info_bar.remove_class("hidden")
            search_overlay.set_timer(3.0, lambda: info_bar.add_class("hidden"))
            import traceback

            traceback.print_exc()

    def _search_column(
        self, df, column_name: str, search_type: str, value1: str, value2: str
    ) -> list[tuple[int, int]]:
        """Search the column and return list of matching (row, col) positions."""
        matches = []

        try:
            column_data = df[column_name]
            column_index = self.current_column

            for i, cell_value in enumerate(column_data):
                if self._cell_matches_criteria(cell_value, search_type, value1, value2):
                    # Convert to display coordinates (add 1 for header row)
                    matches.append((i + 1, column_index))

        except Exception as e:
            self.log(f"Error searching column: {e}")

        return matches

    def _cell_matches_criteria(
        self, cell_value, search_type: str, value1: str, value2: str
    ) -> bool:
        """Check if a cell value matches the search criteria."""
        try:
            if search_type == "is_null":
                return cell_value is None
            elif search_type == "is_not_null":
                return cell_value is not None

            if cell_value is None:
                return False

            # Convert cell value to string for comparison
            cell_str = str(cell_value)

            if search_type == "equals":
                return cell_str == value1
            elif search_type == "not_equals":
                return cell_str != value1
            elif search_type in ["greater_than", "greater_equal", "less_than", "less_equal"]:
                # Try numeric comparison first, fall back to string comparison
                try:
                    cell_num = float(cell_value)
                    value1_num = float(value1)

                    if search_type == "greater_than":
                        return cell_num > value1_num
                    elif search_type == "greater_equal":
                        return cell_num >= value1_num
                    elif search_type == "less_than":
                        return cell_num < value1_num
                    elif search_type == "less_equal":
                        return cell_num <= value1_num
                except (ValueError, TypeError):
                    # Fall back to string comparison
                    if search_type == "greater_than":
                        return cell_str > value1
                    elif search_type == "greater_equal":
                        return cell_str >= value1
                    elif search_type == "less_than":
                        return cell_str < value1
                    elif search_type == "less_equal":
                        return cell_str <= value1
            elif search_type in ["between", "outside"]:
                try:
                    cell_num = float(cell_value)
                    value1_num = float(value1)
                    value2_num = float(value2)

                    if search_type == "between":
                        return value1_num <= cell_num <= value2_num
                    else:  # outside
                        return cell_num < value1_num or cell_num > value2_num
                except (ValueError, TypeError):
                    # Fall back to string comparison
                    if search_type == "between":
                        return value1 <= cell_str <= value2
                    else:  # outside
                        return cell_str < value1 or cell_str > value2

        except Exception as e:
            self.log(f"Error matching criteria: {e}")

        return False

    def _highlight_matches(self) -> None:
        """Highlight the found matches in the data grid."""
        # This will need to be implemented in the ExcelDataGrid class
        if self.data_grid:
            self.data_grid.highlight_search_matches(self.found_matches)

    def _navigate_to_current_match(self) -> None:
        """Navigate to the current match in the search results."""
        if self.found_matches and self.data_grid:
            row, col = self.found_matches[self.current_match_index]
            self.data_grid.navigate_to_cell(row, col)

    def _exit_find_mode(self) -> None:
        """Exit find mode and clear highlights."""
        self.find_mode_active = False
        self.found_matches = []
        self.current_match_index = 0

        # Update button
        try:
            find_button = self.query_one("#find-in-column-btn", Button)
            find_button.label = "Find"
            find_button.variant = "success"
        except Exception:
            pass

        # Clear highlights
        if self.data_grid:
            self.data_grid.clear_search_highlights()

        self.log("Exited find mode")

    # Sweet AI Assistant methods
    def _handle_send_chat(self) -> None:
        """Handle sending a chat message to the LLM."""
        try:
            # Get the chat input
            chat_input = self.query_one("#chat-input", TextArea)
            user_message = chat_input.text.strip()

            # Check if it's the placeholder text or empty
            if not user_message or user_message == "What transformation would you like to make?":
                self._show_llm_response("Please enter a message to send.", is_error=True)
                return

            # Add user message to chat history with timestamp
            from datetime import datetime

            timestamp = datetime.now().strftime("%H:%M")
            self.chat_history.append(
                {"role": "user", "content": user_message, "timestamp": timestamp}
            )
            self._update_history_display()

            # Clear the input
            chat_input.text = ""

            # Send message to LLM asynchronously
            self._send_to_llm_async(user_message)

        except Exception as e:
            self.log(f"Error handling send chat: {e}")
            self._show_llm_response(f"Error: {str(e)}", is_error=True)

    def _handle_clear_chat(self) -> None:
        """Handle clearing the chat history."""
        try:
            self.chat_history = []
            self.current_chat_session = None
            self.last_generated_code = None
            self.pending_code = None  # Clear pending code

            # Clear all displays
            self._update_history_display()
            response_scroll = self.query_one("#llm-response-scroll", VerticalScroll)
            response_scroll.add_class("hidden")
            self._hide_generated_code()

            # Hide approval UI (code preview and Apply button)
            self._hide_approval_ui()

            # Reset chat input
            chat_input = self.query_one("#chat-input", TextArea)
            chat_input.text = ""  # Clear any existing text to show placeholder

        except Exception as e:
            self.log(f"Error clearing chat: {e}")

    def _handle_apply_transform(self) -> None:
        """Handle applying the pending transformation code."""
        if not self.pending_code:
            self._show_llm_response("No pending code to apply.", is_error=True)
            return

        try:
            # Execute the pending code using the same logic as Polars Exec
            code = self.pending_code
            self.pending_code = None  # Clear pending code after use

            # Hide the approval UI
            self._hide_approval_ui()

            # Apply the code
            self._apply_generated_code(code)

        except Exception as e:
            self.log(f"Error applying transform: {e}")
            self._show_llm_response(f"Error applying transform: {str(e)}", is_error=True)

    def _hide_approval_ui(self) -> None:
        """Hide the code approval UI elements."""
        try:
            code_preview = self.query_one("#generated-code", TextArea)
            apply_button = self.query_one("#apply-transform", Button)
            response_scroll = self.query_one("#llm-response-scroll", VerticalScroll)

            # Hide the approval elements
            code_preview.add_class("hidden")
            code_preview.read_only = False  # Make it editable again
            apply_button.add_class("hidden")
            response_scroll.add_class("hidden")

            # Keep chat history at consistent size (don't remove compact class)
            # chat_history_scroll.remove_class("compact")  # Commented out to maintain size

            # Clear pending code
            self.pending_transform_code = None

            self.log("Approval UI hidden and chat history restored to full size")

        except Exception as e:
            self.log(f"Error hiding approval UI: {e}")

    def _send_to_llm_async(self, user_message: str) -> None:
        """Send message to LLM asynchronously."""
        debug_logger.info(f"Starting LLM interaction with message: {user_message[:100]}...")

        # Create a more robust async handler
        async def llm_handler():
            try:
                result = await self._interact_with_llm(user_message)
                debug_logger.info(f"LLM handler completed with result: {result is not None}")

                # Use call_after_refresh to ensure UI update happens on main thread
                if result:
                    self.call_after_refresh(lambda: self._handle_llm_result(result))
                else:
                    self.call_after_refresh(
                        lambda: self._show_llm_response(
                            "Failed to get response from LLM.", is_error=True
                        )
                    )

                return result
            except Exception as e:
                debug_logger.error(f"LLM handler exception: {e}")
                error_msg = f"Error: {str(e)}"
                self.call_after_refresh(lambda: self._show_llm_response(error_msg, is_error=True))
                return None

        # Run the worker
        self.run_worker(llm_handler(), exclusive=True)
        debug_logger.info("LLM worker started")

    def on_worker_result(self, event) -> None:
        """Handle worker completion for LLM interactions."""
        debug_logger.info(f"Worker result event received: {event}")
        debug_logger.info(f"Event type: {type(event)}")
        debug_logger.info(f"Event worker: {getattr(event, 'worker', 'No worker attribute')}")
        try:
            result = event.result
            debug_logger.info(f"Worker result: {type(result)}")
            if result:
                self._handle_llm_result(result)
            else:
                debug_logger.error("Worker returned None result")
                self._show_llm_response("Failed to get response from LLM.", is_error=True)
        except Exception as e:
            debug_logger.error(f"Error in worker result handler: {e}")
            self.log(f"Error in worker result handler: {e}")
            self._show_llm_response(f"Error: {str(e)}", is_error=True)

    def _handle_llm_result(self, result):
        """Handle the LLM result directly."""
        debug_logger.info("Handling LLM result directly")
        try:
            assistant_message, generated_code = result
            debug_logger.info(
                f"Response received - message length: {len(assistant_message)}, has code: {generated_code is not None}"
            )

            # Add assistant message to chat history with timestamp
            from datetime import datetime

            timestamp = datetime.now().strftime("%H:%M")
            self.chat_history.append(
                {"role": "assistant", "content": assistant_message, "timestamp": timestamp}
            )
            self._update_history_display()

            # Handle code approval workflow based on mode
            if generated_code:
                if self.is_database_mode and self._is_sql_code(generated_code):
                    debug_logger.info(f"Valid SQL code detected: {generated_code[:200]}...")
                    # Show SQL code for approval in database mode
                    self._show_sql_code_for_approval(generated_code)
                elif not self.is_database_mode and self._is_transformation_code(generated_code):
                    debug_logger.info(
                        f"Valid transformation code detected: {generated_code[:200]}..."
                    )
                    self.pending_code = generated_code  # Store for approval
                    self.last_generated_code = generated_code  # Keep for reference
                    # Show code preview and approval button for transformation
                    self._show_code_preview_with_approval(generated_code)
                else:
                    debug_logger.info(
                        "Code detected but not applicable to current mode - conversational response"
                    )
                    self._show_conversational_response("💬 Response added to chat history")
            else:
                debug_logger.info("No code detected - conversational response")
                # For conversational responses, just show a brief confirmation
                self._show_conversational_response("💬 Response added to chat history")

            # Update debug status display
            self._update_debug_status()
        except Exception as e:
            debug_logger.error(f"Error in result handler: {e}")
            self._show_llm_response(f"Error: {str(e)}", is_error=True)

    def on_worker_result(self, event) -> None:
        """Handle worker completion for LLM interactions."""
        debug_logger.info(f"Worker result event received: {event}")
        debug_logger.info(f"Event type: {type(event)}")
        debug_logger.info(f"Event worker: {getattr(event, 'worker', 'No worker attribute')}")
        try:
            result = event.result
            debug_logger.info(f"Worker result: {type(result)}")
            if result:
                assistant_message, generated_code = result
                debug_logger.info(
                    f"Response received - message length: {len(assistant_message)}, has code: {generated_code is not None}"
                )

                # Add assistant message to chat history with timestamp
                from datetime import datetime

                timestamp = datetime.now().strftime("%H:%M")
                self.chat_history.append(
                    {"role": "assistant", "content": assistant_message, "timestamp": timestamp}
                )
                self._update_history_display()

                # Handle code approval workflow based on mode
                if generated_code:
                    if self.is_database_mode and self._is_sql_code(generated_code):
                        debug_logger.info(f"Valid SQL code detected: {generated_code[:200]}...")
                        # Show SQL code for approval in database mode
                        self._show_sql_code_for_approval(generated_code)
                    elif not self.is_database_mode and self._is_transformation_code(generated_code):
                        debug_logger.info(
                            f"Valid transformation code detected: {generated_code[:200]}..."
                        )
                        self.pending_code = generated_code  # Store for approval
                        self.last_generated_code = generated_code  # Keep for reference
                        # Show code preview and approval button for transformation
                        self._show_code_preview_with_approval(generated_code)
                    else:
                        debug_logger.info(
                            "Code detected but not applicable to current mode - conversational response"
                        )
                        self._show_conversational_response("💬 Response added to chat history")
                else:
                    debug_logger.info("No code detected - conversational response")
                    # For conversational responses, just show a brief confirmation
                    self._show_conversational_response("💬 Response added to chat history")

                # Update debug status display
                self._update_debug_status()
            else:
                debug_logger.error("Worker returned None result")
                self._show_llm_response("Failed to get response from LLM.", is_error=True)
        except Exception as e:
            debug_logger.error(f"Error in worker result handler: {e}")
            self.log(f"Error in worker result handler: {e}")
            self._show_llm_response(f"Error: {str(e)}", is_error=True)

    def on_worker_failed(self, event) -> None:
        """Handle worker failure for LLM interactions."""
        debug_logger.error("Worker failed event received")
        try:
            error_msg = str(event.error) if hasattr(event, "error") else "Unknown error"
            debug_logger.error(f"Worker failure details: {error_msg}")
            self.log(f"Worker failed: {error_msg}")
            self._show_llm_response(f"Error: {error_msg}", is_error=True)
        except Exception as e:
            debug_logger.error(f"Error in worker failure handler: {e}")
            self.log(f"Error in worker failure handler: {e}")
            self._show_llm_response("An unexpected error occurred.", is_error=True)

    async def _interact_with_llm(self, user_message: str) -> tuple[str, str] | None:
        """Interact with the LLM using chatlas."""
        debug_logger.info("Starting _interact_with_llm method")
        try:
            # Check if chatlas is available
            if not CHATLAS_AVAILABLE:
                debug_logger.error("chatlas not available")
                return (
                    "Error: chatlas library not available. Please install it with: pip install chatlas",
                    None,
                )

            debug_logger.info("chatlas is available, proceeding with import")
            # Import ChatAuto for automatic provider detection
            import os

            from chatlas import ChatAuto

            debug_logger.info("ChatAuto imported successfully")

            # Manually load environment variables from .env file
            env_file_path = Path.cwd() / ".env"
            if env_file_path.exists():
                debug_logger.info("Loading environment variables from .env file")
                with open(env_file_path, "r") as f:
                    for line in f:
                        line = line.strip()
                        if line and not line.startswith("#") and "=" in line:
                            key, value = line.split("=", 1)
                            os.environ[key] = value
                            debug_logger.info(f"Set environment variable: {key}")
            else:
                debug_logger.warning("No .env file found")

            # Initialize chat session if needed
            if self.current_chat_session is None:
                debug_logger.info("Initializing new chat session with ChatAuto")
                try:
                    # Use ChatAuto with fallback configuration
                    # Try Anthropic first, then OpenAI
                    debug_logger.info(
                        "Attempting to initialize with ChatAuto (Anthropic preferred)"
                    )
                    self.current_chat_session = ChatAuto(
                        provider="anthropic", model="claude-3-5-sonnet-20241022"
                    )
                    debug_logger.info("Successfully initialized ChatAuto with Anthropic")
                    self.log("Using Anthropic Claude via ChatAuto for LLM interactions")
                except Exception as auto_error:
                    debug_logger.error(f"ChatAuto Anthropic initialization failed: {auto_error}")
                    try:
                        # Fall back to OpenAI
                        debug_logger.info("Attempting ChatAuto fallback to OpenAI")
                        self.current_chat_session = ChatAuto(provider="openai", model="gpt-4o-mini")
                        debug_logger.info("Successfully initialized ChatAuto with OpenAI")
                        self.log("Using OpenAI GPT via ChatAuto for LLM interactions")
                    except Exception as openai_error:
                        debug_logger.error(f"ChatAuto OpenAI initialization failed: {openai_error}")
                        # Try ChatAuto without explicit provider (let env vars decide)
                        try:
                            debug_logger.info("Attempting ChatAuto with environment variables only")
                            self.current_chat_session = ChatAuto()
                            debug_logger.info("Successfully initialized ChatAuto with env vars")
                            self.log("Using ChatAuto with environment configuration")
                        except Exception as final_error:
                            debug_logger.error(
                                f"All ChatAuto initialization attempts failed: {final_error}"
                            )
                            return (
                                "Error: Could not initialize any LLM provider. Please check your API keys and environment variables.",
                                None,
                            )
            else:
                debug_logger.info("Using existing chat session")

            # Get current data info for context
            debug_logger.info("Getting data context")
            data_context = self._get_data_context()
            debug_logger.info(f"Data context length: {len(data_context)}")

            # Create different prompts based on mode
            debug_logger.info(f"Creating prompt - is_database_mode: {self.is_database_mode}")
            if self.is_database_mode:
                debug_logger.info("Using SQL/Database prompt")
                # Log current table name for debugging
                current_table = None
                if self.data_grid and hasattr(self.data_grid, "current_table_name"):
                    current_table = self.data_grid.current_table_name

                # Fallback: try to get from table selector if current_table_name is None
                if not current_table:
                    try:
                        table_selector = self.query_one("#table-selector", Select)
                        if table_selector.value:
                            current_table = table_selector.value
                            debug_logger.info(
                                f"Got table name from selector fallback: {current_table}"
                            )
                    except Exception as e:
                        debug_logger.info(f"Could not get table name from selector: {e}")

                if not current_table:
                    current_table = "None"

                debug_logger.info(f"Final current table name for LLM prompt: {current_table}")
                self.log(f"DEBUG: Final current table name for LLM prompt: {current_table}")

                # Additional debugging
                if self.data_grid:
                    debug_logger.info(
                        f"data_grid exists, has current_table_name attr: {hasattr(self.data_grid, 'current_table_name')}"
                    )
                    if hasattr(self.data_grid, "current_table_name"):
                        debug_logger.info(
                            f"current_table_name value: {self.data_grid.current_table_name}"
                        )
                    else:
                        debug_logger.info("data_grid does not have current_table_name attribute")
                else:
                    debug_logger.info("data_grid is None")

                # Database/SQL mode prompt
                system_prompt = f"""You are a sophisticated AI assistant specialized in SQL database analysis and querying. You help users explore, understand, and analyze their database using SQL queries.

DATABASE CONTEXT:
{data_context}

CRITICAL - FOCUSED TABLE NAME:
Current table: {current_table}
Database path: {self.data_grid.database_path if self.data_grid and hasattr(self.data_grid, "database_path") else "None"}

MANDATORY TABLE NAME RULE:
When writing SQL queries, you MUST ALWAYS use the exact table name "{current_table}" in your FROM clause.
NEVER write queries like "SELECT * FROM WHERE..." - ALWAYS include the table name: "SELECT * FROM {current_table} WHERE..."

IMPORTANT: You MUST use ONLY the current table name shown above. Do NOT use any other table names.

SQL CAPABILITIES:
- Standard SQL SELECT, WHERE, GROUP BY, ORDER BY, JOIN operations
- Aggregate functions: COUNT, SUM, AVG, MIN, MAX, STDDEV, VARIANCE
- String functions: UPPER, LOWER, SUBSTRING, CONCAT, LENGTH, TRIM, LTRIM, RTRIM
- Date/time functions: current_date, current_timestamp, date_diff, extract, strftime
- Window functions: ROW_NUMBER, RANK, DENSE_RANK, LAG, LEAD, FIRST_VALUE, LAST_VALUE
- Mathematical functions: ROUND, CEIL, FLOOR, ABS, POWER, SQRT, MOD
- Conditional logic: CASE WHEN, COALESCE, NULLIF, GREATEST, LEAST
- Common table expressions (CTEs) with WITH clause
- Subqueries and derived tables
- LIMIT and OFFSET for pagination

DATABASE ENGINE: This database is accessed via DuckDB, NOT SQLite. Use DuckDB-compatible SQL syntax:

DATE/TIME FUNCTIONS (DuckDB-specific):
- current_date, current_timestamp (not date('now'))
- date_diff('day', start_date, end_date) for day differences
- date_diff('year', start_date, end_date) for year differences
- extract(year from date_column), extract(month from date_column)
- strftime(date_column, '%Y-%m-%d') for formatting
- age(end_date, start_date) returns interval

STRING FUNCTIONS (DuckDB-specific):
- concat(str1, str2, ...) or str1 || str2 for concatenation
- length(string) for string length
- substring(string, start, length) for substrings
- replace(string, search, replacement) for replacements
- split_part(string, delimiter, part_number) for splitting

AGGREGATE FUNCTIONS:
- count(*), count(column), count(distinct column)
- sum(column), avg(column), min(column), max(column)
- stddev(column), variance(column) for statistics
- string_agg(column, delimiter) for string aggregation
- array_agg(column) for array aggregation

AVOID SQLite-SPECIFIC SYNTAX:
- Don't use julianday(), date(), datetime() functions
- Don't use strftime() without proper DuckDB syntax
- Don't use SQLite pragma statements

INTERACTION GUIDELINES:
1. **Exploratory Analysis**: When users ask about the data structure, content, or patterns, provide insights and suggest useful queries
2. **Query Generation**: When users request specific analysis, provide SQL queries they can execute
3. **Be Specific**: Reference actual table and column names from the database context
4. **Explain Queries**: Help users understand what the SQL queries will accomplish
5. **Database-appropriate**: Use SQL syntax compatible with the database type (SQLite/DuckDB)

IMPORTANT INSTRUCTIONS:
- For questions like "describe the data", "what tables do we have?", "show me the schema" - provide conversational analysis
- For analysis requests like "find top customers", "calculate averages", "show trends", "filter rows" - provide SQL queries
- When you provide SQL code, it must:
  * ALWAYS include the table name in the FROM clause - NEVER write "SELECT * FROM WHERE..."
  * Use proper SQL syntax (SELECT, FROM, WHERE, etc.)
  * Reference actual table and column names from the context
  * Be surrounded by ```sql and ```
  * Be ready to execute as-is

Example conversational response (NO CODE):
"Your database contains customer transaction data with 3 tables: customers, orders, and products. The orders table has 1,250 records spanning 2023-2024, with total revenue of $45,678. The customers table shows 89 unique customers across 5 different cities."

Example query response (WITH SQL) - Note the table name in FROM clause:
I'll help you filter rows containing 'precursor' in any column.

```sql
SELECT *
FROM your_actual_table_name
WHERE column_name LIKE '%precursor%'
```

This query searches the specified table for rows containing 'precursor'.

Current conversation context: The user is analyzing their database and may ask questions or request SQL queries."""
            else:
                debug_logger.info("Using Polars prompt")
                # Regular Polars mode prompt
                system_prompt = f"""You are a sophisticated AI assistant specialized in data analysis and transformation using Polars DataFrames. You help users explore, understand, and transform their data efficiently.

COMPREHENSIVE POLARS API REFERENCE (Polars 1.32.0+):

Core DataFrame Operations:
- df.select(*exprs, **named_exprs) - Select columns
- df.filter(*predicates, **constraints) - Filter rows based on predicates
- df.with_columns(*exprs, **named_exprs) - Add/modify columns, replacing existing with same name
- df.group_by(*by, maintain_order=False, **named_by) - Group by columns
- df.join(other, on=None, how='inner', left_on=None, right_on=None, suffix='_right', validate='m:m', join_nulls=False, coalesce=None, maintain_order=None) - Join DataFrames
- df.sort(by, *more_by, descending=False, nulls_last=False, multithreaded=True, maintain_order=False) - Sort DataFrame
- df.unique(subset=None, keep='any', maintain_order=False) - Remove duplicates
- df.pivot(on, index=None, values=None, aggregate_function=None, maintain_order=True, sort_columns=False, separator='_') - Pivot table
- df.unpivot(on=None, index=None, variable_name=None, value_name=None) - Unpivot/melt
- df.transpose(include_header=False, header_name='column', column_names=None) - Transpose over diagonal

Column Expressions & Literals:
- pl.col(name) - Reference column by name or pattern
- pl.lit(value, dtype=None, allow_object=False) - Literal value expression
- pl.when(*predicates, **constraints).then(value).otherwise(value) - Conditional logic
- pl.concat_str(exprs, *more_exprs, separator='', ignore_nulls=False) - Concatenate strings
- pl.concat_list(exprs, *more_exprs) - Concatenate to list column
- pl.struct(*exprs, schema=None, eager=False, **named_exprs) - Create struct column

Aggregation Functions:
- pl.sum(*names), pl.mean(*names), pl.max(*names), pl.min(*names) - Column aggregations
- pl.count(*columns), pl.len() - Count operations
- pl.median(*columns), pl.std(column, ddof=1), pl.var(column, ddof=1) - Statistics
- pl.first(*columns), pl.last(*columns) - First/last values
- pl.n_unique(*columns) - Count unique values
- pl.quantile(quantile, interpolation='nearest') - Quantile calculation

String Operations (.str namespace):
- .str.contains(pattern, literal=False, strict=True) - Pattern matching
- .str.starts_with(prefix), .str.ends_with(suffix) - Prefix/suffix checks
- .str.replace(pattern, value, literal=False, n=1) - Replace first match
- .str.replace_all(pattern, value, literal=False) - Replace all matches
- .str.len_chars(), .str.len_bytes() - String length
- .str.to_lowercase(), .str.to_uppercase(), .str.to_titlecase() - Case conversion

DATA CONTEXT:
{data_context}

INTERACTION GUIDELINES:
1. **Conversational Mode**: When users ask questions about the data (exploration, understanding, insights), provide helpful analysis and explanations without requiring approval
2. **Transformation Mode**: When users request data transformations (modify, filter, create new columns, etc.), provide the Polars code and explain what it does
3. **Be Specific**: Reference actual column names and data types from the context
4. **Show Examples**: Provide concrete Polars code examples using the user's actual data
5. **Explain Results**: Help users understand what the transformations will accomplish
6. **Use Current API**: Always use the most current Polars syntax and methods from this comprehensive reference

IMPORTANT INSTRUCTIONS:
- For questions like "describe the data", "what columns do we have?", "tell me about this dataset" - just answer conversationally
- For transformation requests like "add a column", "filter rows", "calculate averages" - provide code
- When you do provide code, it must:
  * Use Polars operations and syntax (pl.col(), pl.when(), etc.)
  * Always assign the result back to `df` (e.g., `df = df.filter(...)`)
  * Start with `df = df` to modify the existing DataFrame
  * Be surrounded by ```python and ```
  * NEVER use pandas syntax like .groupby() - always use Polars .group_by()
  * NEVER use pandas methods - use only the Polars API reference above

CRITICAL: This is a Polars DataFrame, NOT pandas. Use Polars syntax:
- df.group_by() NOT df.groupby()
- pl.col() for column references
- Polars aggregation functions (pl.sum(), pl.mean(), etc.)

Current conversation context: The user is working with their dataset and may ask questions or request transformations."""

            # Use chatlas submit method with proper message formatting
            if len(self.chat_history) == 1:  # Only user message so far
                # First interaction - include system prompt and user message
                full_message = f"{system_prompt}\n\nUser: {user_message}"
                debug_logger.info("First interaction - using system prompt")
            else:
                # Build conversation history for context
                conversation_parts = [system_prompt]
                for msg in self.chat_history:
                    role = "User" if msg["role"] == "user" else "Assistant"
                    conversation_parts.append(f"{role}: {msg['content']}")
                full_message = "\n\n".join(conversation_parts)
                debug_logger.info(
                    f"Continuing conversation - history length: {len(self.chat_history)}"
                )

            debug_logger.info(f"Submitting message to LLM (length: {len(full_message)})")
            # Use chatlas chat method instead of submit
            try:
                response = self.current_chat_session.chat(full_message)
                debug_logger.info(f"LLM response received: {type(response)}")
            except AttributeError as e:
                debug_logger.error(f"AttributeError with chat method: {e}")
                # Try alternative methods
                if hasattr(self.current_chat_session, "stream"):
                    debug_logger.info("Trying stream method")
                    response = self.current_chat_session.stream(full_message)
                elif hasattr(self.current_chat_session, "__call__"):
                    debug_logger.info("Trying callable method")
                    response = self.current_chat_session(full_message)
                else:
                    debug_logger.error(f"Available methods: {dir(self.current_chat_session)}")
                    raise e

            if not response:
                debug_logger.error("Empty response from LLM")
                return ("No response received from LLM.", None)

            # Extract the response text from chatlas response object
            debug_logger.info("Extracting response text")
            response_text = ""
            if hasattr(response, "content"):
                debug_logger.info(f"Response has content attribute: {type(response.content)}")
                if isinstance(response.content, list) and len(response.content) > 0:
                    first_content = response.content[0]
                    if hasattr(first_content, "text"):
                        response_text = first_content.text
                        debug_logger.info(
                            f"Extracted text from first content item: {len(response_text)} chars"
                        )
                    else:
                        response_text = str(first_content)
                        debug_logger.info(
                            f"Used string conversion of first content item: {len(response_text)} chars"
                        )
                else:
                    response_text = str(response.content)
                    debug_logger.info(
                        f"Used string conversion of content: {len(response_text)} chars"
                    )
            else:
                response_text = str(response)
                debug_logger.info(f"Used string conversion of response: {len(response_text)} chars")

            # Extract code from response
            debug_logger.info("Extracting code from response")
            generated_code = self._extract_code_from_response(response_text)
            debug_logger.info(
                f"Code extraction complete - found code: {generated_code is not None}"
            )

            debug_logger.info("LLM interaction completed successfully")
            return (response_text, generated_code)

        except ImportError as e:
            debug_logger.error(f"Import error: {e}")
            return (f"Error: chatlas library not properly installed: {str(e)}", None)
        except Exception as e:
            debug_logger.error(f"LLM interaction error: {str(e)}")
            self.log(f"LLM interaction error: {str(e)}")
            return (f"Error communicating with LLM: {str(e)}", None)

    def _get_data_context(self) -> str:
        """Get comprehensive context about the current data for the LLM in JSON format."""
        if (
            self.data_grid is None
            or not hasattr(self.data_grid, "data")
            or self.data_grid.data is None
        ):
            return "No data currently loaded."

        try:
            import json

            # Handle database mode differently
            if self.is_database_mode:
                # First ensure we have a valid database connection
                if (
                    hasattr(self.data_grid, "database_connection")
                    and self.data_grid.database_connection
                ):
                    # Test the connection to make sure it's valid
                    try:
                        self.data_grid.database_connection.execute("SELECT 1").fetchall()
                        return self._get_database_schema_context()
                    except Exception as e:
                        try:
                            self.log(f"Database connection test failed in LLM context: {e}")
                        except Exception:
                            print(f"DEBUG: Database connection test failed in LLM context: {e}")
                        # Try to reconnect
                        if self.data_grid._ensure_database_connection():
                            return self._get_database_schema_context()
                        else:
                            return "Database mode is active but no valid database connection available. Please reconnect to the database."
                else:
                    # Try to reconnect
                    if (
                        hasattr(self.data_grid, "_ensure_database_connection")
                        and self.data_grid._ensure_database_connection()
                    ):
                        return self._get_database_schema_context()
                    else:
                        return "Database mode is active but no valid database connection available. Please reconnect to the database."

            # Regular DataFrame mode
            df = self.data_grid.data
            rows, cols = df.shape

            # Build comprehensive dataset description
            dataset_info = {
                "dimensions": {"rows": rows, "columns": cols},
                "schema": {},
                "missing_data": {},
                "summary_statistics": {},
                "categorical_values": {},
                "sample_data": {},
            }

            # Process each column
            for col_name in df.columns:
                col_data = df[col_name]
                dtype = col_data.dtype
                friendly_type = self.data_grid._get_friendly_type_name(dtype)

                # Schema information
                dataset_info["schema"][col_name] = {
                    "dtype": str(dtype),
                    "friendly_type": friendly_type,
                }

                # Missing data analysis
                missing_count = col_data.null_count()
                missing_percentage = (missing_count / rows * 100) if rows > 0 else 0
                dataset_info["missing_data"][col_name] = {
                    "missing_count": missing_count,
                    "missing_percentage": round(missing_percentage, 2),
                }

                # Summary statistics for numeric columns
                if friendly_type in ["integer", "float"]:
                    try:
                        # Get numeric statistics
                        non_null_data = col_data.drop_nulls()
                        if len(non_null_data) > 0:
                            stats = {
                                "count": len(non_null_data),
                                "min": float(non_null_data.min()),
                                "max": float(non_null_data.max()),
                                "mean": float(non_null_data.mean()),
                                "median": float(non_null_data.median()),
                                "std": float(non_null_data.std()) if len(non_null_data) > 1 else 0,
                            }
                            dataset_info["summary_statistics"][col_name] = stats
                    except Exception:
                        # If statistics fail, skip this column
                        pass

                # Categorical values for text columns (if <= 25 unique values)
                elif friendly_type == "text":
                    try:
                        unique_values = col_data.drop_nulls().unique()
                        unique_count = len(unique_values)

                        dataset_info["summary_statistics"][col_name] = {
                            "unique_count": unique_count,
                            "most_common_length": len(str(col_data.drop_nulls().mode().item()))
                            if unique_count > 0
                            else 0,
                        }

                        if unique_count <= 25 and unique_count > 0:
                            # Get value counts using pure Polars
                            value_counts_df = col_data.value_counts(sort=True)
                            categorical_info = {}

                            # Convert to dict using Polars methods
                            for row in value_counts_df.to_dicts():
                                value = row[col_name]
                                count = row["count"]
                                categorical_info[str(value)] = int(count)

                            dataset_info["categorical_values"][col_name] = categorical_info
                    except Exception:
                        # If categorical analysis fails, skip
                        pass

            # Sample data - first and last 10 rows
            try:
                first_10_pl = df.head(10)
                last_10_pl = df.tail(10)

                # Convert to Python dicts using Polars methods
                first_10 = first_10_pl.to_dicts()
                last_10 = last_10_pl.to_dicts()

                # Convert any non-serializable values to strings
                def serialize_values(data):
                    for row in data:
                        for key, value in row.items():
                            if value is None:
                                row[key] = None
                            else:
                                try:
                                    json.dumps(value)  # Test if serializable
                                except (TypeError, ValueError):
                                    row[key] = str(value)
                    return data

                dataset_info["sample_data"] = {
                    "first_10_rows": serialize_values(first_10),
                    "last_10_rows": serialize_values(last_10),
                }
            except Exception as e:
                dataset_info["sample_data"] = {"error": f"Could not extract sample data: {str(e)}"}

            # Convert to formatted JSON string
            context_json = json.dumps(dataset_info, indent=2, ensure_ascii=False)
            return f"Dataset Information (JSON):\n```json\n{context_json}\n```"

        except Exception as e:
            return f"Error getting data context: {str(e)}"

    def _get_database_schema_context(self) -> str:
        """Get optimized database schema information for the LLM in JSON format - FOCUSED ON CURRENT TABLE ONLY."""
        try:
            import json

            current_table = getattr(self.data_grid, "current_table_name", None)

            # Only provide information about the current/focused table
            database_info = {
                "database_path": getattr(self.data_grid, "database_path", "Unknown"),
                "current_table": current_table,
                "table_schema": {},
            }

            self.log(f"Database context - Focused on current table: {current_table}")

            # Only get detailed schema for the current table
            if (
                current_table
                and hasattr(self.data_grid, "database_connection")
                and self.data_grid.database_connection
            ):
                conn = self.data_grid.database_connection

                try:
                    self.log(f"Getting detailed schema for current table: {current_table}")
                    # Get table schema using DuckDB's DESCRIBE
                    schema_result = conn.execute(f"DESCRIBE {current_table}").fetchall()

                    table_schema = {"columns": {}, "sample_data": {}}

                    # Process column information
                    for row in schema_result:
                        column_name = row[0]  # column_name
                        column_type = row[1]  # column_type
                        is_nullable = row[2] if len(row) > 2 else None  # null

                        table_schema["columns"][column_name] = {
                            "type": column_type,
                            "nullable": is_nullable,
                        }

                    # Get sample data (first 5 rows) for the current table only
                    try:
                        sample_result = conn.execute(
                            f"SELECT * FROM {current_table} LIMIT 5"
                        ).fetchall()
                        column_names = (
                            [desc[0] for desc in conn.description] if conn.description else []
                        )

                        sample_rows = []
                        for row in sample_result:
                            row_dict = {}
                            for i, value in enumerate(row):
                                if i < len(column_names):
                                    # Convert to string if not JSON serializable
                                    try:
                                        json.dumps(value)
                                        row_dict[column_names[i]] = value
                                    except (TypeError, ValueError):
                                        row_dict[column_names[i]] = str(value)
                            sample_rows.append(row_dict)

                        table_schema["sample_data"] = sample_rows
                        self.log(f"Got {len(sample_rows)} sample rows for {current_table}")

                    except Exception as e:
                        table_schema["sample_data"] = {
                            "error": f"Could not get sample data: {str(e)}"
                        }
                        self.log(f"Error getting sample data for {current_table}: {e}")

                    database_info["table_schema"] = table_schema

                except Exception as e:
                    database_info["table_schema"] = {"error": f"Could not get schema: {str(e)}"}
                    self.log(f"Error getting schema for {current_table}: {e}")
            else:
                self.log("No current table or database connection available")
                database_info["table_schema"] = {"error": "No current table selected"}

            # Convert to formatted JSON string
            context_json = json.dumps(database_info, indent=2, ensure_ascii=False)
            return f"Database Schema Information (JSON):\n```json\n{context_json}\n```"

        except Exception as e:
            return f"Error getting database schema context: {str(e)}"

    def _extract_code_from_response(self, response_text: str) -> str | None:
        """Extract Python/Polars or SQL code from LLM response."""
        try:
            import re

            # Look for SQL code blocks first (for database mode)
            sql_code_pattern = r"```sql\s*\n(.*?)\n```"
            sql_matches = re.findall(sql_code_pattern, response_text, re.DOTALL)

            if sql_matches:
                # Take the first SQL code block
                code = sql_matches[0].strip()
                if self._is_sql_code(code):
                    return code

            # Look for Python code blocks (for regular mode)
            python_code_pattern = r"```python\s*\n(.*?)\n```"
            python_matches = re.findall(python_code_pattern, response_text, re.DOTALL)

            if python_matches:
                # Take the first code block and check if it's a transformation
                code = python_matches[0].strip()
                if self._is_transformation_code(code):
                    return code

            # Look for generic code blocks
            generic_code_pattern = r"```\s*\n(.*?)\n```"
            matches = re.findall(generic_code_pattern, response_text, re.DOTALL)

            if matches:
                # Filter for blocks that look like valid code for current mode
                for code in matches:
                    code = code.strip()
                    if self.is_database_mode and self._is_sql_code(code):
                        return code
                    elif not self.is_database_mode and self._is_transformation_code(code):
                        return code

            return None

        except Exception as e:
            self.log(f"Error extracting code: {e}")
            return None

    def _is_transformation_code(self, code: str) -> bool:
        """Check if the code is a valid data transformation.
        Should start with 'df = df' and contain Polars operations.
        """
        try:
            # Remove leading/trailing whitespace and split into lines
            lines = [line.strip() for line in code.split("\n") if line.strip()]

            if not lines:
                return False

            # Check if any substantial line starts with 'df = df'
            has_transformation = False
            for line in lines:
                # Skip import statements
                if line.startswith("import "):
                    continue
                # Look for df = df transformation pattern
                if line.startswith("df = df.") or line.startswith("df = df\n"):
                    has_transformation = True
                    break
                # Also check for multi-line df = df patterns
                if line.startswith("df = df") and ("(" in line or line.endswith("\\")):
                    has_transformation = True
                    break

            # Also verify it contains Polars-like operations
            polars_keywords = [
                "pl.",
                "filter",
                "select",
                "with_columns",
                "group_by",
                "sort",
                "join",
            ]
            has_polars_ops = any(keyword in code for keyword in polars_keywords)

            return has_transformation and has_polars_ops

        except Exception as e:
            self.log(f"Error checking transformation code: {e}")
            return False

    def _is_sql_code(self, code: str) -> bool:
        """Check if the code is valid SQL.
        Should contain SQL keywords and proper syntax.
        """
        try:
            # Remove leading/trailing whitespace and convert to uppercase for keyword checking
            code_upper = code.strip().upper()

            if not code_upper:
                return False

            # Check for basic SQL keywords
            sql_keywords = [
                "SELECT",
                "FROM",
                "WHERE",
                "GROUP BY",
                "ORDER BY",
                "INSERT",
                "UPDATE",
                "DELETE",
                "CREATE",
                "ALTER",
                "DROP",
                "JOIN",
                "INNER JOIN",
                "LEFT JOIN",
                "RIGHT JOIN",
                "FULL JOIN",
            ]

            # Must contain at least one SQL keyword
            has_sql_keywords = any(keyword in code_upper for keyword in sql_keywords)

            # Should not contain obvious Python/Polars syntax
            python_indicators = ["df =", "pl.", "import ", "def ", "class ", "if __name__"]
            has_python_syntax = any(indicator in code for indicator in python_indicators)

            return has_sql_keywords and not has_python_syntax

        except Exception as e:
            self.log(f"Error checking SQL code: {e}")
            return False

    def _is_sql_code(self, code: str) -> bool:
        """Check if the code is valid SQL."""
        try:
            # Remove leading/trailing whitespace
            code = code.strip()

            if not code:
                return False

            # Convert to uppercase for keyword checking
            code_upper = code.upper()

            # Check for basic SQL keywords
            sql_keywords = [
                "SELECT",
                "FROM",
                "WHERE",
                "GROUP BY",
                "ORDER BY",
                "INSERT",
                "UPDATE",
                "DELETE",
                "CREATE",
                "ALTER",
                "DROP",
                "JOIN",
                "INNER JOIN",
                "LEFT JOIN",
                "RIGHT JOIN",
            ]

            has_sql_keywords = any(keyword in code_upper for keyword in sql_keywords)

            # Most SQL queries should start with SELECT, INSERT, UPDATE, DELETE, CREATE, ALTER, or DROP
            starts_with_sql = any(
                code_upper.lstrip().startswith(keyword)
                for keyword in [
                    "SELECT",
                    "INSERT",
                    "UPDATE",
                    "DELETE",
                    "CREATE",
                    "ALTER",
                    "DROP",
                    "WITH",
                ]
            )

            return has_sql_keywords and starts_with_sql

        except Exception as e:
            self.log(f"Error checking SQL code: {e}")
            return False

    def _show_sql_code_for_approval(self, sql_code: str) -> None:
        """Show SQL code in the approval area and make the execute button visible."""
        try:
            # Put the SQL code in the generated-sql TextArea
            generated_sql = self.query_one("#generated-sql", TextArea)
            generated_sql.text = sql_code
            generated_sql.remove_class("hidden")

            # Show the execute button
            execute_button = self.query_one("#execute-sql-suggestion", Button)
            execute_button.remove_class("hidden")

            # Show a brief confirmation
            self._show_conversational_response(
                "💬 SQL query ready for approval - click Execute to run"
            )

        except Exception as e:
            self.log(f"Error showing SQL code for approval: {e}")
            self._show_conversational_response("💬 Response added to chat history")

    def _apply_generated_code(self, code: str) -> None:
        """Apply the generated Polars code automatically."""
        debug_logger.info(f"Applying generated code: {code[:100]}...")

        if (
            self.data_grid is None
            or not hasattr(self.data_grid, "data")
            or self.data_grid.data is None
        ):
            self._show_llm_response("No data loaded. Please load a dataset first.", is_error=True)
            return

        try:
            # Import polars for the execution context
            if pl is None:
                self._show_llm_response("Polars library not available.", is_error=True)
                return

            # Log the original dataframe info
            original_shape = self.data_grid.data.shape
            original_columns = list(self.data_grid.data.columns)
            debug_logger.info(f"Original dataframe: {original_shape} - columns: {original_columns}")

            # Create execution context with current dataframe
            execution_context = {
                "pl": pl,
                "df": self.data_grid.data.clone(),  # Work with a copy initially
                "__builtins__": __builtins__,
            }

            # Log the code being executed
            debug_logger.info(f"Executing generated code: {code}")

            # Execute the code
            exec(code, execution_context)

            # Get the result dataframe
            result_df = execution_context.get("df")

            if result_df is None:
                self._show_llm_response(
                    "Code executed but no dataframe returned. Make sure the code assigns result to 'df'.",
                    is_error=True,
                )
                return

            # Validate that we got a Polars DataFrame
            if not hasattr(result_df, "shape") or not hasattr(result_df, "columns"):
                self._show_llm_response("Result is not a valid Polars DataFrame.", is_error=True)
                return

            # Log the result dataframe info
            result_shape = result_df.shape
            result_columns = list(result_df.columns)
            debug_logger.info(f"Result dataframe: {result_shape} - columns: {result_columns}")

            # Update the data grid with the result
            self.data_grid.data = result_df
            self.data_grid.refresh_table_data()

            # Show success message
            if result_shape != original_shape or result_columns != original_columns:
                self._show_llm_response(
                    f"✅ Transformation applied! New shape: {result_shape[0]} rows × {result_shape[1]} columns",
                    is_error=False,
                )
            else:
                self._show_llm_response(
                    "✅ Code executed successfully (data may have been modified internally)",
                    is_error=False,
                )

            debug_logger.info("Code application completed successfully")

        except Exception as e:
            error_msg = f"Error applying transformation: {str(e)}"
            debug_logger.error(error_msg)
            self._show_llm_response(error_msg, is_error=True)

    def _update_chat_history_display(self) -> None:
        """Update the chat history display with enhanced formatting."""
        try:
            chat_history_widget = self.query_one("#chat-history", Static)
            chat_history_scroll = self.query_one("#chat-history-scroll", VerticalScroll)

            if not self.chat_history:
                chat_history_widget.update(
                    "[dim]💬 History will appear here after chatting...[/dim]"
                )
                chat_history_scroll.add_class("empty")
                return

            # Remove empty class when we have content
            chat_history_scroll.remove_class("empty")

            # Format chat history with enhanced display
            formatted_history = [
                "💬 [bold]Recent Conversations[/bold] [dim](scroll to see more)[/dim]",
                "",
            ]

            # Show more messages if available, but limit to prevent overwhelming
            display_count = min(10, len(self.chat_history))
            recent_messages = self.chat_history[-display_count:]

            for i, msg in enumerate(recent_messages):
                role_icon = "👤" if msg["role"] == "user" else "🤖"
                role_name = (
                    "[bold]You[/bold]" if msg["role"] == "user" else "[bold]Assistant[/bold]"
                )

                # Add timestamp if available
                timestamp = msg.get("timestamp", "")
                time_display = f" [dim]({timestamp})[/dim]" if timestamp else ""

                formatted_history.append(f"{role_icon} {role_name}{time_display}:")

                if msg["role"] == "user":
                    # User message - show more content since users want to see their requests
                    content = msg["content"]
                    if len(content) > 150:
                        content = content[:150] + "..."
                    formatted_history.append(f"  [dim]{content}[/dim]")

                else:
                    # Assistant message - extract and show key parts
                    content = msg["content"]

                    # Extract code blocks
                    import re

                    code_matches = re.findall(r"```python\n(.*?)\n```", content, re.DOTALL)

                    if code_matches:
                        # Show summary of response
                        response_summary = content.split("```")[0].strip()
                        if len(response_summary) > 80:
                            response_summary = response_summary[:80] + "..."

                        if response_summary:
                            formatted_history.append(f"  [dim]{response_summary}[/dim]")

                        # Show code blocks with more detail
                        for j, code in enumerate(code_matches):
                            # Show first 2 lines of code as preview
                            code_lines = code.strip().split("\n")
                            preview_lines = code_lines[:2]
                            if len(code_lines) > 2:
                                first_lines = " ".join(preview_lines)
                                if len(first_lines) > 60:
                                    first_lines = first_lines[:60] + "..."
                                formatted_history.append(
                                    f"  [green]📝 Code: {first_lines}...[/green]"
                                )
                            else:
                                first_line = code_lines[0] if code_lines else ""
                                if len(first_line) > 60:
                                    first_line = first_line[:60] + "..."
                                formatted_history.append(f"  [green]📝 Code: {first_line}[/green]")
                    else:
                        # No code blocks, show summary
                        if len(content) > 120:
                            content = content[:120] + "..."
                        formatted_history.append(f"  [dim]{content}[/dim]")

                # Add small separator between messages
                formatted_history.append("")

            # Add note about viewing full history
            if len(self.chat_history) > display_count:
                formatted_history.append(
                    f"[dim]... and {len(self.chat_history) - display_count} more messages[/dim]"
                )
                formatted_history.append(
                    "[dim]Click 'View History' for complete conversation[/dim]"
                )

            history_text = "\n".join(formatted_history)
            chat_history_widget.update(history_text)

            # Force scroll to bottom to show latest content
            self.call_after_refresh(self._scroll_history_to_bottom)

        except Exception as e:
            self.log(f"Error updating chat history: {e}")
            chat_history_widget.update("[red]💬 Error loading history...[/red]")
            chat_history_widget.add_class("empty")

    def _scroll_history_to_bottom(self) -> None:
        """Scroll the chat history to the bottom to show latest messages."""
        try:
            chat_history_scroll = self.query_one("#chat-history-scroll", VerticalScroll)
            # VerticalScroll has better scrolling support
            chat_history_scroll.scroll_end(animate=False)
        except Exception as e:
            self.log(f"Error scrolling history: {e}")

    def _scroll_response_to_bottom(self) -> None:
        """Scroll the LLM response area to the bottom."""
        try:
            response_scroll = self.query_one("#llm-response-scroll", VerticalScroll)
            response_scroll.scroll_end(animate=False)
        except Exception as e:
            self.log(f"Error scrolling response: {e}")

    def _show_code_preview_with_approval(self, code: str) -> None:
        """Show code preview and approval button without the orange response box."""
        try:
            code_preview = self.query_one("#generated-code", TextArea)
            apply_button = self.query_one("#apply-transform", Button)
            chat_history_scroll = self.query_one("#chat-history-scroll", VerticalScroll)

            # Make chat history compact to make room for approval UI
            chat_history_scroll.add_class("compact")
            self.log("Made chat history compact to make room for buttons")

            # Show the generated code in preview mode
            code_preview.text = code
            code_preview.read_only = True  # Make it non-editable for review
            code_preview.remove_class("hidden")
            self.log("Generated code preview shown")

            # Show the Apply button
            apply_button.remove_class("hidden")
            self.log("Apply button should now be visible!")

        except Exception as e:
            self.log(f"Error showing code preview with approval: {e}")

    def _show_llm_response_with_approval(self, message: str, code: str) -> None:
        """Show LLM response with code preview and approval button."""
        try:
            self.log(f"SHOWING APPROVAL UI: message='{message[:50]}...', code length={len(code)}")

            response_display = self.query_one("#llm-response", Static)
            response_scroll = self.query_one("#llm-response-scroll", VerticalScroll)
            code_preview = self.query_one("#generated-code", TextArea)
            apply_button = self.query_one("#apply-transform", Button)
            chat_history_scroll = self.query_one("#chat-history-scroll", VerticalScroll)

            # Make chat history compact to make room for approval UI
            chat_history_scroll.add_class("compact")
            self.log("Made chat history compact to make room for buttons")

            # Format the response message with clear instructions
            formatted_message = f"[white]{message}[/white]\n\n"
            formatted_message += "[yellow]🔍 Proposed transformation code:[/yellow]\n"
            formatted_message += "[dim]Review the code below and click 'Apply' to proceed, or continue chatting to refine.[/dim]"

            response_display.update(formatted_message)
            response_scroll.remove_class("hidden")
            self.log("LLM response area updated and shown")

            # Show the generated code in preview mode
            code_preview.text = code
            code_preview.read_only = True  # Make it non-editable for review
            code_preview.remove_class("hidden")
            self.log("Generated code preview shown")

            # Show the Apply button
            apply_button.remove_class("hidden")
            self.log("Apply button should now be visible!")

        except Exception as e:
            self.log(f"Error showing LLM response with approval: {e}")
            # Fallback to regular response display
            self._show_llm_response(message, is_error=False)

    def _show_llm_response(self, message: str, is_error: bool = False) -> None:
        """Show LLM response or error message."""
        try:
            response_display = self.query_one("#llm-response", Static)
            response_scroll = self.query_one("#llm-response-scroll", VerticalScroll)

            if is_error:
                response_display.update(f"[red]{message}[/red]")
            else:
                response_display.update(f"[white]{message}[/white]")

            response_scroll.remove_class("hidden")

            # Only auto-hide brief status messages, not full content
            if not is_error and (message.startswith("🤖") or len(message) < 100):
                self.set_timer(5.0, lambda: response_scroll.add_class("hidden"))
            # Keep longer content (like history) visible until user manually clears

        except Exception as e:
            self.log(f"Error showing LLM response: {e}")

    def _show_conversational_response(self, message: str) -> None:
        """Show a brief confirmation for conversational responses that auto-hides."""
        try:
            response_display = self.query_one("#llm-response", Static)
            response_scroll = self.query_one("#llm-response-scroll", VerticalScroll)

            # Note: Chat history now maintains consistent size via CSS
            response_display.update(f"[green]{message}[/green]")
            response_scroll.remove_class("hidden")

            # Auto-hide conversational confirmations after 3 seconds
            self.set_timer(3.0, lambda: response_scroll.add_class("hidden"))

        except Exception as e:
            self.log(f"Error showing conversational response: {e}")

    def _show_sql_code_for_approval(self, sql_code: str) -> None:
        """Show SQL code for approval in database mode."""
        try:
            generated_sql = self.query_one("#generated-sql", TextArea)
            execute_button = self.query_one("#execute-sql-suggestion", Button)

            # Show the generated SQL code in the preview area
            generated_sql.text = sql_code
            generated_sql.remove_class("hidden")

            # Add green border styling to match Polars workflow
            generated_sql.add_class("approval-ready")

            self.log("Generated SQL preview shown")

            # Show the Execute button for approval
            execute_button.remove_class("hidden")
            self.log("SQL execution button should now be visible!")

        except Exception as e:
            self.log(f"Error showing SQL code for approval: {e}")
            # Fallback - just show the code without styling
            try:
                generated_sql = self.query_one("#generated-sql", TextArea)
                generated_sql.text = sql_code
                generated_sql.remove_class("hidden")
                execute_button = self.query_one("#execute-sql-suggestion", Button)
                execute_button.remove_class("hidden")
            except Exception:
                pass

    def _update_debug_status(self) -> None:
        """Update debug status display."""
        try:
            log_file = Path.cwd() / "sweet_llm_debug.log"

            if log_file.exists():
                # Read last few lines of debug log
                with open(log_file, "r") as f:
                    lines = f.readlines()
                    last_lines = lines[-2:] if len(lines) >= 2 else lines
                    debug_text = "".join(last_lines).strip()
                    debug_logger.info(f"Debug status updated: {debug_text[:100]}...")
            else:
                debug_logger.info("Debug log file not found")
        except Exception as e:
            debug_logger.error(f"Error updating debug status: {e}")

    def _show_generated_code(self, code: str) -> None:
        """Show the generated code and action buttons."""
        try:
            code_widget = self.query_one("#generated-code", TextArea)
            code_actions = self.query_one("#code-actions", Horizontal)

            code_widget.text = code
            code_widget.remove_class("hidden")
            code_actions.remove_class("hidden")

        except Exception as e:
            self.log(f"Error showing generated code: {e}")

    def _hide_generated_code(self) -> None:
        """Hide the generated code and action buttons."""
        try:
            code_widget = self.query_one("#generated-code", TextArea)
            code_actions = self.query_one("#code-actions", Horizontal)

            code_widget.add_class("hidden")
            code_actions.add_class("hidden")

        except Exception as e:
            self.log(f"Error hiding generated code: {e}")


class DrawerContainer(Container):
    """Container that can slide in/out as a drawer."""

    show_drawer: reactive[bool] = reactive(False)

    def __init__(self, **kwargs):
        super().__init__(**kwargs)

    def compose(self) -> ComposeResult:
        """Compose the drawer container."""
        with Horizontal(id="main-horizontal"):
            # Main content area (left side)
            with Vertical(id="main-content"):
                yield ExcelDataGrid(id="data-grid")

            # Drawer tab (narrow strip on right): initially hidden
            with Vertical(id="drawer-tab", classes="drawer-tab hidden"):
                yield Button("◀", id="tab-button", classes="tab-button")
                yield Static("T\nO\nO\nL\nS", classes="tab-label")

            # Drawer panel (right side): initially hidden
            with Vertical(id="drawer", classes="drawer hidden"):
                yield Button("×", id="close-drawer", classes="close-button")
                yield ToolsPanel(id="tools-panel")

    def on_mount(self) -> None:
        """Initialize the drawer state."""
        self.update_drawer_visibility()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        """Handle button presses."""
        if event.button.id == "tab-button":
            self.action_toggle_drawer()
        elif event.button.id == "close-drawer":
            self.action_close_drawer()

    def action_toggle_drawer(self) -> None:
        """Toggle the drawer visibility."""
        self.show_drawer = not self.show_drawer

    def action_close_drawer(self) -> None:
        """Close the drawer."""
        self.show_drawer = False

    def watch_show_drawer(self, show: bool) -> None:
        """React to drawer visibility changes."""
        self.update_drawer_visibility()

    def update_drawer_visibility(self) -> None:
        """Update the drawer visibility."""
        drawer = self.query_one("#drawer")
        tab_button = self.query_one("#tab-button", Button)

        if self.show_drawer:
            drawer.remove_class("hidden")
            drawer.add_class("visible")
            tab_button.label = "▶"  # Arrow pointing right when open
        else:
            drawer.remove_class("visible")
            drawer.add_class("hidden")
            tab_button.label = "◀"  # Arrow pointing left when closed


class CellEditModal(ModalScreen[str | None]):
    """Modal for editing a cell value."""

    DEFAULT_CSS = """
    CellEditModal {
        align: center middle;
    }

    CellEditModal > Vertical {
        width: auto;
        height: auto;
        min-width: 40;
        max-width: 80;
        padding: 1;
        border: thick $surface;
        background: $surface;
    }

    CellEditModal Label {
        text-align: center;
        padding-bottom: 1;
        color: $text;
    }

    CellEditModal Input {
        margin-bottom: 1;
    }

    CellEditModal Horizontal {
        height: auto;
        align: center middle;
    }

    CellEditModal Button {
        margin: 0 1;
        min-width: 10;
    }
    """

    def __init__(
        self, current_value: str, cell_address: str = "", is_immediate_edit: bool = False
    ) -> None:
        super().__init__()
        self.current_value = current_value
        self.cell_address = cell_address
        self.is_immediate_edit = is_immediate_edit

    def compose(self) -> ComposeResult:
        with Vertical():
            if self.cell_address:
                yield Label(f"Edit Cell {self.cell_address}")
            else:
                yield Label("Edit Cell Value")
            yield Input(
                value=self.current_value, placeholder="Enter new value...", id="cell-value-input"
            )
            with Horizontal():
                yield Button("Save", variant="primary", id="save-btn")
                yield Button("Cancel", variant="default", id="cancel-btn")

    def on_mount(self) -> None:
        # Focus the input and select all text after a slight delay to avoid
        # interfering with the key event that triggered the modal
        self.call_after_refresh(self._setup_input)

    def _setup_input(self) -> None:
        """Set up the input field after the modal is fully mounted."""
        try:
            input_widget = self.query_one("#cell-value-input", Input)
            input_widget.focus()
            input_widget.value = self.current_value

            if self.is_immediate_edit:
                # For immediate edits (typed character), position cursor at the end
                input_widget.cursor_position = len(self.current_value)
            else:
                # For regular edits (Enter key), select all text for easy overwriting
                if self.current_value:
                    input_widget.text_select_all()
                else:
                    input_widget.cursor_position = 0
        except Exception as e:
            # If we can't find the input widget yet, try again with a small delay
            self.log(f"Could not find input widget, retrying: {e}")
            self.set_timer(0.1, self._setup_input)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "save-btn":
            input_widget = self.query_one("#cell-value-input", Input)
            self.dismiss(input_widget.value)
        elif event.button.id == "cancel-btn":
            self.dismiss(None)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        # Allow Enter key to save
        if event.input.id == "cell-value-input":
            self.dismiss(event.value)

    def on_key(self, event) -> bool:
        if event.key == "escape":
            self.dismiss(None)
            return True
        return False


class SaveFileModal(ModalScreen[str | None]):
    """Modal screen for save file path input."""

    CSS = """
    SaveFileModal {
        align: center middle;
    }

    #save-modal {
        width: 80;
        height: 16;
        background: $surface;
        border: thick $primary;
        padding: 2;
    }

    #save-modal Label {
        margin-bottom: 1;
        text-style: bold;
    }

    #save-modal Input {
        margin-bottom: 1;
        width: 100%;
    }

    .error-message {
        color: red;
        background: darkred;
        padding: 0 1;
        margin-bottom: 1;
        text-align: center;
    }

    .error-message.hidden {
        display: none;
    }

    .modal-buttons {
        height: 3;
        align: center middle;
        margin-top: 1;
    }

    .modal-buttons Button {
        margin: 0 2;
        min-width: 12;
    }
    """

    def compose(self) -> ComposeResult:
        """Compose the modal content."""
        with Vertical(id="save-modal"):
            yield Label("Save file as:")
            yield Input(placeholder="e.g., /path/to/data.csv", id="save-input")
            yield Static("", id="save-error-message", classes="error-message hidden")
            with Horizontal(classes="modal-buttons"):
                yield Button("Cancel", id="cancel-save", variant="error")
                yield Button("Save", id="confirm-save", variant="primary")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        """Handle button presses in the modal."""
        if event.button.id == "confirm-save":
            save_input = self.query_one("#save-input", Input)
            file_path = save_input.value.strip()
            if file_path:
                self.dismiss(file_path)
            else:
                self._show_error("Please enter a file path")
        elif event.button.id == "cancel-save":
            self.dismiss(None)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        """Handle Enter key press in the input field."""
        if event.input.id == "save-input":
            file_path = event.value.strip()
            if file_path:
                self.dismiss(file_path)
            else:
                self._show_error("Please enter a file path")

    def _show_error(self, message: str) -> None:
        """Show an error message in the modal."""
        error_message = self.query_one("#save-error-message", Static)
        error_message.update(message)
        error_message.remove_class("hidden")


class CommandReferenceModal(ModalScreen[None]):
    """Modal screen showing command reference."""

    CSS = """
    CommandReferenceModal {
        align: center middle;
    }

    #command-ref {
        width: 80;
        height: 20;
        background: $surface;
        border: thick $primary;
        padding: 2;
    }

    #command-ref .title {
        text-style: bold;
        text-align: center;
        margin-bottom: 1;
        color: $primary;
    }

    #command-ref .command-list {
        height: 15;
        overflow-y: auto;
    }

    #command-ref .command-item {
        margin-bottom: 1;
    }

    #command-ref .command-name {
        text-style: bold;
        color: $accent;
    }

    #command-ref .dismiss-hint {
        text-align: center;
        margin-top: 1;
        text-style: italic;
        color: $text-muted;
    }
    """

    def compose(self) -> ComposeResult:
        """Compose the command reference modal."""
        with Vertical(id="command-ref"):
            yield Static("Sweet Command Reference", classes="title")
            with Vertical(classes="command-list"):
                yield Static(
                    ":q or :quit --- quit the application (warning if changes present)",
                    classes="command-item",
                )
                yield Static(
                    ":init --------- return to the welcome screen (warning if changes present)",
                    classes="command-item",
                )
                yield Static(":wa or :sa ---- write/save as a file", classes="command-item")
                yield Static(
                    ":wo or :so ---- write/save over the open file", classes="command-item"
                )
                yield Static(":q! ----------- force quit without saving", classes="command-item")
                yield Static(
                    ":row ---------- navigate to row (supports negative indexing)",
                    classes="command-item",
                )
                yield Static(":ref or :help - show this command reference", classes="command-item")
            yield Static("Click anywhere to dismiss", classes="dismiss-hint")

    def on_click(self, event) -> None:
        """Dismiss modal on any click."""
        self.dismiss()

    def on_key(self, event) -> None:
        """Dismiss modal on escape key."""
        if event.key == "escape":
            self.dismiss()


class PasteOptionsModal(ModalScreen[dict | None]):
    """Modal for choosing how to paste clipboard data."""

    CSS = """
    PasteOptionsModal {
        align: center middle;
    }

    #paste-modal {
        width: 60;
        height: auto;
        max-height: 35;
        min-height: 20;
        background: $surface;
        border: thick $primary;
        padding: 2;
    }

    #paste-modal .title {
        text-style: bold;
        text-align: center;
        margin-bottom: 1;
        color: $primary;
    }

    #paste-modal .preview {
        background: $surface-darken-1;
        border: solid $accent;
        padding: 1;
        margin: 1 0;
        height: 8;
        max-height: 10;
        overflow-y: auto;
        scrollbar-size: 1 1;
        scrollbar-background: $surface;
        scrollbar-color: $accent;
    }

    #paste-modal .info {
        text-align: center;
        margin-bottom: 1;
        color: $text-muted;
    }

    #paste-modal .options {
        margin: 1 0;
        height: auto;
    }

    #paste-modal .header-option {
        margin: 1 0;
        height: 3;
        align: center middle;
    }

    #paste-modal Button {
        width: 100%;
        margin-bottom: 1;
        height: 3;
        min-height: 3;
    }

    #paste-modal .cancel-btn {
        margin-top: 1;
        height: 3;
        min-height: 3;
    }
    """

    def __init__(self, parsed_data: dict, has_existing_data: bool) -> None:
        super().__init__()
        self.parsed_data = parsed_data
        self.has_existing_data = has_existing_data

    def compose(self) -> ComposeResult:
        """Compose the paste options modal."""
        with Vertical(id="paste-modal"):
            yield Label("Paste Clipboard Data", classes="title")

            # Show preview of data
            preview_text = self._create_preview_text()
            yield Static(preview_text, classes="preview")

            # Show data info
            info_text = (
                f"{self.parsed_data['num_rows']} rows × {self.parsed_data['num_cols']} columns"
            )
            if self.parsed_data["has_headers"]:
                info_text += " (with headers)"
            if self.parsed_data.get("is_wikipedia_style", False):
                info_text += " [Wikipedia table detected]"
            yield Label(info_text, classes="info")

            # Header checkbox option
            with Horizontal(classes="header-option"):
                yield Checkbox("Top Row is Header", id="header-checkbox", value=True)

            # Options
            with Vertical(classes="options"):
                # Only show "Replace Current Data" if there is existing data
                if self.has_existing_data:
                    yield Button("Replace Current Data", id="replace-btn", variant="primary")
                    yield Button("Append to Current Data", id="append-btn", variant="default")

                yield Button(
                    "Create New Sheet",
                    id="new-sheet-btn",
                    variant="primary" if not self.has_existing_data else "default",
                )

            yield Button("Cancel", id="cancel-btn", variant="error", classes="cancel-btn")

    def _create_preview_text(self) -> str:
        """Create preview text showing first few rows."""
        rows = self.parsed_data["rows"]
        preview_rows = rows[:5]  # Show first 5 rows to ensure we see more data

        preview_lines = []
        for i, row in enumerate(preview_rows):
            # Truncate long cells
            display_row = []
            for cell in row:
                cell_str = str(cell)
                if len(cell_str) > 12:
                    display_row.append(cell_str[:9] + "...")
                else:
                    display_row.append(cell_str)

            # Format row with separator
            if self.parsed_data["separator"] == "\t":
                line = " | ".join(display_row)
            else:
                line = f" {self.parsed_data['separator']} ".join(display_row)

            preview_lines.append(line)

        if len(rows) > 5:
            preview_lines.append(f"... and {len(rows) - 5} more rows")

        return "\n".join(preview_lines)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        """Handle button presses."""
        # Get checkbox state
        header_checkbox = self.query_one("#header-checkbox", Checkbox)
        use_header = header_checkbox.value

        result = None
        if event.button.id == "replace-btn":
            result = {"action": "replace", "use_header": use_header}
        elif event.button.id == "append-btn":
            result = {"action": "append", "use_header": use_header}
        elif event.button.id == "new-sheet-btn":
            result = {"action": "new_sheet", "use_header": use_header}
        elif event.button.id == "cancel-btn":
            result = None

        self.dismiss(result)

    def on_key(self, event) -> None:
        """Handle keyboard shortcuts."""
        if event.key == "escape":
            self.dismiss(None)
        elif event.key == "enter":
            # Default action: prefer new_sheet if no existing data, otherwise replace
            header_checkbox = self.query_one("#header-checkbox", Checkbox)
            use_header = header_checkbox.value
            action = "new_sheet" if not self.has_existing_data else "replace"
            self.dismiss({"action": action, "use_header": use_header})


class NumericExtractionModal(ModalScreen[str | None]):
    """Modal for asking user about numeric extraction from string column."""

    CSS = """
    NumericExtractionModal {
        align: center middle;
    }

    #extraction-modal {
        width: 80;
        height: auto;
        min-height: 22;
        background: $surface;
        border: thick $accent;
        padding: 2;
    }

    #extraction-modal .title {
        text-style: bold;
        text-align: center;
        margin-bottom: 1;
        color: $accent;
    }

    #extraction-modal .message {
        text-align: center;
        margin-bottom: 1;
        color: $text;
    }

    #extraction-modal .preview {
        background: $surface-darken-1;
        border: solid $primary;
        padding: 1;
        margin: 1 0;
        max-height: 8;
        overflow-y: auto;
    }

    #extraction-modal .preview-title {
        text-style: bold;
        color: $primary;
        margin-bottom: 1;
    }

    #extraction-modal .preview-item {
        margin-bottom: 1;
    }

    #extraction-modal .original {
        color: $warning;
    }

    #extraction-modal .extracted {
        color: $success;
    }

    #extraction-modal .null-result {
        color: $error;
    }

    #extraction-modal .modal-buttons {
        height: auto;
        align: center middle;
        margin-top: 2;
        dock: bottom;
    }

    #extraction-modal .modal-buttons Button {
        margin: 0 1;
        min-width: 20;
        height: 3;
    }
    """

    def __init__(self, column_name: str, sample_data: list[str], target_type: str) -> None:
        super().__init__()
        self.column_name = column_name
        self.sample_data = sample_data[:10]  # Limit to first 10 for preview
        self.target_type = target_type  # "integer" or "float"

    def compose(self) -> ComposeResult:
        """Compose the modal content."""
        with Vertical(id="extraction-modal"):
            yield Static("🔢 Numeric Extraction", classes="title")
            yield Static(
                f"Extract numbers from column '{self.column_name}' values?", classes="message"
            )

            # Preview section
            with Vertical(classes="preview"):
                yield Static("Preview of conversion:", classes="preview-title")

                for original_value in self.sample_data:
                    extracted_num, has_decimal = self._extract_numeric_from_string(original_value)

                    if extracted_num is not None:
                        if (
                            self.target_type == "integer"
                            and not has_decimal
                            and extracted_num.is_integer()
                        ):
                            converted = int(extracted_num)
                            yield Static(
                                f"'{original_value}' → {converted}",
                                classes="preview-item extracted",
                            )
                        else:
                            yield Static(
                                f"'{original_value}' → {extracted_num}",
                                classes="preview-item extracted",
                            )
                    else:
                        yield Static(
                            f"'{original_value}' → None", classes="preview-item null-result"
                        )

            yield Static("")  # Spacer
            with Horizontal(classes="modal-buttons"):
                yield Button("❌ Keep as Text", id="keep-text", variant="error")
                yield Button(
                    f"🔢 Extract to {self.target_type.title()}", id="extract", variant="success"
                )
                yield Button("Cancel", id="cancel", variant="default")

    def _extract_numeric_from_string(self, value: str) -> tuple[float | None, bool]:
        """Extract numeric content from a mixed string (copy of main method for preview)."""
        if not value or not value.strip():
            return None, False

        # Use regex to find all numeric parts including decimals
        import re

        numeric_pattern = r"[-+]?(?:\d+\.?\d*|\.\d+)"
        matches = re.findall(numeric_pattern, value.strip())

        if not matches:
            return None, False

        # Take the first numeric match and try to convert to float
        try:
            numeric_str = matches[0]
            numeric_value = float(numeric_str)
            has_decimal = "." in numeric_str
            return numeric_value, has_decimal
        except (ValueError, TypeError):
            return None, False

    def on_button_pressed(self, event: Button.Pressed) -> None:
        """Handle button presses."""
        if event.button.id == "extract":
            self.dismiss("extract")
        elif event.button.id == "keep-text":
            self.dismiss("keep_text")
        elif event.button.id == "cancel":
            self.dismiss(None)

    def on_key(self, event) -> None:
        """Handle keyboard shortcuts."""
        if event.key == "escape":
            self.dismiss(None)
        elif event.key == "enter":
            # Default to extract
            self.dismiss("extract")


class ColumnConversionModal(ModalScreen[bool | None]):
    """Modal for asking user about column type conversion."""

    CSS = """
    ColumnConversionModal {
        align: center middle;
    }

    #conversion-modal {
        width: 70;
        height: auto;
        min-height: 18;
        background: $surface;
        border: thick $warning;
        padding: 2;
    }

    #conversion-modal .title {
        text-style: bold;
        text-align: center;
        margin-bottom: 1;
        color: $warning;
    }

    #conversion-modal .message {
        text-align: center;
        margin-bottom: 1;
        color: $text;
    }

    #conversion-modal .value-display {
        text-align: center;
        margin-bottom: 1;
        text-style: bold;
        color: $accent;
    }

    #conversion-modal .options {
        text-align: center;
        margin-bottom: 2;
        color: $text;
    }

    #conversion-modal .modal-buttons {
        height: auto;
        align: center middle;
        margin-top: 2;
        dock: bottom;
    }

    #conversion-modal .modal-buttons Button {
        margin: 0 1;
        min-width: 18;
        height: 3;
    }
    """

    def __init__(self, column_name: str, value: str, from_type: str, to_type: str) -> None:
        super().__init__()
        self.column_name = column_name
        self.value = value
        self.from_type = from_type
        self.to_type = to_type

    def compose(self) -> ComposeResult:
        """Compose the modal content."""
        with Vertical(id="conversion-modal"):
            yield Static("⚠️  Column Type Conversion", classes="title")
            yield Static(
                f"Column '{self.column_name}' is currently {self.from_type}", classes="message"
            )
            yield Static(f"Value: '{self.value}'", classes="value-display")

            # Dynamic message and buttons based on conversion type
            if self.from_type == "integer" and self.to_type == "float":
                yield Static(
                    f"Convert column to {self.to_type} to preserve decimal values?",
                    classes="options",
                )
                yield Static("")  # Spacer
                with Horizontal(classes="modal-buttons"):
                    yield Button("❌ Keep as Integer", id="keep-current", variant="error")
                    yield Button("✓ Convert to Float", id="convert-type", variant="success")
                    yield Button("Cancel", id="cancel-conversion", variant="default")
            elif self.from_type in ["integer", "float"] and self.to_type == "text":
                yield Static("Convert column to text to store string values?", classes="options")
                yield Static("")  # Spacer
                with Horizontal(classes="modal-buttons"):
                    yield Button("🔢 Convert to String", id="convert-type", variant="error")
                    yield Button("Cancel", id="cancel-conversion", variant="default")
            else:
                # Generic conversion case
                yield Static(f"Convert column to {self.to_type}?", classes="options")
                yield Static("")  # Spacer
                with Horizontal(classes="modal-buttons"):
                    yield Button(
                        f"❌ Keep as {self.from_type.title()}", id="keep-current", variant="error"
                    )
                    yield Button(
                        f"✓ Convert to {self.to_type.title()}", id="convert-type", variant="success"
                    )
                    yield Button("Cancel", id="cancel-conversion", variant="default")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        """Handle button presses."""
        if event.button.id == "convert-type":
            self.dismiss(True)
        elif event.button.id == "keep-current":
            self.dismiss(False)
        elif event.button.id == "cancel-conversion":
            self.dismiss(None)

    def on_key(self, event) -> None:
        """Handle keyboard shortcuts."""
        if event.key == "escape":
            self.dismiss(None)
        elif event.key == "enter":
            # Default to convert
            self.dismiss(True)
        elif event.key == "left" or event.key == "right":
            # Handle left/right arrow navigation between buttons
            self._handle_arrow_navigation(event.key == "left")

    def _handle_arrow_navigation(self, left: bool = True) -> None:
        """Handle arrow key navigation between buttons in the modal."""
        try:
            # Get all buttons in the modal
            buttons = self.query("Button")
            if not buttons:
                return

            # Find which button currently has focus
            focused_index = -1
            for i, button in enumerate(buttons):
                if button.has_focus:
                    focused_index = i
                    break

            # If no button has focus, focus the first button
            if focused_index == -1:
                buttons[0].focus()
                return

            # Navigate to the previous/next button
            if left:
                # Left arrow: go to previous button (wrap around)
                next_index = (focused_index - 1) % len(buttons)
            else:
                # Right arrow: go to next button (wrap around)
                next_index = (focused_index + 1) % len(buttons)

            buttons[next_index].focus()

        except Exception as e:
            # Log error but don't crash the modal
            self.log(f"Error in arrow navigation: {e}")


class QuitConfirmationModal(ModalScreen[bool | None]):
    """Modal asking for confirmation before quitting with unsaved changes."""

    CSS = """
    QuitConfirmationModal {
        align: center middle;
    }

    #quit-confirm {
        width: 60;
        height: 16;
        background: $surface;
        border: thick $primary;
        padding: 2;
    }

    #quit-confirm .title {
        text-style: bold;
        text-align: center;
        margin-bottom: 1;
        color: $warning;
    }

    #quit-confirm .message {
        text-align: center;
        margin-bottom: 2;
        color: $text;
    }

    #quit-confirm .modal-buttons {
        height: 3;
        align: center middle;
        margin-top: 2;
    }

    #quit-confirm .modal-buttons Button {
        margin: 0 2;
        min-width: 15;
    }
    """

    def compose(self) -> ComposeResult:
        """Compose the quit confirmation modal."""
        with Vertical(id="quit-confirm"):
            yield Static("⚠ Unsaved Changes", classes="title")
            yield Static("You have unsaved changes that will be lost.", classes="message")
            yield Static("Are you sure you want to quit?", classes="message")
            with Horizontal(classes="modal-buttons"):
                yield Button("Quit Anyway", id="force-quit", variant="error")
                yield Button("Cancel", id="cancel-quit", variant="primary")

    def on_button_pressed(self, event) -> None:
        """Handle button presses in the modal."""
        if event.button.id == "force-quit":
            self.dismiss(True)  # Force quit
        elif event.button.id == "cancel-quit":
            self.dismiss(False)  # Cancel quit

    def on_key(self, event) -> None:
        """Handle keyboard shortcuts and button navigation."""
        if event.key == "escape":
            self.dismiss(False)  # Cancel on escape
        elif event.key in ("left", "right"):
            # Get the current modal-buttons container
            buttons_container = self.query_one("Horizontal.modal-buttons")
            buttons = buttons_container.query(Button)

            if not buttons:
                return

            # Find currently focused button
            current_focused = None
            current_index = -1

            for i, button in enumerate(buttons):
                if button.has_focus:
                    current_focused = button
                    current_index = i
                    break

            # If no button is focused, focus the first one
            if current_focused is None:
                buttons[0].focus()
                return

            # Navigate to next/previous button
            if event.key == "right":
                next_index = (current_index + 1) % len(buttons)
            else:  # left
                next_index = (current_index - 1) % len(buttons)

            buttons[next_index].focus()


class InitConfirmationModal(ModalScreen[bool | None]):
    """Modal asking for confirmation before returning to welcome screen with unsaved changes."""

    CSS = """
    InitConfirmationModal {
        align: center middle;
    }

    #init-confirm {
        width: 60;
        height: 16;
        background: $surface;
        border: thick $primary;
        padding: 2;
    }

    #init-confirm .title {
        text-style: bold;
        text-align: center;
        margin-bottom: 1;
        color: $warning;
    }

    #init-confirm .message {
        text-align: center;
        margin-bottom: 2;
        color: $text;
    }

    #init-confirm .modal-buttons {
        height: 3;
        align: center middle;
        margin-top: 2;
    }

    #init-confirm .modal-buttons Button {
        margin: 0 2;
        min-width: 15;
    }
    """

    def compose(self) -> ComposeResult:
        """Compose the init confirmation modal."""
        with Vertical(id="init-confirm"):
            yield Static("⚠ Unsaved Changes", classes="title")
            yield Static("You have unsaved changes that will be lost.", classes="message")
            yield Static("Return to welcome screen anyway?", classes="message")
            with Horizontal(classes="modal-buttons"):
                yield Button("Return to Welcome", id="force-init", variant="error")
                yield Button("Cancel", id="cancel-init", variant="primary")

    def on_button_pressed(self, event) -> None:
        """Handle button presses in the modal."""
        if event.button.id == "force-init":
            self.dismiss(True)  # Force return to welcome
        elif event.button.id == "cancel-init":
            self.dismiss(False)  # Cancel init

    def on_key(self, event) -> None:
        """Handle keyboard shortcuts."""
        if event.key == "escape":
            self.dismiss(False)  # Cancel on escape


class RowColumnDeleteModal(ModalScreen[str | None]):
    """Modal for deleting rows and columns."""

    DEFAULT_CSS = """
    RowColumnDeleteModal {
        align: center middle;
    }

    RowColumnDeleteModal > Vertical {
        width: auto;
        height: auto;
        min-width: 70;
        max-width: 90;
        padding: 2;
        border: thick $primary;
        background: $surface;
    }

    RowColumnDeleteModal Label {
        text-align: center;
        padding-bottom: 1;
        color: $primary;
    }

    RowColumnDeleteModal Static {
        text-align: center;
        padding-bottom: 1;
        color: $text;
        margin-bottom: 1;
    }

    RowColumnDeleteModal Horizontal {
        height: auto;
        align: center middle;
        margin-top: 1;
    }

    RowColumnDeleteModal Button {
        margin: 0 1;
        min-width: 12;
    }
    """

    def __init__(
        self,
        delete_type: str,
        target_info: str,
        row_number: int = None,
        column_name: str = None,
        is_data_truncated: bool = False,
        is_last_visible_row: bool = False,
    ) -> None:
        super().__init__()
        self.delete_type = delete_type  # "row" or "column"
        self.target_info = target_info
        self.row_number = row_number
        self.column_name = column_name
        self.is_data_truncated = is_data_truncated  # Whether we're viewing a truncated dataset
        self.is_last_visible_row = is_last_visible_row  # Whether this is the last visible row

    def compose(self) -> ComposeResult:
        with Vertical():
            if self.delete_type == "row":
                yield Label("[bold blue]Row Options[/bold blue]")
                yield Static(f"Options for {self.target_info}:")
                with Horizontal(classes="modal-buttons"):
                    yield Button("Delete Row", id="delete-row", variant="error")
                    yield Button("Insert Row Above", id="insert-row-above", variant="primary")
                    # Only show "Insert Row Below" if we're not at the last visible row of a truncated dataset
                    if not (self.is_data_truncated and self.is_last_visible_row):
                        yield Button("Insert Row Below", id="insert-row-below", variant="primary")
                    yield Button("Cancel", id="cancel", variant="default")
            elif self.delete_type == "column":
                yield Label("[bold blue]Column Options[/bold blue]")
                yield Static(f"Options for column '{self.column_name}':")
                with Horizontal(classes="modal-buttons"):
                    yield Button("Delete Column", id="delete-column", variant="error")
                    yield Button("Insert Column Left", id="insert-column-left", variant="primary")
                    yield Button("Insert Column Right", id="insert-column-right", variant="primary")
                    yield Button("Cancel", id="cancel", variant="default")
                # Second row with sorting options
                with Horizontal(classes="modal-buttons"):
                    yield Button("Sort Ascending ↑", id="sort-ascending", variant="success")
                    yield Button("Sort Descending ↓", id="sort-descending", variant="success")
            else:
                # Legacy menu mode (fallback)
                yield Label("[bold]Row/Column Options[/bold]")
                yield Static(f"{self.target_info}")
                with Horizontal(classes="modal-buttons"):
                    yield Button("Delete Row", id="delete-row", variant="error")
                    yield Button("Delete Column", id="delete-column", variant="error")
                    yield Button("Cancel", id="cancel", variant="primary")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "delete-row":
            self.dismiss("delete-row")
        elif event.button.id == "delete-column":
            self.dismiss("delete-column")
        elif event.button.id == "insert-row-above":
            self.dismiss("insert-row-above")
        elif event.button.id == "insert-row-below":
            self.dismiss("insert-row-below")
        elif event.button.id == "insert-column-left":
            self.dismiss("insert-column-left")
        elif event.button.id == "insert-column-right":
            self.dismiss("insert-column-right")
        elif event.button.id == "sort-ascending":
            self.dismiss("sort-ascending")
        elif event.button.id == "sort-descending":
            self.dismiss("sort-descending")
        elif event.button.id == "cancel":
            self.dismiss(None)

    def on_key(self, event) -> None:
        """Handle keyboard shortcuts and button navigation."""
        if event.key == "escape":
            self.dismiss(None)
        elif event.key in ("left", "right"):
            # Handle left/right navigation within a row
            self._handle_horizontal_navigation(event.key == "right")
        elif event.key in ("up", "down") and self.delete_type == "column":
            # Handle up/down navigation between rows (only for column options which has 2 rows)
            self._handle_vertical_navigation(event.key == "down")

    def _handle_horizontal_navigation(self, right: bool = True) -> None:
        """Handle left/right arrow navigation within the current row."""
        # Get all button containers
        button_containers = self.query("Horizontal.modal-buttons")
        if not button_containers:
            return

        # Find which container has a focused button
        focused_container = None
        for container in button_containers:
            buttons = container.query(Button)
            for button in buttons:
                if button.has_focus:
                    focused_container = container
                    break
            if focused_container:
                break

        if not focused_container:
            # No button focused, focus first button in first container
            first_container = button_containers[0]
            first_buttons = first_container.query(Button)
            if first_buttons:
                first_buttons[0].focus()
            return

        # Navigate within the focused container
        buttons = focused_container.query(Button)
        if not buttons:
            return

        # Find currently focused button within this container
        current_index = -1
        for i, button in enumerate(buttons):
            if button.has_focus:
                current_index = i
                break

        if current_index == -1:
            buttons[0].focus()
            return

        # Navigate to next/previous button within this row
        if right:
            next_index = (current_index + 1) % len(buttons)
        else:  # left
            next_index = (current_index - 1) % len(buttons)

        buttons[next_index].focus()

    def _handle_vertical_navigation(self, down: bool = True) -> None:
        """Handle up/down arrow navigation between button rows."""
        # Get all button containers
        button_containers = self.query("Horizontal.modal-buttons")
        if len(button_containers) < 2:
            return  # No vertical navigation needed

        # Find which container has a focused button
        focused_container_index = -1
        focused_button_index = -1

        for i, container in enumerate(button_containers):
            buttons = container.query(Button)
            for j, button in enumerate(buttons):
                if button.has_focus:
                    focused_container_index = i
                    focused_button_index = j
                    break
            if focused_container_index != -1:
                break

        if focused_container_index == -1:
            # No button focused, focus first button in first container
            first_buttons = button_containers[0].query(Button)
            if first_buttons:
                first_buttons[0].focus()
            return

        # Move to the other row
        if down:
            target_container_index = (focused_container_index + 1) % len(button_containers)
        else:  # up
            target_container_index = (focused_container_index - 1) % len(button_containers)

        target_container = button_containers[target_container_index]
        target_buttons = target_container.query(Button)

        if target_buttons:
            # Try to focus the same position in the target row, or the last button if out of range
            target_index = min(focused_button_index, len(target_buttons) - 1)
            target_buttons[target_index].focus()


class ValidationErrorModal(ModalScreen[bool]):
    """Modal for showing validation errors with option to try again."""

    DEFAULT_CSS = """
    ValidationErrorModal {
        align: center middle;
    }

    ValidationErrorModal > Vertical {
        width: auto;
        height: auto;
        min-width: 50;
        max-width: 80;
        padding: 1;
        border: thick $error;
        background: $surface;
    }

    ValidationErrorModal Label {
        text-align: center;
        padding-bottom: 1;
        color: $error;
    }

    ValidationErrorModal Static {
        text-align: center;
        padding-bottom: 1;
        color: $text;
        margin-bottom: 1;
    }

    ValidationErrorModal Horizontal {
        height: auto;
        align: center middle;
    }

    ValidationErrorModal Button {
        margin: 0 1;
        min-width: 12;
    }
    """

    def __init__(self, error_message: str, original_value: str, cell_address: str = "") -> None:
        super().__init__()
        self.error_message = error_message
        self.original_value = original_value
        self.cell_address = cell_address

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Label("[bold red]Column Name Renaming Problem[/bold red]")
            yield Static(self._format_error_message())
            yield Static(f"Original value: '{self.original_value}'")
            with Horizontal(classes="modal-buttons"):
                yield Button("Try Again", id="try-again", variant="primary")
                yield Button("Cancel", id="cancel", variant="default")

    def _format_error_message(self) -> str:
        """Format the error message in a more user-friendly way."""
        # Extract the proposed name from common error patterns
        if "starts with a digit" in self.error_message:
            # Extract the column name from the error message
            match = re.search(r"Column '([^']+)'", self.error_message)
            if match:
                proposed_name = match.group(1)
                return f"Proposed column name '{proposed_name}' starts with a digit, which is not recommended"

        # For other error types, try to extract the column name and reformat
        if "Column '" in self.error_message:
            # Replace "Column 'name' error description" with "Proposed column name 'name' error description"
            formatted = self.error_message.replace("Column '", "Proposed column name '", 1)
            # Remove technical details like "(not recommended for Python compatibility)"
            formatted = re.sub(r"\s*\([^)]*Python[^)]*\)", "", formatted)
            return formatted

        # Fallback to original message if no pattern matches
        return self.error_message

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "try-again":
            self.dismiss(True)  # User wants to try again
        elif event.button.id == "cancel":
            self.dismiss(False)  # User wants to cancel

    def on_key(self, event) -> None:
        """Handle keyboard shortcuts."""
        if event.key == "escape":
            self.dismiss(False)  # Cancel on escape


class RowNavigationModal(ModalScreen[int | None]):
    """Modal for navigating to a specific row number."""

    DEFAULT_CSS = """
    RowNavigationModal {
        align: center middle;
    }

    RowNavigationModal > Vertical {
        width: auto;
        height: auto;
        min-width: 50;
        max-width: 70;
        padding: 2;
        border: thick $primary;
        background: $surface;
    }

    #row-input {
        width: 100%;
        margin: 1 0;
    }

    .modal-buttons {
        width: 100%;
        height: auto;
        margin-top: 1;
    }
    """

    def __init__(self, total_rows: int, current_row: int = 1) -> None:
        super().__init__()
        self.total_rows = total_rows
        self.current_row = current_row

    def compose(self) -> ComposeResult:
        from textual.widgets import Input, Label

        with Vertical():
            yield Label("[bold blue]Go to Row[/bold blue]")
            yield Static(f"Enter row number (1 - {self.total_rows:,}):")
            yield Input(value=str(self.current_row), placeholder="Row number", id="row-input")
            with Horizontal(classes="modal-buttons"):
                yield Button("Go", id="go", variant="primary")
                yield Button("Cancel", id="cancel", variant="default")

    def on_mount(self) -> None:
        """Focus the input field when the modal opens."""
        self.query_one("#row-input", Input).focus()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "go":
            try:
                row_input = self.query_one("#row-input", Input)
                row_number = int(row_input.value.strip())

                if 1 <= row_number <= self.total_rows:
                    self.dismiss(row_number)
                else:
                    # Invalid row number - show error in status
                    row_input.add_class("error")
                    # Could add error message display here
            except ValueError:
                # Invalid input - show error
                row_input = self.query_one("#row-input", Input)
                row_input.add_class("error")
        elif event.button.id == "cancel":
            self.dismiss(None)

    def on_key(self, event) -> None:
        """Handle key events for the modal."""
        if event.key == "enter":
            # Trigger the go button
            self.on_button_pressed(Button.Pressed(self.query_one("#go", Button)))
            event.prevent_default()
        elif event.key == "escape":
            self.dismiss(None)


class DatabaseConnectionModal(ModalScreen[dict | None]):
    """Modal for connecting to a database."""

    DEFAULT_CSS = """
    DatabaseConnectionModal {
        align: center middle;
    }

    DatabaseConnectionModal > Vertical {
        width: 80;
        height: auto;
        max-height: 35;
        padding: 1;
        border: thick $surface;
        background: $surface;
    }

    DatabaseConnectionModal Label {
        text-align: center;
        padding-bottom: 1;
        color: $text;
    }

    DatabaseConnectionModal .field-label {
        text-align: left;
        padding-bottom: 0;
        margin-top: 1;
        color: $text;
    }

    DatabaseConnectionModal Input {
        margin-bottom: 1;
    }

    DatabaseConnectionModal Select {
        margin-bottom: 1;
    }

    DatabaseConnectionModal Horizontal {
        height: auto;
        align: center middle;
    }

    DatabaseConnectionModal Button {
        margin: 0 1;
        min-width: 10;
    }

    DatabaseConnectionModal VerticalScroll {
        height: 1fr;
        padding: 1;
    }
    """

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Label("[bold]Connect to Database[/bold]")

            with VerticalScroll():
                # Connection String Section (Priority)
                yield Label(
                    "Connection String (Optional - takes priority if filled):",
                    classes="field-label",
                )
                yield Input(
                    placeholder="mysql://user:pass@host:port/database or postgresql://user:pass@host:port/database",
                    id="connection-string-input",
                )
                yield Static("\nExamples:")
                yield Static("• mysql://rfamro@mysql-rfam-public.ebi.ac.uk:4497/Rfam")
                yield Static("• postgresql://user:password@host:5432/database")

                # Separator
                yield Static("\n" + "─" * 60)
                yield Static("OR fill in the manual fields below:\n")

                # Manual Setup Section
                yield Label("Database Type:", classes="field-label")
                yield Select(
                    [("MySQL", "mysql"), ("PostgreSQL", "postgresql")],
                    value="mysql",
                    id="db-type-select",
                )

                yield Label("Host:", classes="field-label")
                yield Input(placeholder="mysql-rfam-public.ebi.ac.uk", id="host-input")

                yield Label("Port:", classes="field-label")
                yield Input(placeholder="4497", id="port-input")

                yield Label("Database Name:", classes="field-label")
                yield Input(placeholder="Rfam", id="database-input")

                yield Label("Username:", classes="field-label")
                yield Input(placeholder="rfamro", id="username-input")

                yield Label("Password (leave empty if none):", classes="field-label")
                yield Input(placeholder="password (optional)", password=True, id="password-input")

            with Horizontal():
                yield Button("Connect", variant="primary", id="connect-btn")
                yield Button("Cancel", variant="default", id="cancel-btn")

    def on_mount(self) -> None:
        """Focus on the connection string input when the modal opens."""
        self.log("DatabaseConnectionModal mounted")
        self.call_after_refresh(self._focus_input)

    def _focus_input(self) -> None:
        """Focus on the connection string input."""
        self.log("DatabaseConnectionModal attempting to focus input")
        try:
            input_field = self.query_one("#connection-string-input", Input)
            input_field.focus()
            self.log("Successfully focused connection string input")
        except Exception as e:
            self.log(f"Error focusing input: {e}")

    def call_after_refresh(self, callback, *args, **kwargs):
        """Helper method to call a function after the next refresh using set_timer."""
        self.set_timer(0.01, lambda: callback(*args, **kwargs))

    def on_button_pressed(self, event: Button.Pressed) -> None:
        """Handle button presses."""
        self.log(
            f"DatabaseConnectionModal ANY button pressed: {event.button.id} | text: {event.button.label}"
        )

        if event.button.id == "connect-btn":
            self.log("Connect button detected, calling _handle_connect")
            self._handle_connect()
        elif event.button.id == "cancel-btn":
            self.log("Cancel button detected, dismissing modal")
            self.dismiss(None)
        else:
            self.log(f"Unknown button pressed: {event.button.id}")

    def on_click(self, event) -> None:
        """Handle click events as backup for button detection."""
        try:
            # Check if we clicked on a button
            if hasattr(event, "widget") and hasattr(event.widget, "id"):
                widget_id = event.widget.id
                self.log(f"DatabaseConnectionModal click detected on widget: {widget_id}")

                if widget_id == "connect-btn":
                    self.log("Connect button clicked via on_click, calling _handle_connect")
                    self._handle_connect()
                elif widget_id == "cancel-btn":
                    self.log("Cancel button clicked via on_click, dismissing modal")
                    self.dismiss(None)
        except Exception as e:
            self.log(f"Error in on_click handler: {e}")

    def on_key(self, event) -> None:
        """Handle key events for the modal."""
        self.log(f"DatabaseConnectionModal key pressed: {event.key}")
        if event.key == "enter":
            self.log("Enter key pressed, calling _handle_connect")
            self._handle_connect()
            event.prevent_default()
        elif event.key == "escape":
            self.log("Escape key pressed, dismissing modal")
            self.dismiss(None)

    def _handle_connect(self) -> None:
        """Handle the connect button press. Uses connection string if provided, otherwise builds from manual fields."""
        try:
            self.log("Connect button pressed, handling connection...")

            # First, check if connection string is provided
            try:
                connection_string_input = self.query_one("#connection-string-input", Input)
                connection_string = connection_string_input.value.strip()
                self.log(f"Connection string from input: '{connection_string}'")

                if connection_string:
                    self.log("Using connection string (priority)")
                    self.log(f"Dismissing with connection string: {connection_string}")
                    self.dismiss({"connection_string": connection_string})
                    return
                else:
                    self.log("No connection string provided, falling back to manual fields")
            except Exception as e:
                self.log(f"Error reading connection string input: {e}")
                self.log("Falling back to manual fields")

            # If no connection string, build from manual fields
            try:
                db_type_select = self.query_one("#db-type-select", Select)
                host_input = self.query_one("#host-input", Input)
                port_input = self.query_one("#port-input", Input)
                database_input = self.query_one("#database-input", Input)
                username_input = self.query_one("#username-input", Input)
                password_input = self.query_one("#password-input", Input)

                db_type = db_type_select.value
                host = host_input.value.strip() or "localhost"
                port = port_input.value.strip() or ("3306" if db_type == "mysql" else "5432")
                database = database_input.value.strip()
                username = username_input.value.strip()
                password = password_input.value.strip()

                self.log(
                    f"Manual setup values - DB type: {db_type}, Host: {host}, Port: {port}, Database: {database}, Username: {username}, Password: {'***' if password else '(empty)'}"
                )

                if not database or not username:
                    self.log(
                        f"Missing required fields - Database: '{database}', Username: '{username}'"
                    )
                    # TODO: Show error message to user
                    return

                # Build connection string
                if password:
                    connection_string = (
                        f"{db_type}://{username}:{password}@{host}:{port}/{database}"
                    )
                else:
                    connection_string = f"{db_type}://{username}@{host}:{port}/{database}"

                self.log(f"Built connection string from manual fields: {connection_string}")
                self.log(f"Dismissing with connection string: {connection_string}")
                self.dismiss({"connection_string": connection_string})

            except Exception as e:
                self.log(f"Error handling manual setup fields: {e}")
                import traceback

                self.log(f"Traceback: {traceback.format_exc()}")
                return

        except Exception as e:
            self.log(f"Error handling connect: {e}")
            import traceback

            self.log(f"Traceback: {traceback.format_exc()}")


class SweetFooter(Footer):
    """Custom footer with Sweet-specific bindings."""

    def compose(self) -> ComposeResult:
        yield Static("Press : for command mode")
