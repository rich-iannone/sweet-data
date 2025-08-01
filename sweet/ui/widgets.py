"""Custom Textual widgets for Sweet."""
from __future__ import annotations

import keyword
import re
import time
from pathlib import Path
from typing import TYPE_CHECKING

from textual.app import ComposeResult
from textual.containers import Container, Horizontal, Vertical
from textual.reactive import reactive
from textual.screen import ModalScreen
from textual.widget import Widget
from textual.widgets import Button, Checkbox, DataTable, Footer, Input, Label, Select, Static

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


class FileInputModal(ModalScreen[str]):
    """Modal screen for file path input."""

    CSS = """
    FileInputModal {
        align: center middle;
    }
    
    #file-modal {
        width: 80;
        height: 16;
        background: $surface;
        border: thick $primary;
        padding: 2;
    }
    
    #file-modal Label {
        margin-bottom: 1;
        text-style: bold;
    }
    
    #file-modal Input {
        margin-bottom: 1;
        width: 100%;
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
        margin-top: 1;
    }
    
    .modal-buttons Button {
        margin: 0 2;
        min-width: 12;
    }
    """

    def compose(self) -> ComposeResult:
        """Compose the modal content."""
        with Vertical(id="file-modal"):
            yield Label("Enter file path:")
            yield Input(placeholder="e.g., /path/to/data.csv", id="file-input")
            yield Static("", id="error-message", classes="error-message hidden")
            with Horizontal(classes="modal-buttons"):
                yield Button("Cancel", id="cancel-file", variant="error")
                yield Button("OK", id="load-file", variant="primary")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        """Handle button presses in the modal."""
        if event.button.id == "load-file":
            self._try_load_file()
        elif event.button.id == "cancel-file":
            self.dismiss(None)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        """Handle Enter key press in the input field."""
        if event.input.id == "file-input":
            self._try_load_file()

    def _try_load_file(self) -> None:
        """Try to load the file and validate it before dismissing modal."""
        file_input = self.query_one("#file-input", Input)
        file_path = file_input.value.strip()
        error_message = self.query_one("#error-message", Static)
        
        if not file_path:
            self._show_error("Please enter a file path")
            return
        
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
            
            # Try to read just the first few rows to validate format
            try:
                # Check file extension - support multiple formats
                supported_extensions = ('.csv', '.tsv', '.txt', '.parquet', '.json', '.jsonl', '.ndjson', '.xlsx', '.xls', '.feather', '.ipc', '.arrow')
                if not file_path.lower().endswith(supported_extensions):
                    self._show_error("Unsupported file format. Supported: CSV, TSV, TXT, Parquet, JSON, JSONL, Excel, Feather, Arrow")
                    return
                
                # Try to read first few rows to validate
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
                
                # File is valid, dismiss modal with file path
                self.dismiss(file_path)
                
            except Exception as e:
                self._show_error(f"Cannot read file: {str(e)[:50]}...")
                return
                
        except Exception as e:
            self._show_error(f"Error accessing file: {str(e)[:50]}...")
            return

    def _show_error(self, message: str) -> None:
        """Show an error message in the modal."""
        error_message = self.query_one("#error-message", Static)
        error_message.update(message)
        error_message.remove_class("hidden")
        
        # Clear error after a few seconds or when user starts typing
        self.set_timer(5.0, lambda: self._clear_error())
    
    def _clear_error(self) -> None:
        """Clear the error message."""
        try:
            error_message = self.query_one("#error-message", Static)
            error_message.add_class("hidden")
            error_message.update("")
        except Exception:
            pass

    def on_input_changed(self, event: Input.Changed) -> None:
        """Clear error when user starts typing."""
        if event.input.id == "file-input":
            self._clear_error()


class CustomDataTable(DataTable):
    """Custom DataTable that allows immediate editing for specific keys."""
    
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
        self.is_sample_data = False  # Track if we're working with internal sample data
        self.data_source_name = None  # Name of the data source (for sample data)
        
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
            if file_path:
                self.load_file(file_path)
            else:
                # User cancelled - return to welcome screen
                self.show_empty_state()
        
        # Push the modal screen
        modal = FileInputModal()
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
        try:
            if pl is None:
                self._table.clear(columns=True)
                self._table.add_column("Error")
                self._table.add_row("Polars not available")
                return

            # Detect file format and load accordingly
            extension = Path(file_path).suffix.lower()
            
            # Load the file based on extension
            if extension in ['.csv', '.txt']:
                df = pl.read_csv(file_path)
            elif extension == '.tsv':
                df = pl.read_csv(file_path, separator='\t')
            elif extension == '.parquet':
                df = pl.read_parquet(file_path)
            elif extension == '.json':
                df = pl.read_json(file_path)
            elif extension in ['.jsonl', '.ndjson']:
                df = pl.read_ndjson(file_path)
            elif extension in ['.xlsx', '.xls']:
                # Note: Polars Excel support might require additional dependencies
                try:
                    df = pl.read_excel(file_path)
                except AttributeError as e:
                    raise Exception("Excel file support requires additional dependencies. Please install with: pip install polars[xlsx]") from e
            elif extension == '.feather':
                df = pl.read_ipc(file_path)
            elif extension in ['.ipc', '.arrow']:
                df = pl.read_ipc(file_path)
            else:
                # Try CSV as fallback
                df = pl.read_csv(file_path)
            
            self.load_dataframe(df)
            
            # Mark as external file (not sample data)
            self.is_sample_data = False
            self.data_source_name = None
            
            # Update the app title with the filename and format
            file_format = self.get_file_format(file_path)
            filename_with_format = f"{file_path} [{file_format}]"
            self.app.set_current_filename(filename_with_format)
            
        except Exception as e:
            self._table.clear(columns=True)
            self._table.add_column("Error")
            self._table.add_row(f"Failed to load {file_path}: {str(e)}")
            self.log(f"Error loading file {file_path}: {e}")

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
            # Style None values as red text but no background styling
            styled_row = []
            for cell in row:
                if cell is None:
                    styled_row.append("[red]None[/red]")
                else:
                    styled_row.append(str(cell))
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
                
                # Double-click detected - start cell editing
                if not self.editing_cell:  # Only start editing if not already editing
                    self.log(f"Double-click detected on cell {self.get_excel_column_name(col)}{row}")
                    self.call_after_refresh(self.start_cell_edit, row, col)
            
            # Update last click tracking
            self._last_click_time = current_time
            self._last_click_coordinate = (row, col)

    def _notify_script_panel_column_selection(self, col_index: int, column_name: str, column_type: str) -> None:
        """Notify the script panel about column selection."""
        try:
            # Find the script panel through the drawer container
            container = self.app.query_one("#main-container", DrawerContainer)
            script_panel = container.query_one("#script-panel", ScriptPanel)
            script_panel.update_column_selection(col_index, column_name, column_type)
        except Exception as e:
            self.log(f"Could not notify script panel of column selection: {e}")

    def _notify_script_panel_column_clear(self) -> None:
        """Notify the script panel to clear column selection."""
        try:
            # Find the script panel through the drawer container
            container = self.app.query_one("#main-container", DrawerContainer)
            script_panel = container.query_one("#script-panel", ScriptPanel)
            script_panel.clear_column_selection()
        except Exception as e:
            self.log(f"Could not notify script panel to clear column selection: {e}")

    def on_data_table_cell_highlighted(self, event: DataTable.CellHighlighted) -> None:
        """Handle cell highlighting and update address."""
        row, col = event.coordinate
        
        # Show column type info when hovering over header row (row 0)
        if row == 0 and self.data is not None and col < len(self.data.columns):
            column_name = self.data.columns[col]
            dtype = self.data.dtypes[col]
            column_info = self._format_column_info_message(column_name, dtype)
            self.update_address_display(row, col, column_info)
        else:
            self.update_address_display(row, col)

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
            else:
                self.update_address_display(row, col)

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
        
        # Allow the table to handle navigation keys and update display after
        if event.key in ["up", "down", "left", "right", "tab"]:
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
            # Style None values as red text but no background styling
            styled_row = []
            for cell in row:
                if cell is None:
                    styled_row.append("[red]None[/red]")
                else:
                    styled_row.append(str(cell))
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


class ScriptPanel(Widget):
    """Panel for displaying generated code and controls."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.current_column = None
        self.current_column_name = None
        self.data_grid = None

    def compose(self) -> ComposeResult:
        """Compose the script panel."""
        # Column Type Section
        yield Static("Column Type", classes="panel-section-header")
        with Vertical(id="column-type-section", classes="panel-section"):
            yield Static("Select a column header (A, B, C, ...) to modify its type", 
                        id="column-type-instruction", 
                        classes="instruction-text")
            yield Static("No column selected", id="column-info", classes="column-info")
            
            # Data type selector - initially hidden
            yield Select(
                options=[
                    ("Text (String)", "text"),
                    ("Integer", "integer"), 
                    ("Float (Decimal)", "float"),
                    ("Boolean", "boolean"),
                    ("Extract Numbers", "extract_numbers")
                ],
                value="text",
                id="type-selector",
                classes="type-selector hidden"
            )
            
            yield Button("Apply Type Change", 
                        id="apply-type-change", 
                        variant="primary",
                        classes="apply-button hidden")
        
        # Generated Code Section
        yield Static("Generated Polars Code", classes="panel-section-header")
        with Vertical(classes="panel-section"):
            yield Static(
                "# No transformations yet\nimport polars as pl\n\n# Load your data and start transforming!",
                id="code-content",
                classes="code-display",
            )
            with Horizontal(classes="button-row"):
                yield Button("Clear Code", id="clear-code", classes="panel-button")
                yield Button("Export Code", id="export-code", classes="panel-button")

    def on_mount(self) -> None:
        """Set up references to the data grid."""
        try:
            # Find the data grid to interact with
            self.data_grid = self.app.query_one("#data-grid", ExcelDataGrid)
        except Exception as e:
            self.log(f"Could not find data grid: {e}")

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

    def on_button_pressed(self, event: Button.Pressed) -> None:
        """Handle button presses in the script panel."""
        if event.button.id == "apply-type-change":
            self._apply_type_change()
        elif event.button.id == "clear-code":
            self._clear_code()
        elif event.button.id == "export-code":
            self._export_code()

    def _apply_type_change(self) -> None:
        """Apply the selected type change to the current column."""
        if self.current_column is None or self.data_grid is None:
            return
        
        try:
            type_selector = self.query_one("#type-selector", Select)
            selected_type = type_selector.value
            
            if selected_type == "extract_numbers":
                # Use the numeric extraction feature
                self.data_grid._apply_column_numeric_extraction(self.current_column_name)
            else:
                # Use standard type conversion
                self.data_grid._apply_column_type_conversion(self.current_column_name, selected_type)
            
            # Update the column info after conversion
            if hasattr(self.data_grid, 'data') and self.data_grid.data is not None:
                new_type = self.data_grid._get_friendly_type_name(
                    self.data_grid.data.dtypes[self.current_column]
                )
                self.update_column_selection(self.current_column, self.current_column_name, new_type)
            
        except Exception as e:
            self.log(f"Error applying type change: {e}")

    def _clear_code(self) -> None:
        """Clear the generated code display."""
        try:
            code_content = self.query_one("#code-content", Static)
            code_content.update("# No transformations yet\nimport polars as pl\n\n# Load your data and start transforming!")
        except Exception as e:
            self.log(f"Error clearing code: {e}")

    def _export_code(self) -> None:
        """Export the generated code (placeholder for now)."""
        self.log("Export code functionality would be implemented here")


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
                yield Static("S\nc\nr\ni\np\nt", classes="tab-label")

            # Drawer panel (right side) - initially hidden
            with Vertical(id="drawer", classes="drawer hidden"):
                yield Button("×", id="close-drawer", classes="close-button")
                yield ScriptPanel(id="script-panel")

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
