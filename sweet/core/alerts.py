"""Live checks on streaming data.

- `RowRule`: a SQL condition every new row should satisfy (e.g. ``temp_c BETWEEN -40
  AND 60``). Violations are reported with a count and sample rows.
- `DriftRule`: a column statistic over the most recent rows (null rate or mean)
  compared with a baseline (the first full window). It fires when the change passes
  the threshold, e.g. "null rate on temp_c jumped from 0% to 18% in the last 1,000 rows".

An `AlertMonitor` evaluates rules as batches arrive and calls back with `Alert`s.
Drift rules fire once per excursion and re-arm when the statistic returns to normal.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any

import polars as pl

from .steps import sql_expr
from .stream import LIVE_SEQ


@dataclass
class RowRule:
    name: str
    sql: str  # Condition that should hold for each row
    severity: str = "warning"

    def describe(self) -> str:
        return f"every row satisfies {self.sql}"


@dataclass
class DriftRule:
    name: str
    column: str
    stat: str = "null_rate"  # "null_rate" (absolute change) or "mean" (relative change)
    window: int = 1000
    threshold: float = 0.1
    severity: str = "warning"

    def describe(self) -> str:
        unit = "points" if self.stat == "null_rate" else "relative change"
        return f"{self.stat} of {self.column} over the last {self.window:,} rows moves by < {self.threshold:g} ({unit})"


@dataclass
class Alert:
    rule: str
    kind: str  # "row" | "drift"
    sheet: str
    message: str
    severity: str = "warning"
    count: int = 0
    samples: list[dict[str, Any]] = field(default_factory=list)
    seqs: list[int] = field(default_factory=list)
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:8])
    time: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def rule_from_dict(spec: dict[str, Any]) -> RowRule | DriftRule:
    """Build a rule from ``{"sql": ...}`` or ``{"column": ..., "stat": ...}`` (plus name, ...)."""
    spec = dict(spec)
    if "sql" in spec:
        spec.setdefault("name", spec["sql"])
        return RowRule(**{k: spec[k] for k in ("name", "sql", "severity") if k in spec})
    if "column" in spec:
        spec.setdefault("name", f"{spec.get('stat', 'null_rate')}({spec['column']})")
        keys = ("name", "column", "stat", "window", "threshold", "severity")
        rule = DriftRule(**{k: spec[k] for k in keys if k in spec})
        if rule.stat not in ("null_rate", "mean"):
            raise ValueError("stat must be 'null_rate' or 'mean'")
        return rule
    raise ValueError("A rule needs 'sql' (a row condition) or 'column' (a drift check)")


class AlertMonitor:
    """Evaluates rules for one sheet as rows arrive."""

    def __init__(self, sheet: str, on_alert: Callable[[Alert], None]) -> None:
        self.sheet = sheet
        self.on_alert = on_alert
        self.rules: dict[str, RowRule | DriftRule] = {}
        self._baselines: dict[str, float | None] = {}
        self._firing: set[str] = set()
        self.history: list[Alert] = []

    def add(self, rule: RowRule | DriftRule) -> None:
        self.rules[rule.name] = rule
        self._baselines.pop(rule.name, None)
        self._firing.discard(rule.name)

    def remove(self, name: str) -> None:
        self.rules.pop(name, None)

    def check(self, batch: pl.DataFrame, buffer: pl.DataFrame | None = None) -> list[Alert]:
        """Evaluate row rules on `batch` (new rows) and drift rules on `buffer`."""
        alerts: list[Alert] = []
        for rule in list(self.rules.values()):
            try:
                alert = (
                    self._check_row(rule, batch)
                    if isinstance(rule, RowRule)
                    else self._check_drift(rule, buffer if buffer is not None else batch)
                )
            except Exception as e:  # A broken rule shouldn't stop the others
                alert = Alert(
                    rule.name, "error", self.sheet, f"Rule '{rule.name}' failed: {e}", "error"
                )
            if alert is not None:
                alerts.append(alert)
                self.history.append(alert)
                self.history = self.history[-200:]
                self.on_alert(alert)
        return alerts

    def _check_row(self, rule: RowRule, batch: pl.DataFrame) -> Alert | None:
        if batch.height == 0:
            return None
        # Like SQL CHECK constraints, a condition that's NULL (unknown) passes
        bad = batch.filter(~sql_expr(rule.sql).fill_null(True))
        if bad.height == 0:
            return None
        seqs = bad[LIVE_SEQ].to_list() if LIVE_SEQ in bad.columns else []
        samples = (
            bad.select([c for c in bad.columns if not c.startswith("__sweet_")]).head(5).to_dicts()
        )
        return Alert(
            rule.name,
            "row",
            self.sheet,
            f"{bad.height:,} new row(s) violate {rule.sql}",
            rule.severity,
            bad.height,
            samples,
            seqs[:1000],
        )

    def _stat(self, rule: DriftRule, frame: pl.DataFrame) -> float | None:
        if rule.column not in frame.columns or frame.height == 0:
            return None
        col = frame[rule.column]
        if rule.stat == "null_rate":
            return col.null_count() / frame.height
        if not col.dtype.is_numeric():
            return None
        value = col.mean()
        return float(value) if value is not None else None

    def _check_drift(self, rule: DriftRule, buffer: pl.DataFrame) -> Alert | None:
        if buffer.height < rule.window:
            return None
        if self._baselines.get(rule.name) is None:
            self._baselines[rule.name] = self._stat(rule, buffer.head(rule.window))
            return None
        baseline = self._baselines[rule.name]
        current = self._stat(rule, buffer.tail(rule.window))
        if baseline is None or current is None:
            return None
        if rule.stat == "null_rate":
            change = abs(current - baseline)
            message = (
                f"null rate on {rule.column} {'rose' if current > baseline else 'fell'} from "
                f"{baseline:.0%} to {current:.0%} over the last {rule.window:,} rows"
            )
        else:
            change = abs(current - baseline) / abs(baseline) if baseline else abs(current)
            message = (
                f"mean of {rule.column} moved from {baseline:.4g} to {current:.4g} "
                f"({change:+.0%}) over the last {rule.window:,} rows"
            )
        if change >= rule.threshold:
            if rule.name in self._firing:
                return None  # Already reported this excursion
            self._firing.add(rule.name)
            return Alert(rule.name, "drift", self.sheet, message, rule.severity, rule.window)
        self._firing.discard(rule.name)  # Back to normal: re-arm
        return None

    def describe(self) -> list[dict[str, Any]]:
        return [
            {
                "name": r.name,
                "kind": "row" if isinstance(r, RowRule) else "drift",
                "rule": r.describe(),
            }
            for r in self.rules.values()
        ]
