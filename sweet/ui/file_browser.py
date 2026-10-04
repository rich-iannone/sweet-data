"""File browser modal for opening datasets."""

from __future__ import annotations

import os
from pathlib import Path

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, DirectoryTree, Static

from ._common import pl


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
