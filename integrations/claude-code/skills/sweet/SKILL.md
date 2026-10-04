---
name: sweet
description: Work on tabular data (CSV, Parquet, JSON, Excel, S3, Hugging Face datasets) in the Sweet terminal viewer through its MCP tools. Use when the user asks to explore, clean, profile, reshape, or compare data, wants to see the data while you work, or mentions Sweet.
---

# Working in Sweet

Sweet is a terminal data viewer that you and the person use together. When it's running,
the `sweet` MCP tools attach to it: the person sees every row you look at, every column you
highlight, and every change you propose, as a diff they accept or reject.

## Start

1. Call `status`. It tells you the agent mode, who has control, the open sheets, and any
   masks.
   - `read-only`: look and explain; don't propose changes.
   - `propose` (the default): your changes become proposals the person reviews.
   - `auto`: your steps apply directly (they're still undoable).
2. If no sheet is open, `open` the data. If no viewer is attached (`status` shows
   `"ui": false`) and the person wants to watch, use `launch_session` (inside tmux) or ask
   them to run `sweet <data>` and try again.

## Look before you change anything

- `sheets` for shapes and types, `profile` for distributions and nulls, `view` for rows.
- Use `view` with `where`, `sort`, and `columns` to look around. It doesn't change data.
- Use `query` (read-only SQL; tables are named after sheets) for counts and checks.
- Use `get_selection` when the person refers to "these rows" or "this column".

## Change data with steps

- Use `propose_step` with a structured step. Prefer SQL expressions, because they export to
  both Polars and SQL:
  `{"kind": "filter", "params": {"sql": "amount > 0"}}`,
  `{"kind": "mutate", "params": {"column": "region", "sql": "UPPER(TRIM(region))"}}`.
- One logical change per step, with a clear label. The response previews the effect (rows
  removed, cells changed); check that it matches your intent before moving on.
- In `propose` mode, tell the person what you proposed and wait for them; `steps` shows
  whether it was accepted. Don't propose a chain of dependent steps they haven't reviewed.

## Point and explain

- `highlight` a row (use the `#` row id from `view`), a column, or a cell with a short
  `note` to show the person what you mean. Clear your highlights when you're done.
- For walkthroughs, `demo` with action `start` (mode `step` lets the person advance with
  space), `narrate` each point, and `end` afterwards.

## Respect masks and control

- Masked columns show as `•••`, hashes (`h:...`), or partial values. You can filter, count,
  and join on them, but you can't see their values. Never try to infer or reconstruct masked
  values, and don't try to unmask: only the person can.
- If the person asks you to protect personal data, call `set_policy` with `mask_pii: true`
  (or `mask` a specific column) before looking at the data.
- If a call fails because the person took control, stop and wait. They resume you when
  they're ready.

## Finish

- Summarize what changed (`steps`), and offer `export` of the pipeline (`pipeline`,
  `polars`, `sql`, `dbt`, or `marimo`) so the work is reproducible.
