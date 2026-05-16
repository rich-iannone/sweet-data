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
        click.echo(f"  Steps: {result.n_passed} passed, {result.n_failed} failed, "
                   f"{result.n_rolled_back} rolled back\n")
        for step in result.steps:
            s_icon = {"passed": "✓", "failed": "✗", "rolled_back": "↩", "skipped": "⊘"}.get(
                step.status.value, "?"
            )
            click.echo(f"    {s_icon} {step.step_name}: {step.message}")
        click.echo(f"\n  {result.summary}\n")


@main.command(name="list-recipes")
def list_recipes():
    """List all available recipes.

    Example:
        sweet list-recipes
    """
    from .agents import RecipeRegistry

    registry = RecipeRegistry()
    recipes = registry.list()

    click.echo("\n  Available recipes:\n")
    for r in recipes:
        click.echo(f"    {r['key']}")
        click.echo(f"      {r['description']}")
        click.echo(f"      Steps: {' → '.join(r['steps'])}\n")


@main.command()
@click.argument("file", type=click.Path(exists=True))
@click.argument("steps", nargs=-1, required=True)
@click.option("--format", "fmt", type=click.Choice(["text", "json"]), default="text")
@click.option("--no-validate", is_flag=True, help="Skip validation between steps")
@click.option("--no-rollback", is_flag=True, help="Don't rollback on failure")
def run(file: str, steps: tuple[str, ...], fmt: str, no_validate: bool, no_rollback: bool):
    """Run a sequence of agent steps on a data file.

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
        click.echo(f"\n  {icon} Agent run: {result.n_passed} passed, "
                   f"{result.n_failed} failed, {result.n_rolled_back} rolled back")
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
    click.echo(f"    Run history:  {info['n_run_records']} ({info['n_successful_runs']} successful)")
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
                    f"  ✗ Unknown stage: '{stage_name}'. "
                    f"Available: {', '.join(agent_map.keys())}",
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


if __name__ == "__main__":
    main()
