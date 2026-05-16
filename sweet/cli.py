import sys
import tempfile
from pathlib import Path

import click

from .ui.app import run_app


@click.group(invoke_without_command=True)
@click.version_option()
@click.option("--file", "-f", type=click.Path(exists=True), help="Load data file on startup")
@click.option(
    "--db", type=str, help="Connect to remote database (e.g., mysql://user:pass@host:port/db)"
)
@click.pass_context
def main(ctx, file: str | None, db: str | None):
    """Sweet - Interactive data engineering CLI utility."""
    # If a subcommand is invoked, don't run the TUI
    if ctx.invoked_subcommand is not None:
        return

    try:
        # Check if data is being piped from stdin
        if not sys.stdin.isatty() and file is None:
            # Read from stdin
            stdin_data = sys.stdin.read().strip()
            if stdin_data:
                # Check if the input looks like a single filename (no newlines, exists as file)
                if "\n" not in stdin_data and Path(stdin_data).exists():
                    click.echo(f"Starting Sweet with file: {stdin_data}")
                    # Simply set the file parameter and continue normally
                    file = stdin_data
                else:
                    # Treat as file content data
                    temp_file = tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False)
                    temp_file.write(stdin_data)
                    temp_file.flush()
                    temp_file.close()

                    click.echo("Starting Sweet with piped data...")
                    # Set the temp file as the file parameter
                    file = temp_file.name

                # Redirect stdin to /dev/tty without closing the original
                import os

                tty_fd = os.open("/dev/tty", os.O_RDONLY)
                os.dup2(tty_fd, 0)  # Replace stdin file descriptor
                os.close(tty_fd)

        if file:
            click.echo(f"Starting Sweet with file: {file}")
            run_app(startup_file=file)

            # Clean up temp file if it was created from piped data
            if file.startswith("/tmp") and file.endswith(".csv"):
                try:
                    Path(file).unlink()
                except OSError:
                    pass
        elif db:
            click.echo(f"Starting Sweet with database: {db}")
            run_app(startup_db=db)
        else:
            click.echo("Starting Sweet...")
            run_app()
    except KeyboardInterrupt:
        click.echo("\nGoodbye!")


# ---------------------------------------------------------------------------
# Subcommands: Headless operations
# ---------------------------------------------------------------------------


@main.command()
@click.argument("file", type=click.Path(exists=True))
@click.option("--transform", "-t", multiple=True, help="Polars expression(s) to apply")
@click.option("--export", "-e", "export_path", type=click.Path(), help="Export result to file")
@click.option(
    "--format", "fmt", type=click.Choice(["csv", "parquet", "json"]), help="Export format"
)
def transform(file: str, transform: tuple[str, ...], export_path: str | None, fmt: str | None):
    """Load a file, apply transforms, and optionally export (no TUI).

    Example:
        sweet transform data.csv -t "df.filter(pl.col('x') > 5)" -e out.parquet
    """
    from .core.workspace import Workspace

    ws = Workspace()
    ws.load(file)

    for expr in transform:
        ws.transform(expr)
        click.echo(f"Applied: {expr} → {ws.shape[0]} rows × {ws.shape[1]} cols")

    if export_path:
        ws.export(export_path, format=fmt)
        click.echo(f"Exported to: {export_path}")
    else:
        # Print result to stdout as CSV
        click.echo(ws.df.write_csv())


@main.command()
@click.argument("file", type=click.Path(exists=True))
@click.option("--format", "fmt", type=click.Choice(["text", "json"]), default="text")
def profile(file: str, fmt: str):
    """Profile a data file: show schema, shape, nulls, and statistics (no TUI).

    Example:
        sweet profile data.csv
        sweet profile data.parquet --format json
    """
    import json as json_mod

    from .core.workspace import Workspace

    ws = Workspace()
    ws.load(file)
    info = ws.inspect(n_rows=5)

    if fmt == "json":
        click.echo(json_mod.dumps(info, indent=2, default=str))
    else:
        click.echo(f"\n  Sheet: {info['name']}")
        click.echo(f"  Shape: {info['shape'][0]} rows × {info['shape'][1]} columns\n")
        click.echo("  Schema:")
        for col, dtype in info["schema"].items():
            nulls = info["null_counts"].get(col, 0)
            null_str = f" ({nulls} nulls)" if nulls > 0 else ""
            click.echo(f"    {col:<20} {dtype}{null_str}")
        click.echo(f"\n  Sample ({len(info['sample'])} rows):")
        for row in info["sample"]:
            click.echo(f"    {row}")


@main.command()
@click.argument("file", type=click.Path(exists=True))
@click.argument("sql")
@click.option("--export", "-e", "export_path", type=click.Path(), help="Export result to file")
def query(file: str, sql: str, export_path: str | None):
    """Run a SQL query against a data file via DuckDB (no TUI).

    Example:
        sweet query data.csv "SELECT name, COUNT(*) as n FROM data GROUP BY name"
    """
    from .core.workspace import Workspace

    ws = Workspace()
    ws.load(file)
    ws.query(sql)

    if export_path:
        ws.export(export_path)
        click.echo(f"Exported {ws.shape[0]} rows to: {export_path}")
    else:
        click.echo(ws.df.write_csv())


@main.command()
@click.argument("file", type=click.Path(exists=True))
def codegen(file: str):
    """Generate Polars Python code from a data file's inferred operations.

    This is primarily useful after an interactive session — shows the
    reproducible code for all transforms applied.
    """
    from .core.workspace import Workspace

    ws = Workspace()
    ws.load(file)
    click.echo(ws.generate_code())


@main.command()
@click.option(
    "--mcp", "protocol", flag_value="mcp", default=True, help="Use MCP protocol (default)"
)
@click.option("--port", type=int, default=None, help="Port for HTTP mode (not yet implemented)")
def serve(protocol: str, port: int | None):
    """Start Sweet as an MCP tool server for AI agents.

    Example:
        sweet serve --mcp
    """
    import asyncio

    if protocol == "mcp":
        from .mcp import run_mcp_server

        click.echo("Starting Sweet MCP server (stdio)...", err=True)
        asyncio.run(run_mcp_server())
    else:
        click.echo("HTTP mode not yet implemented.", err=True)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
