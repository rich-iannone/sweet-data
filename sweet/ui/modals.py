"""Modal dialogs (editing, saving, confirmations, conversions, connections)."""

from __future__ import annotations

import re

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import (
    Button,
    Checkbox,
    Input,
    Label,
    Select,
    Static,
)


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
                yield Static(":undo or :u --- undo the last transform", classes="command-item")
                yield Static(
                    ":redo --------- redo the last undone transform", classes="command-item"
                )
                yield Static(
                    ":pipeline [path] save the session's steps as a .sweet.yaml pipeline",
                    classes="command-item",
                )
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
