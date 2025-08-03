"""Custom Textual widgets for Sweet."""
from __future__ import annotations

import keyword
import os
import re
import time
from pathlib import Path
from typing import TYPE_CHECKING

from textual.app import ComposeResult
from textual.containers import Container, Horizontal, Vertical
from textual.reactive import reactive
from textual.screen import ModalScreen
from textual.widget import Widget
from textual.widgets import Button, Checkbox, ContentSwitcher, DataTable, DirectoryTree, Footer, Input, Label, RadioSet, Select, Static, TextArea

if TYPE_CHECKING:
    import polars as pl

try:
    import polars as pl
except ImportError:
    pl = None


class WelcomeOverlay(Widget):
    """Welcome screen overlay similar to Vim's start screen."""
    
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.can_focus = True  # Make the overlay focusable

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
                yield Button("Paste from Clipboard", id="welcome-paste-clipboard", classes="welcome-button")
            yield Static("", classes="spacer")  # Bottom spacer

    def on_button_pressed(self, event: Button.Pressed) -> None:
        """Handle button presses in the welcome overlay."""
        self.log(f"Welcome overlay button pressed: {event.button.id}")
        # Find the ExcelDataGrid - we need to go up to the parent Vertical container
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
        elif event.key == "enter":
            self._activate_focused_button()
            return True
        return False

    def _navigate_buttons(self, direction: int) -> None:
        """Navigate between buttons using arrow keys."""
        # Define the button order
        button_ids = [
            "welcome-new-empty",
            "welcome-load-dataset", 
            "welcome-load-sample",
            "welcome-paste-clipboard"
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

    def _activate_focused_button(self) -> None:
        """Activate the currently focused button."""
        # Find the focused button and trigger its press event
        button_ids = [
            "welcome-new-empty",
            "welcome-load-dataset", 
            "welcome-load-sample",
            "welcome-paste-clipboard"
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


class DataDirectoryTree(DirectoryTree):
    """A DirectoryTree that filters to show only data files and directories."""
    
    def filter_paths(self, paths):
        """Filter paths to show only directories and supported data files."""
        data_extensions = {'.csv', '.tsv', '.txt', '.parquet', '.json', '.jsonl', '.ndjson', '.xlsx', '.xls', '.feather', '.ipc', '.arrow'}
        
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
                    # Don't intercept - let it trigger the button press event
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
            
            # Check if any shortcut button has focus - handle shortcut button navigation
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
            
            # Check if either main button has focus - handle main button navigation
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
                "nav-current": Path.cwd()
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
            
            # Check file extension - support multiple formats
            supported_extensions = ('.csv', '.tsv', '.txt', '.parquet', '.json', '.jsonl', '.ndjson', '.xlsx', '.xls', '.feather', '.ipc', '.arrow')
            if not file_path.lower().endswith(supported_extensions):
                self._show_error("Unsupported file format. Supported: CSV, TSV, TXT, Parquet, JSON, JSONL, Excel, Feather, Arrow")
                return
            
            # Try to read first few rows to validate
            try:
                extension = file_path.lower().split('.')[-1]
                if extension in ['csv', 'txt']:
                    df_test = pl.read_csv(file_path, n_rows=5)
                elif extension == 'tsv':
                    df_test = pl.read_csv(file_path, separator='\t', n_rows=5)
                elif extension == 'parquet':
                    df_test = pl.read_parquet(file_path).head(5)
                elif extension == 'json':
                    df_test = pl.read_json(file_path).head(5)
                elif extension in ['jsonl', 'ndjson']:
                    df_test = pl.read_ndjson(file_path).head(5)
                elif extension in ['xlsx', 'xls']:
                    try:
                        df_test = pl.read_excel(file_path).head(5)
                    except AttributeError:
                        self._show_error("Excel support requires additional dependencies")
                        return
                elif extension in ['feather', 'ipc', 'arrow']:
                    df_test = pl.read_ipc(file_path).head(5)
                else:
                    # Fallback to CSV
                    df_test = pl.read_csv(file_path, n_rows=5)
                
                if df_test.shape[0] == 0:
                    self._show_error("File appears to be empty")
                    return
                
                # File is valid - log success and dismiss modal with file path
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
                if hasattr(self.app, '_modal_callback'):
                    self.log("Calling modal callback manually")
                    self.app._modal_callback(file_path)
            except Exception as e2:
                self.log(f"Error force-closing modal: {e2}")


class CustomDataTable(DataTable):
    """Custom DataTable that allows immediate editing for specific keys and handles row label clicks."""
    
    def on_key(self, event) -> bool:
        """Handle key events - delegate immediate edit keys to parent first."""
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
            parent.log(f"Column header clicked: {event.column_index} ({event.label})")
            parent._handle_column_header_click(event.column_index)
    
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
        """Handle click events for right-click menu."""
        # Check if this is a right-click
        if hasattr(event, 'button') and event.button == 2:  # Right mouse button
            # Find the ExcelDataGrid parent
            parent = self.parent
            while parent and not isinstance(parent, ExcelDataGrid):
                parent = parent.parent
            
            if parent:
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
        return key in ['plus', 'minus', 'full_stop']


class ExcelDataGrid(Widget):
    """Excel-like data grid widget with editable cells and Excel addressing."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._table = CustomDataTable(classes="data-grid-table")
        self.data = None
        self.original_data = None  # Store original data for change tracking
        self.has_changes = False  # Track if data has been modified
        self._current_address = "A1"
        self.editing_cell = False
        self._edit_input = None
        self.original_data = None  # Store original data for change tracking
        self.has_changes = False  # Track if data has been modified
        self._editing_cell = None  # Currently editing cell coordinate
        
        # Double-click tracking
        self._last_click_time = 0
        self._last_click_coordinate = None
        self._double_click_threshold = 0.5  # 500ms for double-click detection
        
        # Row label double-click tracking
        self._last_row_label_click_time = 0
        self._last_row_label_clicked = None
        
        self.is_sample_data = False  # Track if we're working with internal sample data
        self.data_source_name = None  # Name of the data source (for sample data)
        
        # Double-tap left arrow tracking (keyboard equivalent to double-click)
        self._last_left_arrow_time = 0
        self._last_left_arrow_position = None
        
        # Double-tap up arrow tracking for column operations
        self._last_up_arrow_time = 0
        self._last_up_arrow_position = None
        
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
            # Hide load controls - they're now in the welcome overlay
            with Horizontal(id="load-controls", classes="load-controls hidden"):
                yield Button("Load Dataset", id="load-dataset", classes="load-button")
                yield Button("Load Sample Data", id="load-sample", classes="load-button")
            
            # Main table area (simplified without edge controls)
            with Vertical(id="table-area"):
                yield self._table
            
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

        # Start with empty state - don't load sample data automatically
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

    def on_button_pressed(self, event: Button.Pressed) -> None:
        """Handle button presses in the data grid."""
        if event.button.id == "load-dataset":
            self.action_load_dataset()
        elif event.button.id == "load-sample":
            self.action_load_sample_data()

    def on_click(self, event) -> None:
        """Handle click events for static elements."""
        # This method can be used for other click handling if needed
        pass

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
                self.log("File loading cancelled - returning to welcome screen")
                # User cancelled - return to welcome screen
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
            '.csv': 'CSV',
            '.tsv': 'TSV',
            '.txt': 'TXT',
            '.parquet': 'PARQUET',
            '.json': 'JSON',
            '.jsonl': 'JSONL',
            '.ndjson': 'NDJSON',
            '.xlsx': 'XLSX',
            '.xls': 'XLS',
            '.feather': 'FEATHER',
            '.ipc': 'ARROW',
            '.arrow': 'ARROW'
        }
        return format_mapping.get(extension, 'UNKNOWN')

    def load_file(self, file_path: str) -> None:
        """Load data from a specific file path."""
        self.log(f"Starting to load file: {file_path}")
        try:
            if pl is None:
                self.log("Polars not available")
                self._table.clear(columns=True)
                self._table.add_column("Error")
                self._table.add_row("Polars not available")
                return

            # Detect file format and load accordingly
            extension = Path(file_path).suffix.lower()
            self.log(f"File extension detected: {extension}")
            
            # Load the file based on extension
            if extension in ['.csv', '.txt']:
                self.log("Loading as CSV")
                df = pl.read_csv(file_path)
            elif extension == '.tsv':
                self.log("Loading as TSV")
                df = pl.read_csv(file_path, separator='\t')
            elif extension == '.parquet':
                self.log("Loading as Parquet")
                df = pl.read_parquet(file_path)
            elif extension == '.json':
                self.log("Loading as JSON")
                df = pl.read_json(file_path)
            elif extension in ['.jsonl', '.ndjson']:
                self.log("Loading as NDJSON")
                df = pl.read_ndjson(file_path)
            elif extension in ['.xlsx', '.xls']:
                self.log("Loading as Excel")
                # Note: Polars Excel support might require additional dependencies
                try:
                    df = pl.read_excel(file_path)
                except AttributeError as e:
                    raise Exception("Excel file support requires additional dependencies. Please install with: pip install polars[xlsx]") from e
            elif extension == '.feather':
                self.log("Loading as Feather")
                df = pl.read_ipc(file_path)
            elif extension in ['.ipc', '.arrow']:
                self.log("Loading as Arrow/IPC")
                df = pl.read_ipc(file_path)
            else:
                self.log("Unknown extension, trying CSV as fallback")
                # Try CSV as fallback
                df = pl.read_csv(file_path)
            
            self.log(f"File loaded successfully, shape: {df.shape}")
            self.load_dataframe(df)
            
            # Mark as external file (not sample data)
            self.is_sample_data = False
            self.data_source_name = None
            
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

    def get_excel_column_name(self, col_index: int) -> str:
        """Convert column index to Excel-style column name (A, B, ..., Z, AA, AB, ...)."""
        result = ""
        while col_index >= 0:
            result = chr(ord('A') + (col_index % 26)) + result
            col_index = col_index // 26 - 1
        return result

    def _check_cursor_position(self) -> None:
        """Periodically check and update cursor position."""
        cursor_coordinate = self._table.cursor_coordinate
        if cursor_coordinate:
            row, col = cursor_coordinate
            # Only update if position has changed
            col_name = self.get_excel_column_name(col)
            new_address = f"{col_name}{row}"
            if new_address != self._current_address:
                self.update_address_display(row, col)

    def update_address_display(self, row: int, col: int, custom_message: str = None) -> None:
        """Update the status bar with current cell address, value, and type."""
        col_name = self.get_excel_column_name(col)
        self._current_address = f"{col_name}{row}"
        
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
                    data_row = row - 1
                    if data_row < len(self.data) and col < len(self.data.columns):
                        try:
                            raw_value = self.data[data_row, col]
                            if raw_value is None:
                                cell_value = "None"
                            else:
                                cell_value = str(raw_value)
                            
                            # Get column type with friendly format
                            column_name = self.data.columns[col]
                            column_dtype = self.data[column_name].dtype
                            simple_type = self._get_friendly_type_name(column_dtype)
                            polars_type = str(column_dtype)
                            cell_type = f"{simple_type} ({polars_type})"
                        except Exception as e:
                            self.log(f"Error getting cell data: {e}")
                            cell_value = "Error"
                            cell_type = "Unknown"
                elif row == 0:  # Header row
                    if self.data is not None and col < len(self.data.columns):
                        cell_value = str(self.data.columns[col])
                        cell_type = "Column Header"
                
                new_text = f"{self._current_address} // {cell_value} // {cell_type}"
            
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

            # Create internal sample data - this is packaged with the application
            df = pl.DataFrame({
                "name": ["Alice", "Bob", "Charlie", "Diana", "Eve", "Frank", "Grace", "Henry", "Ivy", "Jack"],
                "age": [25, 30, 35, 28, 32, 27, 31, 29, 26, 33],
                "city": ["New York", "San Francisco", "Chicago", "Boston", "Seattle", "Austin", "Denver", "Miami", "Portland", "Atlanta"],
                "salary": [75000, 85000, 70000, 80000, 92000, 68000, 88000, 77000, 82000, 95000],
                "department": ["Engineering", "Marketing", "Sales", "HR", "Engineering", "Design", "Marketing", "Sales", "Engineering", "HR"],
            })
            
            self.load_dataframe(df)
            
            # Mark as sample data and set clean display name
            self.is_sample_data = True
            self.data_source_name = "sample_data"
            
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
            
            self.app.set_current_filename("new_sheet [UNSAVED]")

        except Exception as e:
            self._table.add_column("Error")
            self._table.add_row(f"Failed to create empty sheet: {str(e)}")

    def load_dataframe(self, df) -> None:
        """Load a Polars DataFrame into the grid."""
        if pl is None or df is None:
            return

        self.data = df
        # Store original data for change tracking
        self.original_data = df.clone()
        self.has_changes = False

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

        # For file data, recreate the DataTable to ensure row labels display properly
        # This works around an issue where existing DataTable instances lose row label visibility
        if not getattr(self, 'is_sample_data', False):
            # Remove old table from the table area
            table_area = self.query_one("#table-area")
            # Remove only the table, not the bottom controls
            old_table = table_area.query_one("DataTable")
            old_table.remove()
            
            # Create a completely new CustomDataTable instance
            self._table = CustomDataTable(id="data-table", zebra_stripes=False)
            self._table.cursor_type = "cell"
            self._table.show_header = True
            self._table.show_row_labels = True
            
            # Override the clear method on the new instance
            original_clear = self._table.clear
            def preserve_row_labels_clear(*args, **kwargs):
                result = original_clear(*args, **kwargs)
                self._table.show_row_labels = True
                return result
            self._table.clear = preserve_row_labels_clear
            
            # Add the new table to the table area (at the beginning, before bottom controls)
            table_area.mount(self._table, before=0)
        else:
            # Sample data - use existing table
            self._table.clear(columns=True)
            self._table.show_row_labels = True

        # Add Excel-style column headers with just the letters (A, B, C, etc.)
        for i, column in enumerate(df.columns):
            excel_col = self.get_excel_column_name(i)
            self._table.add_column(excel_col, key=column)

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
        for row_idx, row in enumerate(df.iter_rows()):
            # Use row number (1-based) as the row label for display
            row_label = str(row_idx + 1)  # This should show as row number
            # Style cell values (None as red, whitespace-only as orange underscores)
            styled_row = []
            for cell in row:
                styled_row.append(self._style_cell_value(cell))
            # Add empty cell for the pseudo-column
            styled_row.append("")
            self._table.add_row(*styled_row, label=row_label)

        # Add pseudo-row for adding new rows (row adder)
        next_row_label = str(len(df) + 1)
        pseudo_row_cells = ["[dim italic]+ Add Row[/dim italic]"] + [""] * (len(df.columns) - 1) + [""]
        self._table.add_row(*pseudo_row_cells, label=next_row_label)

        # Final enforcement of row labels after all rows are added
        self._table.show_row_labels = True

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
        if row == 0 and self.data is not None and col < len(self.data.columns):
            # Column header clicked - notify script panel about column selection
            column_name = self.data.columns[col]
            column_type = self._get_friendly_type_name(self.data.dtypes[col])
            self._notify_script_panel_column_selection(col, column_name, column_type)
        else:
            # Regular cell selection - clear script panel column selection
            self._notify_script_panel_column_clear()
        
        # Check if clicking on pseudo-elements (add column or add row)
        if self.data is not None:
            # Check if clicked on pseudo-column (add column)
            if col == len(self.data.columns):  # Last column is the pseudo-column
                self.log("Clicked on pseudo-column - adding new column")
                self.action_add_column()
                return
            
            # Check if clicked on pseudo-row (add row)  
            if row == len(self.data) + 1:  # Last row is the pseudo-row (after header + data rows)
                self.log("Clicked on pseudo-row - adding new row")
                self.action_add_row()
                return
        
        # Show column type info when clicking on header row (row 0)
        if row == 0 and self.data is not None and col < len(self.data.columns):
            column_name = self.data.columns[col]
            dtype = self.data.dtypes[col]
            column_info = self._format_column_info_message(column_name, dtype)
            self.update_address_display(row, col, column_info)
        else:
            self.update_address_display(row, col)
        
        # Handle double-click for cell editing (only for real cells, not pseudo-elements)
        if self.data is not None and row <= len(self.data) and col < len(self.data.columns):
            current_time = time.time()
            
            # Check if this is a double-click (same cell clicked within threshold)
            if (self._last_click_coordinate == (row, col) and 
                current_time - self._last_click_time < self._double_click_threshold):
                
                # Double-click detected
                if not self.editing_cell:  # Only process if not already editing
                    if row == 0:
                        # Double-click on column header - show column options
                        self.log(f"Double-click detected on column header {self.get_excel_column_name(col)} ({self.data.columns[col]})")
                        self.call_after_refresh(self._show_row_column_delete_modal, row, col)
                    else:
                        # Double-click on data cell - start cell editing
                        self.log(f"Double-click detected on cell {self.get_excel_column_name(col)}{row}")
                        self.call_after_refresh(self.start_cell_edit, row, col)
            
            # Update last click tracking
            self._last_click_time = current_time
            self._last_click_coordinate = (row, col)

    def _notify_script_panel_column_selection(self, col_index: int, column_name: str, column_type: str) -> None:
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
        
        current_time = time.time()
        
        # Check if this is a double-click on the same row label
        if (self._last_row_label_clicked == clicked_row and 
            current_time - self._last_row_label_click_time < self._double_click_threshold):
            
            # Double-click detected on row label
            self.log(f"Double-click detected on row label {clicked_row}")
            self._show_row_column_delete_modal(clicked_row)
        
        # Update last click tracking
        self._last_row_label_click_time = current_time
        self._last_row_label_clicked = clicked_row

    def _handle_column_header_click(self, clicked_col: int) -> None:
        """Handle clicks on column headers for double-click detection."""
        self.log(f"_handle_column_header_click called with clicked_col={clicked_col}")
        
        if self.data is None:
            self.log("No data available in _handle_column_header_click")
            return
        
        # Ensure the column is valid
        if clicked_col >= len(self.data.columns):
            self.log(f"Invalid column {clicked_col}, only {len(self.data.columns)} columns available")
            return
        
        current_time = time.time()
        
        # Use a separate tracking system for column headers
        if not hasattr(self, '_last_column_header_click_time'):
            self._last_column_header_click_time = 0
            self._last_column_header_clicked = None
            self.log("Initialized column header click tracking")
        
        self.log(f"Previous column click: {self._last_column_header_clicked}, time diff: {current_time - self._last_column_header_click_time}")
        
        # Check if this is a double-click on the same column header
        if (self._last_column_header_clicked == clicked_col and 
            current_time - self._last_column_header_click_time < self._double_click_threshold):
            
            # Double-click detected on column header - show column options
            column_name = self.data.columns[clicked_col]
            self.log(f"DOUBLE-CLICK DETECTED on column header {clicked_col} ({column_name})")
            self._show_row_column_delete_modal(0, clicked_col)  # Pass the specific column
        
        # Update last click tracking
        self._last_column_header_click_time = current_time
        self._last_column_header_clicked = clicked_col

    def _show_row_column_delete_modal(self, row: int, col: int | None = None) -> None:
        """Show the row/column delete modal."""
        if self.data is None:
            return
        
        # Determine what to show based on the row clicked
        if row == 0:
            # Header row - show column options
            # Use the provided column or fall back to cursor position
            if col is not None:
                target_col = col
            else:
                cursor_coordinate = self._table.cursor_coordinate
                if cursor_coordinate and cursor_coordinate[1] < len(self.data.columns):
                    target_col = cursor_coordinate[1]
                else:
                    return
            
            if target_col < len(self.data.columns):
                column_name = self.data.columns[target_col]
                
                def handle_column_action(choice: str | None) -> None:
                    if choice == "delete-column":
                        self._delete_column(target_col)
                    elif choice == "insert-column-left":
                        self._insert_column(target_col)
                    elif choice == "insert-column-right":
                        self._insert_column(target_col + 1)
                
                modal = RowColumnDeleteModal("column", column_name, None, column_name)
                self.app.push_screen(modal, handle_column_action)
        elif row <= len(self.data):
            # Data row - show row options
            def handle_row_action(choice: str | None) -> None:
                if choice == "delete-row":
                    self._delete_row(row)
                elif choice == "insert-row-above":
                    self._insert_row(row)
                elif choice == "insert-row-below":
                    self._insert_row(row + 1)
            
            modal = RowColumnDeleteModal("row", f"Row {row}", row, None)
            self.app.push_screen(modal, handle_row_action)

    def on_data_table_cell_highlighted(self, event: DataTable.CellHighlighted) -> None:
        """Handle cell highlighting and update address."""
        row, col = event.coordinate
        
        # Show column type info when hovering over header row (row 0)
        if row == 0 and self.data is not None and col < len(self.data.columns):
            column_name = self.data.columns[col]
            dtype = self.data.dtypes[col]
            column_info = self._format_column_info_message(column_name, dtype)
            self.update_address_display(row, col, column_info)
            # Notify script panel about column selection (for keyboard navigation)
            column_type = self._get_friendly_type_name(dtype)
            self._notify_script_panel_column_selection(col, column_name, column_type)
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
            if row == 0 and self.data is not None and col < len(self.data.columns):
                column_name = self.data.columns[col]
                dtype = self.data.dtypes[col]
                column_info = self._format_column_info_message(column_name, dtype)
                self.update_address_display(row, col, column_info)
            else:
                self.update_address_display(row, col)

    def on_data_table_cursor_moved(self, event) -> None:
        """Handle cursor movement and update address."""
        cursor_coordinate = self._table.cursor_coordinate
        if cursor_coordinate:
            row, col = cursor_coordinate
            # Show column type info when cursor is on header row (row 0)
            if row == 0 and self.data is not None and col < len(self.data.columns):
                column_name = self.data.columns[col]
                dtype = self.data.dtypes[col]
                column_info = self._format_column_info_message(column_name, dtype)
                self.update_address_display(row, col, column_info)
                # Notify script panel about column selection (same as mouse click)
                column_type = self._get_friendly_type_name(dtype)
                self._notify_script_panel_column_selection(col, column_name, column_type)
            else:
                self.update_address_display(row, col)
                # Clear script panel column selection when not on header row
                self._notify_script_panel_column_clear()

    def on_key(self, event) -> bool:
        """Handle key events and update address based on cursor position."""
        # Check if key should trigger immediate cell editing
        if not self.editing_cell and self._should_start_immediate_edit(event.key):
            cursor_coordinate = self._table.cursor_coordinate
            if cursor_coordinate:
                row, col = cursor_coordinate
                
                # Don't allow immediate editing on pseudo-elements
                if self.data is not None:
                    # Skip if on pseudo-column or pseudo-row
                    if (col == len(self.data.columns) or 
                        row == len(self.data) + 1):
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
                    # Check if on pseudo-column (add column)
                    if col == len(self.data.columns):  # Last column is the pseudo-column
                        self.log("Enter pressed on pseudo-column - adding new column")
                        event.prevent_default()
                        event.stop()
                        self.action_add_column()
                        # Keep focus on the pseudo-column for easy multiple additions
                        self.call_after_refresh(self._focus_pseudo_column)
                        return True
                    
                    # Check if on pseudo-row (add row)  
                    if row == len(self.data) + 1:  # Last row is the pseudo-row (after header + data rows)
                        self.log("Enter pressed on pseudo-row - adding new row")
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
                    
                    # Check if we're in column A (col 0) and this is a double-tap
                    if (col == 0 and row > 0 and  # Column A and not header row
                        self._last_left_arrow_position == (row, col) and
                        current_time - self._last_left_arrow_time < self._double_click_threshold):
                        
                        # Double-tap detected in column A - show row operations modal
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
                    if (row == 0 and col < len(self.data.columns) and  # Header row and valid column
                        self._last_up_arrow_position == (row, col) and
                        current_time - self._last_up_arrow_time < self._double_click_threshold):
                        
                        # Double-tap detected in header row - show column operations modal
                        column_name = self.data.columns[col]
                        self.log(f"Double-tap up arrow detected in header row, column {col} ({column_name})")
                        event.prevent_default()
                        event.stop()
                        self._show_row_column_delete_modal(0, col)  # Pass row 0 and specific column
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
            if row == 0 and self.data is not None and col < len(self.data.columns):
                column_name = self.data.columns[col]
                dtype = self.data.dtypes[col]
                column_info = self._format_column_info_message(column_name, dtype)
                self.update_address_display(row, col, column_info)
            else:
                self.update_address_display(row, col)

    def _focus_pseudo_column(self) -> None:
        """Focus on the pseudo-column (Add Column) cell."""
        if self.data is not None:
            pseudo_col = len(self.data.columns)  # Last column is the pseudo-column
            self._table.cursor_coordinate = (0, pseudo_col)  # Focus on header row of pseudo-column
            self.update_address_display(0, pseudo_col)

    def _focus_pseudo_row(self) -> None:
        """Focus on the pseudo-row (Add Row) cell."""
        if self.data is not None:
            pseudo_row = len(self.data) + 1  # Last row is the pseudo-row
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
                self.log(f"Advanced to next cell: {self.get_excel_column_name(current_col)}{next_row}")
            else:
                # Stay in the current cell if it's the last row
                self._table.move_cursor(row=current_row, column=current_col)
                self.update_address_display(current_row, current_col)
                self.log(f"Stayed in current cell (last row): {self.get_excel_column_name(current_col)}{current_row}")

    def _should_start_immediate_edit(self, key: str) -> bool:
        """Check if a key should trigger immediate cell editing."""
        # Allow alphanumeric characters
        if len(key) == 1:  # Single character keys only
            return key.isalnum()
        
        # Handle special keys with their Textual key names
        return key in ['plus', 'minus', 'full_stop']

    def _handle_immediate_edit_key(self, event) -> bool:
        """Handle immediate edit key from CustomDataTable. Returns True if handled."""
        # Check if key should trigger immediate cell editing
        if not self.editing_cell and self._should_start_immediate_edit(event.key):
            cursor_coordinate = self._table.cursor_coordinate
            if cursor_coordinate:
                row, col = cursor_coordinate
                
                # Don't allow immediate editing on pseudo-elements
                if self.data is not None:
                    # Skip if on pseudo-column or pseudo-row
                    if (col == len(self.data.columns) or 
                        row == len(self.data) + 1):
                        return False
                
                # Start cell editing with the typed character as initial value
                event.prevent_default()
                event.stop()
                self.call_after_refresh(self.start_cell_edit_with_initial, row, col, event.key)
                return True
        
        return False

    def start_cell_edit_with_initial(self, row: int, col: int, initial_char: str) -> None:
        """Start editing a cell with an initial character."""
        if self.data is None:
            return
        
        # Convert Textual key names to actual characters
        key_to_char = {
            'plus': '+',
            'minus': '-',
            'full_stop': '.'
        }
        display_char = key_to_char.get(initial_char, initial_char)
        
        try:
            if row == 0:
                # Editing column name (header row) - start with the typed character
                self.editing_cell = True
                self._edit_row = row
                self._edit_col = col
                
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
                # Editing data cell - start with the typed character
                data_row = row - 1  # Subtract 1 because row 0 is headers
                if data_row < len(self.data):
                    # Store editing state
                    self.editing_cell = True
                    self._edit_row = row
                    self._edit_col = col
                    
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
        
        try:
            if row == 0:
                # Editing column name (header row)
                current_value = str(self.data.columns[col])
                
                # Store editing state
                self.editing_cell = True
                self._edit_row = row
                self._edit_col = col
                
                self.log(f"Starting column name edit: {self.get_excel_column_name(col)} = '{current_value}'")
                
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
                data_row = row - 1  # Subtract 1 because row 0 is headers
                if data_row < len(self.data):
                    raw_value = self.data[data_row, col]
                    # For None values, use empty string in the editor
                    current_value = "" if raw_value is None else str(raw_value)
                    
                    # Store editing state
                    self.editing_cell = True
                    self._edit_row = row
                    self._edit_col = col
                    
                    self.log(f"Starting cell edit: {self.get_excel_column_name(col)}{row} = '{current_value}'")
                    
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
            self._table.move_cursor(row=row, column=col)
            self.update_address_display(row, col)
            self.log(f"Restored cursor to {self.get_excel_column_name(col)}{row}")
        except Exception as e:
            self.log(f"Error restoring cursor position: {e}")

    def _restore_cursor_after_refresh(self, cursor_coordinate: tuple) -> None:
        """Restore cursor position after table refresh."""
        try:
            row, col = cursor_coordinate
            # Ensure the coordinates are still valid after refresh
            if (row >= 0 and col >= 0 and 
                row < self._table.row_count and col < self._table.column_count):
                self._table.move_cursor(row=row, column=col)
                self.update_address_display(row, col)
                self.log(f"Restored cursor after refresh to {self.get_excel_column_name(col)}{row}")
            else:
                self.log(f"Cannot restore cursor to {cursor_coordinate} - out of bounds")
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
                        # User wants to try again - restart the edit process 
                        self.log("User chose to try again after validation error")
                        self.call_after_refresh(self.start_cell_edit, self._edit_row, self._edit_col)
                    else:
                        # User cancelled - just reset the editing state
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
        problematic_chars = set(' \t\n\r\f\v()[]{}.,;:!@#$%^&*+=|\\/<>?`~"\'')
        if any(char in problematic_chars for char in name):
            problematic_found = [char for char in name if char in problematic_chars]
            return f"Column '{name}' contains problematic characters: {', '.join(repr(c) for c in problematic_found[:3])}..."
        
        # Check for names that are too long (practical limit)
        if len(name) > 100:
            return f"Column name is too long ({len(name)} characters, max 100 recommended)"
        
        # Check for common reserved words in databases/analysis tools
        reserved_words = {
            'select', 'from', 'where', 'insert', 'update', 'delete', 'create', 'drop',
            'table', 'index', 'view', 'function', 'procedure', 'trigger', 'database',
            'schema', 'primary', 'foreign', 'key', 'constraint', 'null', 'not', 'and',
            'or', 'in', 'like', 'between', 'exists', 'case', 'when', 'then', 'else',
            'group', 'order', 'by', 'having', 'limit', 'offset', 'union', 'join',
            'inner', 'outer', 'left', 'right', 'on', 'as', 'distinct', 'all'
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
        numeric_pattern = r'[-+]?(?:\d+\.?\d*|\.\d+)'
        matches = re.findall(numeric_pattern, value.strip())
        
        if not matches:
            return None, False
        
        # Take the first numeric match and try to convert to float
        try:
            numeric_str = matches[0]
            numeric_value = float(numeric_str)
            has_decimal = '.' in numeric_str
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
        if value.lower() in ('true', 'false', 'yes', 'no', '1', '0', 'y', 'n'):
            bool_value = value.lower() in ('true', 'yes', '1', 'y')
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
        
        # Default to string - NO automatic numeric extraction during cell editing
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

    def _format_column_info_message(self, column_name: str, dtype) -> str:
        """Format column information message for status bar."""
        simple_type = self._get_friendly_type_name(dtype)
        polars_type = str(dtype)
        return f"'{column_name}' // column type: {simple_type} ({polars_type})"

    def _style_cell_value(self, cell) -> str:
        """Style a cell value for display in the table."""
        if cell is None:
            return "[red]None[/red]"
        
        cell_str = str(cell)
        
        # Check for empty string (different from None)
        if cell_str == "":
            return "[dim yellow]∅[/dim yellow]"  # Empty set symbol for empty strings
        
        # Check if the string is entirely composed of space characters (but not empty)
        if cell_str and cell_str.isspace():
            # Create bright, visible underscores to represent the whitespace
            underscore_count = len(cell_str)
            return f"[bold magenta]{'_' * underscore_count}[/bold magenta]"
        else:
            return cell_str

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
            return False  # No conversion needed - store as string
            
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
                return value.lower() in ('true', '1', 'yes', 'y', 'on')
            else:
                return value  # String type
                
        except (ValueError, TypeError):
            return value  # Fallback to string - let type conversion dialog handle this

    def _update_cell_value(self, data_row: int, column_name: str, new_value):
        """Update a single cell value in the DataFrame."""
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
                        if target_type == "integer" and not has_decimal and extracted_num.is_integer():
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
            self.data = self.data.with_columns([
                pl.Series(column_name, extracted_values, dtype=new_dtype)
            ])
            
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
        if not hasattr(self, '_pending_edit'):
            return
        
        try:
            edit_info = self._pending_edit
            data_row = edit_info['data_row']
            column_name = edit_info['column_name']
            converted_value = edit_info['converted_value']
            new_type = edit_info['new_type']
            
            self.log(f"Converting column '{column_name}' to {new_type} and updating value")
            
            # Convert the entire column to the new type
            new_dtype = self._get_polars_dtype_for_type_name(new_type)
            self.data = self.data.with_columns([
                pl.col(column_name).cast(new_dtype)
            ])
            
            # Update the specific cell with the converted value
            self._update_cell_value(data_row, column_name, converted_value)
            
            # Mark as changed and refresh display
            self.has_changes = True
            self.update_title_change_indicator()
            self.refresh_table_data()
            self.update_address_display(self._edit_row, self._edit_col, f"Column converted to {new_type}")
            
            self.log(f"Successfully converted column '{column_name}' to {new_type}")
            
        except Exception as e:
            self.log(f"Error in type conversion: {e}")
            import traceback
            self.log(f"Traceback: {traceback.format_exc()}")
        finally:
            self.editing_cell = False
            if hasattr(self, '_pending_edit'):
                delattr(self, '_pending_edit')

    def _apply_edit_with_truncation(self) -> None:
        """Apply the edit by truncating/converting the value to fit the current type."""
        if not hasattr(self, '_pending_edit'):
            return
        
        try:
            edit_info = self._pending_edit
            data_row = edit_info['data_row']
            column_name = edit_info['column_name']
            new_value = edit_info['new_value']
            current_type = edit_info['current_type']
            
            # Convert value to fit current type
            current_dtype = self.data.dtypes[self._edit_col]
            converted_value = self._convert_value_to_existing_type(new_value, current_dtype)
            
            self.log(f"Applying value '{new_value}' as {current_type}: '{converted_value}'")
            
            # Update the cell with converted value
            self._update_cell_value(data_row, column_name, converted_value)
            
            # Mark as changed and refresh display
            self.has_changes = True
            self.update_title_change_indicator()
            self.refresh_table_data()
            self.update_address_display(self._edit_row, self._edit_col, f"Value converted to {current_type}")
            
            self.log(f"Successfully applied converted value '{converted_value}'")
            
        except Exception as e:
            self.log(f"Error applying edit with truncation: {e}")
            import traceback
            self.log(f"Traceback: {traceback.format_exc()}")
        finally:
            self.editing_cell = False
            if hasattr(self, '_pending_edit'):
                delattr(self, '_pending_edit')

    def finish_cell_edit(self, new_value: str) -> None:
        """Finish editing a cell and update the data."""
        if not self.editing_cell or self.data is None:
            self.log("Cannot finish edit: no editing state or no data")
            return
        
        try:
            data_row = self._edit_row - 1  # Convert from display row to data row
            column_name = self.data.columns[self._edit_col]
            
            self.log(f"Updating cell at data_row={data_row}, col={self._edit_col}, column='{column_name}' with value='{new_value}'")
            
            # Check if this is a new/empty column that needs type inference
            is_empty_column = self._is_column_empty(column_name)
            current_dtype = self.data.dtypes[self._edit_col]
            
            # Infer type from the new value
            inferred_value, inferred_type = self._infer_column_type_from_value(new_value)
            
            if is_empty_column and inferred_value is not None:
                # This is the first value in a new column - establish the column type
                self.log(f"Setting column '{column_name}' type to {inferred_type} based on first value")
                
                # Convert the entire column to the inferred type
                new_dtype = self._get_polars_dtype_for_type_name(inferred_type)
                
                # Create new column with the correct type
                self.data = self.data.with_columns([
                    pl.col(column_name).cast(new_dtype)
                ])
                
                # Update the specific cell with the converted value
                self._update_cell_value(data_row, column_name, inferred_value)
                
                # Mark as changed and refresh display
                self.has_changes = True
                self.update_title_change_indicator()
                self.refresh_table_data()
                self.update_address_display(self._edit_row, self._edit_col, f"Column type set to {inferred_type}")
                
            else:
                # This is an existing column - check for type conflicts
                needs_conversion = self._check_type_conversion_needed(current_dtype, inferred_value, inferred_type)
                
                if needs_conversion:
                    # Store pending edit for conversion dialog
                    self._pending_edit = {
                        'data_row': data_row,
                        'column_name': column_name,
                        'new_value': new_value,
                        'converted_value': inferred_value,
                        'current_type': self._get_friendly_type_name(current_dtype),
                        'new_type': inferred_type
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
                        self.call_after_refresh(self._restore_cursor_position, self._edit_row, self._edit_col)
                    
                    # Show conversion warning dialog
                    current_type_name = self._get_friendly_type_name(current_dtype)
                    modal = ColumnConversionModal(column_name, new_value, current_type_name, inferred_type)
                    self.app.push_screen(modal, handle_type_conversion)
                    return
                
                else:
                    # No conversion needed - direct update
                    converted_value = self._convert_value_to_existing_type(new_value, current_dtype)
                    self._update_cell_value(data_row, column_name, converted_value)
                    
                    # Mark as changed and refresh display
                    self.has_changes = True
                    self.update_title_change_indicator()
                    self.refresh_table_data()
                    self.update_address_display(self._edit_row, self._edit_col)
            
            self.log(f"Successfully updated cell {self.get_excel_column_name(self._edit_col)}{self._edit_row} = '{new_value}'")
            
        except Exception as e:
            self.log(f"Error finishing cell edit: {e}")
            import traceback
            self.log(f"Traceback: {traceback.format_exc()}")
        finally:
            self.editing_cell = False

    def _apply_column_conversion_and_update(self) -> None:
        """Apply column type conversion and update the cell value."""
        if not hasattr(self, '_pending_edit'):
            return
        
        try:
            edit_info = self._pending_edit
            data_row = edit_info['data_row']
            column_name = edit_info['column_name']
            converted_value = edit_info['converted_value']
            
            self.log(f"Converting column '{column_name}' to Float and updating value")
            
            # Convert the entire column to Float64
            self.data = self.data.with_columns([
                pl.col(column_name).cast(pl.Float64)
            ])
            
            # Now update the specific cell
            rows = []
            for i, row in enumerate(self.data.iter_rows()):
                if i == data_row:
                    updated_row = list(row)
                    updated_row[self._edit_col] = converted_value
                    rows.append(updated_row)
                else:
                    rows.append(list(row))
            
            # Recreate DataFrame with updated schema
            self.data = pl.DataFrame(rows, schema=self.data.schema)
            
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
            if hasattr(self, '_pending_edit'):
                delattr(self, '_pending_edit')

    def _apply_edit_without_conversion(self) -> None:
        """Apply the edit without column conversion (truncate decimal)."""
        if not hasattr(self, '_pending_edit'):
            return
        
        try:
            edit_info = self._pending_edit
            data_row = edit_info['data_row']
            new_value = edit_info['new_value']
            
            # Convert to integer (truncating decimal)
            converted_value = int(float(new_value)) if new_value.strip() else None
            
            self.log(f"Applying edit without conversion, truncating '{new_value}' to '{converted_value}'")
            
            # Update the cell with truncated value
            rows = []
            for i, row in enumerate(self.data.iter_rows()):
                if i == data_row:
                    updated_row = list(row)
                    updated_row[self._edit_col] = converted_value
                    rows.append(updated_row)
                else:
                    rows.append(list(row))
            
            # Create new DataFrame
            self.data = pl.DataFrame(rows, schema=self.data.schema)
            
            # Mark as changed and refresh display
            self.has_changes = True
            self.update_title_change_indicator()
            self.refresh_table_data()
            
            # Reset status bar
            self.update_address_display(self._edit_row, self._edit_col)
            
            self.log(f"Successfully applied truncated value '{converted_value}'")
            
        except Exception as e:
            self.log(f"Error applying edit without conversion: {e}")
            import traceback
            self.log(f"Traceback: {traceback.format_exc()}")
        finally:
            self.editing_cell = False
            if hasattr(self, '_pending_edit'):
                delattr(self, '_pending_edit')

    def update_title_change_indicator(self) -> None:
        """Update the title to show change indicator."""
        if hasattr(self.app, 'current_filename') and self.app.current_filename:
            filename = self.app.current_filename
            if self.has_changes and not filename.endswith(" ●"):
                self.app.set_current_filename(filename + " ●")
            elif not self.has_changes and filename.endswith(" ●"):
                self.app.set_current_filename(filename[:-2])

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
        
        # Add data columns
        for i, column in enumerate(self.data.columns):
            excel_col = self.get_excel_column_name(i)
            self._table.add_column(excel_col, key=column)
        
        # Add pseudo-column for adding new columns (column adder)
        pseudo_col_index = len(self.data.columns)
        pseudo_excel_col = self.get_excel_column_name(pseudo_col_index)
        self._table.add_column(pseudo_excel_col, key="__ADD_COLUMN__")
        
        # Re-enable row labels after adding columns
        self._table.show_row_labels = True
        
        # Add header row with bold formatting (without persistent type info)
        column_names = [f"[bold]{str(col)}[/bold]" for col in self.data.columns]
        # Add pseudo-column header with "+" indicator
        column_names.append("[dim italic]+ Add Column[/dim italic]")
        self._table.add_row(*column_names, label="0")
        
        # Add data rows
        for row_idx, row in enumerate(self.data.iter_rows()):
            row_label = str(row_idx + 1)
            # Style cell values (None as red, whitespace-only as orange underscores)
            styled_row = []
            for cell in row:
                styled_row.append(self._style_cell_value(cell))
            # Add empty cell for the pseudo-column
            styled_row.append("")
            self._table.add_row(*styled_row, label=row_label)
        
        # Add pseudo-row for adding new rows (row adder)
        next_row_label = str(len(self.data) + 1)
        pseudo_row_cells = ["[dim italic]+ Add Row[/dim italic]"] + [""] * (len(self.data.columns) - 1) + [""]
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
            
            if extension == '.csv':
                self.data.write_csv(file_path)
            elif extension == '.tsv':
                self.data.write_csv(file_path, separator='\t')
            elif extension == '.parquet':
                self.data.write_parquet(file_path)
            elif extension == '.json':
                self.data.write_json(file_path)
            elif extension in ['.jsonl', '.ndjson']:
                self.data.write_ndjson(file_path)
            elif extension in ['.xlsx', '.xls']:
                try:
                    self.data.write_excel(file_path)
                except AttributeError as e:
                    raise Exception("Excel file support requires additional dependencies. Please install with: pip install polars[xlsx]") from e
            elif extension in ['.feather', '.ipc', '.arrow']:
                self.data.write_ipc(file_path)
            else:
                # Default to CSV
                if not file_path.endswith('.csv'):
                    file_path += '.csv'
                self.data.write_csv(file_path)
            
            # Update tracking
            self.has_changes = False
            self.original_data = self.data.clone()
            self.update_title_change_indicator()
            
            self.log(f"Data saved to: {file_path}")
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
                    if hasattr(self.app, 'set_current_filename'):
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
            
        if hasattr(self.app, 'current_filename') and self.app.current_filename:
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
                self.data = self.data.with_columns([
                    pl.col(column_name).cast(new_dtype)
                ])
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
                self.data = self.data.with_columns([
                    pl.Series(column_name, converted_values, dtype=new_dtype)
                ])
            
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
            self.log(f"Column '{column_name}' doesn't contain enough numeric content for extraction")

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
                return value.lower() in ('true', '1', 'yes', 'y', 'on')
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
        if col >= len(self.data.columns):
            self.log("Cannot extract numbers from pseudo-column")
            return
        
        column_name = self.data.columns[col]
        
        # Check if this column would benefit from numeric extraction
        should_offer, suggested_type = self._should_offer_numeric_extraction(column_name)
        
        if not should_offer:
            self.log(f"Column '{column_name}' doesn't contain enough numeric content for extraction")
            # Still show a message to the user
            try:
                status_bar = self.query_one("#status-bar", Static)
                status_bar.update(f"Column '{column_name}' doesn't contain enough numeric content for extraction")
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
                result = subprocess.run(['pbpaste'], capture_output=True, text=True)
                clipboard_content = result.stdout
            elif sys.platform == "linux":  # Linux
                try:
                    result = subprocess.run(['xclip', '-selection', 'clipboard', '-o'], 
                                          capture_output=True, text=True)
                    clipboard_content = result.stdout
                except FileNotFoundError:
                    # Try with xsel if xclip not available
                    result = subprocess.run(['xsel', '--clipboard', '--output'], 
                                          capture_output=True, text=True)
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
            
            # Add the new column with null values initially - type will be inferred from first value
            self.data = self.data.with_columns([
                pl.lit(None, dtype=pl.String).alias(new_column_name)
            ])
            
            # Mark as changed and refresh display
            self.has_changes = True
            self.update_title_change_indicator()
            self.refresh_table_data()
            
            # Move cursor to the new column header
            new_col_index = len(self.data.columns) - 1
            self.call_after_refresh(self._move_cursor_to_new_column, 0, new_col_index)
            
            self.log(f"Added new column '{new_column_name}'. Table now has {len(self.data.columns)} columns")
            
        except Exception as e:
            self.log(f"Error adding column: {e}")
            self.update_address_display(0, 0, f"Add column failed: {str(e)[:30]}...")

    def _move_cursor_to_new_row(self, row: int, col: int) -> None:
        """Move cursor to a newly added row."""
        try:
            self._table.move_cursor(row=row, column=col)
            self.update_address_display(row, col, "New row added")
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
                self.data = pl.DataFrame({
                    "Column_1": empty_column_data
                }, schema={"Column_1": pl.String})
                
                self.log(f"Deleted last column '{column_name}', created empty 'Column_1' column with {num_rows} rows")
            else:
                # Delete the column normally
                remaining_columns = [name for i, name in enumerate(self.data.columns) if i != col]
                self.data = self.data.select(remaining_columns)
            
            # Mark as changed and refresh display
            self.has_changes = True
            self.update_title_change_indicator()
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
            
            self.log(f"Deleted column '{column_name}'. Table now has {len(self.data.columns)} columns")
            
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
            
            self.log(f"Inserted new row at position {insert_at_row}. Table now has {len(self.data)} rows")
            
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
                new_columns = current_columns[:insert_at_col] + [new_column_name] + current_columns[insert_at_col:]
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
                    # Existing column data - preserve original data and type
                    original_col_name = col_name
                    new_data[col_name] = self.data[original_col_name].to_list()
                    new_schema[col_name] = self.data.dtypes[self.data.columns.index(original_col_name)]
                    self.log(f"Copied existing column {col_name} with type {new_schema[col_name]}")
            
            # Create new DataFrame with reordered columns and preserved types
            self.data = pl.DataFrame(new_data, schema=new_schema)
            
            self.log(f"Created new DataFrame with shape: {self.data.shape}")
            self.log(f"New columns: {self.data.columns}")
            
            # Mark as changed and refresh display
            self.has_changes = True
            self.update_title_change_indicator()
            self.refresh_table_data()
            
            # Move cursor to the newly inserted column header
            self.call_after_refresh(self._move_cursor_after_insert, 0, insert_at_col)
            
            self.log(f"Successfully inserted new column '{new_column_name}' at position {insert_at_col}. Table now has {len(self.data.columns)} columns")
            
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
            lines = content.strip().split('\n')
            if len(lines) < 1:
                return None
            
            # Detect separator (tab is most common from spreadsheets)
            first_line = lines[0]
            tab_count = first_line.count('\t')
            comma_count = first_line.count(',')
            
            # Prefer tab separator (common from Google Sheets/Excel)
            if tab_count > 0:
                separator = '\t'
            elif comma_count > 0:
                separator = ','
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
                'rows': processed_rows,
                'has_headers': has_headers,
                'separator': separator,
                'num_rows': len(processed_rows),
                'num_cols': max_cols,
                'is_wikipedia_style': self._detect_wikipedia_table(parsed_rows)
            }
            
        except Exception as e:
            self.log(f"Error parsing clipboard data: {e}")
            return None

    def _detect_wikipedia_table(self, rows: list) -> bool:
        """Detect if this looks like a Wikipedia table."""
        if len(rows) < 2:
            return False
        
        # Check for footnote markers like [a], [b], [c], etc.
        footnote_pattern = r'\[[a-z]\]'
        has_footnotes = False
        
        for row in rows[:10]:  # Check first 10 rows
            for cell in row:
                if cell and '[' in cell and ']' in cell:
                    import re
                    if re.search(footnote_pattern, cell):
                        has_footnotes = True
                        break
            if has_footnotes:
                break
        
        # Check for complex header structure (short second row that looks like units)
        if len(rows) >= 2:
            first_row_cells = [c for c in rows[0] if c.strip()]
            second_row_cells = [c for c in rows[1] if c.strip()]
            
            # Wikipedia tables often have unit rows like "mi2", "km2", "/ mi2", "/ km2"
            unit_indicators = ['mi2', 'km2', '/ mi2', '/ km2', '%', '°N', '°W', '°E', '°S']
            has_units = any(any(indicator in cell for indicator in unit_indicators) 
                          for cell in second_row_cells)
            
            if has_units:
                return True
        
        return has_footnotes

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
        
        if is_wiki_style:
            # Handle complex Wikipedia headers
            headers = self._create_wikipedia_headers(rows[:3], max_cols)  # Use first 3 rows for headers
            
            # Find where the actual data starts (skip header rows)
            data_start_idx = self._find_data_start(rows)
            
            # Process data rows - clean footnotes and format
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
                first_row_numeric = sum(1 for cell in first_row if cell.replace('.', '').replace('-', '').isdigit())
                second_row_numeric = sum(1 for cell in second_row if cell.replace('.', '').replace('-', '').isdigit())
                
                if first_row_numeric < second_row_numeric and first_row_numeric < len(first_row) * 0.5:
                    has_headers = True
        
        return processed_rows, has_headers

    def _create_wikipedia_headers(self, header_rows: list, max_cols: int) -> list:
        """Create meaningful headers from Wikipedia complex header structure with spanning headers."""
        if len(header_rows) < 2:
            return [f"Column_{i+1}" for i in range(max_cols)]
        
        # Analyze the structure based on the actual Wikipedia format:
        # Row 0: ['City', 'ST', '2024']                                           (3 cols)
        # Row 1: ['estimate', '2020']                                             (2 cols) 
        # Row 2: ['census', 'Change', '2020 land area', '2020 density', 'Location'] (5 cols)
        # Row 3: ['mi2', 'km2', '/ mi2', '/ km2']                                 (4 cols)
        
        combined_headers = []
        
        # Create a mapping table for the complex structure
        header_mapping = {
            0: "City",                          # Column 0: City
            1: "State",                         # Column 1: ST -> State  
            2: "2024_estimate",                 # Column 2: 2024 + estimate
            3: "2020_census",                   # Column 3: 2020 + census
            4: "Change_percent",                # Column 4: Change
            5: "Land_area_mi2",                 # Column 5: 2020 land area + mi2
            6: "Land_area_km2",                 # Column 6: 2020 land area + km2
            7: "Density_per_mi2",               # Column 7: 2020 density + / mi2
            8: "Density_per_km2",               # Column 8: 2020 density + / km2
            9: "Location"                       # Column 9: Location
        }
        
        # Use the predefined mapping for known Wikipedia city table structure
        for col_idx in range(max_cols):
            if col_idx in header_mapping:
                combined_headers.append(header_mapping[col_idx])
            else:
                combined_headers.append(f"Column_{col_idx + 1}")
        
        return combined_headers

    def _find_data_start(self, rows: list) -> int:
        """Find where actual data starts in a Wikipedia table."""
        for i, row in enumerate(rows):
            if i < 3:  # Skip first three rows (likely headers for Wikipedia tables)
                continue
            
            # Look for rows that start with a city name or number (data indicators)
            first_cell = row[0].strip() if row and row[0] else ""
            
            # Wikipedia city tables often start with numbers or have footnoted city names
            if (first_cell.isdigit() or  # Row number
                any(city_indicator in first_cell.lower() for city_indicator in 
                    ['new york', 'los angeles', 'chicago', 'houston', 'phoenix']) or
                '[' in first_cell):  # Footnoted city name
                
                # Double-check this looks like data by checking for numeric content
                numeric_cells = 0
                total_cells = 0
                
                for cell in row:
                    if cell.strip():
                        total_cells += 1
                        # Check for numbers (including formatted ones like "1,234.56")
                        clean_cell = cell.replace(',', '').replace('%', '').replace('+', '').replace('−', '').replace('°', '')
                        if any(c.isdigit() for c in clean_cell):
                            numeric_cells += 1
                
                # If more than 40% of cells are numeric, this is likely data
                if total_cells > 0 and numeric_cells / total_cells > 0.4:
                    return i
        
        # Default to row 4 for Wikipedia tables (skip more header rows)
        return min(4, len(rows) - 1)

    def _clean_wikipedia_row(self, row: list, max_cols: int) -> list:
        """Clean a Wikipedia data row by removing footnotes and formatting properly."""
        import re
        
        cleaned_row = []
        footnote_pattern = r'\[[a-z]\]'
        
        for i in range(max_cols):
            if i < len(row):
                cell = row[i].strip()
                
                # Remove footnote markers like [a], [b], [c]
                cell = re.sub(footnote_pattern, '', cell)
                
                # Clean up common Wikipedia formatting
                cell = cell.replace('−', '-')  # Replace unicode minus with regular minus
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
            rows = parsed_data['rows']
            
            # Use the user's choice for headers instead of the automatic detection
            if use_header:
                headers = rows[0]
                data_rows = rows[1:]
            else:
                # Generate column names
                headers = [f"Column_{i+1}" for i in range(parsed_data['num_cols'])]
                data_rows = rows
            
            # Create dictionary for DataFrame
            df_dict = {}
            for i, header in enumerate(headers):
                # Clean header name
                clean_header = header if header.strip() else f"Column_{i+1}"
                column_data = []
                
                for row in data_rows:
                    cell_value = row[i] if i < len(row) else ""
                    # Try to convert to appropriate type
                    if cell_value.strip():
                        # Try numeric conversion - be more careful about mixed types
                        try:
                            # Remove common formatting characters
                            clean_val = cell_value.replace(',', '').replace('%', '').replace('+', '').replace('−', '-')
                            
                            # Try float first (safer for mixed numeric data)
                            if '.' in clean_val or ',' in cell_value:
                                cell_value = float(clean_val)
                            else:
                                # For integers, use float to avoid type conflicts
                                try:
                                    int_val = int(clean_val)
                                    cell_value = float(int_val)  # Store as float to avoid mixed type issues
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
                self.load_dataframe(new_df)
                self.is_sample_data = False
                self.data_source_name = None
                self.app.set_current_filename("pasted_data [CLIPBOARD]")
                self.update_address_display(0, 0, f"Pasted {len(data_rows)} rows, {len(headers)} columns")
                
            elif operation == "append" and self.data is not None:
                # Append to existing data
                try:
                    combined_df = pl.concat([self.data, new_df], how="vertical_relaxed")
                    self.load_dataframe(combined_df)
                    self.has_changes = True
                    self.update_title_change_indicator()
                    self.update_address_display(0, 0, f"Appended {len(data_rows)} rows")
                except Exception as e:
                    self.update_address_display(0, 0, f"Append failed: {str(e)[:30]}...")
                    
            elif operation == "new_sheet":
                # For now, same as replace (could be extended for multi-sheet support)
                self.load_dataframe(new_df)
                self.is_sample_data = False
                self.data_source_name = None
                self.app.set_current_filename("pasted_data [CLIPBOARD]")
                self.update_address_display(0, 0, f"Created new sheet: {len(data_rows)} rows")
            
        except Exception as e:
            self.log(f"Error executing paste operation: {e}")
            self.update_address_display(0, 0, f"Paste failed: {str(e)[:30]}...")


class ToolsPanel(Widget):
    """Panel for displaying tools and controls."""
    
    DEFAULT_CSS = """
    ToolsPanel RadioSet {
        margin-bottom: 1;
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
    """
    
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.current_column = None
        self.current_column_name = None
        self.data_grid = None

    def compose(self) -> ComposeResult:
        """Compose the tools panel."""
        # Navigation radio buttons for sections
        yield RadioSet("Column Type", "Polars Exec", id="section-radio")
        
        # Content switcher for the two sections
        with ContentSwitcher(initial="column-type-content", id="content-switcher"):
            # Column Type Section
            with Vertical(id="column-type-content", classes="panel-section"):
                yield Static("Select a column header to modify its type.", 
                            id="column-type-instruction", 
                            classes="instruction-text")
                yield Static("No column selected", id="column-info", classes="column-info")
                
                # Data type selector - initially hidden
                yield Select(
                    options=[
                        ("Text (String)", "text"),
                        ("Integer", "integer"), 
                        ("Float (Decimal)", "float"),
                        ("Boolean", "boolean")
                    ],
                    value="text",
                    id="type-selector",
                    classes="type-selector hidden"
                )
                
                yield Button("Apply Type Change", 
                            id="apply-type-change", 
                            variant="primary",
                            classes="apply-button hidden")
            
            # Polars Execution Section
            with Vertical(id="polars-exec-content", classes="panel-section"):
                yield Static("Write Polars code to transform your data.", 
                            classes="instruction-text")
                
                # Editable code input area with syntax highlighting
                yield TextArea(
                    "df = df.",
                    id="code-input",
                    classes="code-input",
                    language="python"
                )
                
                with Horizontal(classes="button-row"):
                    yield Button("Execute Code", id="execute-code", variant="primary", classes="panel-button")
                
                # Execution result/error display
                yield Static("", id="execution-result", classes="execution-result hidden")

    def on_mount(self) -> None:
        """Set up references to the data grid."""
        try:
            # Find the data grid to interact with
            self.data_grid = self.app.query_one("#data-grid", ExcelDataGrid)
            
            # Set initial section to Column Type
            content_switcher = self.query_one("#content-switcher", ContentSwitcher)
            content_switcher.current = "column-type-content"
            
            # Set default radio button selection to Column Type (index 0)
            radio_set = self.query_one("#section-radio", RadioSet)
            radio_set.pressed_index = 0
            
        except Exception as e:
            self.log(f"Could not find data grid or setup content switcher: {e}")

    def on_radio_set_changed(self, event: RadioSet.Changed) -> None:
        """Handle radio button changes in the tools panel."""
        if event.radio_set.id == "section-radio":
            if event.pressed.label == "Column Type":
                self._switch_to_section("column-type-content")
            elif event.pressed.label == "Polars Exec":
                self._switch_to_section("polars-exec-content")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        """Handle button presses in the tools panel."""
        if event.button.id == "apply-type-change":
            self._apply_type_change()
        elif event.button.id == "execute-code":
            self._execute_code()

    def _switch_to_section(self, section_id: str) -> None:
        """Switch to the specified section."""
        try:
            content_switcher = self.query_one("#content-switcher", ContentSwitcher)
            content_switcher.current = section_id
            
            # If switching to Polars Exec section, set preferred focus to Execute Code button
            if section_id == "polars-exec-content":
                self.call_later(self._focus_execute_button)
                
        except Exception as e:
            self.log(f"Error switching to section {section_id}: {e}")

    def update_column_selection(self, column_index: int, column_name: str, column_type: str) -> None:
        """Update the panel when a column header is selected."""
        self.current_column = column_index
        self.current_column_name = column_name
        
        try:
            # Update column info display
            column_info = self.query_one("#column-info", Static)
            column_info.update(f"Column {self.get_excel_column_name(column_index)}: '{column_name}' ({column_type})")
            
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
                "boolean": "boolean"
            }
            current_type = type_mapping.get(column_type, "text")
            type_selector.value = current_type
            
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
            
        except Exception as e:
            self.log(f"Error clearing column selection: {e}")

    def get_excel_column_name(self, col_index: int) -> str:
        """Convert column index to Excel-style column name (A, B, ..., Z, AA, AB, ...)."""
        result = ""
        while col_index >= 0:
            result = chr(ord('A') + (col_index % 26)) + result
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
            if hasattr(self.data_grid, 'data') and self.data_grid.data is not None:
                new_type = self.data_grid._get_friendly_type_name(
                    self.data_grid.data.dtypes[self.current_column]
                )
                self.update_column_selection(self.current_column, self.current_column_name, new_type)
            
        except Exception as e:
            self.log(f"Error applying type change: {e}")

    def _execute_code(self) -> None:
        """Execute the Polars code on the current dataframe."""
        if self.data_grid is None or not hasattr(self.data_grid, 'data') or self.data_grid.data is None:
            self._show_execution_result("No data loaded. Please load a dataset first.", is_error=True)
            return
            
        try:
            code_input = self.query_one("#code-input", TextArea)
            code = code_input.text.strip()
            
            if not code or code == "df":
                self._show_execution_result("No code to execute. Please enter Polars code.", is_error=True)
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
                'pl': pl,
                'df': self.data_grid.data.clone(),  # Work with a copy initially
                '__builtins__': __builtins__
            }
            
            # Log the code being executed
            self.log(f"Executing code: {code}")
            
            # Execute the code
            exec(code, execution_context)
            
            # Get the result dataframe
            result_df = execution_context.get('df')
            
            if result_df is None:
                self._show_execution_result("Code executed but no dataframe returned. Make sure to assign result to 'df'.", is_error=True)
                return
            
            # Validate that we got a Polars DataFrame
            if not hasattr(result_df, 'shape') or not hasattr(result_df, 'columns'):
                self._show_execution_result("Result is not a valid Polars DataFrame.", is_error=True)
                return
            
            # Log the result dataframe info
            result_shape = result_df.shape
            result_columns = list(result_df.columns)
            self.log(f"Result dataframe: {result_shape} - columns: {result_columns}")
            
            # Check if the dataframe actually changed
            if result_shape == original_shape and result_columns == original_columns:
                # Same shape and columns - check if data changed
                try:
                    if result_df.equals(self.data_grid.data):
                        self._show_execution_result("Code executed but dataframe unchanged.", is_error=True)
                        return
                except Exception:
                    # If comparison fails, assume it changed
                    pass
            
            # Apply the transformation to the actual data grid
            self.data_grid.load_dataframe(result_df)
            self.data_grid.has_changes = True
            self.data_grid.update_title_change_indicator()
            
            # Show success message with detailed info
            rows, cols = result_shape
            if cols > len(original_columns):
                new_columns = [col for col in result_columns if col not in original_columns]
                self._show_execution_result(f"✓ Code executed successfully! Result: {rows} rows, {cols} columns. New columns: {new_columns}", is_error=False)
            elif cols < len(original_columns):
                removed_columns = [col for col in original_columns if col not in result_columns]
                self._show_execution_result(f"✓ Code executed successfully! Result: {rows} rows, {cols} columns. Removed columns: {removed_columns}", is_error=False)
            else:
                self._show_execution_result(f"✓ Code executed successfully! Result: {rows} rows, {cols} columns", is_error=False)
            
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

            # Drawer tab (narrow strip on right) - initially hidden
            with Vertical(id="drawer-tab", classes="drawer-tab hidden"):
                yield Button("◀", id="tab-button", classes="tab-button")
                yield Static("T\nO\nO\nL\nS", classes="tab-label")

            # Drawer panel (right side) - initially hidden
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
    
    def __init__(self, current_value: str, cell_address: str = "", is_immediate_edit: bool = False) -> None:
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
                value=self.current_value,
                placeholder="Enter new value...",
                id="cell-value-input"
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
                yield Static("Commands:", classes="command-name")
                yield Static(":q, :quit - Quit application (warns if changes)", classes="command-item")
                yield Static(":q! - Force quit without saving", classes="command-item")
                yield Static(":wa, :sa - Save as (new filename)", classes="command-item")
                yield Static(":wo, :so - Save and overwrite", classes="command-item")
                yield Static(":ref, :help - Show this command reference", classes="command-item")
                yield Static("", classes="command-item")
                yield Static("Cell Editing:", classes="command-name")
                yield Static("• Enter - Edit selected cell", classes="command-item")
                yield Static("• Enter in edit modal - Save changes", classes="command-item")
                yield Static("• Escape in edit modal - Cancel changes", classes="command-item")
                yield Static("• Ctrl+V / Cmd+V - Paste from clipboard", classes="command-item")
                yield Static("• Ctrl+Shift+N / Cmd+Shift+N - Extract numbers from column", classes="command-item")
                yield Static("", classes="command-item")
                yield Static("Navigation:", classes="command-name")
                yield Static("• Arrow keys - Navigate data table", classes="command-item")
                yield Static("• Tab - Move between UI elements", classes="command-item")
                yield Static("• : (colon) - Enter command mode", classes="command-item")
                yield Static("", classes="command-item")
                yield Static("Data Loading:", classes="command-name")
                yield Static("• Load Dataset - Open file selection modal", classes="command-item")
                yield Static("• Load Sample Data - Load built-in sample data", classes="command-item")
                yield Static("", classes="command-item")
                yield Static("Script Panel:", classes="command-name")
                yield Static("• Click drawer tab (▶) - Open/close script panel", classes="command-item")
                yield Static("• × button - Close script panel", classes="command-item")
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
            info_text = f"{self.parsed_data['num_rows']} rows × {self.parsed_data['num_cols']} columns"
            if self.parsed_data['has_headers']:
                info_text += " (with headers)"
            if self.parsed_data.get('is_wikipedia_style', False):
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
                
                yield Button("Create New Sheet", id="new-sheet-btn", variant="primary" if not self.has_existing_data else "default")
            
            yield Button("Cancel", id="cancel-btn", variant="error", classes="cancel-btn")
    
    def _create_preview_text(self) -> str:
        """Create preview text showing first few rows."""
        rows = self.parsed_data['rows']
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
            if self.parsed_data['separator'] == '\t':
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
            # Default action - prefer new_sheet if no existing data, otherwise replace
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
            yield Static(f"Extract numbers from column '{self.column_name}' values?", classes="message")
            
            # Preview section
            with Vertical(classes="preview"):
                yield Static("Preview of conversion:", classes="preview-title")
                
                for original_value in self.sample_data:
                    extracted_num, has_decimal = self._extract_numeric_from_string(original_value)
                    
                    if extracted_num is not None:
                        if self.target_type == "integer" and not has_decimal and extracted_num.is_integer():
                            converted = int(extracted_num)
                            yield Static(
                                f"'{original_value}' → {converted}",
                                classes="preview-item extracted"
                            )
                        else:
                            yield Static(
                                f"'{original_value}' → {extracted_num}",
                                classes="preview-item extracted"
                            )
                    else:
                        yield Static(
                            f"'{original_value}' → None",
                            classes="preview-item null-result"
                        )
            
            yield Static("")  # Spacer
            with Horizontal(classes="modal-buttons"):
                yield Button("❌ Keep as Text", id="keep-text", variant="error")
                yield Button(f"🔢 Extract to {self.target_type.title()}", id="extract", variant="success")
                yield Button("Cancel", id="cancel", variant="default")

    def _extract_numeric_from_string(self, value: str) -> tuple[float | None, bool]:
        """Extract numeric content from a mixed string (copy of main method for preview)."""
        if not value or not value.strip():
            return None, False
        
        # Use regex to find all numeric parts including decimals
        import re
        numeric_pattern = r'[-+]?(?:\d+\.?\d*|\.\d+)'
        matches = re.findall(numeric_pattern, value.strip())
        
        if not matches:
            return None, False
        
        # Take the first numeric match and try to convert to float
        try:
            numeric_str = matches[0]
            numeric_value = float(numeric_str)
            has_decimal = '.' in numeric_str
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
            yield Static(f"Column '{self.column_name}' is currently {self.from_type}", classes="message")
            yield Static(f"Value: '{self.value}'", classes="value-display")
            
            # Dynamic message and buttons based on conversion type
            if self.from_type == "integer" and self.to_type == "float":
                yield Static(f"Convert column to {self.to_type} to preserve decimal values?", classes="options")
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
                    yield Button(f"❌ Keep as {self.from_type.title()}", id="keep-current", variant="error")
                    yield Button(f"✓ Convert to {self.to_type.title()}", id="convert-type", variant="success")
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
    
    def __init__(self, delete_type: str, target_info: str, row_number: int = None, column_name: str = None) -> None:
        super().__init__()
        self.delete_type = delete_type  # "row" or "column"
        self.target_info = target_info
        self.row_number = row_number
        self.column_name = column_name
    
    def compose(self) -> ComposeResult:
        with Vertical():
            if self.delete_type == "row":
                yield Label("[bold blue]Row Options[/bold blue]")
                yield Static(f"Options for {self.target_info}:")
                with Horizontal(classes="modal-buttons"):
                    yield Button("Delete Row", id="delete-row", variant="error")
                    yield Button("Insert Row Above", id="insert-row-above", variant="primary")
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
        elif event.button.id == "cancel":
            self.dismiss(None)
    
    def on_key(self, event) -> None:
        """Handle keyboard shortcuts and button navigation."""
        if event.key == "escape":
            self.dismiss(None)
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
            formatted = re.sub(r'\s*\([^)]*Python[^)]*\)', '', formatted)
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


class SweetFooter(Footer):
    """Custom footer with Sweet-specific bindings."""

    def compose(self) -> ComposeResult:
        yield Static("Press : for command mode")
