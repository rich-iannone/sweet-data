"""Live sessions: a token-authenticated socket that lets agents work in a running Sweet."""

from .client import RemoteError, SessionClient
from .registry import SessionInfo, find_session, list_sessions, sessions_dir
from .server import SessionServer

__all__ = [
    "RemoteError",
    "SessionClient",
    "SessionInfo",
    "SessionServer",
    "find_session",
    "list_sessions",
    "sessions_dir",
]
