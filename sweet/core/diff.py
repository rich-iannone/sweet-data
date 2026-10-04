"""Table diffs: what changed between two versions of a table.

`diff_frames()` compares a *before* and an *after* frame, matching rows by key
columns (or by lineage: the row ids Sweet carries through a step), and returns
a `TableDiff` with:

- schema changes (added, removed, and retyped columns)
- row counts by status: added, removed, changed, unchanged
- changed-cell counts per column
- a lazy *diff frame* for display: one row per before/after row, with a status
  column and the old value of every changed cell

Everything is lazy until counted or fetched, so diffs work on large data.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

import polars as pl

from .steps import Step, StepError
from .view import ROW_ID

#: Diff frame column holding each row's status: "=", "~" (changed), "+", or "-".
STATUS = "__sweet_status"
#: Prefix of diff frame columns holding a changed cell's previous value.
OLD_PREFIX = "__sweet_old::"
#: Prefix of diff frame columns flagging whether a cell changed.
CHANGED_PREFIX = "__sweet_chg::"

_IN_BEFORE = "__sweet_in_before"
_IN_AFTER = "__sweet_in_after"
_SUFFIX = "__sweet_after"


def old_column(name: str) -> str:
    return f"{OLD_PREFIX}{name}"


def changed_column(name: str) -> str:
    return f"{CHANGED_PREFIX}{name}"


def is_internal(name: str) -> bool:
    """Whether a column is Sweet bookkeeping (hidden from display)."""
    return name.startswith("__sweet_")


@dataclass
class TableDiff:
    """The result of comparing two tables.

    Attributes:
        key: Columns used to match rows (``[ROW_ID]`` for lineage diffs), or
            None if rows couldn't be matched (only schema and counts compared).
        added_columns / removed_columns: Column names (after-only / before-only).
        type_changes: {column: (before dtype, after dtype)}.
        rows_before / rows_after: Row counts.
        rows_added / rows_removed / rows_changed / rows_unchanged: By status.
        changed_by_column: Changed-cell counts per shared column.
        reordered: Whether matched rows changed order (lineage diffs).
        frame: The display frame (see module docs); None if rows aren't matched.
        after: The after frame (for display when rows aren't matched).
    """

    key: list[str] | None
    added_columns: list[str] = field(default_factory=list)
    removed_columns: list[str] = field(default_factory=list)
    type_changes: dict[str, tuple[str, str]] = field(default_factory=dict)
    rows_before: int = 0
    rows_after: int = 0
    rows_added: int = 0
    rows_removed: int = 0
    rows_changed: int = 0
    rows_unchanged: int = 0
    changed_by_column: dict[str, int] = field(default_factory=dict)
    reordered: bool = False
    frame: pl.LazyFrame | None = field(default=None, repr=False)
    after: pl.LazyFrame | None = field(default=None, repr=False)

    @property
    def has_changes(self) -> bool:
        return bool(
            self.added_columns
            or self.removed_columns
            or self.type_changes
            or self.rows_added
            or self.rows_removed
            or self.rows_changed
            or self.reordered
            or (self.key is None and self.rows_before != self.rows_after)
        )

    @property
    def cells_changed(self) -> int:
        return sum(self.changed_by_column.values())

    def summary(self) -> str:
        """One line, e.g. "−312 rows · +1 col · 2 cols changed (1,204 cells)"."""
        parts = []
        if self.key is None and self.rows_before != self.rows_after:
            delta = self.rows_after - self.rows_before
            parts.append(f"{delta:+,} rows")
        if self.rows_added:
            parts.append(f"+{self.rows_added:,} rows")
        if self.rows_removed:
            parts.append(f"−{self.rows_removed:,} rows")
        if self.added_columns:
            parts.append(f"+{len(self.added_columns)} col{'s' * (len(self.added_columns) > 1)}")
        if self.removed_columns:
            n = len(self.removed_columns)
            parts.append(f"−{n} col{'s' * (n > 1)}")
        if self.type_changes:
            n = len(self.type_changes)
            parts.append(f"{n} type change{'s' * (n > 1)}")
        changed_cols = [c for c, n in self.changed_by_column.items() if n]
        if changed_cols:
            parts.append(
                f"{len(changed_cols)} col{'s' * (len(changed_cols) > 1)} changed "
                f"({self.cells_changed:,} cell{'s' * (self.cells_changed != 1)})"
            )
        if self.reordered:
            parts.append("rows reordered")
        return " · ".join(parts) if parts else "no changes"

    def to_dict(self, *, sample: int = 0) -> dict[str, Any]:
        d = {
            "key": self.key,
            "summary": self.summary(),
            "has_changes": self.has_changes,
            "rows": {
                "before": self.rows_before,
                "after": self.rows_after,
                "added": self.rows_added,
                "removed": self.rows_removed,
                "changed": self.rows_changed,
                "unchanged": self.rows_unchanged,
            },
            "columns": {
                "added": self.added_columns,
                "removed": self.removed_columns,
                "type_changes": {c: list(t) for c, t in self.type_changes.items()},
                "changed_cells": self.changed_by_column,
            },
            "reordered": self.reordered,
        }
        if sample:
            d["samples"] = self.samples(sample)
        return d

    def to_json(self, *, sample: int = 0) -> str:
        return json.dumps(self.to_dict(sample=sample), indent=2, default=str)

    def samples(self, n: int = 10, statuses: str = "~+-") -> list[dict[str, Any]]:
        """Up to `n` example rows with a non-"=" status, for reports."""
        if self.frame is None:
            return []
        rows = self.frame.filter(pl.col(STATUS).is_in(list(statuses))).head(n).collect().to_dicts()
        out = []
        for row in rows:
            entry: dict[str, Any] = {"status": row[STATUS]}
            if self.key and self.key != [ROW_ID]:
                entry["key"] = {k: row.get(k) for k in self.key}
            elif ROW_ID in row:
                entry["row"] = row[ROW_ID]
            changes = {
                name[len(CHANGED_PREFIX) :]: {
                    "from": row.get(old_column(name[len(CHANGED_PREFIX) :])),
                    "to": row.get(name[len(CHANGED_PREFIX) :]),
                }
                for name, flag in row.items()
                if name.startswith(CHANGED_PREFIX) and flag
            }
            if row[STATUS] == "~":
                entry["changes"] = changes
            else:
                entry["values"] = {k: v for k, v in row.items() if not is_internal(k)}
            out.append(entry)
        return out

    def to_markdown(self, *, sample: int = 10) -> str:
        lines = [f"**Diff:** {self.summary()}", ""]
        lines.append("| | before | after |")
        lines.append("|---|---:|---:|")
        lines.append(f"| rows | {self.rows_before:,} | {self.rows_after:,} |")
        if self.added_columns:
            lines.append(f"\nAdded columns: {', '.join(self.added_columns)}")
        if self.removed_columns:
            lines.append(f"\nRemoved columns: {', '.join(self.removed_columns)}")
        for col, (old, new) in self.type_changes.items():
            lines.append(f"\nType change: `{col}` {old} → {new}")
        changed = {c: n for c, n in self.changed_by_column.items() if n}
        if changed:
            lines.append("\n| column | changed cells |\n|---|---:|")
            lines += [f"| {c} | {n:,} |" for c, n in sorted(changed.items(), key=lambda kv: -kv[1])]
        examples = self.samples(sample)
        if examples:
            lines.append("\nExamples:")
            for ex in examples:
                lines.append(f"- `{ex['status']}` {json.dumps(ex, default=str)}")
        return "\n".join(lines)


def diff_frames(
    before: pl.DataFrame | pl.LazyFrame,
    after: pl.DataFrame | pl.LazyFrame,
    *,
    key: list[str] | str | None = None,
    reordered: bool | None = None,
) -> TableDiff:
    """Compare `before` and `after`.

    Args:
        key: Column(s) identifying rows in both frames. Without a key, only
            schema and row counts are compared (`frame` is None).
        reordered: Whether matched rows changed order. Computed for lineage
            (ROW_ID) diffs if not given.
    """
    b, a = before.lazy(), after.lazy()
    b_schema, a_schema = b.collect_schema(), a.collect_schema()
    b_cols = [c for c in b_schema.names() if not is_internal(c)]
    a_cols = [c for c in a_schema.names() if not is_internal(c)]
    keys = [key] if isinstance(key, str) else (list(key) if key else None)

    result = TableDiff(key=keys, after=a)
    result.added_columns = [c for c in a_cols if c not in b_schema]
    result.removed_columns = [c for c in b_cols if c not in a_schema]
    shared = [c for c in a_cols if c in b_schema]
    result.type_changes = {
        c: (str(b_schema[c]), str(a_schema[c])) for c in shared if b_schema[c] != a_schema[c]
    }

    if keys is None:
        counts = pl.concat(
            [b.select(pl.len().alias("n")), a.select(pl.len().alias("n"))]
        ).collect()["n"]
        result.rows_before, result.rows_after = int(counts[0]), int(counts[1])
        return result

    missing = [k for k in keys if k not in b_schema or k not in a_schema]
    if missing:
        raise ValueError(f"Key column(s) not in both tables: {', '.join(missing)}")

    lineage = keys == [ROW_ID]
    if lineage and reordered is None:
        reordered = bool(
            a.select((pl.col(ROW_ID).drop_nulls().diff() < 0).any()).collect().item() or False
        )
    result.reordered = bool(reordered)

    values = [c for c in shared if c not in keys]
    b_side = b.select([*keys, *values, *result.removed_columns]).with_columns(
        pl.lit(True).alias(_IN_BEFORE)
    )
    a_side = a.select([*keys, *values, *result.added_columns]).with_columns(
        pl.lit(True).alias(_IN_AFTER)
    )
    joined = b_side.join(
        a_side,
        on=keys,
        how="full",
        coalesce=True,
        suffix=_SUFFIX,
        maintain_order="right_left" if result.reordered else "left_right",
    )

    in_b = pl.col(_IN_BEFORE).fill_null(False)
    in_a = pl.col(_IN_AFTER).fill_null(False)
    changed_flags = {}
    for c in values:
        old, new = pl.col(c), pl.col(f"{c}{_SUFFIX}")
        if c in result.type_changes:
            old, new = old.cast(pl.String), new.cast(pl.String)
        changed_flags[c] = in_b & in_a & old.ne_missing(new)
    any_changed = (
        pl.any_horizontal(list(changed_flags.values())) if changed_flags else pl.lit(False)
    )
    status = (
        pl.when(~in_b)
        .then(pl.lit("+"))
        .when(~in_a)
        .then(pl.lit("-"))
        .when(any_changed)
        .then(pl.lit("~"))
        .otherwise(pl.lit("="))
    )

    display = [pl.col(k) for k in keys]
    for c in a_cols:
        if c in keys:
            continue
        if c in values:
            display.append(
                pl.when(~in_a).then(pl.col(c)).otherwise(pl.col(f"{c}{_SUFFIX}")).alias(c)
            )
        else:  # added column
            display.append(pl.col(c))
    display += [pl.col(c) for c in result.removed_columns]
    display.append(status.alias(STATUS))
    display += [
        pl.when(flag).then(pl.col(c)).otherwise(None).alias(old_column(c))
        for c, flag in changed_flags.items()
    ]
    flags_frame = joined.select(
        [*display, *[flag.alias(changed_column(c)) for c, flag in changed_flags.items()]]
    )

    stats = (
        flags_frame.select(
            [
                pl.len().alias("rows"),
                *[
                    (pl.col(STATUS) == s).sum().alias(name)
                    for s, name in (
                        ("+", "added"),
                        ("-", "removed"),
                        ("~", "changed"),
                        ("=", "same"),
                    )
                ],
                *[pl.col(changed_column(c)).sum().alias(c) for c in changed_flags],
            ]
        )
        .collect(engine="streaming")
        .row(0, named=True)
    )
    result.rows_added = int(stats["added"])
    result.rows_removed = int(stats["removed"])
    result.rows_changed = int(stats["changed"])
    result.rows_unchanged = int(stats["same"])
    result.rows_before = result.rows_removed + result.rows_changed + result.rows_unchanged
    result.rows_after = result.rows_added + result.rows_changed + result.rows_unchanged
    result.changed_by_column = {c: int(stats[c]) for c in changed_flags}
    result.frame = flags_frame
    return result


def diff_step(frame: pl.DataFrame | pl.LazyFrame, step: Step) -> TableDiff:
    """Preview the effect of `step` on `frame`, matching rows by lineage.

    Raises:
        StepError: If the step fails.
    """
    before = frame.lazy().with_row_index(ROW_ID)
    probe = step
    if step.kind == "select":
        # Keep the lineage column through a column selection
        probe = Step("select", {**step.params, "columns": [*step.params["columns"], ROW_ID]})
    after = probe.apply(before)
    if isinstance(after, pl.DataFrame):
        after = after.lazy()
    if ROW_ID not in after.collect_schema().names():
        # The step doesn't preserve row identity (e.g. an aggregation)
        return diff_frames(before.drop(ROW_ID), after)
    return diff_frames(before, after, key=[ROW_ID])


def diff_lineage(
    before: pl.DataFrame | pl.LazyFrame, after_fn, *, reordered: bool | None = None
) -> TableDiff:
    """Diff `before` against `after_fn(before_with_row_ids)` by lineage."""
    b = before.lazy().with_row_index(ROW_ID)
    a = after_fn(b)
    a = a.lazy() if isinstance(a, pl.DataFrame) else a
    if ROW_ID not in a.collect_schema().names():
        return diff_frames(b.drop(ROW_ID), a)
    return diff_frames(b, a, key=[ROW_ID], reordered=reordered)


__all__ = [
    "CHANGED_PREFIX",
    "OLD_PREFIX",
    "changed_column",
    "STATUS",
    "StepError",
    "TableDiff",
    "diff_frames",
    "diff_lineage",
    "diff_step",
    "is_internal",
    "old_column",
]
