"""Command-line interface for Sweet."""

from pathlib import Path

import click

from .core.workbook import Workbook
from .ui.app import run_app


@click.group()
@click.version_option()
def cli():
    """Sweet - Interactive data engineering CLI utility."""
    pass


@cli.command()
@click.option("--demo", is_flag=True, help="Run with demo data")
@click.option(
    "--file", "-f", type=click.Path(exists=True), help="Load data file on startup"
)
@click.option("--format", default="csv", help="File format (csv, parquet, json)")
def run(demo: bool, file: str | None, format: str):
    """Run the Sweet interactive application."""
    try:
        if demo:
            click.echo("Starting Sweet with demo data...")
            # TODO: Load demo data
            run_app()
        elif file:
            click.echo(f"Starting Sweet with file: {file}")
            run_app(startup_file=file)
        else:
            click.echo("Starting Sweet...")
            run_app()
    except KeyboardInterrupt:
        click.echo("\nGoodbye!")


@cli.command()
@click.argument("input_file", type=click.Path(exists=True))
@click.argument("output_file", type=click.Path())
@click.option("--format", default="parquet", help="Output format (csv, parquet, json)")
def convert(input_file: str, output_file: str, format: str):
    """Convert a data file from one format to another."""
    try:
        # Create a temporary workbook
        wb = Workbook()

        # Detect input format
        input_path = Path(input_file)
        input_format = input_path.suffix.lower().lstrip(".")

        # Load the file
        sheet = wb.load_sheet_from_file("temp", input_file, input_format)

        # Save in new format
        sheet.save_to_file(output_file, format)

        click.echo(f"Converted {input_file} to {output_file} ({format})")

    except Exception as e:
        click.echo(f"Error: {e}", err=True)
        raise click.Abort() from e


@cli.command()
@click.argument("file", type=click.Path(exists=True))
@click.option("--format", default="csv", help="File format (csv, parquet, json)")
def info(file: str, format: str):
    """Show information about a data file."""
    try:
        # Create a temporary workbook
        wb = Workbook()
        sheet = wb.load_sheet_from_file("temp", file, format)

        if sheet.df is not None:
            click.echo(f"File: {file}")
            click.echo(f"Shape: {sheet.df.shape}")
            click.echo(f"Columns: {list(sheet.df.columns)}")
            click.echo("\nSchema:")
            for col, dtype in sheet.get_schema().items():
                click.echo(f"  {col}: {dtype}")
        else:
            click.echo("No data found in file")

    except Exception as e:
        click.echo(f"Error: {e}", err=True)
        raise click.Abort() from e


def main():
    """Main entry point for the CLI."""
    cli()


if __name__ == "__main__":
    main()
