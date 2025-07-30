"""Custom Textual widgets for Sweet."""
from __future__ import annotations

import keyword
from pathlib import Path
from typing import TYPE_CHECKING

from textual.app import ComposeResult
from textual.containers import Container, Horizontal, Vertical
from textual.reactive import reactive
from textual.screen import ModalScreen
from textual.widget import Widget
from textual.widgets import Button, DataTable, Footer, Input, Label, Static

if TYPE_CHECKING:
    import polars as pl

try:
    import polars as pl
except ImportError:
    pl = None


class WelcomeOverlay(Widget):
    """Welcome screen overlay similar to Vim's start screen."""

    def compose(self) -> ComposeResult:
        """Compose the welcome overlay."""
        with Vertical(id="welcome-overlay", classes="welcome-overlay"):
            yield Static("", classes="spacer")  # Top spacer
            yield Static("Sweet", classes="welcome-title")
            yield Static("Interactive data engineering CLI", classes="welcome-subtitle")
            yield Static("", classes="spacer-small")  # Small spacer
            with Horizontal(classes="welcome-buttons"):
                yield Button("Load Dataset", id="welcome-load-dataset", classes="welcome-button")
                yield Button("Load Sample Data", id="welcome-load-sample", classes="welcome-button")
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
            else:
                self.log(f"Data grid not found, parent.parent is: {type(data_grid)}")
        except Exception as e:
            self.log(f"Error accessing data grid: {e}")
        
        # Consume the event to prevent further propagation
        event.stop()


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


class ExcelDataGrid(Widget):
    """Excel-like data grid widget with editable cells and Excel addressing."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._table = DataTable()
        self.data = None
        self.original_data = None  # Store original data for change tracking
        self.has_changes = False  # Track if data has been modified
        self._current_address = "A1"
        self.editing_cell = False
        self._edit_input = None
        self.original_data = None  # Store original data for change tracking
        self.has_changes = False  # Track if data has been modified
        self._editing_cell = None  # Currently editing cell coordinate
        self.is_sample_data = False  # Track if we're working with internal sample data
        self.data_source_name = None  # Name of the data source (for sample data)

    def compose(self) -> ComposeResult:
        """Compose the data grid widget."""
        with Vertical():
            # Hide load controls - they're now in the welcome overlay
            with Horizontal(id="load-controls", classes="load-controls hidden"):
                yield Button("Load Dataset", id="load-dataset", classes="load-button")
                yield Button("Load Sample Data", id="load-sample", classes="load-button")
            with Container(id="table-container"):
                yield self._table
            # Create status bar with simple content
            yield Static("No data loaded", id="status-bar", classes="status-bar")
            # Add welcome overlay
            yield WelcomeOverlay(id="welcome-overlay")

    def on_mount(self) -> None:
        """Initialize the data grid on mount."""
        self._table.cursor_type = "cell"  # Enable cell-level navigation
        self._table.zebra_stripes = True
        self._table.show_header = True
        self._table.show_row_labels = True  # This shows row numbers

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
        
        # Reset data tracking flags
        self.is_sample_data = False
        self.data_source_name = None
        self.has_changes = False
        
        # Clear the filename from title
        self.app.set_current_filename(None)
        
        # Show welcome overlay
        try:
            welcome_overlay = self.query_one("#welcome-overlay", WelcomeOverlay)
            welcome_overlay.remove_class("hidden")
            welcome_overlay.display = True  # Also set display to True
        except Exception as e:
            self.log(f"Error showing welcome overlay: {e}")
        
        # Update status bar
        try:
            status_bar = self.query_one("#status-bar", Static)
            status_bar.update("Welcome to Sweet - Select an option to get started")
        except Exception as e:
            self.log(f"Error updating status bar: {e}")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        """Handle button presses in the data grid."""
        if event.button.id == "load-dataset":
            self.action_load_dataset()
        elif event.button.id == "load-sample":
            self.action_load_sample_data()

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
            self.log(f"Loaded data from: {file_path}")
            
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
            new_address = f"{col_name}{row + 1}"
            if new_address != self._current_address:
                self.update_address_display(row, col)

    def update_address_display(self, row: int, col: int, custom_message: str = None) -> None:
        """Update the status bar with current cell address or custom message."""
        col_name = self.get_excel_column_name(col)
        self._current_address = f"{col_name}{row + 1}"
        
        # Update status bar at bottom with robust approach
        try:
            status_bar = self.query_one("#status-bar", Static)
            if custom_message:
                new_text = f"Cell: {self._current_address} - {custom_message}"
            else:
                new_text = f"Cell: {self._current_address}"
            # Try multiple approaches to ensure text is set
            status_bar.update(new_text)
            status_bar.renderable = new_text
            status_bar.refresh()
            self.log(f"Status bar updated to: {new_text}")
        except Exception as e:
            self.log(f"Error updating status bar: {e}")
            # Try fallback approach
            try:
                status_widgets = self.query(".status-bar")
                for widget in status_widgets:
                    if isinstance(widget, Static):
                        widget.update(f"Cell: {self._current_address}")
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

        # Hide load controls when data is loaded
        try:
            load_controls = self.query_one("#load-controls")
            load_controls.add_class("hidden")
        except Exception:
            pass

        # Clear existing data
        self._table.clear(columns=True)

        # Add Excel-style column headers with just the letters (A, B, C, etc.)
        for i, column in enumerate(df.columns):
            excel_col = self.get_excel_column_name(i)
            self._table.add_column(excel_col, key=column)

        # Add column names as the first row (row 0) with bold formatting
        column_names = [f"[bold]{str(col)}[/bold]" for col in df.columns]
        self._table.add_row(*column_names, label="0")

        # Add data rows with proper row numbering (starting from 1)
        for row_idx, row in enumerate(df.iter_rows()):
            # Use row number (1-based) as the row label for display
            row_label = str(row_idx + 1)  # This should show as row number
            self._table.add_row(*[str(cell) for cell in row], label=row_label)

        # Initialize address display after loading data
        self.update_address_display(0, 0)
        
        # Force a complete refresh of the display
        self.refresh()
        
        # Show the drawer tab when data is loaded
        try:
            # Find the parent container and show the drawer tab
            container = self.app.query_one("#main-container", DrawerContainer)
            drawer_tab = container.query_one("#drawer-tab")
            drawer_tab.remove_class("hidden")
        except Exception:
            pass

    def on_data_table_cell_selected(self, event: DataTable.CellSelected) -> None:
        """Handle cell selection and update address."""
        row, col = event.coordinate
        self.update_address_display(row, col)
        self.log(f"Cell selected: {self._current_address} (Row {row + 1}, Col {col + 1})")

    def on_data_table_cell_highlighted(self, event: DataTable.CellHighlighted) -> None:
        """Handle cell highlighting and update address."""
        row, col = event.coordinate
        self.update_address_display(row, col)

    def on_data_table_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        """Handle row highlighting and update address."""
        # Get the current cursor position
        cursor_coordinate = self._table.cursor_coordinate
        if cursor_coordinate:
            row, col = cursor_coordinate
            self.update_address_display(row, col)

    def on_data_table_cursor_moved(self, event) -> None:
        """Handle cursor movement and update address."""
        cursor_coordinate = self._table.cursor_coordinate
        if cursor_coordinate:
            row, col = cursor_coordinate
            self.update_address_display(row, col)

    def on_click(self, event) -> None:
        """Handle click events and update address based on cursor position."""
        cursor_coordinate = self._table.cursor_coordinate
        if cursor_coordinate:
            row, col = cursor_coordinate
            self.update_address_display(row, col)
            self.log(f"Click detected, cursor at: Row {row + 1}, Col {col + 1}")

    def on_key(self, event) -> bool:
        """Handle key events and update address based on cursor position."""
        # Handle cell editing
        if event.key == "enter" and not self.editing_cell:
            cursor_coordinate = self._table.cursor_coordinate
            if cursor_coordinate:
                row, col = cursor_coordinate
                # Allow editing both header row (row 0) and data rows (row > 0)
                # Prevent default to stop event propagation
                event.prevent_default()
                event.stop()
                # Use call_after_refresh to start editing after the current event cycle
                self.call_after_refresh(self.start_cell_edit, row, col)
                return True
        
        # Allow the table to handle navigation keys
        if event.key in ["up", "down", "left", "right", "tab"]:
            cursor_coordinate = self._table.cursor_coordinate
            if cursor_coordinate:
                row, col = cursor_coordinate
                self.update_address_display(row, col)
                self.log(f"Key {event.key} pressed, cursor at: Row {row + 1}, Col {col + 1}")
        
        return False

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
                def handle_column_name_edit(new_value: str | None) -> None:
                    self.log(f"Column name edit callback: new_value = {new_value}")
                    if new_value is not None and new_value.strip():
                        # Update the address display to show we're processing
                        self.update_address_display(row, col, f"UPDATING COLUMN: {new_value}")
                        self.finish_column_name_edit(new_value.strip())
                    else:
                        self.editing_cell = False
                        self.log("Column name edit cancelled or empty")
                
                modal = CellEditModal(current_value)
                self.app.push_screen(modal, handle_column_name_edit)
                
            else:
                # Editing data cell
                data_row = row - 1  # Subtract 1 because row 0 is headers
                if data_row < len(self.data):
                    current_value = str(self.data[data_row, col])
                    
                    # Store editing state
                    self.editing_cell = True
                    self._edit_row = row
                    self._edit_col = col
                    
                    self.log(f"Starting cell edit: {self.get_excel_column_name(col)}{row} = '{current_value}'")
                    
                    # Create and show the cell edit modal for data
                    def handle_cell_edit(new_value: str | None) -> None:
                        self.log(f"Cell edit callback: new_value = {new_value}")
                        if new_value is not None:
                            # Update the address display to show we're processing
                            self.update_address_display(row, col, f"UPDATING: {new_value}")
                            self.finish_cell_edit(new_value)
                        else:
                            self.editing_cell = False
                            self.log("Cell edit cancelled")
                    
                    modal = CellEditModal(current_value)
                    self.app.push_screen(modal, handle_cell_edit)
                
        except Exception as e:
            self.log(f"Error starting cell edit: {e}")
            self.editing_cell = False

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
                self.update_address_display(self._edit_row, self._edit_col, f"ERROR: {validation_error}")
                self.editing_cell = False
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

    def finish_cell_edit(self, new_value: str) -> None:
        """Finish editing a cell and update the data."""
        if not self.editing_cell or self.data is None:
            self.log("Cannot finish edit: no editing state or no data")
            return
        
        try:
            data_row = self._edit_row - 1  # Convert from display row to data row
            column_name = self.data.columns[self._edit_col]
            
            self.log(f"Updating cell at data_row={data_row}, col={self._edit_col}, column='{column_name}' with value='{new_value}'")
            
            # Get the current column dtype for type conversion
            column_dtype = self.data.dtypes[self._edit_col]
            
            # Convert new value to appropriate type based on column dtype
            converted_value = new_value  # Default to string
            needs_column_conversion = False
            
            try:
                if column_dtype in [pl.Int64, pl.Int32, pl.Int16, pl.Int8]:
                    # Check if the value contains a decimal point
                    if '.' in new_value.strip() and new_value.strip():
                        try:
                            float_val = float(new_value)
                            # If it's not a whole number, we need to convert the column
                            if float_val != int(float_val):
                                needs_column_conversion = True
                                self.log(f"Detected decimal value '{new_value}' for integer column '{column_name}'")
                        except ValueError:
                            pass
                    
                    if not needs_column_conversion:
                        converted_value = int(new_value) if new_value.strip() else None
                    else:
                        # We'll handle this after asking the user
                        converted_value = float(new_value) if new_value.strip() else None
                elif column_dtype in [pl.Float64, pl.Float32]:
                    converted_value = float(new_value) if new_value.strip() else None
                elif column_dtype == pl.Boolean:
                    converted_value = new_value.lower() in ('true', '1', 'yes', 'y', 'on') if new_value.strip() else None
                else:
                    converted_value = new_value  # Keep as string for other types
            except ValueError as ve:
                self.log(f"Type conversion failed, keeping as string: {ve}")
                converted_value = new_value
            
            # If we need column conversion, ask the user
            if needs_column_conversion:
                self._pending_edit = {
                    'data_row': data_row,
                    'column_name': column_name,
                    'new_value': new_value,
                    'converted_value': converted_value
                }
                
                def handle_column_conversion(convert: bool | None) -> None:
                    if convert is True:
                        self._apply_column_conversion_and_update()
                    elif convert is False:
                        # Apply as integer (truncated)
                        self._apply_edit_without_conversion()
                    else:
                        # Cancel the edit
                        self.editing_cell = False
                        self.log("Column conversion cancelled")
                
                modal = ColumnConversionModal(column_name, new_value, "Integer", "Float")
                self.app.push_screen(modal, handle_column_conversion)
                return
            
            # Use a more direct approach: create a new dataframe with the updated value
            # First, convert to list of rows
            rows = []
            for i, row in enumerate(self.data.iter_rows()):
                if i == data_row:
                    # Update this row
                    updated_row = list(row)
                    updated_row[self._edit_col] = converted_value
                    rows.append(updated_row)
                else:
                    rows.append(list(row))
            
            # Create new DataFrame from the updated rows
            self.data = pl.DataFrame(rows, schema=self.data.schema)
            
            # Mark as changed and refresh display
            self.has_changes = True
            self.update_title_change_indicator()
            self.refresh_table_data()  # Use refresh instead of load_dataframe
            
            # Reset the status bar to normal
            self.update_address_display(self._edit_row, self._edit_col)
            
            self.log(f"Successfully updated cell {self.get_excel_column_name(self._edit_col)}{self._edit_row} = '{new_value}'")
            self.log(f"Updated data shape: {self.data.shape}")
            self.log(f"Cell value after update: {self.data[data_row, self._edit_col]}")
            
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

    def refresh_table_data(self) -> None:
        """Refresh the table display with current data."""
        if self.data is None:
            return
        
        # Clear and rebuild the table
        self._table.clear(columns=True)
        
        # Add columns
        for i, column in enumerate(self.data.columns):
            excel_col = self.get_excel_column_name(i)
            self._table.add_column(excel_col, key=column)
        
        # Add header row with bold formatting
        column_names = [f"[bold]{str(col)}[/bold]" for col in self.data.columns]
        self._table.add_row(*column_names, label="0")
        
        # Add data rows
        for row_idx, row in enumerate(self.data.iter_rows()):
            row_label = str(row_idx + 1)
            self._table.add_row(*[str(cell) for cell in row], label=row_label)

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


class ScriptPanel(Widget):
    """Panel for displaying generated code and controls."""

    def compose(self) -> ComposeResult:
        """Compose the script panel."""
        yield Static("Generated Polars Code:", classes="panel-header")
        yield Static(
            "# No transformations yet\nimport polars as pl\n\n# Load your data and start transforming!",
            id="code-content",
            classes="code-display",
        )
        yield Button("Clear Code", id="clear-code", classes="panel-button")
        yield Button("Export Code", id="export-code", classes="panel-button")


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
    
    def __init__(self, current_value: str) -> None:
        super().__init__()
        self.current_value = current_value
    
    def compose(self) -> ComposeResult:
        with Vertical():
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
        # Set cursor to end to select all text when user starts typing
        input_widget.cursor_position = len(self.current_value)
    
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
            yield Static(f"Convert column to {self.to_type} to preserve decimal values?", classes="options")
            yield Static("")  # Spacer
            with Horizontal(classes="modal-buttons"):
                yield Button("❌ Keep as Integer", id="keep-int", variant="error")
                yield Button("✓ Convert to Float", id="convert-float", variant="success")
                yield Button("Cancel", id="cancel-conversion", variant="default")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        """Handle button presses."""
        if event.button.id == "convert-float":
            self.dismiss(True)
        elif event.button.id == "keep-int":
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


class SweetFooter(Footer):
    """Custom footer with Sweet-specific bindings."""

    def compose(self) -> ComposeResult:
        yield Static("Press : for command mode")
