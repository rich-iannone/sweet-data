"""Great Tables integration — produce publication-quality tables from Sweet data.

Provides a bridge between Sweet workspaces and great_tables, enabling
export to styled HTML/LaTeX tables with formatting, grouping, and styling.
"""

from __future__ import annotations

from typing import Any

import polars as pl


def to_great_table(
    df: pl.DataFrame,
    *,
    title: str | None = None,
    subtitle: str | None = None,
    rowname_col: str | None = None,
    groupname_col: str | None = None,
    fmt_number: list[str] | None = None,
    fmt_currency: list[str] | None = None,
    fmt_percent: list[str] | None = None,
    fmt_integer: list[str] | None = None,
    locale: str | None = None,
    source_note: str | None = None,
    striping: bool = False,
    stylize: int | None = None,
) -> Any:
    """Create a great_tables GT object from a Polars DataFrame.

    Args:
        df: Polars DataFrame to render as a table.
        title: Table title (header).
        subtitle: Table subtitle.
        rowname_col: Column to use as row names (stub).
        groupname_col: Column to use for row grouping.
        fmt_number: Columns to format as numbers (with separators).
        fmt_currency: Columns to format as currency.
        fmt_percent: Columns to format as percentages.
        fmt_integer: Columns to format as integers.
        locale: Locale for formatting (e.g., 'en', 'de').
        source_note: Source note displayed at table footer.
        striping: Enable row striping.
        stylize: Apply a built-in style preset (1-6).

    Returns:
        A great_tables GT object (can be saved, shown, or further customized).

    Raises:
        ImportError: If great_tables is not installed.
        ValueError: If referenced columns don't exist in the DataFrame.
    """
    try:
        from great_tables import GT
    except ImportError as e:
        raise ImportError(
            "great_tables is required for table export. "
            "Install with: pip install great-tables"
        ) from e

    # Validate referenced columns exist
    all_cols = set(df.columns)
    for col_list, label in [
        (fmt_number, "fmt_number"),
        (fmt_currency, "fmt_currency"),
        (fmt_percent, "fmt_percent"),
        (fmt_integer, "fmt_integer"),
    ]:
        if col_list:
            missing = set(col_list) - all_cols
            if missing:
                raise ValueError(
                    f"{label} references non-existent columns: {sorted(missing)}"
                )

    if rowname_col and rowname_col not in all_cols:
        raise ValueError(f"rowname_col '{rowname_col}' not found in DataFrame")
    if groupname_col and groupname_col not in all_cols:
        raise ValueError(f"groupname_col '{groupname_col}' not found in DataFrame")

    # Build the GT object
    gt = GT(
        df,
        rowname_col=rowname_col,
        groupname_col=groupname_col,
        locale=locale,
    )

    # Header
    if title or subtitle:
        gt = gt.tab_header(title=title, subtitle=subtitle)

    # Formatting
    if fmt_number:
        gt = gt.fmt_number(columns=fmt_number)
    if fmt_currency:
        gt = gt.fmt_currency(columns=fmt_currency)
    if fmt_percent:
        gt = gt.fmt_percent(columns=fmt_percent)
    if fmt_integer:
        gt = gt.fmt_integer(columns=fmt_integer)

    # Source note
    if source_note:
        gt = gt.tab_source_note(source_note=source_note)

    # Styling options
    if striping:
        gt = gt.opt_row_striping()
    if stylize is not None:
        gt = gt.opt_stylize(style=stylize)

    return gt


def save_great_table(
    df: pl.DataFrame,
    dest: str,
    *,
    title: str | None = None,
    subtitle: str | None = None,
    rowname_col: str | None = None,
    groupname_col: str | None = None,
    fmt_number: list[str] | None = None,
    fmt_currency: list[str] | None = None,
    fmt_percent: list[str] | None = None,
    fmt_integer: list[str] | None = None,
    locale: str | None = None,
    source_note: str | None = None,
    striping: bool = False,
    stylize: int | None = None,
) -> str:
    """Create and save a great_tables table to a file.

    Supports HTML output (.html extension) or raw HTML string for
    other extensions.

    Args:
        df: Polars DataFrame to render.
        dest: Output file path (.html).
        All other args: Same as to_great_table().

    Returns:
        The output file path.
    """
    gt = to_great_table(
        df,
        title=title,
        subtitle=subtitle,
        rowname_col=rowname_col,
        groupname_col=groupname_col,
        fmt_number=fmt_number,
        fmt_currency=fmt_currency,
        fmt_percent=fmt_percent,
        fmt_integer=fmt_integer,
        locale=locale,
        source_note=source_note,
        striping=striping,
        stylize=stylize,
    )

    from pathlib import Path

    path = Path(dest)
    path.parent.mkdir(parents=True, exist_ok=True)

    # Use as_raw_html() to avoid selenium dependency
    html = gt.as_raw_html()
    path.write_text(html, encoding="utf-8")

    return str(path)
