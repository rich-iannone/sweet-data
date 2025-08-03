"""Command-line interface for Sweet."""

import sys
import tempfile
from pathlib import Path

import click

from .ui.app import run_app


@click.command()
@click.version_option()
@click.option(
    "--file", "-f", type=click.Path(exists=True), help="Load data file on startup"
)
def main(file: str | None):
    """Sweet - Interactive data engineering CLI utility."""
    try:
        # Check if data is being piped from stdin
        if not sys.stdin.isatty() and file is None:
            # Read from stdin and create a temporary file
            stdin_data = sys.stdin.read()
            if stdin_data.strip():
                # Create a temporary CSV file
                temp_file = tempfile.NamedTemporaryFile(mode='w', suffix='.csv', delete=False)
                temp_file.write(stdin_data)
                temp_file.flush()
                temp_file.close()
                
                # Restore stdin to the terminal for the Textual app
                sys.stdin = open('/dev/tty', 'r')
                
                click.echo("Starting Sweet with piped data...")
                run_app(startup_file=temp_file.name)
                
                # Clean up the temporary file after the app closes
                try:
                    Path(temp_file.name).unlink()
                except OSError:
                    pass  # File might already be deleted
                return
        
        if file:
            click.echo(f"Starting Sweet with file: {file}")
            run_app(startup_file=file)
        else:
            click.echo("Starting Sweet...")
            run_app()
    except KeyboardInterrupt:
        click.echo("\nGoodbye!")


if __name__ == "__main__":
    main()
