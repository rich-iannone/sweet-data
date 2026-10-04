"""The tools drawer (Polars/SQL code panels and AI assistant)."""

from __future__ import annotations

from pathlib import Path

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.widget import Widget
from textual.widgets import (
    Button,
    ContentSwitcher,
    Input,
    RadioSet,
    Select,
    Static,
    TextArea,
)

from ..core.steps import Step, StepError
from ._common import CHATLAS_AVAILABLE, debug_logger, pl


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

            # Create execution context with the current (canonical, unsorted) data
            execution_context = {
                "pl": pl,
                "df": self.data_grid.workspace.df.clone(),  # Work with a copy initially
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

            # Record the transformation as a step (journaled and undoable)
            self.data_grid.apply_step(
                Step("polars", {"code": code}), result=result_df, reset_sort=True
            )
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

SCHEMA INFORMATION USAGE:
You have COMPLETE access to the table schema information provided in the DATABASE CONTEXT above. This includes:
- Column names and their data types
- Sample data showing actual values
- Table structure and relationships
- Row counts and statistics

When users ask questions about the table (like "describe this table", "what columns are there?", "what's in this data?"), you should DIRECTLY use the schema information provided to give detailed, accurate answers. DO NOT say you don't have access to the schema - you do have it in the JSON context above.

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
1. **Schema Questions**: When users ask "describe this table", "what columns are there?", "what's the structure?" - analyze the schema information in the DATABASE CONTEXT and provide detailed descriptions of columns, data types, and sample values
2. **Exploratory Analysis**: When users ask about the data content or patterns, use both the schema and sample data to provide insights
3. **Query Generation**: When users request specific analysis, provide SQL queries they can execute using the schema information
4. **Be Specific**: Reference actual table and column names from the database context provided above
5. **Explain Queries**: Help users understand what the SQL queries will accomplish
6. **Database-appropriate**: Use SQL syntax compatible with DuckDB

IMPORTANT INSTRUCTIONS:
- You HAVE FULL ACCESS to the table schema in the DATABASE CONTEXT section above
- For questions like "describe the data", "what columns exist?", "show me the structure" - analyze the JSON schema provided and give detailed answers about column names, types, sample values, etc.
- For analysis requests like "find patterns", "calculate averages", "show trends", "filter rows" - provide SQL queries
- When you provide SQL code, it must:
  * ALWAYS include the table name in the FROM clause: "SELECT * FROM {current_table} WHERE..."
  * Use proper SQL syntax (SELECT, FROM, WHERE, etc.)
  * Reference actual table and column names from the schema context
  * Be surrounded by ```sql and ```
  * Be ready to execute as-is

Example schema description response (using provided context):
"The {current_table} table contains [X] columns: [list column names and types from schema]. Based on the sample data, this appears to be [describe purpose]. Key columns include [highlight important columns with their types]. The table has approximately [row count] records."

Example query response (WITH SQL):
I'll help you analyze [specific request].

```sql
SELECT column_name, COUNT(*)
FROM {current_table}
WHERE condition
GROUP BY column_name
ORDER BY COUNT(*) DESC
```

This query analyzes [explanation of what it does].

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

            # Check if we have cached schema information
            if (
                hasattr(self.data_grid, "cached_table_schema")
                and self.data_grid.cached_table_schema
            ):
                cached_schema = self.data_grid.cached_table_schema

                # Verify the cached schema is for the current table
                if cached_schema.get("table_name") == current_table:
                    self.log(f"Using cached schema for table: {current_table}")

                    database_info = {
                        "database_path": getattr(self.data_grid, "database_path", "Unknown"),
                        "current_table": current_table,
                        "table_schema": cached_schema,
                    }

                    # Convert to formatted JSON string
                    context_json = json.dumps(database_info, indent=2, ensure_ascii=False)
                    return f"Database Schema Information (JSON):\n```json\n{context_json}\n```"

            # Fallback to original method if no cache or cache is stale
            self.log(
                f"No cached schema available, building fresh schema for table: {current_table}"
            )

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
                    self.log(f"DEBUG: current_table = {current_table}")
                    self.log(
                        f"DEBUG: has database_connection = {hasattr(self.data_grid, 'database_connection')}"
                    )
                    if hasattr(self.data_grid, "database_connection"):
                        self.log(
                            f"DEBUG: database_connection is None = {self.data_grid.database_connection is None}"
                        )
                    database_info["table_schema"] = {
                        "error": "No current table selected"
                    }  # Convert to formatted JSON string
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

            # Run model-generated code as a restricted Polars step (no imports,
            # no private attributes) against the canonical data
            debug_logger.info(f"Executing generated code: {code}")
            step = Step("polars", {"code": code}, author="agent:assistant")
            try:
                result_df = step.apply(self.data_grid.workspace.df)
            except StepError as e:
                self._show_llm_response(f"Error applying transformation: {e}", is_error=True)
                return

            # Validate that we got a Polars DataFrame
            if not hasattr(result_df, "shape") or not hasattr(result_df, "columns"):
                self._show_llm_response("Result is not a valid Polars DataFrame.", is_error=True)
                return

            # Log the result dataframe info
            result_shape = result_df.shape
            result_columns = list(result_df.columns)
            debug_logger.info(f"Result dataframe: {result_shape} - columns: {result_columns}")

            # Record the transformation as a step (journaled and undoable)
            self.data_grid.apply_step(step, result=result_df, reset_sort=True)

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


# Imported last: these modules refer to each other at runtime only.
from .grid import ExcelDataGrid  # noqa: E402
from .search import SearchOverlay  # noqa: E402
