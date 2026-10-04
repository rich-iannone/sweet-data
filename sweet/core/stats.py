"""Column statistics for headers, the inspector, and agents.

`summarize()` profiles columns of a DataFrame or LazyFrame in two passes,
regardless of the number of columns:

1. Counts, nulls, approximate distinct values, min/max, and moments.
2. Histograms (numeric and temporal columns) and top values (everything else).

Both passes run on Polars' streaming engine, so they work on data larger
than memory.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

import polars as pl

SPARK_CHARS = "▁▂▃▄▅▆▇█"


def column_kind(dtype: pl.DataType) -> str:
    """Classify a dtype as numeric, temporal, boolean, string, or other."""
    if dtype.is_numeric():
        return "numeric"
    if dtype.is_temporal():
        return "temporal"
    if dtype == pl.Boolean:
        return "boolean"
    if dtype in (pl.String, pl.Categorical) or isinstance(dtype, (pl.Enum, pl.Categorical)):
        return "string"
    return "other"


@dataclass
class ColumnStats:
    """Summary of one column.

    `histogram` holds bin counts over [min, max] (numeric/temporal), and
    `bin_edges` the bins' lower edges plus the final upper edge. `top_values`
    holds (value, count) pairs, most frequent first (string/boolean/other).
    """

    name: str
    dtype: str
    kind: str
    count: int
    null_count: int
    n_unique: int | None = None
    min: Any = None
    max: Any = None
    mean: float | None = None
    std: float | None = None
    quantiles: dict[str, Any] = field(default_factory=dict)
    histogram: list[int] | None = None
    bin_edges: list[Any] | None = None
    top_values: list[tuple[Any, int]] | None = None

    @property
    def null_fraction(self) -> float:
        return self.null_count / self.count if self.count else 0.0

    def sparkline(self, width: int = 8) -> str:
        """A compact unicode rendering of the distribution."""
        if self.histogram:
            return sparkline(_rebin(self.histogram, width))
        if self.top_values:
            counts = [c for _, c in self.top_values[:width]]
            return sparkline(counts)
        return ""

    def to_dict(self) -> dict[str, Any]:
        """JSON-friendly form (for agents and the CLI)."""
        d = {k: _jsonable(v) for k, v in self.__dict__.items()}
        d["null_fraction"] = round(self.null_fraction, 6)
        if self.top_values is not None:
            d["top_values"] = [[_jsonable(v), c] for v, c in self.top_values]
        return d


def sparkline(values: list[int | float]) -> str:
    if not values:
        return ""
    peak = max(values)
    if peak <= 0:
        return SPARK_CHARS[0] * len(values)
    last = len(SPARK_CHARS) - 1
    return "".join(
        " " if v == 0 else SPARK_CHARS[max(0, math.ceil(v / peak * last))] for v in values
    )


def _rebin(counts: list[int], width: int) -> list[int]:
    if len(counts) <= width:
        return counts
    size = len(counts) / width
    return [sum(counts[int(i * size) : int((i + 1) * size)]) for i in range(width)]


def _jsonable(value: Any) -> Any:
    if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
        return None
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    if isinstance(value, dict):
        return {k: _jsonable(v) for k, v in value.items()}
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def summarize(
    frame: pl.DataFrame | pl.LazyFrame,
    columns: list[str] | None = None,
    *,
    bins: int = 20,
    top: int = 10,
) -> dict[str, ColumnStats]:
    """Compute `ColumnStats` for `columns` (default: all) of `frame`."""
    lf = frame.lazy()
    schema = lf.collect_schema()
    names = [c for c in (columns or schema.names()) if c in schema]
    if not names:
        return {}

    # Pass 1: one row of aggregates for all columns
    aggs: list[pl.Expr] = [pl.len().alias("__len")]
    for i, name in enumerate(names):
        col = pl.col(name)
        kind = column_kind(schema[name])
        aggs.append(col.null_count().alias(f"{i}_nulls"))
        if _countable(schema[name]):
            distinct = col.to_physical() if kind == "temporal" else col
            aggs.append(distinct.approx_n_unique().alias(f"{i}_unique"))
        if kind in ("numeric", "temporal"):
            aggs += [col.min().alias(f"{i}_min"), col.max().alias(f"{i}_max")]
        if kind == "numeric" and schema[name] != pl.Boolean:
            as_float = col.cast(pl.Float64)
            aggs += [
                as_float.mean().alias(f"{i}_mean"),
                as_float.std().alias(f"{i}_std"),
                *[
                    as_float.quantile(q, interpolation="nearest").alias(f"{i}_q{int(q * 100)}")
                    for q in (0.25, 0.5, 0.75)
                ],
            ]
    first = lf.select(aggs).collect(engine="streaming").row(0, named=True)
    count = first["__len"]

    stats: dict[str, ColumnStats] = {}
    for i, name in enumerate(names):
        dtype = schema[name]
        kind = column_kind(dtype)
        st = ColumnStats(
            name=name,
            dtype=str(dtype),
            kind=kind,
            count=count,
            null_count=first[f"{i}_nulls"],
            n_unique=first.get(f"{i}_unique"),
            min=first.get(f"{i}_min"),
            max=first.get(f"{i}_max"),
            mean=first.get(f"{i}_mean"),
            std=first.get(f"{i}_std"),
        )
        st.quantiles = {
            f"q{p}": first[f"{i}_q{p}"] for p in (25, 50, 75) if f"{i}_q{p}" in first
        }
        stats[name] = st

    # Pass 2: histograms and top values, each imploded to a single-row list
    second: list[pl.Expr] = []
    plans: dict[str, tuple[str, Any]] = {}
    for i, name in enumerate(names):
        st = stats[name]
        col = pl.col(name)
        if st.kind in ("numeric", "temporal") and st.min is not None and st.max is not None:
            physical = col.to_physical().cast(pl.Float64) if st.kind == "temporal" else col
            lo, hi = _physical(st.min, schema[name]), _physical(st.max, schema[name])
            if schema[name].is_integer() and hi - lo + 1 <= bins:
                # Small integer ranges get one bin per value
                n_bins, width = int(hi - lo) + 1, 1.0
            else:
                n_bins = 1 if hi == lo else bins
                width = (hi - lo) / n_bins if hi != lo else 1.0
            bin_index = (
                ((physical.cast(pl.Float64) - lo) / width).floor().clip(0, n_bins - 1).cast(pl.Int32)
            )
            second.append(
                bin_index.drop_nulls().value_counts().implode().alias(f"{i}_hist")
            )
            plans[name] = ("hist", (lo, width, n_bins))
        elif st.kind in ("string", "boolean", "other") and _countable(schema[name]):
            second.append(
                col.value_counts(sort=True, name="__count").head(top).implode().alias(f"{i}_top")
            )
            plans[name] = ("top", None)
    if second:
        row = lf.select(second).collect(engine="streaming").row(0, named=True)
        for i, name in enumerate(names):
            plan = plans.get(name)
            if plan is None:
                continue
            st = stats[name]
            if plan[0] == "hist":
                lo, width, n_bins = plan[1]
                counts = [0] * n_bins
                for entry in row[f"{i}_hist"] or []:
                    values = list(entry.values())
                    counts[int(values[0])] = int(values[1])
                st.histogram = counts
                st.bin_edges = [_from_physical(lo + k * width, schema[name]) for k in range(n_bins + 1)]
            else:
                pairs = []
                for entry in row[f"{i}_top"] or []:
                    pairs.append((entry[name], int(entry["__count"])))
                st.top_values = pairs
    return stats


def _countable(dtype: pl.DataType) -> bool:
    """Whether value_counts is meaningful (not nested types)."""
    return not isinstance(dtype, (pl.List, pl.Array, pl.Struct, pl.Object))


def _physical(value: Any, dtype: pl.DataType) -> float:
    if dtype.is_temporal():
        return float(pl.Series([value], dtype=dtype).to_physical()[0])
    return float(value)


def _from_physical(value: float, dtype: pl.DataType) -> Any:
    if dtype.is_temporal():
        physical_dtype = pl.Series([], dtype=dtype).to_physical().dtype
        return pl.Series([int(value)]).cast(physical_dtype).cast(dtype)[0]
    return value
