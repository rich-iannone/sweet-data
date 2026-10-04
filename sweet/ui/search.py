"""In-grid search overlay."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.widget import Widget
from textual.widgets import Static


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


# Imported last: these modules refer to each other at runtime only.
from .grid import ExcelDataGrid  # noqa: E402
