"""Custom Textual widgets for Sweet."""
from __future__ import annotations

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


class FileInputModal(ModalScreen[str]):
    """Modal screen for file path input."""

    def compose(self) -> ComposeResult:
        """Compose the modal content."""
        with Vertical(id="file-modal"):
            yield Label("Enter file path:")
            yield Input(placeholder="e.g., /path/to/data.csv", id="file-input")
            with Horizontal():
                yield Button("Load", id="load-file", variant="primary")
                yield Button("Cancel", id="cancel-file")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        """Handle button presses in the modal."""
        if event.button.id == "load-file":
            file_input = self.query_one("#file-input", Input)
            file_path = file_input.value.strip()
            if file_path:
                self.dismiss(file_path)
            else:
                # Could show an error message here
                pass
        elif event.button.id == "cancel-file":
            self.dismiss(None)


class ExcelDataGrid(Widget):
    """Excel-like data grid widget with editable cells and Excel addressing."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._table = DataTable()
        self.data = None
        self._current_address = "A1"

    def compose(self) -> ComposeResult:
        """Compose the data grid widget."""
        with Vertical():
            # Add load controls at the top
            with Horizontal(id="load-controls", classes="load-controls"):
                yield Button("Load Dataset", id="load-dataset", classes="load-button")
                yield Button("Load Sample Data", id="load-sample", classes="load-button")
            with Container(id="table-container"):
                yield self._table
            # Create status bar with simple content
            yield Static("No data loaded", id="status-bar", classes="status-bar")

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
        """Show empty state with instructions."""
        self._table.clear(columns=True)
        self._table.add_column("Welcome to Sweet!", key="welcome")
        self._table.add_row("Click 'Load Dataset' to load a CSV file", label="1")
        self._table.add_row("Or click 'Load Sample Data' for demo data", label="2")
        self._table.add_row("Use arrow keys to navigate when data is loaded", label="3")
        
        # Update status bar
        try:
            status_bar = self.query_one("#status-bar", Static)
            status_bar.update("No data loaded - click a button above to get started")
        except Exception:
            pass

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
        
        # Push the modal screen
        modal = FileInputModal()
        self.app.push_screen(modal, handle_file_input)

    def action_load_sample_data(self) -> None:
        """Load sample data for demonstration."""
        self.load_sample_data()
        self.log("Load sample data button clicked")

    def load_file(self, file_path: str) -> None:
        """Load data from a specific file path."""
        try:
            if pl is None:
                self._table.clear(columns=True)
                self._table.add_column("Error")
                self._table.add_row("Polars not available")
                return

            # Load the specified CSV file
            df = pl.read_csv(file_path)
            self.load_dataframe(df)
            self.log(f"Loaded data from: {file_path}")
            
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

    def update_address_display(self, row: int, col: int) -> None:
        """Update the status bar with current cell address."""
        col_name = self.get_excel_column_name(col)
        self._current_address = f"{col_name}{row + 1}"
        
        # Update status bar at bottom with robust approach
        try:
            status_bar = self.query_one("#status-bar", Static)
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

            # Try to load the sample CSV file
            sample_file = Path(__file__).parent.parent.parent / "sample_data.csv"
            if sample_file.exists():
                df = pl.read_csv(sample_file)
                self.load_dataframe(df)
            else:
                # Create sample data if file doesn't exist
                df = pl.DataFrame({
                    "name": ["Alice", "Bob", "Charlie", "Diana"],
                    "age": [25, 30, 35, 28],
                    "city": ["New York", "San Francisco", "Chicago", "Boston"],
                    "salary": [75000, 85000, 70000, 80000],
                })
                self.load_dataframe(df)

        except Exception as e:
            self._table.add_column("Error")
            self._table.add_row(f"Failed to load data: {str(e)}")

    def load_dataframe(self, df) -> None:
        """Load a Polars DataFrame into the grid."""
        if pl is None or df is None:
            return

        self.data = df

        # Hide load controls when data is loaded
        try:
            load_controls = self.query_one("#load-controls")
            load_controls.add_class("hidden")
        except Exception:
            pass

        # Clear existing data
        self._table.clear(columns=True)

        # Add Excel-style column headers (A, B, C, etc.)
        for i, column in enumerate(df.columns):
            excel_col = self.get_excel_column_name(i)
            # Show Excel address with original column name in parentheses
            display_name = f"{excel_col} ({column})"
            self._table.add_column(display_name, key=column)

        # Add rows with proper row numbering
        for row_idx, row in enumerate(df.iter_rows()):
            # Use row number (1-based) as the row key for display
            row_label = str(row_idx + 1)  # This should show as row number
            self._table.add_row(*[str(cell) for cell in row], label=row_label)

        # Initialize address display after loading data
        self.update_address_display(0, 0)

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

    def on_key(self, event) -> None:
        """Handle key events and update address based on cursor position."""
        # Allow the table to handle the key first
        if event.key in ["up", "down", "left", "right", "enter", "tab"]:
            cursor_coordinate = self._table.cursor_coordinate
            if cursor_coordinate:
                row, col = cursor_coordinate
                self.update_address_display(row, col)
                self.log(f"Key {event.key} pressed, cursor at: Row {row + 1}, Col {col + 1}")


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

            # Drawer tab (narrow strip on right)
            with Vertical(id="drawer-tab", classes="drawer-tab"):
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


class SweetFooter(Footer):
    """Custom footer with Sweet-specific bindings."""

    def compose(self) -> ComposeResult:
        yield Static("F1: Help | : Command Mode | Click tab to open/close Script Panel | ESC: Close | Arrow keys: Navigate")
