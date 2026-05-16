"""Notebook integration — use Sweet interactively in Jupyter/Marimo notebooks.

Provides:
- IPython magic extension: `%load_ext sweet` then `%sweet df`
- SweetWidget class for programmatic use: `SweetWidget(df)`
- Rich HTML rendering of DataFrames with Sweet's inspection capabilities
"""

from __future__ import annotations

from typing import Any

import polars as pl


class SweetWidget:
    """Interactive Sweet widget for notebook environments.

    Displays a Polars DataFrame with rich HTML formatting including
    schema info, shape, profiling summary, and the data itself.

    Usage:
        from sweet import SweetWidget
        SweetWidget(df)
        SweetWidget(df, title="Sales Data", max_rows=20)
    """

    def __init__(
        self,
        data: pl.DataFrame | Any,
        *,
        title: str | None = None,
        max_rows: int = 10,
        show_profile: bool = True,
        show_schema: bool = True,
    ):
        """Create a Sweet widget for notebook display.

        Args:
            data: Polars DataFrame (or pandas DataFrame, auto-converted).
            title: Optional title displayed above the table.
            max_rows: Maximum rows to display (default 10).
            show_profile: Show column profiling info (null counts, types).
            show_schema: Show schema summary above the table.
        """
        self.df = _ensure_polars(data)
        self.title = title
        self.max_rows = max_rows
        self.show_profile = show_profile
        self.show_schema = show_schema

    def _repr_html_(self) -> str:
        """Render as HTML for Jupyter notebook display."""
        return self._build_html()

    def _build_html(self) -> str:
        """Build the full HTML representation."""
        parts: list[str] = []
        parts.append('<div class="sweet-widget" style="font-family: system-ui, sans-serif;">')

        # Title
        if self.title:
            parts.append(
                f'<h3 style="margin: 0 0 8px 0; color: #333;">{_escape(self.title)}</h3>'
            )

        # Shape badge
        parts.append(
            f'<div style="margin-bottom: 8px;">'
            f'<span style="background: #e8f4fd; color: #1a73e8; padding: 2px 8px; '
            f'border-radius: 4px; font-size: 12px; font-weight: 500;">'
            f'{self.df.height:,} rows × {self.df.width} cols</span>'
            f'</div>'
        )

        # Schema summary
        if self.show_schema:
            parts.append(self._build_schema_html())

        # Profile
        if self.show_profile:
            parts.append(self._build_profile_html())

        # Data table
        parts.append(self._build_table_html())

        parts.append("</div>")
        return "\n".join(parts)

    def _build_schema_html(self) -> str:
        """Build schema summary."""
        type_counts: dict[str, int] = {}
        for dtype in self.df.dtypes:
            type_name = str(dtype)
            base = type_name.split("(")[0] if "(" in type_name else type_name
            type_counts[base] = type_counts.get(base, 0) + 1

        badges = " ".join(
            f'<span style="background: #f0f0f0; padding: 1px 6px; border-radius: 3px; '
            f'font-size: 11px; margin-right: 4px;">{t}: {c}</span>'
            for t, c in sorted(type_counts.items())
        )
        return f'<div style="margin-bottom: 8px;">{badges}</div>'

    def _build_profile_html(self) -> str:
        """Build a quick profile of columns with nulls."""
        null_cols = []
        for col in self.df.columns:
            null_count = self.df[col].null_count()
            if null_count > 0:
                pct = (null_count / self.df.height) * 100
                null_cols.append(f"{_escape(col)}: {null_count} ({pct:.1f}%)")

        if not null_cols:
            return ""

        items = ", ".join(null_cols[:5])
        suffix = f" +{len(null_cols) - 5} more" if len(null_cols) > 5 else ""
        return (
            f'<div style="margin-bottom: 8px; font-size: 12px; color: #666;">'
            f'⚠ Nulls: {items}{suffix}</div>'
        )

    def _build_table_html(self) -> str:
        """Build the data table HTML."""
        display_df = self.df.head(self.max_rows)
        truncated = self.df.height > self.max_rows

        # Use Polars' built-in HTML repr and wrap it
        html = display_df._repr_html_()

        if truncated:
            remaining = self.df.height - self.max_rows
            html += (
                f'<div style="font-size: 12px; color: #888; margin-top: 4px;">'
                f'... {remaining:,} more rows</div>'
            )

        return html

    def inspect(self) -> dict[str, Any]:
        """Return inspection metadata (same format as Workspace.inspect())."""
        from .core.workspace import Workspace

        ws = Workspace()
        ws.load_df(self.df, name=self.title or "widget_data")
        return ws.inspect()

    def profile(self) -> str:
        """Return a profiling/describe DataFrame for the data."""
        from .core.workspace import Workspace

        ws = Workspace()
        ws.load_df(self.df, name=self.title or "widget_data")
        return ws.describe()

    def to_workspace(self, name: str | None = None) -> Any:
        """Convert this widget to a full Workspace for deeper operations.

        Returns:
            A Workspace instance with this data loaded.
        """
        from .core.workspace import Workspace

        ws = Workspace()
        ws.load_df(self.df, name=name or self.title or "data")
        return ws

    def to_great_table(self, **kwargs) -> Any:
        """Export this widget's data as a Great Tables object.

        Args:
            **kwargs: Passed to gt_export.to_great_table().

        Returns:
            A great_tables GT object.
        """
        from .core.gt_export import to_great_table

        return to_great_table(self.df, **kwargs)


# =============================================================================
# IPython Magic Extension
# =============================================================================


def load_ipython_extension(ipython):
    """Register the %sweet magic when `%load_ext sweet` is run.

    This is the standard IPython extension entry point.
    """
    ipython.register_magics(SweetMagics)


def unload_ipython_extension(ipython):
    """Clean up when the extension is unloaded."""
    pass


try:
    from IPython.core.magic import Magics, line_magic, magics_class

    @magics_class
    class SweetMagics(Magics):
        """IPython magics for Sweet.

        Usage:
            %load_ext sweet
            %sweet df              # Display a DataFrame as a Sweet widget
            %sweet df --title "My Data"
            %sweet df --rows 20
        """

        @line_magic
        def sweet(self, line: str):
            """Display a DataFrame as a Sweet widget.

            Usage:
                %sweet <variable_name> [--title "Title"] [--rows N] [--no-profile]
            """
            args = _parse_magic_args(line)
            var_name = args.get("var")

            if not var_name:
                from IPython.display import HTML, display

                display(HTML(
                    '<div style="color: #666; font-size: 13px;">'
                    "Usage: %sweet &lt;dataframe_variable&gt; [--title 'Title'] [--rows N]"
                    "</div>"
                ))
                return

            # Get the variable from the user's namespace
            user_ns = self.shell.user_ns
            if var_name not in user_ns:
                from IPython.display import HTML, display

                display(HTML(
                    f'<div style="color: #c00;">Variable "{_escape(var_name)}" not found.</div>'
                ))
                return

            data = user_ns[var_name]
            widget = SweetWidget(
                data,
                title=args.get("title", var_name),
                max_rows=args.get("rows", 10),
                show_profile=args.get("profile", True),
            )

            from IPython.display import HTML, display

            display(HTML(widget._repr_html_()))

except ImportError:
    # IPython not available — magics won't be registered but module still works
    pass


# =============================================================================
# Helpers
# =============================================================================


def _ensure_polars(data: Any) -> pl.DataFrame:
    """Convert input to a Polars DataFrame if needed."""
    if isinstance(data, pl.DataFrame):
        return data

    # Try pandas conversion
    try:
        import pandas as pd

        if isinstance(data, pd.DataFrame):
            return pl.from_pandas(data)
    except ImportError:
        pass

    # Try dict/list
    if isinstance(data, (dict, list)):
        return pl.DataFrame(data)

    raise TypeError(
        f"Cannot create SweetWidget from {type(data).__name__}. "
        "Pass a Polars DataFrame, pandas DataFrame, dict, or list."
    )


def _escape(text: str) -> str:
    """HTML-escape a string."""
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def _parse_magic_args(line: str) -> dict[str, Any]:
    """Parse %sweet magic arguments."""
    parts = line.strip().split()
    result: dict[str, Any] = {}

    if not parts:
        return result

    # First non-flag argument is the variable name
    i = 0
    if not parts[0].startswith("--"):
        result["var"] = parts[0]
        i = 1

    while i < len(parts):
        if parts[i] == "--title" and i + 1 < len(parts):
            # Collect title (may be quoted)
            i += 1
            title_parts = []
            if parts[i].startswith(("'", '"')):
                quote = parts[i][0]
                title_parts.append(parts[i][1:])
                while i + 1 < len(parts) and not parts[i].endswith(quote):
                    i += 1
                    title_parts.append(parts[i])
                if title_parts[-1].endswith(quote):
                    title_parts[-1] = title_parts[-1][:-1]
            else:
                title_parts.append(parts[i])
            result["title"] = " ".join(title_parts)
        elif parts[i] == "--rows" and i + 1 < len(parts):
            i += 1
            try:
                result["rows"] = int(parts[i])
            except ValueError:
                result["rows"] = 10
        elif parts[i] == "--no-profile":
            result["profile"] = False
        i += 1

    return result
