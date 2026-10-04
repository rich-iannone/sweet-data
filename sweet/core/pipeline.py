"""Pipelines: an ordered list of steps over a source, persisted as YAML.

A pipeline is what a Sweet session *means*: where the data came from and every
step applied to it. It can be saved (``pipeline.sweet.yaml``), replayed on new
data, and exported as a Polars script or a DuckDB SQL query.

Example file::

    version: 1
    name: clean-orders
    source:
      path: orders.csv
      format: csv
    steps:
      - id: 3f2a1c9e
        kind: filter
        params:
          sql: revenue > 0
      - id: 8b1d0e4f
        kind: cast
        params:
          columns:
            created_at: Date
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import polars as pl

from .io import READERS, format_for_path, read_file
from .steps import NotExportable, Step, StepError, q

PIPELINE_VERSION = 1
PIPELINE_SUFFIX = ".sweet.yaml"


@dataclass
class Pipeline:
    """A source plus ordered steps.

    Attributes:
        steps: Steps in application order (disabled steps are skipped).
        source: Where the input comes from, e.g. ``{"path": "a.csv", "format": "csv"}``.
            ``None`` means the input DataFrame must be supplied to `run()`.
        name: Optional pipeline name.
        schema: Optional input schema ({column: dtype name}), used for SQL
            rendering and for detecting input drift.
        requires: Plugins the pipeline depends on ({name: version spec}).
    """

    steps: list[Step] = field(default_factory=list)
    source: dict[str, Any] | None = None
    name: str | None = None
    schema: dict[str, str] | None = None
    requires: dict[str, str] = field(default_factory=dict)

    @property
    def active_steps(self) -> list[Step]:
        return [s for s in self.steps if s.enabled]

    # -- execution -------------------------------------------------------------

    def load_source(self) -> pl.DataFrame:
        if not self.source or "path" not in self.source:
            raise StepError("Pipeline has no file source; pass a DataFrame to run()")
        return read_file(self.source["path"], self.source.get("format"))

    def run(self, df: pl.DataFrame | None = None, *, upto: int | None = None) -> pl.DataFrame:
        """Apply the pipeline's steps.

        Args:
            df: Input data. Loaded from `source` if omitted.
            upto: Only apply the first `upto` steps (counting disabled ones),
                to view the data at an intermediate point.
        """
        if df is None:
            df = self.load_source()
        for step in self.steps[:upto]:
            if step.enabled:
                df = step.apply(df)
        return df

    # -- export ----------------------------------------------------------------

    def to_polars_script(self, *, output: str | None = None) -> str:
        """Render a standalone Python script that reproduces the pipeline."""
        steps = self.active_steps
        lines = ["import polars as pl"]
        if any(s.kind == "sql" for s in steps):
            lines.append("import duckdb")
        lines.append("")

        if self.source and "path" in self.source:
            path = self.source["path"]
            fmt = self.source.get("format") or format_for_path(path)
            lines.append(f"df = {READERS.get(fmt).code(path)}")
        else:
            lines.append("df = ...  # input DataFrame")

        for i, step in enumerate(steps, 1):
            lines.append("")
            lines.append(f"# Step {i}: {step.label}")
            lines.append(step.to_polars_statements())

        if output:
            from .io import WRITERS

            lines.append("")
            lines.append(WRITERS.get(format_for_path(output, WRITERS)).code(output))
        return "\n".join(lines) + "\n"

    def _sql_ctes(self, source_ref: str) -> tuple[list[tuple[str, str]], str]:
        """[(cte name, query)] for the source and each active step, plus the last name.

        Raises:
            NotExportable: If any active step has no SQL rendering.
        """
        columns = list(self.schema) if self.schema else None
        sample = self._empty_input()
        ctes = [("source", f"SELECT * FROM {source_ref}")]
        prev = q("source")
        problems = []
        for i, step in enumerate(self.active_steps, 1):
            try:
                ctes.append((f"step_{i}", step.to_sql(prev, columns)))
            except NotExportable as e:
                problems.append(str(e))
            prev = q(f"step_{i}")
            try:
                sample = step.apply(sample) if sample is not None else None
                columns = sample.columns if sample is not None else None
            except StepError:
                sample, columns = None, None
        if problems:
            raise NotExportable("\n".join(problems))
        return ctes, ctes[-1][0]

    def to_sql(self, *, source_ref: str | None = None) -> str:
        """Render the pipeline as a single DuckDB query built from CTEs.

        Args:
            source_ref: SQL expression for the input relation. Defaults to a
                DuckDB table function reading the source file.

        Raises:
            NotExportable: If any active step has no SQL rendering.
        """
        ctes, last = self._sql_ctes(source_ref or self._sql_source_ref())
        body = ",\n  ".join(f"{q(name)} AS ({query})" for name, query in ctes)
        return f"WITH\n  {body}\nSELECT * FROM {q(last)}"

    def to_dbt(self, *, source_name: str = "raw", table: str | None = None) -> str:
        """Render the pipeline as a dbt model (SQL over ``{{ source(...) }}``).

        Pair it with a sources entry declaring `source_name`.`table` (see
        `to_dbt_sources()`).

        Raises:
            NotExportable: If any active step has no SQL rendering.
        """
        table = table or self.name or "data"
        ref = f"{{{{ source('{source_name}', '{table}') }}}}"
        ctes, last = self._sql_ctes(ref)
        lines = [f"-- Generated by Sweet from pipeline '{self.name or table}'", ""]
        lines.append("with")
        for i, (name, query) in enumerate(ctes):
            comma = "," if i < len(ctes) - 1 else ""
            label = self.active_steps[i - 1].label if i else "source"
            lines.append(f"-- {label}")
            lines.append(f"{q(name)} as (\n    {query}\n){comma}")
        lines.append(f"select * from {q(last)}")
        return "\n".join(lines) + "\n"

    def to_dbt_sources(self, *, source_name: str = "raw", table: str | None = None) -> str:
        """A dbt `sources.yml` declaring the pipeline's input."""
        from yaml12 import format_yaml

        table = table or self.name or "data"
        entry: dict[str, Any] = {"name": table}
        if self.source and "path" in self.source:
            entry["meta"] = {"sweet_source": self.source["path"]}
        if self.schema:
            entry["columns"] = [{"name": c, "data_type": t} for c, t in self.schema.items()]
        return format_yaml({"version": 2, "sources": [{"name": source_name, "tables": [entry]}]})

    def to_marimo(self) -> str:
        """Render the pipeline as a marimo notebook: one cell per step.

        Each step produces its own frame (`df_1`, `df_2`, ...), because marimo
        cells can't redefine each other's variables.
        """
        steps = self.active_steps
        uses_duckdb = any(s.kind == "sql" for s in steps)
        imports = ["import polars as pl"] + (["import duckdb"] if uses_duckdb else [])
        modules = "pl, duckdb" if uses_duckdb else "pl"

        def cell(args: str, body: list[str], returns: str) -> list[str]:
            return [
                "",
                "@app.cell",
                f"def _({args}):",
                *[f"    {line}" if line else "" for line in body],
                f"    return ({returns},)",
                "",
            ]

        out = ["import marimo", "", '__generated_with = "sweet"', "app = marimo.App()", ""]
        out += cell("", imports, modules)
        if self.source and "path" in self.source:
            path = self.source["path"]
            fmt = self.source.get("format") or format_for_path(path)
            load = READERS.get(fmt).code(path)
        else:
            load = "pl.DataFrame()  # replace with your input data"
        out += cell(modules, [f"df_0 = {load}", "df_0"], "df_0")
        for i, step in enumerate(steps, 1):
            body = [f"# Step {i}: {step.label}", "def _step(df):"]
            body += [f"    {line}" for line in step.to_polars_statements().splitlines()]
            body += ["    return df", "", f"df_{i} = _step(df_{i - 1})", f"df_{i}"]
            out += cell(f"df_{i - 1}, {modules}", body, f"df_{i}")
        out += ["", 'if __name__ == "__main__":', "    app.run()", ""]
        return "\n".join(out)

    def _sql_source_ref(self) -> str:
        if not self.source or "path" not in self.source:
            return q("input")
        path = self.source["path"].replace("'", "''")
        fmt = self.source.get("format") or format_for_path(path)
        fn = {"csv": "read_csv_auto", "parquet": "read_parquet", "json": "read_json_auto",
              "ndjson": "read_json_auto"}.get(fmt)  # fmt: skip
        if fn is None:
            return q("input")
        return f"{fn}('{path}')"

    def _empty_input(self) -> pl.DataFrame | None:
        """An empty frame with the input schema, for propagating column names."""
        if not self.schema:
            return None
        from .steps import parse_dtype

        try:
            return pl.DataFrame(schema={c: parse_dtype(t) for c, t in self.schema.items()})
        except StepError:
            return None

    # -- serialization ---------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"version": PIPELINE_VERSION}
        if self.name:
            d["name"] = self.name
        if self.requires:
            d["requires"] = dict(self.requires)
        if self.source:
            d["source"] = dict(self.source)
        if self.schema:
            d["schema"] = dict(self.schema)
        d["steps"] = [s.to_dict() for s in self.steps]
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Pipeline:
        version = d.get("version", PIPELINE_VERSION)
        if version > PIPELINE_VERSION:
            raise StepError(
                f"Pipeline file version {version} is newer than this Sweet supports "
                f"({PIPELINE_VERSION}). Upgrade Sweet to run it."
            )
        return cls(
            steps=[Step.from_dict(s) for s in d.get("steps") or []],
            source=d.get("source"),
            name=d.get("name"),
            schema=d.get("schema"),
            requires=dict(d.get("requires") or {}),
        )

    def to_yaml(self) -> str:
        from yaml12 import format_yaml

        return format_yaml(self.to_dict())

    @classmethod
    def from_yaml(cls, text: str) -> Pipeline:
        from yaml12 import parse_yaml

        data = parse_yaml(text)
        if not isinstance(data, dict):
            raise StepError("Pipeline YAML must be a mapping")
        return cls.from_dict(data)

    def save(self, path: str | Path) -> Path:
        path = Path(path)
        path.write_text(self.to_yaml())
        return path

    @classmethod
    def load(cls, path: str | Path) -> Pipeline:
        return cls.from_yaml(Path(path).read_text())
