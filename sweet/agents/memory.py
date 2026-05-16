"""Agent Memory: Persistent context for Sweet agents.

Agents remember context across sessions:
- Data context: What datasets have been loaded, their schemas, past transforms
- User preferences: Preferred date formats, naming conventions, quality thresholds
- Domain knowledge: Business rules, valid value ranges, known relationships
- History: What worked before on similar data (successful recipes, common patterns)
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

DEFAULT_MEMORY_DIR = Path.home() / ".sweet" / "memory"


@dataclass
class DatasetFingerprint:
    """A lightweight fingerprint of a dataset for matching similar data later."""

    columns: list[str]
    dtypes: dict[str, str]
    row_count: int
    column_count: int
    file_name: str | None = None
    content_hash: str | None = None

    def similarity(self, other: DatasetFingerprint) -> float:
        """Compute similarity score (0.0–1.0) between two fingerprints."""
        if not self.columns or not other.columns:
            return 0.0

        # Jaccard similarity on column names
        set_a = set(self.columns)
        set_b = set(other.columns)
        if not set_a and not set_b:
            return 0.0
        jaccard = len(set_a & set_b) / len(set_a | set_b)

        # Dtype overlap for shared columns
        shared = set_a & set_b
        if shared:
            dtype_match = sum(
                1 for c in shared if self.dtypes.get(c) == other.dtypes.get(c)
            ) / len(shared)
        else:
            dtype_match = 0.0

        return 0.6 * jaccard + 0.4 * dtype_match

    def to_dict(self) -> dict[str, Any]:
        return {
            "columns": self.columns,
            "dtypes": self.dtypes,
            "row_count": self.row_count,
            "column_count": self.column_count,
            "file_name": self.file_name,
            "content_hash": self.content_hash,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> DatasetFingerprint:
        return cls(
            columns=d["columns"],
            dtypes=d["dtypes"],
            row_count=d["row_count"],
            column_count=d["column_count"],
            file_name=d.get("file_name"),
            content_hash=d.get("content_hash"),
        )


@dataclass
class RunRecord:
    """Record of a completed agent run."""

    timestamp: str
    recipe_name: str | None
    steps: list[str]
    dataset_fingerprint: DatasetFingerprint
    success: bool
    n_passed: int
    n_failed: int
    n_rolled_back: int
    duration_s: float
    notes: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "timestamp": self.timestamp,
            "recipe_name": self.recipe_name,
            "steps": self.steps,
            "dataset_fingerprint": self.dataset_fingerprint.to_dict(),
            "success": self.success,
            "n_passed": self.n_passed,
            "n_failed": self.n_failed,
            "n_rolled_back": self.n_rolled_back,
            "duration_s": self.duration_s,
            "notes": self.notes,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> RunRecord:
        return cls(
            timestamp=d["timestamp"],
            recipe_name=d.get("recipe_name"),
            steps=d["steps"],
            dataset_fingerprint=DatasetFingerprint.from_dict(d["dataset_fingerprint"]),
            success=d["success"],
            n_passed=d["n_passed"],
            n_failed=d["n_failed"],
            n_rolled_back=d["n_rolled_back"],
            duration_s=d["duration_s"],
            notes=d.get("notes"),
        )


@dataclass
class AgentMemory:
    """Persistent memory store for Sweet agents.

    Stores:
    - User preferences (date formats, naming conventions, thresholds)
    - Domain knowledge (business rules, valid ranges)
    - Run history (what recipes/steps worked on what data)
    - Dataset fingerprints (for finding similar past work)

    Memory persists to disk as JSON files in ~/.sweet/memory/ by default.
    """

    preferences: dict[str, Any] = field(default_factory=dict)
    domain_rules: dict[str, Any] = field(default_factory=dict)
    run_history: list[RunRecord] = field(default_factory=list)
    _memory_dir: Path = field(default_factory=lambda: DEFAULT_MEMORY_DIR)

    def __post_init__(self) -> None:
        self._memory_dir.mkdir(parents=True, exist_ok=True)

    @classmethod
    def load(cls, memory_dir: Path | None = None) -> AgentMemory:
        """Load memory from disk.

        Args:
            memory_dir: Directory to load from. Defaults to ~/.sweet/memory/.

        Returns:
            AgentMemory instance with loaded state.
        """
        directory = memory_dir or DEFAULT_MEMORY_DIR
        directory.mkdir(parents=True, exist_ok=True)

        preferences = {}
        domain_rules = {}
        run_history: list[RunRecord] = []

        prefs_file = directory / "preferences.json"
        if prefs_file.exists():
            preferences = json.loads(prefs_file.read_text())

        rules_file = directory / "domain_rules.json"
        if rules_file.exists():
            domain_rules = json.loads(rules_file.read_text())

        history_file = directory / "run_history.json"
        if history_file.exists():
            records = json.loads(history_file.read_text())
            run_history = [RunRecord.from_dict(r) for r in records]

        return cls(
            preferences=preferences,
            domain_rules=domain_rules,
            run_history=run_history,
            _memory_dir=directory,
        )

    def save(self) -> None:
        """Persist all memory to disk."""
        self._memory_dir.mkdir(parents=True, exist_ok=True)

        prefs_file = self._memory_dir / "preferences.json"
        prefs_file.write_text(json.dumps(self.preferences, indent=2))

        rules_file = self._memory_dir / "domain_rules.json"
        rules_file.write_text(json.dumps(self.domain_rules, indent=2))

        history_file = self._memory_dir / "run_history.json"
        history_file.write_text(
            json.dumps([r.to_dict() for r in self.run_history], indent=2)
        )

    # -------------------------------------------------------------------------
    # Preferences
    # -------------------------------------------------------------------------

    def set_preference(self, key: str, value: Any) -> None:
        """Set a user preference.

        Args:
            key: Preference key (e.g., "date_format", "null_handling").
            value: Preference value.
        """
        self.preferences[key] = value

    def get_preference(self, key: str, default: Any = None) -> Any:
        """Get a user preference.

        Args:
            key: Preference key.
            default: Default value if not set.

        Returns:
            The preference value or the default.
        """
        return self.preferences.get(key, default)

    # -------------------------------------------------------------------------
    # Domain Rules
    # -------------------------------------------------------------------------

    def add_rule(self, name: str, rule: dict[str, Any]) -> None:
        """Add a domain rule.

        Args:
            name: Rule name (e.g., "revenue_positive", "valid_countries").
            rule: Rule definition dict (column, check, severity, etc.).
        """
        self.domain_rules[name] = rule

    def get_rule(self, name: str) -> dict[str, Any] | None:
        """Get a domain rule by name."""
        return self.domain_rules.get(name)

    def list_rules(self) -> list[dict[str, Any]]:
        """List all domain rules."""
        return [{"name": k, **v} for k, v in self.domain_rules.items()]

    # -------------------------------------------------------------------------
    # Run History
    # -------------------------------------------------------------------------

    def record_run(self, record: RunRecord) -> None:
        """Record a completed agent run.

        Args:
            record: The RunRecord to store.
        """
        self.run_history.append(record)
        # Keep at most 500 records
        if len(self.run_history) > 500:
            self.run_history = self.run_history[-500:]

    def find_similar_runs(
        self, fingerprint: DatasetFingerprint, threshold: float = 0.5, limit: int = 5
    ) -> list[RunRecord]:
        """Find past runs on similar datasets.

        Args:
            fingerprint: The current dataset's fingerprint.
            threshold: Minimum similarity score (0.0–1.0).
            limit: Maximum number of results.

        Returns:
            List of RunRecords for similar datasets, most recent first.
        """
        scored = []
        for record in reversed(self.run_history):
            score = fingerprint.similarity(record.dataset_fingerprint)
            if score >= threshold:
                scored.append((score, record))

        scored.sort(key=lambda x: x[0], reverse=True)
        return [record for _, record in scored[:limit]]

    def suggest_recipe(self, fingerprint: DatasetFingerprint) -> str | None:
        """Suggest a recipe based on what worked on similar data before.

        Args:
            fingerprint: Current dataset fingerprint.

        Returns:
            Recipe name that succeeded on similar data, or None.
        """
        similar = self.find_similar_runs(fingerprint, threshold=0.6)
        # Find most common successful recipe
        recipe_counts: dict[str, int] = {}
        for record in similar:
            if record.success and record.recipe_name:
                recipe_counts[record.recipe_name] = recipe_counts.get(record.recipe_name, 0) + 1

        if not recipe_counts:
            return None
        return max(recipe_counts, key=recipe_counts.get)  # type: ignore[arg-type]

    # -------------------------------------------------------------------------
    # Dataset Fingerprinting
    # -------------------------------------------------------------------------

    @staticmethod
    def fingerprint_workspace(workspace: Any) -> DatasetFingerprint:
        """Create a fingerprint of the current workspace data.

        Args:
            workspace: A Workspace instance.

        Returns:
            DatasetFingerprint for the current data.
        """
        df = workspace.df
        if df is None:
            return DatasetFingerprint(
                columns=[], dtypes={}, row_count=0, column_count=0
            )

        # Compute a content hash from first/last few rows + schema
        schema_str = str(sorted(df.schema.items()))
        sample_str = str(df.head(5).to_dicts()) + str(df.tail(5).to_dicts())
        content_hash = hashlib.sha256(
            (schema_str + sample_str).encode()
        ).hexdigest()[:16]

        return DatasetFingerprint(
            columns=df.columns,
            dtypes={col: str(dtype) for col, dtype in df.schema.items()},
            row_count=df.height,
            column_count=df.width,
            file_name=getattr(workspace, "_source_file", None),
            content_hash=content_hash,
        )

    # -------------------------------------------------------------------------
    # Summary
    # -------------------------------------------------------------------------

    def summary(self) -> dict[str, Any]:
        """Get a summary of memory contents.

        Returns:
            Dict with counts of preferences, rules, and history records.
        """
        successful_runs = sum(1 for r in self.run_history if r.success)
        return {
            "n_preferences": len(self.preferences),
            "n_domain_rules": len(self.domain_rules),
            "n_run_records": len(self.run_history),
            "n_successful_runs": successful_runs,
            "memory_dir": str(self._memory_dir),
        }
