"""Anomaly detection and explanation for tabular data.

Identifies statistical outliers, pattern breaks, and distribution anomalies
in DataFrame columns and provides human-readable explanations.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import polars as pl

# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------


@dataclass
class Anomaly:
    """A single detected anomaly with explanation."""

    column: str
    kind: str  # "outlier", "spike", "gap", "pattern_break", "null_cluster"
    severity: str  # "low", "medium", "high"
    description: str
    rows: list[int] = field(default_factory=list)  # affected row indices
    values: list[object] = field(default_factory=list)  # anomalous values
    stats: dict[str, object] = field(default_factory=dict)  # supporting stats
    explanation: str = ""  # human-readable explanation of why

    def to_dict(self) -> dict:
        return {
            "column": self.column,
            "kind": self.kind,
            "severity": self.severity,
            "description": self.description,
            "rows": self.rows[:20],  # cap for readability
            "values": [_safe_serialize(v) for v in self.values[:20]],
            "stats": {k: _safe_serialize(v) for k, v in self.stats.items()},
            "explanation": self.explanation,
        }


def _safe_serialize(val: object) -> object:
    """Make a value JSON-serializable."""
    if isinstance(val, float) and (math.isnan(val) or math.isinf(val)):
        return str(val)
    if isinstance(val, (int, float, str, bool, type(None))):
        return val
    return str(val)


# ---------------------------------------------------------------------------
# Main detection entry point
# ---------------------------------------------------------------------------


def explain_anomalies(
    df: pl.DataFrame,
    *,
    z_threshold: float = 3.0,
    iqr_factor: float = 1.5,
    null_cluster_threshold: float = 0.10,
    max_anomalies_per_column: int = 10,
) -> list[Anomaly]:
    """Detect and explain anomalies in a DataFrame.

    Parameters
    ----------
    df
        The DataFrame to analyze.
    z_threshold
        Number of standard deviations for z-score outlier detection.
    iqr_factor
        IQR multiplier for box-plot outlier detection.
    null_cluster_threshold
        Fraction of nulls in a column to flag as a null cluster.
    max_anomalies_per_column
        Maximum anomalous rows to report per column.

    Returns
    -------
    list[Anomaly]
        List of detected anomalies with explanations.
    """
    anomalies: list[Anomaly] = []

    for col_name in df.columns:
        col = df[col_name]
        dtype = col.dtype

        # Numeric anomaly detection
        if dtype.is_numeric():
            anomalies.extend(
                _detect_numeric_anomalies(
                    df,
                    col_name,
                    z_threshold=z_threshold,
                    iqr_factor=iqr_factor,
                    max_rows=max_anomalies_per_column,
                )
            )

        # Null cluster detection (all types)
        null_count = col.null_count()
        total = len(col)
        if total > 0 and null_count > 0:
            null_frac = null_count / total
            if null_frac >= null_cluster_threshold:
                anomalies.append(
                    Anomaly(
                        column=col_name,
                        kind="null_cluster",
                        severity=_null_severity(null_frac),
                        description=(
                            f"Column '{col_name}' has {null_count} null values "
                            f"({null_frac:.1%} of rows)"
                        ),
                        stats={
                            "null_count": null_count,
                            "null_fraction": round(null_frac, 4),
                            "total_rows": total,
                        },
                        explanation=_explain_null_cluster(col_name, null_frac),
                    )
                )

        # String pattern anomalies
        if dtype == pl.Utf8:
            anomalies.extend(
                _detect_string_anomalies(df, col_name, max_rows=max_anomalies_per_column)
            )

    return anomalies


# ---------------------------------------------------------------------------
# Numeric anomaly detection
# ---------------------------------------------------------------------------


def _detect_numeric_anomalies(
    df: pl.DataFrame,
    col_name: str,
    *,
    z_threshold: float,
    iqr_factor: float,
    max_rows: int,
) -> list[Anomaly]:
    """Detect outliers in a numeric column using z-score and IQR methods."""
    anomalies: list[Anomaly] = []
    col = df[col_name].drop_nulls()

    if len(col) < 4:
        return anomalies

    mean_val = col.mean()
    std_val = col.std()

    # Z-score outliers
    if std_val is not None and std_val > 0:
        z_scores = ((col - mean_val) / std_val).abs()
        outlier_mask = z_scores > z_threshold
        outlier_indices = (
            df.with_row_index("__idx__")
            .filter(
                ((pl.col(col_name) - mean_val) / std_val).abs() > z_threshold
            )["__idx__"]
            .to_list()
        )

        if outlier_indices:
            outlier_values = df[col_name].gather(outlier_indices).to_list()
            n_outliers = len(outlier_indices)
            anomalies.append(
                Anomaly(
                    column=col_name,
                    kind="outlier",
                    severity=_outlier_severity(n_outliers, len(col)),
                    description=(
                        f"Column '{col_name}' has {n_outliers} statistical "
                        f"outlier(s) beyond {z_threshold}σ from the mean"
                    ),
                    rows=outlier_indices[:max_rows],
                    values=outlier_values[:max_rows],
                    stats={
                        "mean": round(mean_val, 4),
                        "std": round(std_val, 4),
                        "z_threshold": z_threshold,
                        "outlier_count": n_outliers,
                        "min_value": col.min(),
                        "max_value": col.max(),
                    },
                    explanation=_explain_numeric_outliers(
                        col_name, n_outliers, mean_val, std_val, outlier_values
                    ),
                )
            )

    # IQR-based detection (complementary)
    q1 = col.quantile(0.25)
    q3 = col.quantile(0.75)
    if q1 is not None and q3 is not None:
        iqr = q3 - q1
        if iqr > 0:
            lower_bound = q1 - iqr_factor * iqr
            upper_bound = q3 + iqr_factor * iqr

            iqr_outlier_indices = (
                df.with_row_index("__idx__")
                .filter(
                    (pl.col(col_name) < lower_bound)
                    | (pl.col(col_name) > upper_bound)
                )["__idx__"]
                .to_list()
            )

            # Only report IQR outliers if they differ from z-score outliers
            z_set = set(anomalies[-1].rows) if anomalies else set()
            iqr_only = [i for i in iqr_outlier_indices if i not in z_set]

            if iqr_only and not anomalies:
                iqr_values = df[col_name].gather(iqr_only).to_list()
                anomalies.append(
                    Anomaly(
                        column=col_name,
                        kind="outlier",
                        severity=_outlier_severity(len(iqr_only), len(col)),
                        description=(
                            f"Column '{col_name}' has {len(iqr_only)} value(s) "
                            f"outside the IQR fence [{lower_bound:.2f}, {upper_bound:.2f}]"
                        ),
                        rows=iqr_only[:max_rows],
                        values=iqr_values[:max_rows],
                        stats={
                            "q1": round(q1, 4),
                            "q3": round(q3, 4),
                            "iqr": round(iqr, 4),
                            "lower_bound": round(lower_bound, 4),
                            "upper_bound": round(upper_bound, 4),
                            "outlier_count": len(iqr_only),
                        },
                        explanation=_explain_iqr_outliers(
                            col_name, len(iqr_only), lower_bound, upper_bound
                        ),
                    )
                )

    # Spike detection: sudden large changes in sequential data
    if len(col) >= 10:
        spike_anomaly = _detect_spikes(df, col_name, max_rows=max_rows)
        if spike_anomaly:
            anomalies.append(spike_anomaly)

    return anomalies


def _detect_spikes(
    df: pl.DataFrame, col_name: str, *, max_rows: int
) -> Anomaly | None:
    """Detect sudden spikes/drops in sequential numeric data."""
    col = df[col_name]
    diffs = col.diff().drop_nulls()

    if len(diffs) < 4:
        return None

    diff_mean = diffs.mean()
    diff_std = diffs.std()

    if diff_std is None or diff_std == 0:
        return None

    # Find points where the change is > 3σ of the typical change
    spike_mask = ((diffs - diff_mean) / diff_std).abs() > 3.0
    spike_indices = [
        i + 1 for i, v in enumerate(spike_mask.to_list()) if v
    ]

    if not spike_indices:
        return None

    spike_values = df[col_name].gather(spike_indices[:max_rows]).to_list()
    return Anomaly(
        column=col_name,
        kind="spike",
        severity="medium" if len(spike_indices) <= 3 else "high",
        description=(
            f"Column '{col_name}' has {len(spike_indices)} sudden "
            f"spike(s)/drop(s) in sequential values"
        ),
        rows=spike_indices[:max_rows],
        values=spike_values,
        stats={
            "spike_count": len(spike_indices),
            "typical_change_mean": round(diff_mean, 4),
            "typical_change_std": round(diff_std, 4),
        },
        explanation=_explain_spikes(col_name, len(spike_indices), diff_mean, diff_std),
    )


# ---------------------------------------------------------------------------
# String anomaly detection
# ---------------------------------------------------------------------------


def _detect_string_anomalies(
    df: pl.DataFrame, col_name: str, *, max_rows: int
) -> list[Anomaly]:
    """Detect anomalies in string columns (length outliers, format breaks)."""
    anomalies: list[Anomaly] = []
    col = df[col_name].drop_nulls()

    if len(col) < 4:
        return anomalies

    # Length-based outlier detection
    lengths = col.str.len_chars()
    mean_len = lengths.mean()
    std_len = lengths.std()

    if std_len is not None and std_len > 0 and mean_len is not None:
        length_outlier_indices = (
            df.with_row_index("__idx__")
            .filter(pl.col(col_name).is_not_null())
            .filter(
                ((pl.col(col_name).str.len_chars() - mean_len) / std_len).abs() > 3.0
            )["__idx__"]
            .to_list()
        )

        if length_outlier_indices:
            outlier_values = df[col_name].gather(length_outlier_indices[:max_rows]).to_list()
            anomalies.append(
                Anomaly(
                    column=col_name,
                    kind="pattern_break",
                    severity="low",
                    description=(
                        f"Column '{col_name}' has {len(length_outlier_indices)} value(s) "
                        f"with unusual string length (mean: {mean_len:.0f} chars)"
                    ),
                    rows=length_outlier_indices[:max_rows],
                    values=outlier_values,
                    stats={
                        "mean_length": round(mean_len, 2),
                        "std_length": round(std_len, 2),
                        "outlier_count": len(length_outlier_indices),
                    },
                    explanation=_explain_string_length_anomaly(
                        col_name, len(length_outlier_indices), mean_len
                    ),
                )
            )

    return anomalies


# ---------------------------------------------------------------------------
# Severity helpers
# ---------------------------------------------------------------------------


def _null_severity(null_frac: float) -> str:
    if null_frac >= 0.5:
        return "high"
    if null_frac >= 0.25:
        return "medium"
    return "low"


def _outlier_severity(n_outliers: int, total: int) -> str:
    frac = n_outliers / total if total > 0 else 0
    if frac >= 0.05 or n_outliers >= 20:
        return "high"
    if frac >= 0.01 or n_outliers >= 5:
        return "medium"
    return "low"


# ---------------------------------------------------------------------------
# Explanation generators
# ---------------------------------------------------------------------------


def _explain_numeric_outliers(
    col_name: str,
    n_outliers: int,
    mean: float,
    std: float,
    values: list,
) -> str:
    """Generate a human-readable explanation for numeric outliers."""
    if not values:
        return f"Column '{col_name}' contains statistical outliers."

    extreme = max(values, key=lambda x: abs(x - mean) if x is not None else 0)
    direction = "above" if extreme > mean else "below"
    z = abs(extreme - mean) / std if std > 0 else 0

    parts = [
        f"Column '{col_name}' has {n_outliers} value(s) that deviate significantly "
        f"from the distribution (mean={mean:.2f}, std={std:.2f}).",
    ]
    if extreme is not None:
        parts.append(
            f"The most extreme value is {extreme} ({z:.1f}σ {direction} the mean)."
        )
    if n_outliers == 1:
        parts.append("This could be a data entry error or a genuine rare event.")
    else:
        parts.append(
            "Multiple outliers may indicate a subpopulation, measurement errors, "
            "or a regime change in the data."
        )
    return " ".join(parts)


def _explain_iqr_outliers(
    col_name: str, n_outliers: int, lower: float, upper: float
) -> str:
    return (
        f"Column '{col_name}' has {n_outliers} value(s) outside the interquartile "
        f"fence [{lower:.2f}, {upper:.2f}]. These are statistically unusual relative "
        f"to the central 50% of values."
    )


def _explain_spikes(
    col_name: str, n_spikes: int, diff_mean: float, diff_std: float
) -> str:
    return (
        f"Column '{col_name}' shows {n_spikes} sudden jump(s) in sequential values. "
        f"The typical change between consecutive values is {diff_mean:.2f} "
        f"(±{diff_std:.2f}), but these points deviate by more than 3x the usual variation. "
        f"This may indicate events, measurement resets, or data entry errors."
    )


def _explain_null_cluster(col_name: str, null_frac: float) -> str:
    return (
        f"Column '{col_name}' is {null_frac:.0%} null. "
        f"This may indicate systematic missing data (e.g., the field was added later, "
        f"is optional for certain record types, or failed to collect in some conditions)."
    )


def _explain_string_length_anomaly(
    col_name: str, n_outliers: int, mean_len: float
) -> str:
    return (
        f"Column '{col_name}' has {n_outliers} value(s) with unusual string length "
        f"compared to the average of {mean_len:.0f} characters. "
        f"This may indicate truncation, concatenation errors, or data from a different source."
    )
