"""Usage pattern learning — observe transforms and learn recurring behaviors.

Records what transforms users apply and builds a knowledge base of patterns:
- Column-type patterns: "when loading Float64 columns named *_pct, user divides by 100"
- Name patterns: "user always renames camelCase to snake_case"
- Sequence patterns: "after loading CSV, user always trims whitespace then casts dates"

Patterns are stored persistently and used to boost suggestion confidence.
"""

from __future__ import annotations

import json
import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


@dataclass
class PatternEntry:
    """A single observed usage pattern.

    Attributes:
        kind: Category of pattern (transform, cast, rename, drop, etc.).
        trigger: What triggered this (column name pattern, dtype, shape, etc.).
        action: What the user did (the expression or description).
        count: How many times this pattern has been observed.
        first_seen: When the pattern was first observed.
        last_seen: When the pattern was most recently observed.
        metadata: Additional context.
    """

    kind: str
    trigger: str
    action: str
    count: int = 1
    first_seen: str = ""
    last_seen: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "trigger": self.trigger,
            "action": self.action,
            "count": self.count,
            "first_seen": self.first_seen,
            "last_seen": self.last_seen,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "PatternEntry":
        return cls(
            kind=d["kind"],
            trigger=d["trigger"],
            action=d["action"],
            count=d.get("count", 1),
            first_seen=d.get("first_seen", ""),
            last_seen=d.get("last_seen", ""),
            metadata=d.get("metadata", {}),
        )


class PatternStore:
    """Persistent store for learned usage patterns.

    Observes workspace operations and builds a knowledge base of recurring
    behaviors. When a pattern has been seen multiple times, it becomes a
    high-confidence recommendation.

    Storage: ~/.sweet/memory/patterns.json
    """

    DEFAULT_DIR = Path.home() / ".sweet" / "memory"
    MIN_COUNT_TO_SUGGEST = 3  # Need 3+ observations before suggesting

    def __init__(self, memory_dir: Path | None = None) -> None:
        self._memory_dir = memory_dir or self.DEFAULT_DIR
        self._patterns: list[PatternEntry] = []
        self._load()

    @property
    def patterns(self) -> list[PatternEntry]:
        """All stored patterns."""
        return list(self._patterns)

    @property
    def pattern_count(self) -> int:
        """Number of distinct patterns stored."""
        return len(self._patterns)

    def observe(
        self,
        kind: str,
        trigger: str,
        action: str,
        *,
        metadata: dict[str, Any] | None = None,
    ) -> PatternEntry:
        """Record an observed usage pattern.

        If a matching pattern already exists (same kind + trigger + action),
        its count is incremented. Otherwise, a new entry is created.

        Args:
            kind: Pattern category (e.g., "cast", "trim", "rename", "drop").
            trigger: What triggered this (e.g., column dtype, name pattern).
            action: What was done (expression or description).
            metadata: Optional additional context.

        Returns:
            The created or updated PatternEntry.
        """
        now = datetime.now(timezone.utc).isoformat()

        # Check for existing matching pattern
        for pattern in self._patterns:
            if pattern.kind == kind and pattern.trigger == trigger and pattern.action == action:
                pattern.count += 1
                pattern.last_seen = now
                if metadata:
                    pattern.metadata.update(metadata)
                self._save()
                return pattern

        # New pattern
        entry = PatternEntry(
            kind=kind,
            trigger=trigger,
            action=action,
            count=1,
            first_seen=now,
            last_seen=now,
            metadata=metadata or {},
        )
        self._patterns.append(entry)
        self._save()
        return entry

    def query(
        self,
        *,
        kind: str | None = None,
        trigger: str | None = None,
        min_count: int | None = None,
    ) -> list[PatternEntry]:
        """Query patterns matching the given criteria.

        Args:
            kind: Filter by pattern kind.
            trigger: Filter by trigger (exact match or regex if starts with ^).
            min_count: Only return patterns seen at least this many times.

        Returns:
            Matching patterns sorted by count (descending).
        """
        results = self._patterns

        if kind is not None:
            results = [p for p in results if p.kind == kind]

        if trigger is not None:
            if trigger.startswith("^"):
                # Regex match
                pattern_re = re.compile(trigger)
                results = [p for p in results if pattern_re.search(p.trigger)]
            else:
                results = [p for p in results if p.trigger == trigger]

        if min_count is not None:
            results = [p for p in results if p.count >= min_count]

        return sorted(results, key=lambda p: -p.count)

    def suggestions_for(
        self,
        columns: dict[str, str],
        *,
        min_count: int | None = None,
    ) -> list[dict[str, Any]]:
        """Get learned suggestions applicable to the given columns.

        Matches patterns against column names and types, returning those
        that have been observed enough times to be confident.

        Args:
            columns: Dict of {column_name: dtype_string} from current data.
            min_count: Minimum observation count. Defaults to MIN_COUNT_TO_SUGGEST.

        Returns:
            List of suggestion dicts with: kind, trigger, action, count, confidence.
        """
        threshold = min_count if min_count is not None else self.MIN_COUNT_TO_SUGGEST
        suggestions: list[dict[str, Any]] = []

        for pattern in self._patterns:
            if pattern.count < threshold:
                continue

            # Check if trigger matches any current column
            matched = False

            if pattern.trigger.startswith("dtype:"):
                # Match by data type
                target_dtype = pattern.trigger.removeprefix("dtype:")
                if any(dtype == target_dtype for dtype in columns.values()):
                    matched = True
            elif pattern.trigger.startswith("name:"):
                # Match by column name pattern (glob-style)
                name_pattern = pattern.trigger.removeprefix("name:")
                regex = _glob_to_regex(name_pattern)
                if any(regex.match(col) for col in columns):
                    matched = True
            elif pattern.trigger.startswith("all:"):
                # Always matches (global patterns like "always trim whitespace")
                matched = True
            else:
                # Exact column name match
                if pattern.trigger in columns:
                    matched = True

            if matched:
                # Confidence scales with observation count
                confidence = min(0.5 + (pattern.count - threshold) * 0.1, 0.95)
                suggestions.append(
                    {
                        "kind": pattern.kind,
                        "trigger": pattern.trigger,
                        "action": pattern.action,
                        "count": pattern.count,
                        "confidence": confidence,
                        "source": "learned",
                    }
                )

        return sorted(suggestions, key=lambda s: (-s["confidence"], -s["count"]))

    def top_patterns(self, limit: int = 10) -> list[dict[str, Any]]:
        """Get the most frequently observed patterns.

        Args:
            limit: Maximum number to return.

        Returns:
            List of pattern dicts, sorted by count descending.
        """
        sorted_patterns = sorted(self._patterns, key=lambda p: -p.count)
        return [p.to_dict() for p in sorted_patterns[:limit]]

    def summary(self) -> dict[str, Any]:
        """Get a summary of stored patterns."""
        if not self._patterns:
            return {
                "total_patterns": 0,
                "actionable_patterns": 0,
                "kinds": {},
                "top_patterns": [],
            }

        kind_counts = Counter(p.kind for p in self._patterns)
        actionable = sum(1 for p in self._patterns if p.count >= self.MIN_COUNT_TO_SUGGEST)

        return {
            "total_patterns": len(self._patterns),
            "actionable_patterns": actionable,
            "kinds": dict(kind_counts),
            "top_patterns": self.top_patterns(5),
        }

    def forget(self, *, kind: str | None = None, trigger: str | None = None) -> int:
        """Remove patterns matching the criteria.

        Args:
            kind: Remove only this kind. None = all kinds.
            trigger: Remove only this trigger. None = all triggers.

        Returns:
            Number of patterns removed.
        """
        before = len(self._patterns)

        if kind is None and trigger is None:
            self._patterns.clear()
        else:
            self._patterns = [
                p
                for p in self._patterns
                if not (
                    (kind is None or p.kind == kind)
                    and (trigger is None or p.trigger == trigger)
                )
            ]

        removed = before - len(self._patterns)
        if removed > 0:
            self._save()
        return removed

    # -------------------------------------------------------------------------
    # Persistence
    # -------------------------------------------------------------------------

    def _load(self) -> None:
        """Load patterns from disk."""
        path = self._memory_dir / "patterns.json"
        if path.exists():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                self._patterns = [PatternEntry.from_dict(d) for d in data]
            except (json.JSONDecodeError, KeyError):
                self._patterns = []

    def _save(self) -> None:
        """Save patterns to disk."""
        self._memory_dir.mkdir(parents=True, exist_ok=True)
        path = self._memory_dir / "patterns.json"
        data = [p.to_dict() for p in self._patterns]
        path.write_text(json.dumps(data, indent=2), encoding="utf-8")


# ---------------------------------------------------------------------------
# Transform observer — extracts patterns from workspace operations
# ---------------------------------------------------------------------------


def observe_transform(
    store: PatternStore,
    expression: str,
    columns: dict[str, str],
    *,
    description: str = "",
) -> list[PatternEntry]:
    """Observe a transform and extract learnable patterns from it.

    Analyzes the expression to determine what kind of operation was performed
    and what triggered it (column types, names, etc.).

    Args:
        store: The PatternStore to record to.
        expression: The Polars expression that was applied.
        columns: Dict of {column_name: dtype_string} from the data.
        description: Optional human description of the transform.

    Returns:
        List of patterns that were recorded.
    """
    recorded: list[PatternEntry] = []

    # Detect cast patterns
    cast_match = re.findall(r"pl\.col\(['\"](\w+)['\"]\)\.cast\(pl\.(\w+)\)", expression)
    for col, target_type in cast_match:
        if col in columns:
            source_type = columns[col]
            entry = store.observe(
                kind="cast",
                trigger=f"dtype:{source_type}",
                action=f"cast to {target_type}",
                metadata={"column": col, "expression": expression},
            )
            recorded.append(entry)

    # Detect trim/strip patterns
    if "strip_chars" in expression or "strip()" in expression:
        entry = store.observe(
            kind="trim",
            trigger="all:string_columns",
            action="trim whitespace",
            metadata={"expression": expression},
        )
        recorded.append(entry)

    # Detect rename patterns
    if ".rename(" in expression:
        rename_match = re.findall(r"'(\w+)':\s*'(\w+)'", expression)
        for old_name, new_name in rename_match:
            # Detect naming convention change
            if _is_camel(old_name) and _is_snake(new_name):
                entry = store.observe(
                    kind="rename",
                    trigger="all:camel_case_columns",
                    action="convert to snake_case",
                    metadata={"example": f"{old_name} → {new_name}"},
                )
                recorded.append(entry)
                break  # One entry for the whole rename

    # Detect drop patterns
    drop_match = re.findall(r"\.drop\(['\"](\w+)['\"]\)", expression)
    for col in drop_match:
        if col in columns:
            entry = store.observe(
                kind="drop",
                trigger=f"name:{col}",
                action="drop column",
                metadata={"dtype": columns[col]},
            )
            recorded.append(entry)

    # Detect filter patterns (by description)
    if "filter" in expression.lower() and description:
        entry = store.observe(
            kind="filter",
            trigger="all:post_load",
            action=description or "filter rows",
            metadata={"expression": expression},
        )
        recorded.append(entry)

    return recorded


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _glob_to_regex(pattern: str) -> re.Pattern:
    """Convert a simple glob pattern to a regex."""
    escaped = re.escape(pattern)
    regex_str = escaped.replace(r"\*", ".*").replace(r"\?", ".")
    return re.compile(f"^{regex_str}$", re.IGNORECASE)


def _is_camel(name: str) -> bool:
    """Check if a name is camelCase or PascalCase."""
    return bool(re.search(r"[a-z][A-Z]", name))


def _is_snake(name: str) -> bool:
    """Check if a name is snake_case."""
    return "_" in name and name == name.lower()
