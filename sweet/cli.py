import os
import sys
import tempfile
from pathlib import Path

import click



class SweetGroup(click.Group):
    """A command group that also accepts things to open as positional arguments.

    ``sweet data.parquet 'logs/*.csv'`` opens the viewer; ``sweet profile data.csv``
    runs the `profile` subcommand. Arguments are only treated as targets when the
    first positional argument isn't a subcommand name.
    """

    def parse_args(self, ctx: click.Context, args: list[str]) -> list[str]:
        value_options = {
            name
            for param in self.params
            if isinstance(param, click.Option) and not param.is_flag
            for name in param.opts
        }
        positional: list[int] = []
        skip = False
        for i, arg in enumerate(args):
            if skip:
                skip = False
            elif arg in value_options:
                skip = True
            elif arg == "-" or not arg.startswith("-"):
                positional.append(i)
        if positional and args[positional[0]] not in self.commands:
            ctx.meta["sweet_targets"] = [args[i] for i in positional]
            args = [a for i, a in enumerate(args) if i not in positional]
        return super().parse_args(ctx, args)


def _read_piped_stdin() -> bytes | None:
    """Read piped stdin (if any), then reattach stdin to the terminal for the TUI."""
    if sys.stdin.isatty():
        return None
    data = sys.stdin.buffer.read()
    try:
        import os

        tty_fd = os.open("/dev/tty", os.O_RDONLY)
        os.dup2(tty_fd, 0)
        os.close(tty_fd)
    except OSError:
        pass
    return data if data.strip() else None


@click.group(cls=SweetGroup, invoke_without_command=True)
@click.version_option()
@click.option("--file", "-f", type=str, help="Data to open (same as a positional TARGET)")
@click.option(
    "--db", type=str, help="Connect to remote database (e.g., mysql://user:pass@host:port/db)"
)
@click.option("--classic", is_flag=True, help="Use the classic spreadsheet UI")
@click.option(
    "--lazy/--eager",
    default=None,
    help="Force lazy scanning or in-memory loading (default: lazy for large or remote data)",
)
@click.option("--session", "session_name", help="Name for the live session agents attach to")
@click.option("--no-session", is_flag=True, help="Don't let agents attach to this viewer")
@click.option("--mask-pii", is_flag=True, help="Mask detected personal data from agents")
@click.pass_context
def main(
    ctx,
    file: str | None,
    db: str | None,
    classic: bool,
    lazy: bool | None,
    session_name: str | None,
    no_session: bool,
    mask_pii: bool,
):
    """Sweet: look at, understand, and reshape data in your terminal.

    \b
    Open files, globs, directories, URLs, or stdin:
        sweet data.parquet
        sweet 'logs/2026-*.csv' customers.xlsx
        sweet s3://bucket/events/
        cat data.csv | sweet

    Or run a subcommand (see below).
    """
    # If a subcommand is invoked, don't run the TUI
    if ctx.invoked_subcommand is not None:
        return

    targets = list(ctx.meta.get("sweet_targets", []))
    if file:
        targets.insert(0, file)

    try:
        stdin_data = _read_piped_stdin()
        if stdin_data is not None and not targets:
            targets = ["-"]

        from .core.sources import is_database_file

        if classic or db or any(is_database_file(t) for t in targets):
            _run_classic(targets, db, stdin_data)
            return

        from .ui.viewer import run_viewer

        run_viewer(
            targets,
            stdin_data=stdin_data,
            lazy=lazy,
            session=None if no_session else (session_name or ""),
            mask_pii=mask_pii or os.environ.get("SWEET_MASK_PII", "") not in ("", "0"),
        )
    except KeyboardInterrupt:
        click.echo("\nGoodbye!")


@main.command(name="mcp")
@click.option("--attach", "attach", help="Attach to the running session with this name")
@click.option("--headless", is_flag=True, help="Don't attach to a viewer; work in a private session")
@click.option("--launch", "launch", multiple=True, help="Open a viewer on this data in a new tmux pane, then attach")
@click.option("--labs", is_flag=True, help="Also expose the older, larger tool set (headless only)")
@click.option("--client", "client_name", default="mcp", help="Name agents appear under (agent:<name>)")
def mcp_cmd(attach: str | None, headless: bool, launch: tuple[str, ...], labs: bool, client_name: str):
    """Run Sweet's MCP server for AI agents (stdio).

    \b
    By default, attaches to the most recently started viewer so the person sees
    every change and approves proposals; otherwise runs a headless session.
        sweet mcp
        sweet mcp --attach demo
        sweet mcp --launch sales.parquet
    """
    import asyncio

    try:
        from .session.mcp import serve
    except ImportError as e:
        raise click.ClickException(
            f"The MCP server needs the 'mcp' package ({e}). Install it with: pip install 'sweet-data[mcp]'"
        ) from e

    asyncio.run(
        serve(
            attach=attach,
            headless=headless,
            launch=list(launch) if launch else None,
            labs=labs,
            client_name=client_name,
        )
    )


@main.command(name="sessions")
def sessions_cmd():
    """List running viewer sessions that agents can attach to."""
    from .session import list_sessions

    found = list_sessions()
    if not found:
        click.echo("  No running sessions. Start one with `sweet <data>`.")
        return
    for info in found:
        click.echo(f"  {info.name:<20} pid {info.pid:<7} {info.cwd}  {info.title}")


def _run_classic(targets: list[str], db: str | None, stdin_data: bytes | None) -> None:
    """Launch the classic UI (one file or database at a time)."""
    from .ui.app import run_app

    if db:
        run_app(startup_db=db)
        return
    target = targets[0] if targets else None
    if target == "-" and stdin_data is not None:
        with tempfile.NamedTemporaryFile(mode="wb", suffix=".csv", delete=False) as f:
            f.write(stdin_data)
        try:
            run_app(startup_file=f.name)
        finally:
            Path(f.name).unlink(missing_ok=True)
        return
    run_app(startup_file=target)


# ---------------------------------------------------------------------------
# Subcommands: Headless operations
# ---------------------------------------------------------------------------


@main.command()
@click.argument("source")
@click.option("--name", "-n", type=str, default=None, help="Sheet name.")
@click.option(
    "--format", "fmt", type=str, default=None, help="Force format (csv, parquet, json, etc.)"
)
@click.option("--query", "-q", type=str, default=None, help="SQL query (for database sources).")
@click.option("--table", type=str, default=None, help="Table name (for database sources).")
@click.option("--selector", type=int, default=0, help="Table index (for web pages with multiple tables).")
@click.option("--export", "-e", "export_path", type=click.Path(), help="Export result to file.")
def load(source: str, name: str | None, fmt: str | None, query: str | None, table: str | None, selector: int, export_path: str | None):
    """Load data from a file, URL, database, or web page.

    SOURCE can be a local file path, HTTP(S) URL, database connection string,
    or a web page URL (tables will be extracted).

    Examples:
        sweet load data.csv
        sweet load https://example.com/data.csv
        sweet load "sqlite:///my.db" --table users
        sweet load "https://en.wikipedia.org/wiki/List_of_countries" --selector 0
        sweet load s3://bucket/path/file.parquet
    """
    from .core.workspace import Workspace

    ws = Workspace()
    ws.load(source, name=name, format=fmt, query=query, table=table, selector=selector)

    info = ws.inspect()
    click.echo(f"  ✓ Loaded: {info['name']} ({info['n_rows']} rows × {info['n_cols']} cols)")

    if export_path:
        ws.export(export_path)
        click.echo(f"  ✓ Exported to: {export_path}")
    else:
        # Show first few rows
        click.echo(str(ws.df.head(5)))


@main.command()
@click.argument("source", type=click.Path(exists=True))
@click.argument("dest")
@click.option(
    "--format", "-f", "fmt", type=str, default=None,
    help="Force export format (csv, parquet, json, tsv, ndjson, ipc)."
)
@click.option("--table", type=str, default=None, help="Table name (for database destinations).")
@click.option(
    "--mode", type=click.Choice(["replace", "append", "fail"]), default="replace",
    help="Write mode for databases."
)
def export(source: str, dest: str, fmt: str | None, table: str | None, mode: str):
    """Export data to a file, database, or cloud storage.

    Load SOURCE, then export to DEST. DEST can be a file path, cloud URL
    (s3://, gs://), or database connection string.

    Examples:
        sweet export data.csv output.parquet
        sweet export data.csv s3://bucket/path/data.parquet
        sweet export data.csv "sqlite:///my.db" --table results
        sweet export data.csv output.tsv --format tsv
    """
    from .core.workspace import Workspace

    ws = Workspace()
    ws.load(source)
    ws.export(dest, format=fmt, table=table, mode=mode)
    click.echo(f"  ✓ Exported {ws.shape[0]} rows × {ws.shape[1]} cols to: {dest}")


@main.command()
@click.argument("source", type=click.Path(exists=True))
@click.argument("dest", type=click.Path())
@click.option("--title", type=str, default=None, help="Table title.")
@click.option("--subtitle", type=str, default=None, help="Table subtitle.")
@click.option("--rowname-col", type=str, default=None, help="Column to use as row names.")
@click.option("--groupname-col", type=str, default=None, help="Column to use for grouping.")
@click.option("--fmt-number", multiple=True, help="Column(s) to format as numbers.")
@click.option("--fmt-currency", multiple=True, help="Column(s) to format as currency.")
@click.option("--fmt-percent", multiple=True, help="Column(s) to format as percentages.")
@click.option("--fmt-integer", multiple=True, help="Column(s) to format as integers.")
@click.option("--striping", is_flag=True, help="Enable row striping.")
@click.option("--stylize", type=int, default=None, help="Style preset (1-6).")
@click.option("--source-note", type=str, default=None, help="Source note at table footer.")
def gt(
    source: str,
    dest: str,
    title: str | None,
    subtitle: str | None,
    rowname_col: str | None,
    groupname_col: str | None,
    fmt_number: tuple,
    fmt_currency: tuple,
    fmt_percent: tuple,
    fmt_integer: tuple,
    striping: bool,
    stylize: int | None,
    source_note: str | None,
):
    """Export data as a publication-quality HTML table using Great Tables.

    Examples:
        sweet gt data.csv report.html --title "Summary"
        sweet gt data.csv table.html --fmt-currency revenue --striping
        sweet gt data.csv styled.html --stylize 3 --groupname-col region
    """
    from .core.gt_export import save_great_table
    from .core.workspace import Workspace

    ws = Workspace()
    ws.load(source)

    save_great_table(
        ws.df,
        dest,
        title=title,
        subtitle=subtitle,
        rowname_col=rowname_col,
        groupname_col=groupname_col,
        fmt_number=list(fmt_number) or None,
        fmt_currency=list(fmt_currency) or None,
        fmt_percent=list(fmt_percent) or None,
        fmt_integer=list(fmt_integer) or None,
        source_note=source_note,
        striping=striping,
        stylize=stylize,
    )
    click.echo(f"  ✓ Great Tables export: {dest}")


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
@click.option("--format", "fmt", type=click.Choice(["text", "json"]), default="text")
def scan(file: str, fmt: str):
    """Deep statistical scan of a data file via Pointblank (no TUI).

    Shows per-column statistics: type, missingness, uniqueness, descriptive
    statistics (mean, median, std, quartiles), and sample values.

    Example:
        sweet scan data.csv
        sweet scan data.parquet --format json
    """
    import json as json_mod

    from .core.workspace import Workspace

    ws = Workspace()
    ws.load(file)
    result = ws.scan()

    if fmt == "json":
        click.echo(json_mod.dumps(result, indent=2, default=str))
    else:
        click.echo(f"\n  Sheet: {result['name']}")
        click.echo(f"  Shape: {result['shape'][0]} rows × {result['shape'][1]} columns\n")
        click.echo("  Column Profiles:")
        for col in result["columns"]:
            col_name = col.get("colname", "?")
            col_type = col.get("coltype", "?")
            n_missing = col.get("n_missing", 0)
            n_unique = col.get("n_unique", "?")
            mean = col.get("mean")
            median = col.get("median")

            click.echo(f"    {col_name:<20} {col_type:<10} missing={n_missing} unique={n_unique}")
            if mean is not None:
                std = col.get("std", "?")
                min_val = col.get("min", "?")
                max_val = col.get("max", "?")
                click.echo(
                    f"      {'':20} mean={mean:.4g}  std={std:.4g}  "
                    f"min={min_val}  max={max_val}  median={median:.4g}"
                    if isinstance(std, (int, float))
                    else f"      {'':20} mean={mean}  min={min_val}  max={max_val}"
                )


@main.command()
@click.argument("file", type=click.Path(exists=True))
@click.option("--yaml", "yaml_path", type=click.Path(exists=True), help="Pointblank YAML file")
@click.option("--format", "fmt", type=click.Choice(["text", "json"]), default="text")
@click.option("--warning", type=float, default=None, help="Warning threshold (fraction 0-1)")
@click.option("--error", "error_thresh", type=float, default=None, help="Error threshold")
@click.option("--critical", type=float, default=None, help="Critical threshold")
@click.option("--extracts", is_flag=True, help="Show failing rows for each step")
def validate(
    file: str,
    yaml_path: str | None,
    fmt: str,
    warning: float | None,
    error_thresh: float | None,
    critical: float | None,
    extracts: bool,
):
    """Run data quality validation via Pointblank (no TUI).

    Without --yaml, checks all columns for non-null values plus
    rows_distinct and rows_complete. Supports graduated severity thresholds.

    Example:
        sweet validate data.csv
        sweet validate data.csv --yaml rules.yaml
        sweet validate data.csv --warning 0.1 --error 0.3
        sweet validate data.csv --extracts --format json
    """
    import json as json_mod

    from .core.workspace import Workspace

    ws = Workspace()
    ws.load(file)

    thresholds = None
    if any(v is not None for v in (warning, error_thresh, critical)):
        thresholds = {}
        if warning is not None:
            thresholds["warning"] = warning
        if error_thresh is not None:
            thresholds["error"] = error_thresh
        if critical is not None:
            thresholds["critical"] = critical

    result = ws.validate(yaml_path=yaml_path, thresholds=thresholds, get_extracts=extracts)

    if fmt == "json":
        click.echo(json_mod.dumps(result, indent=2, default=str))
    else:
        status = "✓ ALL PASSED" if result["all_passed"] else "✗ FAILURES DETECTED"
        click.echo(f"\n  Validation: {status}")
        click.echo(f"  Steps: {result['n_steps']}\n")
        for step in result["steps"]:
            icon = "✓" if step["all_passed"] else "✗"
            severity = ""
            if step.get("critical"):
                severity = " [CRITICAL]"
            elif step.get("error"):
                severity = " [ERROR]"
            elif step.get("warning"):
                severity = " [WARNING]"
            col_info = f" on '{step['column']}'" if step["column"] else ""
            click.echo(
                f"    {icon} {step['type']}{col_info}: "
                f"{step['n_passed']}/{step['n']} passed "
                f"({step['f_passed']:.0%}){severity}"
            )
            if extracts and step.get("extracts"):
                click.echo(f"      Failing rows ({len(step['extracts'])}):")
                for row in step["extracts"][:5]:
                    click.echo(f"        {row}")


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


@main.command(name="detect-types")
@click.argument("file", type=click.Path(exists=True))
@click.option("--format", "fmt", type=click.Choice(["text", "json"]), default="text")
def detect_types(file: str, fmt: str):
    """Detect semantic types and suggest casts for string columns (no TUI).

    Identifies dates, emails, URLs, integers, booleans in string columns.
    Also flags potential PII columns.

    Example:
        sweet detect-types data.csv
        sweet detect-types data.csv --format json
    """
    import json as json_mod

    from .core.workspace import Workspace

    ws = Workspace()
    ws.load(file)
    result = ws.detect_types()

    if fmt == "json":
        click.echo(json_mod.dumps(result, indent=2, default=str))
    else:
        click.echo(f"\n  Type Detection: {result['name']}\n")
        has_suggestions = False
        for s in result["suggestions"]:
            if s["detected_type"] or s["pii"]:
                has_suggestions = True
                pii_flag = " ⚠ PII" if s["pii"] else ""
                if s["detected_type"]:
                    click.echo(
                        f"    {s['column']:<20} {s['current_type']:<10} → "
                        f"detected: {s['detected_type']} "
                        f"(confidence: {s['confidence']:.0%}){pii_flag}"
                    )
                    if s["suggestion"]:
                        click.echo(f"      {'':20} Suggestion: {s['suggestion']}")
                elif s["pii"]:
                    click.echo(f"    {s['column']:<20} {s['current_type']:<10}{pii_flag}")
        if not has_suggestions:
            click.echo("    No type suggestions or PII flags detected.")


@main.command(name="detect-outliers")
@click.argument("file", type=click.Path(exists=True))
@click.option(
    "--method", type=click.Choice(["iqr", "zscore"]), default="iqr", help="Detection method"
)
@click.option("--threshold", type=float, default=1.5, help="IQR multiplier or z-score threshold")
@click.option("--format", "fmt", type=click.Choice(["text", "json"]), default="text")
def detect_outliers(file: str, method: str, threshold: float, fmt: str):
    """Detect statistical outliers in numeric columns (no TUI).

    Example:
        sweet detect-outliers data.csv
        sweet detect-outliers data.csv --method zscore --threshold 3.0
    """
    import json as json_mod

    from .core.workspace import Workspace

    ws = Workspace()
    ws.load(file)
    result = ws.detect_outliers(method=method, threshold=threshold)

    if fmt == "json":
        click.echo(json_mod.dumps(result, indent=2, default=str))
    else:
        click.echo(
            f"\n  Outlier Detection: {result['name']} (method={result['method']}, threshold={result['threshold']})\n"
        )
        if not result["columns"]:
            click.echo("    No outliers detected.")
        else:
            for col in result["columns"]:
                if col["n_outliers"] > 0:
                    click.echo(
                        f"    {col['column']:<20} {col['n_outliers']} outlier(s) "
                        f"[bounds: {col['lower_bound']:.4g} – {col['upper_bound']:.4g}]"
                    )
                    if col["outlier_indices"]:
                        idx_str = ", ".join(str(i) for i in col["outlier_indices"][:10])
                        click.echo(f"      {'':20} rows: {idx_str}")


@main.command()
@click.argument("file", type=click.Path(exists=True))
def describe(file: str):
    """Generate a plain-English description of a data file (no TUI).

    Example:
        sweet describe data.csv
    """
    from .core.workspace import Workspace

    ws = Workspace()
    ws.load(file)
    click.echo(f"\n  {ws.describe()}\n")


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
@click.argument("file", type=click.Path(exists=True))
@click.option(
    "--format",
    "-f",
    "fmt",
    type=click.Choice(["polars", "sql", "dbt", "script"]),
    default="polars",
    help="Output format.",
)
@click.option("--output", "-o", type=str, default=None, help="Output file path for the pipeline.")
@click.option("--name", "-n", type=str, default=None, help="Pipeline/model name.")
@click.option("--steps", "-s", multiple=True, help="Steps to run before generating (repeatable).")
@click.option("--recipe", "-r", type=str, default=None, help="Recipe to run before generating.")
def generate(
    file: str, fmt: str, output: str | None, name: str | None, steps: tuple, recipe: str | None
):
    """Generate production-ready pipeline code from data transforms.

    Optionally run a recipe or steps first, then export the transform
    history as a standalone script.

    Examples:
        sweet generate data.csv --format polars
        sweet generate data.csv --format sql --name my_model
        sweet generate data.csv --format dbt -r clean-csv
        sweet generate data.csv -f script -s detect_and_cast_types -s trim_whitespace
    """
    from .agents import DataAgent, RecipeRegistry
    from .core.workspace import Workspace

    ws = Workspace()
    ws.load(file)

    # Run transforms if requested
    if recipe:
        registry = RecipeRegistry()
        r = registry.get(recipe)
        if r is None:
            click.echo(f"  ✗ Unknown recipe: '{recipe}'", err=True)
            raise SystemExit(1)
        agent = DataAgent(workspace=ws, validate_between_steps=False)
        agent.run_recipe(r)
    elif steps:
        agent = DataAgent(workspace=ws, validate_between_steps=False)
        agent.run_steps(list(steps))

    code = ws.generate_pipeline(format=fmt, source=file, output=output, name=name)
    click.echo(code)


@main.command(name="detect-pii")
@click.argument("file", type=click.Path(exists=True))
@click.option("--format", "fmt", type=click.Choice(["text", "json"]), default="text")
def detect_pii(file: str, fmt: str):
    """Detect columns likely containing PII (no TUI).

    Example:
        sweet detect-pii data.csv
    """
    import json as json_mod

    from .core.workspace import Workspace

    ws = Workspace()
    ws.load(file)
    result = ws.detect_pii()

    if fmt == "json":
        click.echo(json_mod.dumps(result, indent=2, default=str))
    else:
        if result["has_pii"]:
            click.echo("\n  ⚠ PII Detected:\n")
            for col_info in result["pii_columns"]:
                click.echo(
                    f"    • {col_info['column']}: {col_info['pii_type']} "
                    f"(confidence: {col_info['confidence']:.0%}, "
                    f"detected by: {col_info['detected_by']})"
                )
        else:
            click.echo("\n  ✓ No PII detected.\n")


@main.command()
@click.argument("file", type=click.Path(exists=True))
@click.option("--method", type=click.Choice(["pearson", "spearman"]), default="pearson")
@click.option("--min-abs", type=float, default=0.3, help="Min |correlation| to display")
@click.option("--format", "fmt", type=click.Choice(["text", "json"]), default="text")
def correlations(file: str, method: str, min_abs: float, fmt: str):
    """Compute pairwise correlations between numeric columns (no TUI).

    Example:
        sweet correlations data.csv --min-abs 0.5
    """
    import json as json_mod

    from .core.workspace import Workspace

    ws = Workspace()
    ws.load(file)
    result = ws.correlations(method=method, min_abs=min_abs)

    if fmt == "json":
        click.echo(json_mod.dumps(result, indent=2, default=str))
    else:
        click.echo(f"\n  Correlations ({method}, |r| >= {min_abs}):")
        click.echo(f"  Numeric columns: {result['n_numeric_columns']}\n")
        if not result["pairs"]:
            click.echo("    No correlations found above threshold.")
        for pair in result["pairs"]:
            r = pair["correlation"]
            strength = "strong" if abs(r) >= 0.7 else "moderate" if abs(r) >= 0.4 else "weak"
            click.echo(f"    {pair['column_a']} ↔ {pair['column_b']}: {r:+.4f} ({strength})")


@main.command(name="suggest-casts")
@click.argument("file", type=click.Path(exists=True))
@click.option("--format", "fmt", type=click.Choice(["text", "json"]), default="text")
def suggest_casts(file: str, fmt: str):
    """Suggest type casts for string columns containing typed data (no TUI).

    Example:
        sweet suggest-casts data.csv
    """
    import json as json_mod

    from .core.workspace import Workspace

    ws = Workspace()
    ws.load(file)
    suggestions = ws.suggest_casts()

    if fmt == "json":
        click.echo(json_mod.dumps(suggestions, indent=2, default=str))
    else:
        if not suggestions:
            click.echo("\n  ✓ No cast suggestions — all columns have appropriate types.\n")
        else:
            click.echo("\n  Suggested casts:\n")
            for s in suggestions:
                click.echo(
                    f"    • {s['column']}: {s['from_type']} → {s['to_type']} "
                    f"(confidence: {s['confidence']:.0%})"
                )
                click.echo(f"      Expression: {s['expression']}")


@main.command()
@click.argument("file", type=click.Path(exists=True))
@click.option("--max", "max_suggestions", type=int, default=20, help="Max suggestions to show")
@click.option("--format", "fmt", type=click.Choice(["text", "json"]), default="text")
def suggest(file: str, max_suggestions: int, fmt: str):
    """Suggest transforms based on data patterns (no TUI).

    Analyzes data for currency extraction, whitespace, date parsing,
    column merging, naming normalization, and more.

    Example:
        sweet suggest data.csv
        sweet suggest messy.csv --max 5 --format json
    """
    import json as json_mod

    from .core.workspace import Workspace

    ws = Workspace()
    ws.load(file)
    suggestions = ws.suggest(max_suggestions=max_suggestions)

    if fmt == "json":
        click.echo(json_mod.dumps(suggestions, indent=2, default=str))
    else:
        if not suggestions:
            click.echo("\n  ✓ No suggestions — data looks clean.\n")
        else:
            click.echo(f"\n  {len(suggestions)} suggestion(s):\n")
            for i, s in enumerate(suggestions, 1):
                conf = f"{s['confidence']:.0%}"
                click.echo(f"  {i}. [{s['kind']}] {s['description']} ({conf})")
                click.echo(f"     → {s['expression']}")
            click.echo()


@main.command(name="infer-contract")
@click.argument("file", type=click.Path(exists=True))
def infer_contract(file: str):
    """Infer a schema contract for a data file (outputs JSON).

    Example:
        sweet infer-contract data.csv > contract.json
    """
    import json as json_mod

    from .core.workspace import Workspace

    ws = Workspace()
    ws.load(file)
    contract = ws.infer_contract()
    click.echo(json_mod.dumps(contract, indent=2, default=str))


@main.command()
@click.argument("file", type=click.Path(exists=True))
@click.argument("recipe_name")
@click.option("--format", "fmt", type=click.Choice(["text", "json"]), default="text")
def recipe(file: str, recipe_name: str, fmt: str):
    """Run a named recipe (multi-step workflow) on a data file.

    Built-in recipes: clean-csv, quality-check, prepare-export.

    Example:
        sweet recipe data.csv clean-csv
        sweet recipe data.csv quality-check --format json
    """
    import json as json_mod

    from .agents import DataAgent, RecipeRegistry
    from .core.workspace import Workspace

    ws = Workspace()
    ws.load(file)

    registry = RecipeRegistry()
    recipe_obj = registry.get(recipe_name)
    if recipe_obj is None:
        available = [r["key"] for r in registry.list()]
        click.echo(f"Unknown recipe: '{recipe_name}'. Available: {', '.join(available)}", err=True)
        raise SystemExit(1)

    agent = DataAgent(workspace=ws)
    result = agent.run_recipe(recipe_obj)

    if fmt == "json":
        click.echo(json_mod.dumps(result.to_dict(), indent=2, default=str))
    else:
        icon = "✓" if result.success else "✗"
        click.echo(f"\n  {icon} Recipe: {recipe_obj.name}")
        click.echo(f"  Duration: {result.total_duration_s:.2f}s")
        click.echo(
            f"  Steps: {result.n_passed} passed, {result.n_failed} failed, "
            f"{result.n_rolled_back} rolled back\n"
        )
        for step in result.steps:
            s_icon = {"passed": "✓", "failed": "✗", "rolled_back": "↩", "skipped": "⊘"}.get(
                step.status.value, "?"
            )
            click.echo(f"    {s_icon} {step.step_name}: {step.message}")
        click.echo(f"\n  {result.summary}\n")


def _run_agent_steps(
    file: str, steps: tuple[str, ...], fmt: str, no_validate: bool, no_rollback: bool
) -> None:
    """Run a sequence of agent steps on a data file (`sweet run FILE STEP...`).

    Available steps: detect_and_cast_types, remove_duplicates,
    standardize_nulls, trim_whitespace, drop_all_null_columns,
    drop_all_null_rows, detect_outliers, validate, generate_report.

    Example:
        sweet run data.csv detect_and_cast_types remove_duplicates validate
    """
    import json as json_mod

    from .agents import DataAgent
    from .core.workspace import Workspace

    ws = Workspace()
    ws.load(file)

    agent = DataAgent(
        workspace=ws,
        validate_between_steps=not no_validate,
        rollback_on_failure=not no_rollback,
    )
    result = agent.run_steps(list(steps))

    if fmt == "json":
        click.echo(json_mod.dumps(result.to_dict(), indent=2, default=str))
    else:
        icon = "✓" if result.success else "✗"
        click.echo(
            f"\n  {icon} Agent run: {result.n_passed} passed, "
            f"{result.n_failed} failed, {result.n_rolled_back} rolled back"
        )
        click.echo(f"  Duration: {result.total_duration_s:.2f}s\n")
        for step in result.steps:
            s_icon = {"passed": "✓", "failed": "✗", "rolled_back": "↩", "skipped": "⊘"}.get(
                step.status.value, "?"
            )
            click.echo(f"    {s_icon} {step.step_name}: {step.message}")
        click.echo(f"\n  {result.summary}\n")


@main.command()
@click.option(
    "--mcp", "protocol", flag_value="mcp", default=True, help="Use MCP protocol (default)"
)
@click.option("--http", "protocol", flag_value="http", help="Use HTTP REST API")
@click.option("--port", type=int, default=8421, help="Port for HTTP mode (default: 8421)")
@click.option("--host", type=str, default="127.0.0.1", help="Host to bind (default: 127.0.0.1)")
def serve(protocol: str, port: int, host: str):
    """Start Sweet as a server for AI agents or HTTP clients.

    Example:
        sweet serve --mcp
        sweet serve --http
        sweet serve --http --port 9000 --host 0.0.0.0
    """
    import asyncio

    if protocol == "mcp":
        from .mcp import run_mcp_server

        click.echo("Starting Sweet MCP server (stdio)...", err=True)
        asyncio.run(run_mcp_server())
    else:
        from .http_api import run_http_server

        click.echo(f"Starting Sweet HTTP API on {host}:{port}...", err=True)
        run_http_server(host=host, port=port)


# =============================================================================
# Memory commands
# =============================================================================


@main.group()
def memory():
    """Manage agent memory (preferences, domain rules, run history)."""
    pass


@memory.command("show")
def memory_show():
    """Show a summary of what the agent remembers.

    Example:
        sweet memory show
    """
    from .agents import AgentMemory

    mem = AgentMemory.load()
    info = mem.summary()
    click.echo(f"\n  Agent Memory ({info['memory_dir']})\n")
    click.echo(f"    Preferences:  {info['n_preferences']}")
    click.echo(f"    Domain rules: {info['n_domain_rules']}")
    click.echo(
        f"    Run history:  {info['n_run_records']} ({info['n_successful_runs']} successful)"
    )
    click.echo()


@memory.command("preferences")
def memory_preferences():
    """List all stored preferences.

    Example:
        sweet memory preferences
    """
    from .agents import AgentMemory

    mem = AgentMemory.load()
    if not mem.preferences:
        click.echo("\n  No preferences stored.\n")
        return
    click.echo("\n  Preferences:\n")
    for key, value in sorted(mem.preferences.items()):
        click.echo(f"    {key}: {value}")
    click.echo()


@memory.command("set")
@click.argument("key")
@click.argument("value")
def memory_set(key: str, value: str):
    """Set a preference (key-value pair).

    Example:
        sweet memory set date_format ISO-8601
        sweet memory set null_handling explicit
    """
    import json as json_mod

    from .agents import AgentMemory

    mem = AgentMemory.load()
    # Try to parse as JSON for booleans, numbers, objects
    try:
        parsed = json_mod.loads(value)
    except (json_mod.JSONDecodeError, ValueError):
        parsed = value
    mem.set_preference(key, parsed)
    mem.save()
    click.echo(f"  ✓ Set '{key}' = {parsed}")


@memory.command("rules")
def memory_rules():
    """List all domain rules.

    Example:
        sweet memory rules
    """
    from .agents import AgentMemory

    mem = AgentMemory.load()
    rules = mem.list_rules()
    if not rules:
        click.echo("\n  No domain rules stored.\n")
        return
    click.echo("\n  Domain Rules:\n")
    for rule in rules:
        name = rule.pop("name")
        click.echo(f"    {name}: {rule}")
    click.echo()


@memory.command("history")
@click.option("--limit", "-n", default=10, help="Number of records to show")
def memory_history(limit: int):
    """Show recent agent run history.

    Example:
        sweet memory history
        sweet memory history -n 20
    """
    from .agents import AgentMemory

    mem = AgentMemory.load()
    if not mem.run_history:
        click.echo("\n  No run history recorded.\n")
        return
    recent = mem.run_history[-limit:]
    click.echo(f"\n  Recent runs (last {len(recent)}):\n")
    for record in reversed(recent):
        icon = "✓" if record.success else "✗"
        recipe_info = f" ({record.recipe_name})" if record.recipe_name else ""
        click.echo(
            f"    {icon} {record.timestamp[:19]}{recipe_info} "
            f"— {record.n_passed} passed, {record.n_failed} failed "
            f"[{record.duration_s:.2f}s]"
        )
    click.echo()


@memory.command("clear")
@click.option("--confirm", is_flag=True, help="Skip confirmation prompt")
def memory_clear(confirm: bool):
    """Clear all agent memory.

    Example:
        sweet memory clear --confirm
    """
    from .agents import AgentMemory

    if not confirm:
        click.confirm("  Clear all agent memory? This cannot be undone", abort=True)
    mem = AgentMemory.load()
    mem.preferences = {}
    mem.domain_rules = {}
    mem.run_history = []
    mem.save()
    click.echo("  ✓ Agent memory cleared.")


@memory.command("patterns")
@click.option("--limit", "-n", type=int, default=10, help="Number of patterns to show")
def memory_patterns(limit: int):
    """Show learned usage patterns.

    Example:
        sweet memory patterns
        sweet memory patterns -n 20
    """
    from .core.patterns import PatternStore

    store = PatternStore()
    info = store.summary()

    click.echo(f"\n  Learned Patterns ({info['total_patterns']} total, "
               f"{info['actionable_patterns']} actionable)\n")

    if info["kinds"]:
        click.echo("  By kind:")
        for kind, count in info["kinds"].items():
            click.echo(f"    {kind}: {count}")
        click.echo()

    top = store.top_patterns(limit)
    if top:
        click.echo("  Top patterns:")
        for p in top:
            click.echo(f"    [{p['kind']}] {p['trigger']} → {p['action']} (×{p['count']})")
    elif not info["total_patterns"]:
        click.echo("  No patterns learned yet. Use Sweet to build up your knowledge base.")
    click.echo()


@memory.command("forget")
@click.option("--kind", "-k", type=str, default=None, help="Pattern kind to forget")
@click.option("--trigger", "-t", type=str, default=None, help="Pattern trigger to forget")
@click.option("--all", "forget_all", is_flag=True, help="Forget all patterns")
@click.option("--confirm", is_flag=True, help="Skip confirmation prompt")
def memory_forget(kind: str | None, trigger: str | None, forget_all: bool, confirm: bool):
    """Forget learned usage patterns.

    Example:
        sweet memory forget --all --confirm
        sweet memory forget --kind cast
        sweet memory forget --trigger "dtype:Utf8"
    """
    from .core.patterns import PatternStore

    if forget_all:
        kind = None
        trigger = None

    if not confirm:
        msg = "  Forget"
        if kind:
            msg += f" kind='{kind}'"
        if trigger:
            msg += f" trigger='{trigger}'"
        if forget_all:
            msg = "  Forget ALL patterns?"
        click.confirm(f"{msg}? This cannot be undone", abort=True)

    store = PatternStore()
    removed = store.forget(kind=kind, trigger=trigger)
    click.echo(f"  ✓ Removed {removed} pattern(s).")


# =============================================================================
# Pipeline commands
# =============================================================================


@main.command()
@click.argument("file", type=click.Path(exists=True))
@click.option(
    "--stages",
    "-s",
    multiple=True,
    help="Stages to run (ingestion, quality, transform, export). Repeatable.",
)
@click.option("--no-validate", is_flag=True, help="Disable validation between transform steps.")
def pipeline(file: str, stages: tuple[str, ...], no_validate: bool):
    """Run a multi-agent pipeline on a data file.

    Without --stages, runs the standard pipeline:
    ingest → quality → transform → export.

    Examples:
        sweet pipeline data.csv
        sweet pipeline data.csv -s ingestion -s quality
        sweet pipeline data.csv --no-validate
    """
    from .agents import (
        ExportAgent,
        IngestionAgent,
        Pipeline,
        QualityAgent,
        TransformAgent,
    )
    from .core.workspace import Workspace

    ws = Workspace()
    ws.load(file)

    if stages:
        agent_map = {
            "ingestion": IngestionAgent,
            "quality": QualityAgent,
            "transform": TransformAgent,
            "export": ExportAgent,
        }
        pipe = Pipeline(workspace=ws)
        for stage_name in stages:
            agent_cls = agent_map.get(stage_name)
            if agent_cls is None:
                click.echo(
                    f"  ✗ Unknown stage: '{stage_name}'. Available: {', '.join(agent_map.keys())}",
                    err=True,
                )
                raise SystemExit(1)
            kwargs = {}
            if stage_name == "transform":
                kwargs["validate_between_steps"] = not no_validate
            pipe.add_stage(stage_name, agent_cls(ws, **kwargs))
    else:
        pipe = Pipeline.standard(ws, validate=not no_validate)

    result = pipe.run()

    # Display results
    icon = "✓" if result.success else "✗"
    click.echo(f"\n  {icon} {result.summary}")
    click.echo(f"  Duration: {result.total_duration_s:.2f}s\n")

    for stage_info in result.stages:
        s_icon = "✓" if stage_info["success"] else "✗"
        click.echo(
            f"    {s_icon} [{stage_info['domain']}] {stage_info['name']}: "
            f"{stage_info['n_passed']} passed, {stage_info['n_failed']} failed, "
            f"{stage_info['n_rolled_back']} rolled back"
        )

    click.echo()


# =============================================================================
# Version control commands
# =============================================================================


@main.command(name="commit")
@click.argument("file", type=click.Path(exists=True))
@click.option("--message", "-m", required=True, help="Commit message describing this state")
def vc_commit(file: str, message: str):
    """Snapshot the current data state with a message.

    Example:
        sweet commit sales.csv -m "raw data loaded"
    """
    from .core.workspace import Workspace

    ws = Workspace()
    ws.load(file)
    result = ws.commit(message)
    click.echo(f"\n  ✓ Committed [{result['id']}] \"{result['message']}\"")
    click.echo(f"    Sheet: {result['sheet']} | Shape: {result['shape'][0]}×{result['shape'][1]}")
    click.echo()


@main.command(name="log")
@click.argument("file", type=click.Path(exists=True))
@click.option("--limit", "-n", type=int, default=None, help="Number of commits to show")
def vc_log(file: str, limit: int | None):
    """Show commit history for a dataset.

    Example:
        sweet log sales.csv
        sweet log sales.csv -n 5
    """
    from .core.workspace import Workspace

    ws = Workspace()
    ws.load(file)
    entries = ws.version_log(limit=limit)

    if not entries:
        click.echo("\n  No commits yet.\n")
        return

    click.echo(f"\n  Commit history ({len(entries)} commits):\n")
    for entry in entries:
        ts = entry["timestamp"][:19]
        shape = f"{entry['shape'][0]}×{entry['shape'][1]}"
        parent = f" ← {entry['parent_id']}" if entry["parent_id"] else ""
        click.echo(f"  [{entry['id']}] {ts} | {shape}{parent}")
        click.echo(f"    {entry['message']}")
    click.echo()


@main.command(name="diff")
@click.argument("file1")
@click.argument("file2", required=False)
@click.option("--key", "-k", multiple=True, help="Key column(s) for row matching")
@click.option(
    "--format", "fmt", type=click.Choice(["text", "json", "markdown"]), default="text"
)
@click.option("--sample", "-n", type=int, default=5, help="Example changed rows to show")
def vc_diff(
    file1: str, file2: str | None, key: tuple[str, ...], fmt: str, sample: int
):
    """Diff two datasets (schema, added/removed/changed rows, changed cells).

    Inputs can be any files, globs, or URLs Sweet can open. Without --key,
    only schema and row counts are compared.

    \b
        sweet diff before.parquet after.parquet --key id
        sweet diff old.csv new.csv -k id --format json
        sweet diff data.csv            # against its last commit
    """
    if file2 is None:
        _diff_against_commit(file1, key)
        return

    from .core.diff import diff_frames
    from .core.sources import open_source

    try:
        before = open_source(file1, lazy=True).frame
        after = open_source(file2, lazy=True).frame
        result = diff_frames(before, after, key=list(key) or None)
    except (FileNotFoundError, ValueError) as e:
        raise click.ClickException(str(e)) from e

    if fmt == "json":
        click.echo(result.to_json(sample=sample))
    elif fmt == "markdown":
        click.echo(result.to_markdown(sample=sample))
    else:
        _print_diff(result, sample)
    raise SystemExit(1 if result.has_changes else 0)


def _print_diff(result, sample: int) -> None:
    click.echo(f"\n  {result.summary()}\n")
    click.echo(f"  rows: {result.rows_before:,} → {result.rows_after:,}")
    if result.key:
        click.echo(
            f"  added {result.rows_added:,} · removed {result.rows_removed:,} · "
            f"changed {result.rows_changed:,} · unchanged {result.rows_unchanged:,}"
        )
    for col in result.added_columns:
        click.echo(f"  + column {col}")
    for col in result.removed_columns:
        click.echo(f"  − column {col}")
    for col, (old, new) in result.type_changes.items():
        click.echo(f"  ~ column {col}: {old} → {new}")
    changed = {c: n for c, n in result.changed_by_column.items() if n}
    if changed:
        click.echo("\n  changed cells by column:")
        for col, n in sorted(changed.items(), key=lambda kv: -kv[1]):
            click.echo(f"    {col:<24} {n:>10,}")
    examples = result.samples(sample)
    if examples:
        click.echo("\n  examples:")
        for ex in examples:
            click.echo(f"    {ex}")
    click.echo()


def _diff_against_commit(file1: str, key: tuple[str, ...]) -> None:
    from .core.workspace import Workspace

    ws = Workspace()
    ws.load(file1)
    result = ws.diff(key_columns=list(key) or None)
    if not result["has_changes"]:
        click.echo("\n  No changes detected.\n")
        return
    click.echo(f"\n  {result['summary']}")
    if result["sample_changes"]:
        click.echo(f"\n  Sample changes ({len(result['sample_changes'])} shown):")
        for change in result["sample_changes"][:5]:
            click.echo(f"    {change}")
    click.echo()


@main.command(name="run")
@click.argument("target", type=click.Path(exists=True, dir_okay=False))
@click.argument("steps", nargs=-1)
@click.option("--input", "-i", "input_path", help="Run on this data instead of the pipeline's source")
@click.option("--output", "-o", "output_path", help="Write the result here (format from extension)")
@click.option("--format", "fmt", type=click.Choice(["text", "json"]), default="text")
@click.option("--show", type=int, default=10, help="Rows to preview when not writing output")
@click.option("--no-validate", is_flag=True, help="Agent steps: skip validation between steps")
@click.option("--no-rollback", is_flag=True, help="Agent steps: don't roll back on failure")
def run(
    target: str,
    steps: tuple[str, ...],
    input_path: str | None,
    output_path: str | None,
    fmt: str,
    show: int,
    no_validate: bool,
    no_rollback: bool,
):
    """Replay a .sweet.yaml pipeline, or run agent steps on a data file.

    \b
    Replay a pipeline (e.g. on new data):
        sweet run clean.sweet.yaml
        sweet run clean.sweet.yaml -i orders_2026.csv -o clean_2026.parquet

    \b
    Run agent steps (detect_and_cast_types, remove_duplicates, standardize_nulls,
    trim_whitespace, drop_all_null_columns, drop_all_null_rows, detect_outliers,
    validate, generate_report):
        sweet run data.csv detect_and_cast_types remove_duplicates validate
    """
    if not target.endswith((".yaml", ".yml")):
        if not steps:
            raise click.UsageError("Give agent steps to run, or a .sweet.yaml pipeline")
        _run_agent_steps(target, steps, fmt, no_validate, no_rollback)
        return
    if steps:
        raise click.UsageError("Agent steps can't be combined with a pipeline file")
    _replay_pipeline(target, input_path, output_path, fmt, show)


def _replay_pipeline(
    pipeline_file: str, input_path: str | None, output_path: str | None, fmt: str, show: int
) -> None:
    import json
    import time

    from .core.io import write_file
    from .core.pipeline import Pipeline
    from .core.sources import open_source
    from .core.steps import StepError

    start = time.perf_counter()
    pipeline = Pipeline.load(pipeline_file)
    target = input_path or (pipeline.source or {}).get("path")
    if target is None:
        raise click.ClickException("The pipeline has no file source; pass --input")
    try:
        opened = open_source(
            target, format=None if input_path else (pipeline.source or {}).get("format")
        )
        drift = _schema_drift(pipeline.schema, opened.frame)
        result = pipeline.run(opened.frame)
        df = result.collect(engine="streaming") if hasattr(result, "collect") else result
        if output_path:
            write_file(df, output_path)
    except (StepError, ValueError, FileNotFoundError) as e:
        raise click.ClickException(str(e)) from e
    elapsed = time.perf_counter() - start

    summary = {
        "pipeline": pipeline_file,
        "input": target,
        "output": output_path,
        "steps": len(pipeline.active_steps),
        "rows": df.height,
        "columns": df.width,
        "seconds": round(elapsed, 3),
        "schema_drift": drift,
    }
    if fmt == "json":
        click.echo(json.dumps(summary, indent=2))
        return
    for message in drift:
        click.echo(f"  ⚠ {message}", err=True)
    click.echo(
        f"  ✓ {len(pipeline.active_steps)} step(s) on {target}: "
        f"{df.height:,} rows × {df.width} cols in {elapsed:.2f}s"
    )
    if output_path:
        click.echo(f"  → {output_path}")
    else:
        click.echo(df.head(show))


def _schema_drift(expected: dict[str, str] | None, frame) -> list[str]:
    """Differences between a pipeline's recorded input schema and the actual input."""
    if not expected:
        return []
    schema = frame.collect_schema() if hasattr(frame, "collect_schema") else frame.schema
    actual = {c: str(t) for c, t in schema.items()}
    messages = [f"missing input column '{c}'" for c in expected if c not in actual]
    messages += [f"new input column '{c}'" for c in actual if c not in expected]
    messages += [
        f"column '{c}' is {actual[c]} (pipeline expects {t})"
        for c, t in expected.items()
        if c in actual and actual[c] != t
    ]
    return messages


@main.command(name="compile")
@click.argument("pipeline_file", type=click.Path(exists=True, dir_okay=False))
@click.option(
    "--to", "target", type=click.Choice(["polars", "sql", "dbt", "marimo"]), default="polars"
)
@click.option("--output", "-o", "output_path", help="Write here instead of printing")
@click.option("--source-name", default="raw", help="dbt source name")
def compile_pipeline_cmd(pipeline_file: str, target: str, output_path: str | None, source_name: str):
    """Export a .sweet.yaml pipeline as a Polars script, DuckDB SQL, a dbt model, or marimo.

    \b
        sweet compile clean.sweet.yaml --to polars -o clean.py
        sweet compile clean.sweet.yaml --to dbt -o models/clean_orders.sql
    """
    from .core.pipeline import Pipeline
    from .core.steps import NotExportable

    pipeline = Pipeline.load(pipeline_file)
    try:
        code = {
            "polars": pipeline.to_polars_script,
            "sql": pipeline.to_sql,
            "dbt": lambda: pipeline.to_dbt(source_name=source_name),
            "marimo": pipeline.to_marimo,
        }[target]()
    except NotExportable as e:
        raise click.ClickException(str(e)) from e
    if output_path:
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        Path(output_path).write_text(code if code.endswith("\n") else code + "\n")
        click.echo(f"  → {output_path}")
        if target == "dbt":
            sources = Path(output_path).with_name("sources.yml")
            if not sources.exists():
                sources.write_text(pipeline.to_dbt_sources(source_name=source_name))
                click.echo(f"  → {sources}")
    else:
        click.echo(code)


# =============================================================================
# Bundle commands (shareable workspaces)
# =============================================================================


@main.command()
@click.argument("file", type=click.Path(exists=True))
@click.argument("output", type=click.Path())
@click.option("--description", "-d", default="", help="Description for the bundle")
@click.option("--no-journal", is_flag=True, help="Exclude operation history")
def share(file: str, output: str, description: str, no_journal: bool):
    """Save a dataset as a shareable .sweet bundle.

    Example:
        sweet share sales.csv sales-analysis
        sweet share data.csv my-bundle -d "Cleaned Q4 data"
    """
    from .core.workspace import Workspace

    ws = Workspace()
    ws.load(file)
    result = ws.save(output, description=description, include_journal=not no_journal)
    size_kb = result.stat().st_size / 1024
    click.echo(f"\n  ✓ Bundle saved: {result} ({size_kb:.1f} KB)")
    click.echo(f"    Sheets: {len(ws.sheet_names)} | Description: {description or '(none)'}")
    click.echo()


@main.command(name="open")
@click.argument("bundle", type=click.Path(exists=True))
@click.option("--info", "info_only", is_flag=True, help="Just show bundle info, don't open TUI")
def open_bundle(bundle: str, info_only: bool):
    """Open a .sweet bundle (inspect or launch TUI).

    Example:
        sweet open analysis.sweet --info
        sweet open analysis.sweet
    """
    if info_only:
        from .core.workspace import Workspace

        info = Workspace.inspect_bundle(bundle)
        manifest = info["manifest"]
        click.echo(f"\n  Bundle: {bundle}")
        click.echo(f"  Created: {manifest['created_at'][:19]}")
        click.echo(f"  Description: {manifest.get('description') or '(none)'}")
        click.echo(f"  File size: {info['file_size'] / 1024:.1f} KB")
        click.echo(f"\n  Sheets ({len(manifest['sheets'])}):")
        for s in manifest["sheets"]:
            shape = f"{s['shape'][0]}×{s['shape'][1]}"
            click.echo(f"    • {s['name']} ({shape}, {s['n_transforms']} transforms)")
        click.echo()
    else:
        from .core.workspace import Workspace

        ws = Workspace.open(bundle)
        click.echo(f"  ✓ Restored workspace from {bundle}")
        click.echo(f"    Sheets: {', '.join(ws.sheet_names)}")
        click.echo(f"    Active: {ws.current_sheet_name}")
        click.echo()


# =============================================================================
# Semantic understanding commands
# =============================================================================


@main.command(name="semantic-types")
@click.argument("file", type=click.Path(exists=True))
@click.option("--min-confidence", "-c", default=0.4, type=float, help="Minimum confidence threshold")
@click.option("--json-output", "as_json", is_flag=True, help="Output as JSON")
def semantic_types(file: str, min_confidence: float, as_json: bool):
    """Infer semantic types for columns in a dataset.

    Example:
        sweet semantic-types sales.csv
        sweet semantic-types data.parquet --min-confidence 0.7
    """
    import json as json_mod

    from .core.workspace import Workspace

    ws = Workspace()
    ws.load(file)
    results = ws.semantic_types(min_confidence=min_confidence)

    if as_json:
        click.echo(json_mod.dumps(results, indent=2))
    else:
        click.echo(f"\n  Semantic types for: {file}")
        click.echo(f"  {'Column':<25} {'Type':<15} {'Confidence':<12} Reasoning")
        click.echo(f"  {'─' * 25} {'─' * 15} {'─' * 12} {'─' * 30}")
        for r in results:
            conf = f"{r['confidence']:.0%}"
            click.echo(f"  {r['column']:<25} {r['semantic_type']:<15} {conf:<12} {r['reasoning']}")
        click.echo()


@main.command(name="discover-joins")
@click.argument("files", nargs=-1, required=True, type=click.Path(exists=True))
@click.option("--min-confidence", "-c", default=0.6, type=float, help="Minimum confidence threshold")
@click.option("--min-overlap", "-o", default=0.3, type=float, help="Minimum value overlap ratio")
@click.option("--json-output", "as_json", is_flag=True, help="Output as JSON")
def discover_joins(files: tuple[str, ...], min_confidence: float, min_overlap: float, as_json: bool):
    """Discover potential joins across multiple datasets.

    Example:
        sweet discover-joins orders.csv customers.csv
        sweet discover-joins *.csv --min-overlap 0.5
    """
    import json as json_mod

    from .core.workspace import Workspace

    ws = Workspace()
    for f in files:
        ws.load(f)
    results = ws.discover_joins(min_confidence=min_confidence, min_overlap=min_overlap)

    if as_json:
        click.echo(json_mod.dumps(results, indent=2))
    else:
        if not results:
            click.echo("\n  No join relationships discovered.")
            click.echo()
            return
        click.echo(f"\n  Discovered joins ({len(results)}):")
        click.echo()
        for i, r in enumerate(results, 1):
            click.echo(f"  {i}. {r['description']}")
            click.echo(f"     Confidence: {r['confidence']:.0%} | Overlap: {r['overlap_ratio']:.0%}")
            click.echo()


# =============================================================================
# Data synthesis & augmentation commands
# =============================================================================


@main.command()
@click.argument("file", type=click.Path(exists=True))
@click.option("--rows", "-n", default=1000, type=int, help="Number of rows to generate")
@click.option("--seed", "-s", default=None, type=int, help="Random seed for reproducibility")
@click.option("--output", "-o", default=None, type=click.Path(), help="Output file path")
def synthesize(file: str, rows: int, seed: int | None, output: str | None):
    """Generate synthetic data matching a dataset's schema and profile.

    Example:
        sweet synthesize customers.csv -n 5000 -o fake_customers.csv
        sweet synthesize data.parquet --rows 10000 --seed 42
    """
    from .core.workspace import Workspace

    ws = Workspace()
    ws.load(file)
    ws.synthesize(rows=rows, seed=seed)

    if output:
        ws.export(output)
        click.echo(f"\n  ✓ Generated {rows} synthetic rows → {output}")
    else:
        # Show a preview
        click.echo(f"\n  ✓ Generated {rows} synthetic rows from {file}")
        click.echo(f"    Schema: {len(ws.df.columns)} columns, {ws.df.shape[0]} rows")
        click.echo("\n  Preview (first 5 rows):")
        click.echo(f"  {ws.df.head(5)}")
    click.echo()


@main.command()
@click.argument("file", type=click.Path(exists=True))
@click.argument("column")
@click.option(
    "--method", "-m", default="median",
    type=click.Choice(["mean", "median", "mode", "forward", "backward", "zero", "interpolate"]),
    help="Imputation strategy",
)
@click.option("--output", "-o", default=None, type=click.Path(), help="Output file path")
def impute(file: str, column: str, method: str, output: str | None):
    """Fill missing values in a column.

    Example:
        sweet impute sales.csv revenue --method mean -o filled.csv
        sweet impute data.csv name --method mode
    """
    from .core.workspace import Workspace

    ws = Workspace()
    ws.load(file)
    before_nulls = ws.df[column].null_count()
    ws.impute(column, method=method)
    after_nulls = ws.df[column].null_count()

    if output:
        ws.export(output)
        click.echo(f"\n  ✓ Imputed '{column}' ({method}): {before_nulls} → {after_nulls} nulls → {output}")
    else:
        click.echo(f"\n  ✓ Imputed '{column}' ({method}): {before_nulls} → {after_nulls} nulls")
    click.echo()


@main.command()
@click.argument("file", type=click.Path(exists=True))
@click.argument("kind", type=click.Choice(["fill_rate", "row_hash", "row_number"]))
@click.option("--output", "-o", default=None, type=click.Path(), help="Output file path")
def augment(file: str, kind: str, output: str | None):
    """Add a derived column to a dataset.

    Example:
        sweet augment data.csv fill_rate -o enriched.csv
        sweet augment data.csv row_hash
    """
    from .core.workspace import Workspace

    ws = Workspace()
    ws.load(file)
    ws.augment(kind)

    col_name = f"_{kind}"
    if output:
        ws.export(output)
        click.echo(f"\n  ✓ Added '{col_name}' column → {output}")
    else:
        click.echo(f"\n  ✓ Added '{col_name}' column ({ws.df.shape[0]} rows, {ws.df.shape[1]} columns)")
        click.echo("\n  Preview (first 5 rows):")
        click.echo(f"  {ws.df.head(5)}")
    click.echo()


# =============================================================================
# Conventions commands
# =============================================================================


@main.group()
def conventions():
    """Manage team conventions (.sweet/conventions.yaml)."""


@conventions.command(name="init")
@click.option("--path", "-p", default=".sweet/conventions.yaml", help="Output path")
def conventions_init(path: str):
    """Create a starter conventions.yaml file.

    Example:
        sweet conventions init
        sweet conventions init -p custom/path.yaml
    """
    from pathlib import Path

    from .core.conventions import generate_default_yaml

    p = Path(path)
    if p.exists():
        click.echo(f"  ✗ File already exists: {p}")
        raise SystemExit(1)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(generate_default_yaml())
    click.echo(f"\n  ✓ Created conventions file: {p}")
    click.echo("    Edit this file to define your team's standards.")
    click.echo()


@conventions.command(name="check")
@click.argument("file", type=click.Path(exists=True))
@click.option("--conventions-file", "-c", default=None, type=click.Path(exists=True), help="Path to conventions.yaml")
@click.option("--json-output", "as_json", is_flag=True, help="Output as JSON")
def conventions_check(file: str, conventions_file: str | None, as_json: bool):
    """Validate a dataset against team conventions.

    Example:
        sweet conventions check data.csv
        sweet conventions check data.csv -c .sweet/conventions.yaml
    """
    import json as json_mod

    from .core.workspace import Workspace

    ws = Workspace()
    ws.load(file)
    ws.load_conventions(conventions_file)
    violations = ws.check_conventions()

    if as_json:
        click.echo(json_mod.dumps(violations, indent=2))
    elif not violations:
        click.echo(f"\n  ✓ {file} passes all conventions.")
        click.echo()
    else:
        errors = [v for v in violations if v["severity"] == "error"]
        warnings = [v for v in violations if v["severity"] == "warning"]
        click.echo(f"\n  Conventions check: {file}")
        click.echo(f"  {len(errors)} error(s), {len(warnings)} warning(s)")
        click.echo()
        for v in violations:
            icon = "✗" if v["severity"] == "error" else "⚠"
            click.echo(f"  {icon} [{v['rule']}] {v['message']}")
        click.echo()
        if errors:
            raise SystemExit(1)


@conventions.command(name="show")
@click.option("--conventions-file", "-c", default=None, type=click.Path(exists=True), help="Path to conventions.yaml")
def conventions_show(conventions_file: str | None):
    """Show the active conventions.

    Example:
        sweet conventions show
        sweet conventions show -c path/to/conventions.yaml
    """
    from pathlib import Path

    from .core.conventions import find_conventions_file, load_conventions

    if conventions_file:
        path = Path(conventions_file)
    else:
        path = find_conventions_file()
        if path is None:
            click.echo("\n  No .sweet/conventions.yaml found.")
            click.echo("  Run 'sweet conventions init' to create one.")
            click.echo()
            return

    conv = load_conventions(path)
    click.echo(f"\n  Conventions: {path}")
    click.echo()
    if conv.naming.columns or conv.naming.sheets:
        click.echo("  Naming:")
        if conv.naming.columns:
            click.echo(f"    Columns: {conv.naming.columns}")
        if conv.naming.sheets:
            click.echo(f"    Sheets: {conv.naming.sheets}")
        click.echo()
    if conv.quality.max_null_pct < 100.0 or conv.quality.require_unique or conv.quality.banned_values:
        click.echo("  Quality:")
        if conv.quality.max_null_pct < 100.0:
            click.echo(f"    Max null %: {conv.quality.max_null_pct}")
        if conv.quality.require_unique:
            click.echo(f"    Require unique: {conv.quality.require_unique}")
        if conv.quality.banned_values:
            click.echo(f"    Banned values: {conv.quality.banned_values}")
        click.echo()


# =============================================================================
# Natural language transform commands
# =============================================================================


@main.command(name="nl")
@click.argument("file", type=click.Path(exists=True))
@click.argument("text")
@click.option("--dry-run", is_flag=True, help="Show the expression without applying it")
@click.option("--output", "-o", default=None, type=click.Path(), help="Output file path")
def nl_transform(file: str, text: str, dry_run: bool, output: str | None):
    """Apply a natural language transform to a dataset.

    Example:
        sweet nl data.csv "filter rows where price greater than 100"
        sweet nl data.csv "sort by name descending" -o sorted.csv
        sweet nl data.csv "rename column old_name to new_name" --dry-run
    """
    from .core.workspace import Workspace

    ws = Workspace()
    ws.load(file)

    if dry_run:
        result = ws.nl_translate(text)
        if result is None:
            click.echo("\n  ✗ Could not translate to a Polars expression.")
            raise SystemExit(1)
        click.echo(f"\n  Translation (confidence: {result['confidence']:.0%}):")
        click.echo(f"    {result['expression']}")
        click.echo(f"    Operation: {result['operation']}")
        click.echo()
    else:
        ws.nl_transform(text)
        if output:
            ws.export(output)
            click.echo(f"\n  ✓ Applied → {output} ({ws.df.shape[0]} rows)")
        else:
            click.echo(f"\n  ✓ Applied: {text}")
            click.echo(f"    Result: {ws.df.shape[0]} rows × {ws.df.shape[1]} columns")
        click.echo()


@main.command(name="nl-pipeline")
@click.argument("file", type=click.Path(exists=True))
@click.argument("text")
@click.option("--output", "-o", default=None, type=click.Path(), help="Output file path")
def nl_pipeline(file: str, text: str, output: str | None):
    """Apply multiple natural language transforms (separated by 'then' or ';').

    Example:
        sweet nl-pipeline data.csv "filter price > 10; then sort by name"
        sweet nl-pipeline sales.csv "keep rows where country is US then sort by revenue descending"
    """
    from .core.workspace import Workspace

    ws = Workspace()
    ws.load(file)
    ws.nl_pipeline(text)

    if output:
        ws.export(output)
        click.echo(f"\n  ✓ Pipeline applied → {output} ({ws.df.shape[0]} rows)")
    else:
        click.echo(f"\n  ✓ Pipeline applied: {ws.df.shape[0]} rows × {ws.df.shape[1]} columns")
    click.echo()


# =============================================================================
# Anomaly explanation commands
# =============================================================================


@main.command(name="anomalies")
@click.argument("file", type=click.Path(exists=True))
@click.option("--z-threshold", default=3.0, type=float, help="Z-score threshold for outliers")
@click.option("--json", "as_json", is_flag=True, help="Output as JSON")
def anomalies_cmd(file: str, z_threshold: float, as_json: bool):
    """Detect and explain anomalies in a dataset.

    Example:
        sweet anomalies data.csv
        sweet anomalies data.csv --z-threshold 2.5
        sweet anomalies data.csv --json
    """
    import json as json_mod

    from .core.workspace import Workspace

    ws = Workspace()
    ws.load(file)
    results = ws.explain_anomalies(z_threshold=z_threshold)

    if as_json:
        click.echo(json_mod.dumps(results, indent=2))
    elif not results:
        click.echo("\n  ✓ No anomalies detected.")
        click.echo()
    else:
        click.echo(f"\n  Found {len(results)} anomaly/anomalies:\n")
        for i, a in enumerate(results, 1):
            sev_icon = {"low": "○", "medium": "◑", "high": "●"}.get(a["severity"], "?")
            click.echo(f"  {i}. [{sev_icon} {a['severity']}] {a['description']}")
            if a.get("explanation"):
                click.echo(f"     → {a['explanation']}")
            click.echo()


# =============================================================================
# Cross-dataset intelligence commands
# =============================================================================


@main.command(name="relationships")
@click.argument("files", nargs=-1, type=click.Path(exists=True))
@click.option("--min-match", default=0.5, type=float, help="Minimum match rate (0.0-1.0)")
@click.option("--json", "as_json", is_flag=True, help="Output as JSON")
def relationships_cmd(files: tuple[str, ...], min_match: float, as_json: bool):
    """Discover relationships between multiple datasets.

    Example:
        sweet relationships orders.csv customers.csv
        sweet relationships *.csv --min-match 0.7
    """
    import json as json_mod

    from .core.workspace import Workspace

    if len(files) < 2:
        click.echo("\n  ✗ Need at least 2 files to discover relationships.")
        raise SystemExit(1)

    ws = Workspace()
    for f in files:
        ws.load(f)

    results = ws.discover_relationships(min_match_rate=min_match)

    if as_json:
        click.echo(json_mod.dumps(results, indent=2))
    elif not results:
        click.echo("\n  No relationships discovered between the loaded datasets.")
        click.echo()
    else:
        click.echo(f"\n  Discovered {len(results)} relationship(s):\n")
        for i, r in enumerate(results, 1):
            click.echo(f"  {i}. {r['description']}")
            click.echo(f"     Match rate: {r['match_rate']:.0%} | Confidence: {r['confidence']:.0%}")
            click.echo()


@main.command(name="auto-join")
@click.argument("left_file", type=click.Path(exists=True))
@click.argument("right_file", type=click.Path(exists=True))
@click.option("--output", "-o", default=None, type=click.Path(), help="Output file path")
@click.option("--join-type", default=None, type=click.Choice(["inner", "left"]), help="Join type")
@click.option("--min-match", default=0.5, type=float, help="Minimum match rate")
def auto_join_cmd(left_file: str, right_file: str, output: str | None, join_type: str | None, min_match: float):
    """Automatically join two datasets by discovering the best key.

    Example:
        sweet auto-join orders.csv customers.csv -o enriched.csv
        sweet auto-join sales.csv products.csv --join-type left
    """
    from .core.workspace import Workspace

    ws = Workspace()
    ws.load(left_file)
    left_name = ws.active_sheet_name
    ws.load(right_file)
    right_name = ws.active_sheet_name

    ws.auto_join(left_name, right_name, min_match_rate=min_match, join_type=join_type)

    if output:
        ws.export(output)
        click.echo(f"\n  ✓ Auto-joined → {output} ({ws.df.shape[0]} rows × {ws.df.shape[1]} cols)")
    else:
        click.echo(f"\n  ✓ Auto-joined: {ws.df.shape[0]} rows × {ws.df.shape[1]} columns")
    click.echo()


# =============================================================================
# Data quality validation commands
# =============================================================================


@main.command(name="validate")
@click.argument("file", type=click.Path(exists=True))
@click.option("--rules", "-r", required=True, type=click.Path(exists=True), help="YAML rules file")
@click.option("--json", "as_json", is_flag=True, help="Output as JSON")
def validate_cmd(file: str, rules: str, as_json: bool):
    """Validate a dataset against data quality rules.

    Exit codes: 0 = all pass, 1 = warnings only, 2 = errors found.

    Example:
        sweet validate data.csv --rules rules.yaml
        sweet validate data.csv -r quality-checks.yaml --json
    """
    import json as json_mod

    from .core.workspace import Workspace

    ws = Workspace()
    ws.load(file)
    result = ws.validate_rules(rules)

    if as_json:
        click.echo(json_mod.dumps(result, indent=2))
    else:
        checked = result["rules_checked"]
        passed = result["rules_passed"]
        errors = result["error_count"]
        warnings = result["warning_count"]

        if result["passed"] and warnings == 0:
            click.echo(f"\n  ✓ All {checked} rules passed.")
        else:
            click.echo(f"\n  Checked {checked} rules: {passed} passed, {errors} error(s), {warnings} warning(s)\n")
            for v in result["violations"]:
                icon = "✗" if v["severity"] == "error" else "⚠"
                click.echo(f"  {icon} [{v['severity']}] {v['rule_name']}: {v['message']}")
                if v.get("sample_values"):
                    click.echo(f"    Samples: {v['sample_values']}")
                click.echo()

    # Exit codes
    if result["error_count"] > 0:
        raise SystemExit(2)
    elif result["warning_count"] > 0:
        raise SystemExit(1)


# ---------------------------------------------------------------------------
# recipe commands
# ---------------------------------------------------------------------------


@main.group()
def recipe():
    """Manage and run reusable data recipes."""


@recipe.command(name="list")
@click.option("--recipe-dir", "-d", default=None, type=click.Path(exists=True), help="Directory with recipe YAML files")
@click.option("--json-output", "as_json", is_flag=True, help="Output as JSON")
def recipe_list(recipe_dir: str | None, as_json: bool):
    """List available recipes.

    Example:
        sweet recipe list
        sweet recipe list -d ./my-recipes
    """
    import json

    from .core.recipes import list_recipes

    recipes = list_recipes(include_builtin=True, recipe_dir=recipe_dir)

    if as_json:
        click.echo(json.dumps(recipes, indent=2))
        return

    if not recipes:
        click.echo("\n  No recipes found.\n")
        return

    click.echo("\n  Available Recipes:\n")
    for r in recipes:
        tags_str = f" [{', '.join(r['tags'])}]" if r.get("tags") else ""
        click.echo(f"    {r['name']}{tags_str}")
        click.echo(f"      {r['description']}")
        click.echo(f"      Steps: {r['steps']}  Source: {r['source']}")
        click.echo()


@recipe.command(name="run")
@click.argument("name")
@click.argument("file", type=click.Path(exists=True))
@click.option("--param", "-p", multiple=True, help="Parameter as key=value")
@click.option("--output", "-o", default=None, type=click.Path(), help="Output CSV path")
@click.option("--stop-on-error/--no-stop-on-error", default=True, help="Stop at first error")
@click.option("--json-output", "as_json", is_flag=True, help="Output result as JSON")
def recipe_run(name: str, file: str, param: tuple, output: str | None, stop_on_error: bool, as_json: bool):
    """Run a recipe against a data file.

    Example:
        sweet recipe run clean-csv data.csv
        sweet recipe run my-recipe.yaml data.csv -p sample_size=500
        sweet recipe run clean-csv data.csv -o cleaned.csv
    """
    import json

    import polars as pl

    from .core.recipes import BUILTIN_RECIPES, execute_recipe, load_recipe

    # Parse params
    params: dict = {}
    for p in param:
        if "=" in p:
            k, v = p.split("=", 1)
            # Try to parse as int/float
            try:
                params[k] = int(v)
            except ValueError:
                try:
                    params[k] = float(v)
                except ValueError:
                    params[k] = v
        else:
            click.echo(f"  ✗ Invalid parameter format: {p} (expected key=value)")
            raise SystemExit(1)

    # Load recipe
    try:
        if name in BUILTIN_RECIPES:
            recipe_obj = BUILTIN_RECIPES[name]
        else:
            recipe_obj = load_recipe(name)
    except Exception as e:
        click.echo(f"  ✗ Failed to load recipe: {e}")
        raise SystemExit(1)

    # Load data
    try:
        df = pl.read_csv(file)
    except Exception as e:
        click.echo(f"  ✗ Failed to read file: {e}")
        raise SystemExit(1)

    # Execute
    result_df, result = execute_recipe(df, recipe_obj, params, stop_on_error=stop_on_error)

    if as_json:
        click.echo(json.dumps(result.to_dict(), indent=2))
    else:
        click.echo(f"\n  Recipe: {result.recipe_name}")
        click.echo(f"  Status: {'✓ Success' if result.success else '✗ Failed'}")
        click.echo(f"  Steps:  {result.steps_completed}/{result.total_steps} completed")
        click.echo()
        for sr in result.step_results:
            icon = "✓" if sr.success else "✗"
            click.echo(f"    {icon} [{sr.action}] {sr.message}")
            if sr.rows_before != sr.rows_after:
                click.echo(f"      Rows: {sr.rows_before} → {sr.rows_after}")
        click.echo()
        if result.error:
            click.echo(f"  Error: {result.error}\n")

    # Write output if requested
    if output and result.success:
        result_df.write_csv(output)
        click.echo(f"  Output written to: {output}")

    if not result.success:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
