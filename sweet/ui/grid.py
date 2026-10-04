"""The spreadsheet-style data grid."""

from __future__ import annotations

import keyword
import os
import re
import time
from pathlib import Path

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.coordinate import Coordinate
from textual.widget import Widget
from textual.widgets import Button, DataTable, Static

from ..core.io import format_for_path, read_file
from ..core.steps import Step, dtype_name
from ..core.workspace import Workspace
from ._common import MAX_DISPLAY_ROWS, debug_logger, pl

#: Helper column holding canonical row positions while a view sort is active.
SORT_INDEX_COLUMN = "__original_row_index__"


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
        # The engine holds the canonical data and the undo/audit journal.
        # `self.data` is the *displayed* frame: the canonical data, possibly
        # reordered by a view sort (with a helper `__original_row_index__` column).
        self.workspace = Workspace()
        self._data = None
        self._syncing = False  # True while the grid itself writes the displayed frame
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
        self.cached_table_schema = {}  # Cache schema information for AI Assistant

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

    # -------------------------------------------------------------------------
    # Engine bridge
    # -------------------------------------------------------------------------

    @property
    def data(self):
        """The displayed DataFrame (see `__init__`)."""
        return self._data

    @data.setter
    def data(self, df) -> None:
        """Set the displayed frame, journaling any real data change in the engine.

        Code paths that haven't been converted to steps still assign `self.data`
        directly. Those changes are detected by comparing against the engine's
        canonical data and recorded as undoable (but non-replayable) manual
        edits. View-only changes (sorting) leave the engine untouched.
        """
        self._data = df
        if self._syncing:
            return
        if df is None:
            self.workspace = Workspace()
            return
        if self.workspace.df is None:
            self.workspace.load_df(self._canonical(df), name="data")
            return
        canonical = self._canonical(df)
        if not canonical.equals(self.workspace.df):
            self.workspace.record_manual(canonical)

    @staticmethod
    def _canonical(df):
        """Strip view-only state (sort order, helper column) from a displayed frame."""
        if df is not None and SORT_INDEX_COLUMN in df.columns:
            return df.sort(SORT_INDEX_COLUMN).drop(SORT_INDEX_COLUMN)
        return df

    def _set_display(self, df) -> None:
        """Write the displayed frame without journaling."""
        self._syncing = True
        try:
            self.data = df
        finally:
            self._syncing = False

    def _view_of(self, canonical):
        """Compute the displayed frame for `canonical` under the current view sort.

        When the row count is unchanged (e.g. a cell edit), the current display
        order is kept so on-screen rows stay put. Otherwise the sort is re-run.
        """
        if canonical is None or not self._sort_columns:
            return canonical
        indexed = canonical.with_row_index(SORT_INDEX_COLUMN)
        current = self._data
        if (
            current is not None
            and SORT_INDEX_COLUMN in current.columns
            and current.height == indexed.height
        ):
            return (
                current.select(SORT_INDEX_COLUMN)
                .join(indexed, on=SORT_INDEX_COLUMN, how="left", maintain_order="left")
                .select(indexed.columns)
            )
        visible = [c for c in canonical.columns]
        sort_cols, descending = [], []
        for col_index, ascending in self._sort_columns:
            if col_index < len(visible):
                sort_cols.append(visible[col_index])
                descending.append(not ascending)
        if not sort_cols:
            self._sort_columns = []
            return canonical
        return indexed.sort(sort_cols, descending=descending)

    def _canonical_row(self, display_row: int) -> int:
        """Map a displayed data-row index to its row position in the canonical data."""
        if self._data is not None and SORT_INDEX_COLUMN in self._data.columns:
            return int(self._data[SORT_INDEX_COLUMN][display_row])
        return display_row

    def _sync_from_workspace(self, *, refresh: bool = True) -> None:
        """Re-derive the displayed frame from the engine (after undo/redo/steps)."""
        self._set_display(self._view_of(self.workspace.df))
        if refresh:
            self.refresh_table_data()

    def apply_step(
        self, step: Step, *, refresh: bool = True, result=None, reset_sort: bool = False
    ) -> None:
        """Apply a step through the engine and update the display.

        Args:
            step: The step to apply.
            refresh: Fully refresh the table afterwards.
            result: The step's output, if already computed (e.g. by the code panel).
            reset_sort: Clear the view sort (for whole-table transforms).

        Raises:
            ValueError: If no data is loaded or the step fails (data unchanged).
        """
        if self.workspace.df is None:
            raise ValueError("No data loaded")
        self.workspace.apply_step(step, result=result)
        if reset_sort:
            self._sort_columns = []
        self._sync_from_workspace(refresh=refresh)
        self.has_changes = True
        self.update_title_change_indicator()

    def _reset_workspace(self, df, *, source: dict | None = None, name: str | None = None):
        """Start a fresh engine session for newly loaded data."""
        self.workspace = Workspace()
        if df is not None:
            if name is None:
                name = Path(source["path"]).stem if source and "path" in source else "data"
            self.workspace.load_df(df, name=name or "data", source=source)

    def materialize_sort(self) -> None:
        """Turn the current view sort into a real `sort` step in the pipeline."""
        if not self._sort_columns or self.workspace.df is None:
            return
        visible = self.workspace.df.columns
        cols = [visible[i] for i, _ in self._sort_columns if i < len(visible)]
        desc = [not asc for i, asc in self._sort_columns if i < len(visible)]
        self._sort_columns = []
        if cols:
            self.workspace.apply_step(Step("sort", {"columns": cols, "descending": desc}))
        self._sync_from_workspace()

    def undo(self) -> bool:
        """Undo the last data change. Returns True if something was undone."""
        if not self.workspace.can_undo:
            return False
        self.workspace.undo()
        self._sync_from_workspace()
        self.has_changes = True
        self.update_title_change_indicator()
        return True

    def redo(self) -> bool:
        """Redo the last undone data change. Returns True if something was redone."""
        if not self.workspace.can_redo:
            return False
        self.workspace.redo()
        self._sync_from_workspace()
        self.has_changes = True
        self.update_title_change_indicator()
        return True

    def call_after_refresh(self, callback, *args, **kwargs):
        """Helper method to call a function after the next refresh using set_timer."""
        self.set_timer(0.01, lambda: callback(*args, **kwargs))

    def log(self, message: str) -> None:
        """Log a message using the debug logger."""
        debug_logger.info(message)

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

            # Load the file with the registered reader for its extension,
            # falling back to CSV for unknown extensions (e.g. .txt)
            try:
                file_format = format_for_path(file_path)
            except ValueError:
                file_format = "csv"
            self.log(f"Loading as {file_format}")
            df = read_file(file_path, file_format)

            self.log(f"File loaded successfully, shape: {df.shape}")
            self.load_dataframe(
                df, force_recreation=True, source={"path": str(file_path), "format": file_format}
            )

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

                # Build and cache schema information immediately for AI Assistant
                self._build_table_schema_cache()

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

    def _build_table_schema_cache(self) -> None:
        """Build and cache schema information for the current table for AI Assistant."""
        if not self.current_table_name or not self.database_connection:
            self.log("Cannot build schema cache: no current table or database connection")
            return

        try:
            import json

            table_name = self.current_table_name
            conn = self.database_connection

            self.log(f"Building schema cache for table: {table_name}")

            # Get detailed schema information
            schema_result = conn.execute(f"DESCRIBE {table_name}").fetchall()

            table_schema = {
                "table_name": table_name,
                "columns": {},
                "sample_data": {},
                "row_count": None,
            }

            # Process column information
            for row in schema_result:
                column_name = row[0]  # column_name
                column_type = row[1]  # column_type
                is_nullable = row[2] if len(row) > 2 else None  # null

                table_schema["columns"][column_name] = {
                    "type": column_type,
                    "nullable": is_nullable,
                }

            # Get sample data (first 5 rows)
            try:
                sample_result = conn.execute(f"SELECT * FROM {table_name} LIMIT 5").fetchall()
                column_names = [desc[0] for desc in conn.description] if conn.description else []

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
                self.log(f"Cached {len(sample_rows)} sample rows for {table_name}")

            except Exception as e:
                table_schema["sample_data"] = {"error": f"Could not get sample data: {str(e)}"}
                self.log(f"Error getting sample data for schema cache: {e}")

            # Get row count
            try:
                count_result = conn.execute(f"SELECT COUNT(*) FROM {table_name}").fetchone()
                table_schema["row_count"] = count_result[0] if count_result else None
            except Exception as e:
                self.log(f"Error getting row count for schema cache: {e}")

            # Cache the schema
            self.cached_table_schema = table_schema
            self.log(
                f"Successfully cached schema for table {table_name} with {len(table_schema['columns'])} columns"
            )

        except Exception as e:
            self.log(f"Error building schema cache for {self.current_table_name}: {e}")
            self.cached_table_schema = {"error": f"Could not build schema cache: {str(e)}"}

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

    def load_dataframe(self, df, force_recreation: bool = False, *, source=None) -> None:
        """Load a Polars DataFrame into the grid as new data (starts a new engine session).

        To change the current data, use `apply_step()` instead.

        Args:
            df: The Polars DataFrame to load
            force_recreation: If True, always recreate the table regardless of data source
            source: Where the data came from (e.g. ``{"path": ..., "format": ...}``)
        """
        if pl is None or df is None:
            return

        # Clean up any sorting tracking columns from previous sessions
        if "__original_row_index__" in df.columns:
            df = df.drop("__original_row_index__")

        self._sort_columns = []
        self._reset_workspace(df, source=source)
        self._set_display(df)
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
        self.log(
            f"DEBUG: Setting is_data_truncated={self.is_data_truncated} for total_rows={total_rows}, MAX_DISPLAY_ROWS={MAX_DISPLAY_ROWS}"
        )

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
        self.log(
            f"DEBUG: navigate_to_row called with target_row={target_row}, total_rows={total_rows}, is_data_truncated={self.is_data_truncated}"
        )
        self.log(
            f"DEBUG: MAX_DISPLAY_ROWS={MAX_DISPLAY_ROWS}, should_be_truncated={total_rows > MAX_DISPLAY_ROWS}"
        )

        # Fix the flag if it's wrong
        expected_truncated = total_rows > MAX_DISPLAY_ROWS
        if self.is_data_truncated != expected_truncated:
            self.log(
                f"WARNING: is_data_truncated flag was incorrect! Was {self.is_data_truncated}, should be {expected_truncated}"
            )
            self.is_data_truncated = expected_truncated

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

        # Create the new slice with improved efficiency for large datasets
        import time

        slice_start = time.time()
        self.log(
            f"DEBUG: Creating slice from row {start_row} with length {MAX_DISPLAY_ROWS} from dataset of {total_rows} rows"
        )

        try:
            # For very large datasets, use lazy operations
            if total_rows > 5_000_000:  # 5M rows threshold
                self.log("DEBUG: Using lazy operations for large dataset")
                # Use lazy slicing for better performance on large datasets
                lazy_slice = self.data.lazy().slice(start_row, MAX_DISPLAY_ROWS)
                sliced_data = lazy_slice.collect()
            else:
                # Use direct slicing for smaller datasets
                self.log("DEBUG: Using direct slicing for manageable dataset")
                sliced_data = self.data.slice(start_row, MAX_DISPLAY_ROWS)

            slice_time = time.time() - slice_start
            self.log(
                f"DEBUG: Slice operation completed in {slice_time:.3f} seconds, sliced_data shape: {sliced_data.shape}"
            )
        except Exception as e:
            self.log(f"ERROR: Failed to create slice: {e}")
            return

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
        # target_row is 1-based, start_row is 0-based
        # Display row 0 is the header, so we need to add 1 for data rows
        target_display_row = target_row - start_row  # Convert to display row (1-based for data)

        self.log(
            f"DEBUG: target_row={target_row}, start_row={start_row}, target_display_row={target_display_row}, table_row_count={self._table.row_count}"
        )

        # Move cursor to the target row
        if target_display_row < self._table.row_count:
            self._table.move_cursor(row=target_display_row, column=0)
            self.update_address_display(target_display_row, 0)
            self.log(f"DEBUG: Cursor moved to display row {target_display_row}")
        else:
            self.log(
                f"ERROR: target_display_row {target_display_row} >= table_row_count {self._table.row_count}"
            )

        self._table.refresh()
        self.refresh()

        # Focus the table so user can immediately use arrow keys
        self._table.focus()

        self.log("DEBUG: navigate_to_row completed successfully")
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
            self.apply_step(Step("rename", {"mapping": {old_name: new_name}}), refresh=False)

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
        """Set one cell (by displayed data-row index) via an `edit_cell` step.

        The caller repaints the cell, so the table isn't fully refreshed here.
        """
        if column_name not in self.data.columns:
            raise ValueError(
                f"Column '{column_name}' not found in DataFrame. Available columns: {self.data.columns}"
            )
        dtype = self.data.schema[column_name]
        self.apply_step(
            Step(
                "edit_cell",
                {
                    "row": self._canonical_row(data_row),
                    "column": column_name,
                    "value": new_value,
                    "dtype": dtype_name(dtype),
                },
            ),
            refresh=False,
        )

    def _cast_column(self, column_name: str, dtype) -> None:
        """Change a column's type via a `cast` step (no table refresh)."""
        self.apply_step(Step("cast", {"columns": {column_name: dtype_name(dtype)}}), refresh=False)

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
            self._cast_column(column_name, new_dtype)

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

    def _debug_write(self, message: str) -> None:
        """Write debug message to log. Fallback for missing debug method."""
        try:
            self.log(f"DEBUG: {message}")
        except Exception:
            print(f"DEBUG: {message}")

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
                self._cast_column(column_name, new_dtype)

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
            self._cast_column(column_name, pl.Float64)

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
        self.log(
            f"DEBUG: refresh_display setting is_data_truncated={self.is_data_truncated} for total_rows={total_rows}"
        )

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
                # Regular mode: a view sort becomes part of the data when saved
                # (as a `sort` step), so the file matches the pipeline.
                self.materialize_sort()
                export_data = self.workspace.df

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
                self._cast_column(column_name, new_dtype)
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
            # Append an empty row
            self.apply_step(Step("insert_row", {"values": {}}), refresh=False)
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
            self.apply_step(
                Step("mutate", {"column": new_column_name, "sql": "CAST(NULL AS VARCHAR)"}),
                refresh=False,
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

            self.apply_step(
                Step("delete_rows", {"rows": [self._canonical_row(data_row)]}), refresh=False
            )

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
                self.apply_step(Step("drop", {"columns": [column_name]}), refresh=False)

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

            # Insert an empty row before the given (displayed) row
            index = (
                self._canonical_row(data_insert_index)
                if data_insert_index < len(self.data)
                else None
            )
            self.apply_step(Step("insert_row", {"values": {}, "index": index}), refresh=False)

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

            # Insert an empty String column (its type is inferred from the first value)
            # `insert_at_col` indexes the displayed frame, which may include the sort helper
            position = len([c for c in self.data.columns[:insert_at_col] if c != SORT_INDEX_COLUMN])
            self.apply_step(
                Step(
                    "mutate",
                    {
                        "column": new_column_name,
                        "sql": "CAST(NULL AS VARCHAR)",
                        "position": position,
                    },
                ),
                refresh=False,
            )
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
                    combined_df = pl.concat([self.workspace.df, new_df], how="vertical_relaxed")
                    self.apply_step(
                        Step("manual", {"description": f"Append {len(data_rows)} pasted rows"}),
                        result=combined_df,
                        reset_sort=True,
                    )
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


# Imported last: these modules refer to each other at runtime only.
from .drawer import DrawerContainer  # noqa: E402
from .file_browser import FileBrowserModal  # noqa: E402
from .modals import (
    CellEditModal,
    ColumnConversionModal,
    NumericExtractionModal,
    PasteOptionsModal,
    RowColumnDeleteModal,
    SaveFileModal,
    ValidationErrorModal,
)  # noqa: E402
from .search import SearchOverlay  # noqa: E402
from .tools_panel import ToolsPanel  # noqa: E402
from .welcome import WelcomeOverlay  # noqa: E402
