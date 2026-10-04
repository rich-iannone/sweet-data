![Sweet](assets/sweet-logo.svg)

_The agent-native data workbench for your terminal_

<div align="left">

[![Python Versions](https://img.shields.io/pypi/pyversions/sweet-data.svg)](https://pypi.python.org/pypi/sweet-data)
[![PyPI](https://img.shields.io/pypi/v/sweet-data)](https://pypi.org/project/sweet-data/#history)
[![PyPI Downloads](https://static.pepy.tech/badge/sweet-data)](https://pepy.tech/projects/sweet-data)
[![License](https://img.shields.io/github/license/rich-iannone/sweet-data)](https://img.shields.io/github/license/rich-iannone/sweet-data)

[![CI Build](https://github.com/rich-iannone/sweet-data/actions/workflows/ci.yaml/badge.svg)](https://github.com/rich-iannone/sweet-data/actions/workflows/ci.yaml)
[![Repo Status](https://www.repostatus.org/badges/latest/active.svg)](https://www.repostatus.org/#active)
[![Contributor Covenant](https://img.shields.io/badge/Contributor%20Covenant-v2.1%20adopted-ff69b4.svg)](https://www.contributor-covenant.org/version/2/1/code_of_conduct.html)

</div>

Sweet is the only data tool that is simultaneously **interactive** (human-friendly TUI), **programmable** (Python SDK), **agent-native** (MCP server), and **pipeline-aware** (reproducible transforms that export to production code). It's a terminal-native data workspace designed from the ground up to be operated by both humans and AI agents with the same fidelity.

Load a dataset, ask an agent to clean it, visually inspect the results, branch off an experiment, and ship a reproducible pipeline — all without leaving your terminal.

## Four Surfaces, One Engine

```
┌──────────────────────────────────────────────────────────────┐
│                       SWEET PLATFORM                          │
├──────────┬──────────┬────────────┬───────────────────────────┤
│  TUI     │   MCP    │  Python    │  HTTP API                 │
│ (human)  │ (agents) │   SDK      │ (automation)              │
├──────────┴──────────┴────────────┴───────────────────────────┤
│                    WORKSPACE ENGINE                           │
│  Polars · DuckDB · undo/redo · profiling · quality rules     │
│  contracts · versioning · codegen · connectors · agents      │
└──────────────────────────────────────────────────────────────┘
```

| Surface | For | Launch |
|---------|-----|--------|
| **TUI** | Interactive exploration and editing | `sweet data.parquet` |
| **MCP Server** | AI agents (Claude, Copilot, Cursor, custom) | `sweet mcp` |
| **Python SDK** | Scripts, notebooks, programmatic workflows | `from sweet import Workspace` |
| **HTTP API** | Automation, dashboards, remote access | `sweet serve --http` |

All four surfaces drive the same workspace engine — same transforms, same undo/redo, same quality rules.

## Quick Start

```bash
pip install sweet-data
sweet
```

### As a human

Open anything. Large, remote, and multi-file data is scanned lazily, so it appears
immediately:

```bash
sweet sales.csv
sweet 'logs/2026-*.parquet' customers.xlsx   # several sheets
sweet exports/                               # a directory (e.g. hive-partitioned Parquet)
sweet s3://bucket/events/*.parquet
sweet hf://datasets/org/name
cat data.csv | sweet
```

Each column header shows its type, null share, and a sparkline of its distribution. Press
`i` for the column inspector, `o` for an overview of every column, `s` to sort, `f` to
filter to the value under the cursor, `u` to undo, and `Ctrl+P` for every command.

Every change is a step. Press `n` to write one in SQL or Polars and see exactly what it
would change before you accept it (removed rows struck through, changed cells
highlighted). Press `t` for the steps panel: view the data as of any step, toggle, edit,
reorder, or remove steps, or branch a new sheet from any point.

The previous spreadsheet UI (with the AI chat panel and database browser) is still
available with `sweet --classic`.

### With an agent, live

Open your data in one terminal pane and your agent (Claude Code, Cursor, or any MCP client)
in another. `sweet mcp` attaches the agent to the running viewer: you see every row it
reads and every column it highlights, and its changes arrive as proposals you accept or
reject as diffs.

```bash
sweet customers.parquet        # pane 1: the viewer
claude                         # pane 2: an agent with the sweet MCP server (see integrations/)
```

- `M` cycles the agent's mode: read-only → propose (default) → auto. `ctrl+x` stops it.
- `m` masks the column under the cursor from agents (they see `•••`, you see the data).
  `sweet --mask-pii` masks everything that looks like personal data. Agents can tighten
  masks but never remove them, and masks follow renamed and derived columns.
- In demos, `space` steps the agent forward, `+`/`-` change its pace, any other key takes
  control, and `ctrl+r` hands it back.

Agents get a small tool set (`status`, `view`, `profile`, `query`, `propose_step`, `steps`,
`diff`, `highlight`, `screen`, `command`, ...), about a fifth of the context of the older
70-tool server (`sweet mcp --labs` still offers those). Install the Claude Code plugin with:

```bash
claude plugin marketplace add rich-iannone/sweet-data
claude plugin install sweet@sweet-data
```

Configs for Claude Desktop, Cursor, and VS Code are in [integrations/](integrations/README.md).

### As a script

```python
from sweet import Workspace

ws = Workspace()
ws.load("sales.csv")
ws.transform("df.filter(pl.col('revenue') > 0)")
print(ws.describe())
ws.export("cleaned.parquet")
ws.save_pipeline("sales.sweet.yaml")  # Replayable steps
print(ws.generate_code())  # Reproducible Polars code
```

## See It in Action

### Interactive Editing

![Loading data and editing values](assets/open-dataset-modify-cell-values.gif)

### AI-Powered Transforms

![AI-powered data discussion and transformation](assets/ai-data-discuss-transform.gif)

### Polars Expressions

![Loading data and modifying with Polars](assets/load-data-modify-with-polars.gif)

## Core Capabilities

### TUI (Interactive)

- **Spreadsheet-style editing** — click cells, add/remove rows and columns
- **AI chat assistant** — natural language transforms via Claude or GPT
- **Polars code panel** — write and apply expressions directly
- **Database connections** — connect, browse tables, run SQL
- **Multi-format I/O** — load/save CSV, JSON, Parquet, Excel; paste from clipboard
- **Find and filter** — search within columns, navigate large datasets

### Headless Engine (SDK / MCP / CLI)

- **Multi-sheet workbooks** with branching and merge
- **Full undo/redo** with operation journal and time-travel
- **Schema contracts** — infer, enforce, validate on every transform
- **Data quality rules** — YAML-based, severity levels, CI-friendly
- **Auto-profiling** — statistics, distributions, PII detection, anomalies
- **Version control** — commit, diff, log, checkout for your data
- **Built-in agent** that plans, executes, validates, and rolls back
- **Recipes** — reusable YAML workflows, shareable across teams

### Pipeline Generation

Every change you make is recorded as a step. In the TUI, `:pipeline` saves the session to a
`.sweet.yaml` file that you can replay or export as code:

```python
from sweet.core.pipeline import Pipeline

p = Pipeline.load("sales.sweet.yaml")
df = p.run()                 # Replay on the original source
print(p.to_polars_script())  # Standalone Polars script
print(p.to_sql())            # A single DuckDB query (if every step has a SQL form)
```

Pipelines also run and compile from the command line:

```bash
sweet run sales.sweet.yaml -i sales_2026.csv -o clean_2026.parquet   # replay on new data
sweet compile sales.sweet.yaml --to polars -o clean.py               # or sql, dbt, marimo
sweet diff before.parquet after.parquet --key id                     # what changed?
```

`sweet generate` writes pipeline code for a file after running a recipe or steps:

```bash
sweet generate sales.csv --format polars -r clean-csv
sweet generate sales.csv --format sql
sweet generate sales.csv --format dbt
```

### Connectors

Load from and export to: CSV, TSV, JSON, NDJSON, Parquet, Arrow/Feather, Excel, URLs (including HTML tables on web pages), S3/GCS/Azure object storage, PostgreSQL, MySQL, SQLite, and DuckDB.

### Integrations

- **Great Tables** — export to publication-quality HTML tables
- **Jupyter/Marimo** — inline interactive widget
- **Pointblank** — data validation with rich reporting

## AI Assistant

Sweet's built-in AI assistant understands your data and generates Polars transformations from natural language:

- "Filter rows where revenue is negative"
- "Cast the date column and add a month-over-month growth rate"
- "Deduplicate on email, keeping the most recent entry"

The assistant sees your schema, column types, and data profile — so it generates code that works on the first try.

### Setup

```bash
# .env in your working directory
ANTHROPIC_API_KEY=your_key_here
# or
OPENAI_API_KEY=your_key_here
```

## How Sweet Compares

| Tool | Focus | Sweet's Advantage |
|------|-------|-------------------|
| Jupyter | Notebooks, exploration | Interactive-first, no cell management |
| Excel/Sheets | GUI spreadsheets | Terminal-native, scriptable, reproducible |
| dbt | Transform pipelines | Interactive; generates dbt as output |
| Pandas/Polars | Code-first data work | Adds interactivity + AI on top of Polars |
| VisiData | Terminal data viewer | Adds AI, agents, reproducibility, ecosystem |
| csvkit | CLI CSV tools | Interactive + AI + multi-format |

## Built On

- **[Polars](https://pola.rs)** — high-performance DataFrame engine
- **[DuckDB](https://duckdb.org)** — embedded analytical SQL
- **[Textual](https://textual.textualize.io)** — modern terminal UI framework
- **[chatlas](https://posit-dev.github.io/chatlas/)** — multi-provider LLM integration
- **[Rich](https://github.com/Textualize/rich)** — terminal formatting
- **[Click](https://github.com/pallets/click)** — CLI framework

## Contributing

There are many ways to contribute — from fixing typos and filing issues to submitting PRs with code changes. All contributions are appreciated.

## Code of Conduct

This project is released with a [Contributor Code of Conduct](https://www.contributor-covenant.org/version/2/1/code_of_conduct/). By participating you agree to abide by its terms.

## License

MIT © sweet-data authors

## Governance

Maintained by [Rich Iannone](https://bsky.app/profile/richmeister.bsky.social).
