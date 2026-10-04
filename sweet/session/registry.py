"""Where running sessions advertise themselves.

Each running session writes ``<name>.json`` (mode 0600) next to its socket in
the sessions directory (``$SWEET_SESSIONS_DIR`` or ``~/.sweet/sessions``). The
file holds the socket path and the session's secret token; only the user who
started the session can read it.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import asdict, dataclass
from pathlib import Path


@dataclass
class SessionInfo:
    name: str
    socket: str
    token: str
    pid: int
    started: str
    cwd: str
    title: str = ""

    @property
    def alive(self) -> bool:
        if not Path(self.socket).exists():
            return False
        try:
            os.kill(self.pid, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        return True


def sessions_dir() -> Path:
    path = Path(os.environ.get("SWEET_SESSIONS_DIR") or Path.home() / ".sweet" / "sessions")
    path.mkdir(parents=True, exist_ok=True)
    try:
        path.chmod(0o700)
    except OSError:
        pass
    return path


def safe_name(name: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_.-]+", "-", name).strip("-.")
    return cleaned[:40] or "sweet"


def unique_name(base: str, directory: Path | None = None) -> str:
    directory = directory or sessions_dir()
    base = safe_name(base)
    name, n = base, 2
    while (directory / f"{name}.json").exists():
        existing = read_info(directory / f"{name}.json")
        if existing is None or not existing.alive:
            break
        name, n = f"{base}-{n}", n + 1
    return name


def write_info(info: SessionInfo, directory: Path | None = None) -> Path:
    directory = directory or sessions_dir()
    path = directory / f"{info.name}.json"
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(asdict(info), f)
    return path


def read_info(path: Path) -> SessionInfo | None:
    try:
        return SessionInfo(**json.loads(path.read_text()))
    except (OSError, ValueError, TypeError):
        return None


def list_sessions(directory: Path | None = None, *, clean: bool = True) -> list[SessionInfo]:
    """Running sessions, most recently started first (stale entries are removed)."""
    directory = directory or sessions_dir()
    found = []
    for path in directory.glob("*.json"):
        info = read_info(path)
        if info is not None and info.alive:
            found.append(info)
        elif clean:
            path.unlink(missing_ok=True)
            if info is not None:
                Path(info.socket).unlink(missing_ok=True)
    return sorted(found, key=lambda i: i.started, reverse=True)


def find_session(name: str | None = None, directory: Path | None = None) -> SessionInfo | None:
    """A running session by name, or the most recent one."""
    sessions = list_sessions(directory)
    if name is None:
        return sessions[0] if sessions else None
    return next((s for s in sessions if s.name == name), None)
