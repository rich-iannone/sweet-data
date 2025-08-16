import sys
import tempfile
from pathlib import Path

import click

from .ui.app import run_app


@click.command()
@click.version_option()
@click.option("--file", "-f", type=click.Path(exists=True), help="Load data file on startup")
@click.option(
    "--db",
    type=str,
    help="Connect to database: local file path or remote connection (e.g., mysql://user:pass@host:port/db)",
)
def main(file: str | None, db: str | None):
    """Sweet - Interactive data engineering CLI utility."""
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
            # Check if db parameter is a local file path or remote connection
            if Path(db).exists() and Path(db).is_file():
                # Local database file - treat as startup_file
                click.echo(f"Starting Sweet with local database file: {db}")
                run_app(startup_file=db)
            else:
                # Remote database connection - treat as startup_db
                click.echo(f"Starting Sweet with remote database: {db}")
                run_app(startup_db=db)
        else:
            click.echo("Starting Sweet...")
            run_app()
    except KeyboardInterrupt:
        click.echo("\nGoodbye!")


if __name__ == "__main__":
    main()
