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

    def to_sql(self, *, source_ref: str | None = None) -> str:
        """Render the pipeline as a single DuckDB query built from CTEs.

        Args:
            source_ref: SQL expression for the input relation. Defaults to a
                DuckDB table function reading the source file.

        Raises:
            NotExportable: If any active step has no SQL rendering.
        """
        if source_ref is None:
            source_ref = self._sql_source_ref()

        columns = list(self.schema) if self.schema else None
        sample = self._empty_input()
        ctes = [f"{q('source')} AS (SELECT * FROM {source_ref})"]
        prev = q("source")
        problems = []
        for i, step in enumerate(self.active_steps, 1):
            try:
                ctes.append(f"{q(f'step_{i}')} AS ({step.to_sql(prev, columns)})")
            except NotExportable as e:
                problems.append(str(e))
            prev = q(f"step_{i}")
            if sample is not None:
                sample = step.apply(sample)
                columns = sample.columns
            else:
                columns = None
        if problems:
            raise NotExportable("\n".join(problems))
        return "WITH\n  " + ",\n  ".join(ctes) + f"\nSELECT * FROM {prev}"

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
