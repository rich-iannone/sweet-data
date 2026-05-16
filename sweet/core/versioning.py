"""Version control for tabular data — commits, diffs, log, and checkout.

Provides git-like semantics optimized for DataFrames:
- Commit: Named snapshot of data state
- Log: Commit history with messages
- Checkout: Restore to a previous commit
- Diff: Column-aware comparison between two data states
"""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

import polars as pl


@dataclass
class Commit:
    """A named snapshot of a sheet's data state.

    Attributes:
        id: Short unique identifier (first 8 chars of UUID).
        message: Human-readable description of this state.
        timestamp: When the commit was created.
        sheet_name: Which sheet was committed.
        data_hash: Hash of the DataFrame at commit time.
        schema: Column name → type mapping at commit time.
        shape: (rows, cols) at commit time.
        snapshot: The actual DataFrame (stored for checkout).
        parent_id: ID of the previous commit (None for first).
        metadata: Additional info (operation count, etc.).
    """

    id: str
    message: str
    timestamp: datetime
    sheet_name: str
    data_hash: str
    schema: dict[str, str]
    shape: tuple[int, int]
    snapshot: pl.DataFrame = field(repr=False)
    parent_id: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class DiffResult:
    """Result of comparing two DataFrames.

    Attributes:
        rows_added: Number of rows present only in the right DataFrame.
        rows_removed: Number of rows present only in the left DataFrame.
        rows_modified: Number of rows with value changes.
        columns_added: Columns present only in the right DataFrame.
        columns_removed: Columns present only in the left DataFrame.
        schema_changes: Columns whose types differ (col → (old_type, new_type)).
        shape_left: Shape of the left DataFrame.
        shape_right: Shape of the right DataFrame.
        sample_changes: Sample of row-level changes (list of dicts).
    """

    rows_added: int = 0
    rows_removed: int = 0
    rows_modified: int = 0
    columns_added: list[str] = field(default_factory=list)
    columns_removed: list[str] = field(default_factory=list)
    schema_changes: dict[str, tuple[str, str]] = field(default_factory=dict)
    shape_left: tuple[int, int] = (0, 0)
    shape_right: tuple[int, int] = (0, 0)
    sample_changes: list[dict[str, Any]] = field(default_factory=list)

    @property
    def has_changes(self) -> bool:
        """Whether any differences exist."""
        return (
            self.rows_added > 0
            or self.rows_removed > 0
            or self.rows_modified > 0
            or len(self.columns_added) > 0
            or len(self.columns_removed) > 0
            or len(self.schema_changes) > 0
        )

    def summary(self) -> str:
        """Human-readable summary of the diff."""
        if not self.has_changes:
            return "No changes detected."

        parts: list[str] = []
        parts.append(
            f"Shape: {self.shape_left[0]}×{self.shape_left[1]} → "
            f"{self.shape_right[0]}×{self.shape_right[1]}"
        )

        if self.columns_added:
            parts.append(f"Columns added: {', '.join(self.columns_added)}")
        if self.columns_removed:
            parts.append(f"Columns removed: {', '.join(self.columns_removed)}")
        if self.schema_changes:
            changes = [f"{c}: {old}→{new}" for c, (old, new) in self.schema_changes.items()]
            parts.append(f"Type changes: {', '.join(changes)}")
        if self.rows_added:
            parts.append(f"Rows added: {self.rows_added}")
        if self.rows_removed:
            parts.append(f"Rows removed: {self.rows_removed}")
        if self.rows_modified:
            parts.append(f"Rows modified: {self.rows_modified}")

        return "\n".join(parts)


class VersionStore:
    """Manages commits for a workspace — git-like history for tabular data.

    Each sheet maintains its own linear commit history. Commits store full
    DataFrame snapshots enabling checkout to any previous state.
    """

    def __init__(self) -> None:
        self._commits: list[Commit] = []

    @property
    def commits(self) -> list[Commit]:
        """All commits in chronological order."""
        return list(self._commits)

    def commit(
        self,
        df: pl.DataFrame,
        sheet_name: str,
        message: str,
        *,
        metadata: dict[str, Any] | None = None,
    ) -> Commit:
        """Create a new commit snapshot.

        Args:
            df: The DataFrame to snapshot.
            sheet_name: Name of the sheet being committed.
            message: Commit message describing this state.
            metadata: Optional additional info.

        Returns:
            The created Commit object.
        """
        # Find parent (most recent commit for this sheet)
        parent_id: str | None = None
        for c in reversed(self._commits):
            if c.sheet_name == sheet_name:
                parent_id = c.id
                break

        commit_id = uuid.uuid4().hex[:8]
        data_hash = _hash_dataframe(df)
        schema = {col: str(dtype) for col, dtype in df.schema.items()}

        new_commit = Commit(
            id=commit_id,
            message=message,
            timestamp=datetime.now(timezone.utc),
            sheet_name=sheet_name,
            data_hash=data_hash,
            schema=schema,
            shape=df.shape,
            snapshot=df.clone(),
            parent_id=parent_id,
            metadata=metadata or {},
        )

        self._commits.append(new_commit)
        return new_commit

    def log(self, sheet_name: str | None = None, *, limit: int | None = None) -> list[Commit]:
        """Get commit history, optionally filtered by sheet.

        Args:
            sheet_name: Filter to only this sheet's commits. None = all.
            limit: Maximum number of commits to return (most recent first).

        Returns:
            List of commits in reverse chronological order.
        """
        commits = self._commits
        if sheet_name is not None:
            commits = [c for c in commits if c.sheet_name == sheet_name]

        # Reverse for most-recent-first
        result = list(reversed(commits))

        if limit is not None:
            result = result[:limit]

        return result

    def get_commit(self, commit_id: str) -> Commit:
        """Retrieve a specific commit by its ID.

        Args:
            commit_id: The commit ID (or prefix).

        Returns:
            The matching Commit.

        Raises:
            ValueError: If no commit matches the ID.
        """
        matches = [c for c in self._commits if c.id.startswith(commit_id)]
        if not matches:
            raise ValueError(f"No commit found matching '{commit_id}'")
        if len(matches) > 1:
            raise ValueError(
                f"Ambiguous commit ID '{commit_id}' — matches: "
                f"{[c.id for c in matches]}"
            )
        return matches[0]

    def checkout(self, commit_id: str) -> pl.DataFrame:
        """Retrieve the DataFrame from a specific commit.

        Args:
            commit_id: The commit ID (or unique prefix).

        Returns:
            A clone of the DataFrame at that commit.

        Raises:
            ValueError: If no commit matches.
        """
        commit = self.get_commit(commit_id)
        return commit.snapshot.clone()


def diff(
    left: pl.DataFrame,
    right: pl.DataFrame,
    *,
    key_columns: list[str] | None = None,
    max_sample: int = 10,
) -> DiffResult:
    """Compute a column-aware diff between two DataFrames.

    Compares schema, shape, and row-level content. If key_columns are provided,
    uses them for row matching; otherwise uses positional comparison.

    Args:
        left: The "before" DataFrame.
        right: The "after" DataFrame.
        key_columns: Columns to use as row identity for matching.
        max_sample: Maximum number of sample changes to include.

    Returns:
        DiffResult with detailed change information.
    """
    result = DiffResult(
        shape_left=left.shape,
        shape_right=right.shape,
    )

    # Schema comparison
    left_cols = set(left.columns)
    right_cols = set(right.columns)

    result.columns_added = sorted(right_cols - left_cols)
    result.columns_removed = sorted(left_cols - right_cols)

    # Type changes for shared columns
    shared_cols = sorted(left_cols & right_cols)
    for col in shared_cols:
        left_type = str(left.schema[col])
        right_type = str(right.schema[col])
        if left_type != right_type:
            result.schema_changes[col] = (left_type, right_type)

    # Row-level comparison (on shared columns only)
    if not shared_cols:
        # No shared columns — everything is added/removed
        result.rows_removed = left.height
        result.rows_added = right.height
        return result

    if key_columns:
        # Key-based diff
        _diff_by_keys(left, right, key_columns, shared_cols, result, max_sample)
    else:
        # Positional diff
        _diff_positional(left, right, shared_cols, result, max_sample)

    return result


def _diff_by_keys(
    left: pl.DataFrame,
    right: pl.DataFrame,
    key_columns: list[str],
    shared_cols: list[str],
    result: DiffResult,
    max_sample: int,
) -> None:
    """Key-based row diff: match rows by key columns, detect adds/removes/changes."""
    # Validate key columns exist in both
    for kc in key_columns:
        if kc not in left.columns or kc not in right.columns:
            raise ValueError(f"Key column '{kc}' not found in both DataFrames")

    # Select shared columns for comparison
    left_sub = left.select(shared_cols)
    right_sub = right.select(shared_cols)

    # Build key expressions
    left_keys = left_sub.select(key_columns)
    right_keys = right_sub.select(key_columns)

    # Find rows by key presence using anti-joins
    left_only = left_sub.join(right_sub, on=key_columns, how="anti")
    right_only = right_sub.join(left_sub, on=key_columns, how="anti")

    result.rows_removed = left_only.height
    result.rows_added = right_only.height

    # Find modified rows (same key, different values)
    value_cols = [c for c in shared_cols if c not in key_columns]
    if value_cols:
        # Inner join on keys
        joined = left_sub.join(right_sub, on=key_columns, how="inner", suffix="_right")

        # Check which rows have changes in non-key columns
        change_mask = pl.lit(False)
        for col in value_cols:
            right_col = f"{col}_right"
            if right_col in joined.columns:
                change_mask = change_mask | (
                    joined[col].ne_missing(joined[right_col])
                )

        modified = joined.filter(change_mask)
        result.rows_modified = modified.height

        # Sample changes
        if modified.height > 0:
            sample = modified.head(max_sample)
            for row in sample.iter_rows(named=True):
                change: dict[str, Any] = {"_keys": {k: row[k] for k in key_columns}}
                for col in value_cols:
                    right_col = f"{col}_right"
                    if right_col in row and row[col] != row[right_col]:
                        change[col] = {"from": row[col], "to": row[right_col]}
                if len(change) > 1:  # More than just _keys
                    result.sample_changes.append(change)


def _diff_positional(
    left: pl.DataFrame,
    right: pl.DataFrame,
    shared_cols: list[str],
    result: DiffResult,
    max_sample: int,
) -> None:
    """Positional diff: compare row-by-row up to the shorter length."""
    min_rows = min(left.height, right.height)

    if right.height > left.height:
        result.rows_added = right.height - left.height
    elif left.height > right.height:
        result.rows_removed = left.height - right.height

    # Compare shared portion
    if min_rows == 0:
        return

    left_sub = left.select(shared_cols).head(min_rows)
    right_sub = right.select(shared_cols).head(min_rows)

    # Row-by-row comparison using ne_missing for null-safe comparison
    change_mask = pl.lit(False)
    for col in shared_cols:
        if col in left_sub.columns and col in right_sub.columns:
            change_mask = change_mask | left_sub[col].ne_missing(right_sub[col])

    modified_indices = left_sub.with_row_index("__idx").filter(change_mask)["__idx"]
    result.rows_modified = len(modified_indices)

    # Sample changes
    if len(modified_indices) > 0:
        sample_idx = modified_indices[:max_sample].to_list()
        for idx in sample_idx:
            change: dict[str, Any] = {"_row": idx}
            for col in shared_cols:
                lval = left_sub[col][idx]
                rval = right_sub[col][idx]
                if lval != rval:
                    change[col] = {"from": lval, "to": rval}
            if len(change) > 1:
                result.sample_changes.append(change)


def _hash_dataframe(df: pl.DataFrame) -> str:
    """Compute a stable hash for a DataFrame."""
    h = hashlib.sha256()
    # Include schema
    for col, dtype in df.schema.items():
        h.update(f"{col}:{dtype}".encode())
    # Include shape
    h.update(f"{df.height}x{df.width}".encode())
    # Include data sample for content hashing
    if df.height > 0:
        try:
            data_bytes = df.to_pandas().to_csv(index=False).encode()
            h.update(data_bytes)
        except Exception:
            # Fallback: hash a string repr of first/last rows
            sample = pl.concat([df.head(10), df.tail(10)]) if df.height > 20 else df
            h.update(str(sample).encode())
    return h.hexdigest()[:16]
