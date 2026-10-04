"""Append-only, hash-chained audit journal.

Every entry records who did what to which sheet, and carries the hash of the
previous entry. Changing, removing, or reordering any past entry breaks the
chain, which `verify()` detects.

This is separate from the undo stack: undoing a step *appends* an "undo" entry
here rather than deleting history.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

GENESIS_HASH = "0" * 64


def _canonical(data: dict[str, Any]) -> str:
    return json.dumps(data, sort_keys=True, separators=(",", ":"), default=str)


@dataclass(frozen=True)
class JournalEntry:
    """One audit record.

    Attributes:
        seq: Position in the journal (0-based).
        action: What happened ("load", "step", "undo", "redo", "branch", ...).
        sheet: Sheet the action targeted (or "" for workspace-level actions).
        author: "human", "agent:<name>", "system", ...
        payload: JSON-serializable details (step dict, source, hashes...).
        timestamp: ISO-8601 UTC timestamp.
        id: Unique entry id.
        prev_hash: Hash of the previous entry (GENESIS_HASH for the first).
        hash: SHA-256 over this entry's content and `prev_hash`.
    """

    seq: int
    action: str
    sheet: str
    author: str
    payload: dict[str, Any]
    timestamp: str
    id: str
    prev_hash: str
    hash: str = field(default="")

    def content(self) -> dict[str, Any]:
        d = asdict(self)
        d.pop("hash")
        return d

    def compute_hash(self) -> str:
        return hashlib.sha256(_canonical(self.content()).encode()).hexdigest()


class Journal:
    """An append-only list of hash-chained `JournalEntry` records."""

    def __init__(self) -> None:
        self._entries: list[JournalEntry] = []

    @property
    def entries(self) -> list[JournalEntry]:
        return list(self._entries)

    @property
    def head_hash(self) -> str:
        return self._entries[-1].hash if self._entries else GENESIS_HASH

    def __len__(self) -> int:
        return len(self._entries)

    def append(
        self,
        action: str,
        *,
        sheet: str = "",
        author: str = "human",
        payload: dict[str, Any] | None = None,
    ) -> JournalEntry:
        # Round-trip the payload through JSON so the stored entry is exactly what
        # gets hashed and exported (no live DataFrames or mutable references).
        clean_payload = json.loads(_canonical(payload or {}))
        entry = JournalEntry(
            seq=len(self._entries),
            action=action,
            sheet=sheet,
            author=author,
            payload=clean_payload,
            timestamp=datetime.now(timezone.utc).isoformat(),
            id=uuid.uuid4().hex,
            prev_hash=self.head_hash,
        )
        entry = JournalEntry(**{**entry.content(), "hash": entry.compute_hash()})
        self._entries.append(entry)
        return entry

    def verify(self) -> int | None:
        """Check the chain. Returns the seq of the first bad entry, or None if intact."""
        prev = GENESIS_HASH
        for i, entry in enumerate(self._entries):
            if entry.seq != i or entry.prev_hash != prev or entry.compute_hash() != entry.hash:
                return i
            prev = entry.hash
        return None

    def to_records(self) -> list[dict[str, Any]]:
        return [asdict(e) for e in self._entries]

    def export_jsonl(self, path: str | Path) -> Path:
        path = Path(path)
        with path.open("w") as f:
            for record in self.to_records():
                f.write(_canonical(record) + "\n")
        return path

    @classmethod
    def from_records(cls, records: list[dict[str, Any]]) -> Journal:
        """Rebuild a journal from exported records (call `verify()` afterwards)."""
        journal = cls()
        journal._entries = [JournalEntry(**r) for r in records]
        return journal

    @classmethod
    def load_jsonl(cls, path: str | Path) -> Journal:
        lines = Path(path).read_text().splitlines()
        return cls.from_records([json.loads(line) for line in lines if line.strip()])
