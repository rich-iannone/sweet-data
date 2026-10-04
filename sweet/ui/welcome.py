"""Welcome overlay shown when no data is loaded."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.widget import Widget
from textual.widgets import Button, Static


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


# Imported last: these modules refer to each other at runtime only.
from .grid import ExcelDataGrid  # noqa: E402
from .modals import DatabaseConnectionModal  # noqa: E402
