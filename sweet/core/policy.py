"""Governance for agent access: permission modes and column masking.

**Permission modes** limit what agents may do to data:

- ``read-only``: agents can look (through masks) but not change anything.
- ``propose`` (default): agent changes become proposals a human accepts or rejects.
- ``auto``: agent steps apply directly (still journaled and undoable).

**Masking** hides column values from agents. Humans see raw data; anything an
agent reads (row windows, query results, statistics, diffs, selections, the
screen) passes through `Policy.mask_frame()`. Masking is off until a human turns
it on (a policy file, a flag, or the TUI) or the agent's user asks for it.
Agents may add or tighten masks, never remove them.

Masks follow data: a renamed or derived column stays masked, and free-form code
or SQL that references a masked column masks *every* column it outputs (fail
closed). Masks hide values, not existence: row counts, null counts, and distinct
counts of masked columns remain visible.
"""

from __future__ import annotations

import re
import secrets
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import polars as pl

MODES = ("read-only", "propose", "auto")
METHODS = ("redact", "hash", "partial", "null")
REDACTED = "•••"


class PolicyError(PermissionError):
    """An action isn't allowed by the session's policy."""


def is_agent(author: str) -> bool:
    return author.startswith("agent")


@dataclass
class ColumnMask:
    column: str
    method: str = "redact"
    by: str = "human"
    reason: str = ""


@dataclass
class Policy:
    """A session's permission mode and masks.

    Attributes:
        mode: Agent permission mode (see module docs).
        masks: {column: ColumnMask}. Column names are matched across sheets.
        mask_pii: Automatically mask columns detected as PII.
        salt: Per-session salt for the "hash" method (hashes are joinable within
            a session but not across sessions).
    """

    mode: str = "propose"
    masks: dict[str, ColumnMask] = field(default_factory=dict)
    mask_pii: bool = False
    salt: int = field(default_factory=lambda: secrets.randbits(62))

    # -- changes ----------------------------------------------------------------

    def set_mode(self, mode: str, *, author: str = "human") -> None:
        if mode not in MODES:
            raise ValueError(f"Unknown mode '{mode}'. Choose from: {', '.join(MODES)}")
        if is_agent(author) and MODES.index(mode) > MODES.index(self.mode):
            raise PolicyError("Agents can only reduce their own permissions")
        self.mode = mode

    def add_mask(
        self, column: str, method: str = "redact", *, author: str = "human", reason: str = ""
    ) -> None:
        if method not in METHODS:
            raise ValueError(f"Unknown mask method '{method}'. Choose from: {', '.join(METHODS)}")
        existing = self.masks.get(column)
        if (
            existing is not None
            and is_agent(author)
            and _strength(method) < _strength(existing.method)
        ):
            raise PolicyError(f"Agents can't weaken the mask on '{column}'")
        self.masks[column] = ColumnMask(column, method, author, reason)

    def remove_mask(self, column: str, *, author: str = "human") -> None:
        if is_agent(author):
            raise PolicyError("Only a person can remove a mask")
        self.masks.pop(column, None)

    def set_mask_pii(self, enabled: bool, *, author: str = "human") -> None:
        if is_agent(author) and not enabled:
            raise PolicyError("Only a person can turn off PII masking")
        self.mask_pii = enabled

    # -- applying ---------------------------------------------------------------

    @property
    def active(self) -> bool:
        return bool(self.masks) or self.mask_pii

    def masked_columns(
        self, columns: list[str], pii: dict[str, str] | None = None
    ) -> dict[str, str]:
        """{column: method} for the given columns (explicit masks, then detected PII)."""
        out = {c: self.masks[c].method for c in columns if c in self.masks}
        if self.mask_pii and pii:
            for c in columns:
                if c in pii and c not in out:
                    out[c] = "redact"
        return out

    def mask_frame(
        self, frame: pl.DataFrame | pl.LazyFrame, pii: dict[str, str] | None = None
    ) -> pl.DataFrame | pl.LazyFrame:
        """Return `frame` with masked columns' values replaced (shape is unchanged)."""
        schema = frame.collect_schema() if isinstance(frame, pl.LazyFrame) else frame.schema
        masked = self.masked_columns(schema.names(), pii)
        if not masked:
            return frame
        return frame.with_columns([self._mask_expr(c, method) for c, method in masked.items()])

    def _mask_expr(self, column: str, method: str) -> pl.Expr:
        col = pl.col(column)
        text = col.cast(pl.String)
        if method == "null":
            masked = pl.lit(None, dtype=pl.String)
        elif method == "hash":
            masked = pl.lit("h:") + text.hash(self.salt).cast(pl.String).str.slice(0, 10)
        elif method == "partial":
            masked = pl.lit(REDACTED) + text.str.slice(-4)
        else:
            masked = pl.lit(REDACTED)
        return (
            pl.when(col.is_null())
            .then(pl.lit(None, dtype=pl.String))
            .otherwise(masked)
            .alias(column)
        )

    # -- taint ------------------------------------------------------------------

    def propagate(
        self,
        step: Any,
        before: list[str],
        after: list[str],
        pii: dict[str, str] | None = None,
    ) -> list[str]:
        """Extend masks to columns derived from masked ones by `step`.

        Returns the newly masked column names.
        """
        masked = self.masked_columns(before, pii)
        if not masked:
            return []
        new_columns = [c for c in after if c not in before]
        tainted: list[str] = []
        kind, params = step.kind, step.params
        if kind == "rename":
            tainted = [new for old, new in params.get("mapping", {}).items() if old in masked]
        elif kind == "mutate":
            if references_any(params.get("sql") or params.get("expr") or "", masked):
                tainted = [params["column"]]
        elif kind in ("polars", "sql", "manual"):
            code = params.get("code") or params.get("query") or ""
            if kind == "manual" or references_any(code, masked):
                tainted = list(after)  # Fail closed: anything could carry masked values
        else:
            tainted = [c for c in new_columns if references_any(str(params), masked)]
        added = []
        for column in tainted:
            if column not in self.masks and column not in masked:
                strongest = max((masked[c] for c in masked), key=_strength)
                self.masks[column] = ColumnMask(
                    column, strongest, "policy", "derived from a masked column"
                )
                added.append(column)
            elif column in masked and column not in self.masks:
                self.masks[column] = ColumnMask(column, masked[column], "policy", "detected PII")
        return added

    # -- persistence ------------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "mask_pii": self.mask_pii,
            "masks": {
                c: {"method": m.method, "by": m.by, "reason": m.reason}
                for c, m in self.masks.items()
            },
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Policy:
        policy = cls(mode=data.get("mode", "propose"), mask_pii=bool(data.get("mask_pii", False)))
        if policy.mode not in MODES:
            raise ValueError(f"Unknown mode '{policy.mode}' in policy")
        for column, spec in (data.get("masks") or {}).items():
            spec = {"method": spec} if isinstance(spec, str) else (spec or {})
            policy.add_mask(column, spec.get("method", "redact"), author="config")
        return policy

    @classmethod
    def load(cls, path: str | Path) -> Policy:
        from yaml12 import read_yaml

        data = read_yaml(str(path)) or {}
        if not isinstance(data, dict):
            raise ValueError(f"{path}: a policy must be a YAML mapping")
        return cls.from_dict(data)

    @classmethod
    def discover(cls, start: str | Path | None = None) -> Policy | None:
        """Load `.sweet/policy.yaml` from `start` (default: cwd) or a parent directory."""
        directory = Path(start or Path.cwd()).resolve()
        for candidate in (directory, *directory.parents):
            path = candidate / ".sweet" / "policy.yaml"
            if path.is_file():
                return cls.load(path)
        return None


def _strength(method: str) -> int:
    return {"partial": 0, "hash": 1, "redact": 2, "null": 3}.get(method, 2)


def references_any(code: str, columns: dict[str, str] | list[str]) -> bool:
    """Whether `code` mentions any of `columns` (conservatively, as a substring)."""
    lowered = code.lower()
    return any(c.lower() in lowered for c in columns)


# -----------------------------------------------------------------------------
# PII detection
# -----------------------------------------------------------------------------

_NAME_PATTERNS: dict[str, re.Pattern[str]] = {
    "ssn": re.compile(r"(ssn|social.?security)", re.I),
    "credit_card": re.compile(r"(credit.?card|card.?num|cc.?num)", re.I),
    "phone": re.compile(r"(phone|mobile|cell|fax|tel)", re.I),
    "email": re.compile(r"(e.?mail)", re.I),
    "address": re.compile(r"(address|street|zip|postal)", re.I),
    "name": re.compile(r"(first.?name|last.?name|full.?name|surname|given.?name)", re.I),
    "date_of_birth": re.compile(r"(birth.?date|dob|date.?of.?birth)", re.I),
    "passport": re.compile(r"(passport)", re.I),
    "ip_address": re.compile(r"(ip.?addr|^ip$)", re.I),
}
_VALUE_PATTERNS: dict[str, re.Pattern[str]] = {
    "ssn": re.compile(r"^\d{3}-\d{2}-\d{4}$"),
    "credit_card": re.compile(r"^\d{4}[\s-]?\d{4}[\s-]?\d{4}[\s-]?\d{4}$"),
    "email": re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$"),
    "ip_address": re.compile(r"^\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}$"),
    "phone": re.compile(r"^\+?\(?\d{1,4}\)?[\s.-]?\d{2,4}[\s.-]?\d{3,4}[\s.-]?\d{0,4}$"),
}


def detect_pii(frame: pl.DataFrame | pl.LazyFrame, *, sample: int = 200) -> dict[str, str]:
    """{column: pii type} for columns that look like personal data.

    Checks column names and (for string columns) a sample of values.
    """
    lf = frame.lazy()
    schema = lf.collect_schema()
    string_cols = [c for c, t in schema.items() if t == pl.String]
    values = lf.select(string_cols).head(sample).collect() if string_cols else pl.DataFrame()
    found: dict[str, str] = {}
    for column in schema.names():
        if column in values.columns:
            non_null = [str(v) for v in values[column].drop_nulls().to_list()]
            if non_null:
                for kind, pattern in _VALUE_PATTERNS.items():
                    if sum(1 for v in non_null if pattern.match(v)) / len(non_null) >= 0.7:
                        found[column] = kind
                        break
        if column not in found:
            for kind, pattern in _NAME_PATTERNS.items():
                if pattern.search(column):
                    found[column] = kind
                    break
    return found
