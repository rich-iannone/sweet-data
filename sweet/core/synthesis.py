"""Data synthesis and augmentation for Sweet.

Generate realistic synthetic data matching a DataFrame's schema and
statistical profile, impute missing values, and augment datasets
with derived columns.
"""

from __future__ import annotations

import math
import random
import string
from datetime import timedelta

import polars as pl

# ---------------------------------------------------------------------------
# Synthesize: generate realistic data matching a schema + profile
# ---------------------------------------------------------------------------


def synthesize(
    source: pl.DataFrame,
    rows: int = 1000,
    *,
    seed: int | None = None,
) -> pl.DataFrame:
    """Generate synthetic data that mirrors the schema and distribution of *source*.

    For each column the generator picks a strategy based on dtype and
    observed value patterns:

    * **Categorical strings** (low cardinality) — sample from observed values.
    * **Free-text strings** — generate random strings of similar length.
    * **Integers / floats** — sample from a normal distribution fitted to the
      column's mean/std, clipped to observed min/max.
    * **Dates / datetimes** — uniform random within the observed range.
    * **Booleans** — Bernoulli with the observed true-ratio.

    Null values are injected at approximately the same rate as the source.

    Parameters
    ----------
    source
        The template DataFrame whose schema and profile are mimicked.
    rows
        Number of rows to generate.
    seed
        Optional random seed for reproducibility.

    Returns
    -------
    pl.DataFrame
        A new DataFrame with *rows* rows and the same columns/dtypes.
    """
    if source.is_empty():
        raise ValueError("Cannot synthesize from an empty DataFrame")

    rng = random.Random(seed)
    columns: dict[str, pl.Series] = {}

    for col in source.columns:
        series = source[col]
        dtype = series.dtype
        null_rate = series.null_count() / len(series) if len(series) > 0 else 0.0

        raw_values = _generate_column(series, dtype, rows, rng)

        # Inject nulls at observed rate
        if null_rate > 0:
            mask = [rng.random() < null_rate for _ in range(rows)]
            raw_values = [None if m else v for m, v in zip(mask, raw_values)]

        # Build series; let Polars infer then cast to preserve dtype with nulls
        try:
            columns[col] = pl.Series(col, raw_values, dtype=dtype)
        except TypeError:
            columns[col] = pl.Series(col, raw_values).cast(dtype)

    return pl.DataFrame(columns)


def _generate_column(
    series: pl.Series, dtype: pl.DataType, rows: int, rng: random.Random
) -> list:
    """Generate a list of values for a single column."""
    non_null = series.drop_nulls()

    if len(non_null) == 0:
        return [None] * rows

    # Boolean
    if dtype == pl.Boolean:
        true_rate = non_null.sum() / len(non_null)
        return [rng.random() < true_rate for _ in range(rows)]

    # Date
    if dtype == pl.Date:
        dates = non_null.to_list()
        min_d, max_d = min(dates), max(dates)
        delta_days = (max_d - min_d).days
        if delta_days == 0:
            return [min_d] * rows
        return [min_d + timedelta(days=rng.randint(0, delta_days)) for _ in range(rows)]

    # Datetime
    if dtype == pl.Datetime:
        dts = non_null.cast(pl.Int64).to_list()
        min_ts, max_ts = min(dts), max(dts)
        if min_ts == max_ts:
            return non_null.to_list()[:1] * rows
        raw = [rng.randint(min_ts, max_ts) for _ in range(rows)]
        return pl.Series("_tmp", raw, dtype=pl.Int64).cast(dtype).to_list()

    # String
    if dtype == pl.Utf8:
        return _generate_string_column(non_null, rows, rng)

    # Numeric (int or float)
    if dtype.is_numeric():
        return _generate_numeric_column(non_null, dtype, rows, rng)

    # Fallback: sample from observed values
    values = non_null.to_list()
    return [rng.choice(values) for _ in range(rows)]


def _generate_string_column(
    non_null: pl.Series, rows: int, rng: random.Random
) -> list[str]:
    """Generate string values — sample if categorical, else random strings."""
    values = non_null.to_list()
    n_unique = non_null.n_unique()
    n_total = len(non_null)

    # Low cardinality → sample from observed values
    if n_unique <= max(20, n_total * 0.1):
        return [rng.choice(values) for _ in range(rows)]

    # High cardinality → generate random strings of similar length
    lengths = [len(str(v)) for v in values[:100]]
    avg_len = max(1, int(sum(lengths) / len(lengths)))
    charset = string.ascii_lowercase + string.digits
    return ["".join(rng.choices(charset, k=avg_len)) for _ in range(rows)]


def _generate_numeric_column(
    non_null: pl.Series, dtype: pl.DataType, rows: int, rng: random.Random
) -> list:
    """Generate numeric values from fitted normal distribution."""
    values = non_null.cast(pl.Float64).to_list()
    n_unique = len(set(values))

    # Very low cardinality (e.g., 0/1 flags) → sample directly
    if n_unique <= 10:
        if dtype.is_integer():
            return [int(v) for v in [rng.choice(values) for _ in range(rows)]]
        return [rng.choice(values) for _ in range(rows)]

    mean = sum(values) / len(values)
    variance = sum((v - mean) ** 2 for v in values) / len(values)
    std = math.sqrt(variance) if variance > 0 else 0.0
    min_val, max_val = min(values), max(values)

    if std == 0:
        if dtype.is_integer():
            raw = [int(round(mean))] * rows
        else:
            raw = [mean] * rows
    else:
        raw = [max(min_val, min(max_val, rng.gauss(mean, std))) for _ in range(rows)]

    # Cast back to original dtype
    if dtype.is_integer():
        return [int(round(v)) for v in raw]
    return raw


# ---------------------------------------------------------------------------
# Impute: fill missing values
# ---------------------------------------------------------------------------


def impute(
    df: pl.DataFrame,
    column: str,
    *,
    method: str = "median",
) -> pl.DataFrame:
    """Fill null values in a column using the specified method.

    Parameters
    ----------
    df
        Input DataFrame.
    column
        Column name to impute.
    method
        Imputation strategy:
        - ``"mean"`` — fill with column mean (numeric only).
        - ``"median"`` — fill with column median (numeric only).
        - ``"mode"`` — fill with most frequent value.
        - ``"forward"`` — forward-fill (last observation carried forward).
        - ``"backward"`` — backward-fill.
        - ``"zero"`` — fill with 0 (numeric) or empty string (string).
        - ``"interpolate"`` — linear interpolation (numeric only).

    Returns
    -------
    pl.DataFrame
        DataFrame with nulls filled in the specified column.

    Raises
    ------
    ValueError
        If column not found or method is invalid for the column dtype.
    """
    if column not in df.columns:
        raise ValueError(f"Column '{column}' not found in DataFrame")

    series = df[column]
    dtype = series.dtype

    if method == "mean":
        if not dtype.is_numeric():
            raise ValueError(f"Cannot use 'mean' imputation on non-numeric column '{column}' ({dtype})")
        fill_val = series.mean()
        return df.with_columns(pl.col(column).fill_null(fill_val))

    elif method == "median":
        if not dtype.is_numeric():
            raise ValueError(f"Cannot use 'median' imputation on non-numeric column '{column}' ({dtype})")
        fill_val = series.median()
        return df.with_columns(pl.col(column).fill_null(fill_val))

    elif method == "mode":
        mode_val = series.drop_nulls().mode().to_list()
        if not mode_val:
            return df
        fill_val = mode_val[0]
        return df.with_columns(pl.col(column).fill_null(fill_val))

    elif method == "forward":
        return df.with_columns(pl.col(column).forward_fill())

    elif method == "backward":
        return df.with_columns(pl.col(column).backward_fill())

    elif method == "zero":
        if dtype.is_numeric():
            return df.with_columns(pl.col(column).fill_null(0))
        elif dtype == pl.Utf8:
            return df.with_columns(pl.col(column).fill_null(""))
        else:
            raise ValueError(f"Cannot use 'zero' imputation on column '{column}' ({dtype})")

    elif method == "interpolate":
        if not dtype.is_numeric():
            raise ValueError(
                f"Cannot use 'interpolate' imputation on non-numeric column '{column}' ({dtype})"
            )
        return df.with_columns(pl.col(column).interpolate())

    else:
        valid = ["mean", "median", "mode", "forward", "backward", "zero", "interpolate"]
        raise ValueError(f"Unknown imputation method '{method}'. Valid methods: {valid}")


# ---------------------------------------------------------------------------
# Augment: add derived columns
# ---------------------------------------------------------------------------


def augment_fill_rate(df: pl.DataFrame) -> pl.DataFrame:
    """Add a ``_fill_rate`` column showing per-row completeness (0–1).

    Each row's fill rate is the fraction of non-null columns.
    """
    n_cols = len(df.columns)
    if n_cols == 0:
        return df

    # Count nulls per row
    null_expr = sum(pl.col(c).is_null().cast(pl.Int64) for c in df.columns)
    return df.with_columns(
        ((pl.lit(n_cols) - null_expr) / n_cols).alias("_fill_rate")
    )


def augment_row_hash(df: pl.DataFrame) -> pl.DataFrame:
    """Add a ``_row_hash`` column with a hash of each row's values.

    Useful for deduplication and change detection.
    """
    if len(df.columns) == 0:
        return df

    concat_expr = pl.concat_str(
        [pl.col(c).cast(pl.Utf8).fill_null("∅") for c in df.columns],
        separator="|",
    )
    return df.with_columns(concat_expr.hash().alias("_row_hash"))


def augment_row_number(df: pl.DataFrame, *, name: str = "_row_number") -> pl.DataFrame:
    """Add a sequential row number column (1-based)."""
    return df.with_row_index(name, offset=1)
