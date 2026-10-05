"""The session server: JSON-RPC 2.0 over a Unix socket, one JSON object per line.

Protocol:

1. The client sends ``{"jsonrpc": "2.0", "id": 1, "method": "hello",
   "params": {"token": "...", "client": "claude"}}``. A wrong token closes the
   connection. Clients are always agents: their author is ``agent:<client>``.
2. Then any method in `METHODS`, with keyword params. Results come back as
   ``{"id": ..., "result": ...}``; failures as ``{"id": ..., "error": {"code",
   "message"}}`` (-32001: not allowed by policy, -32002: the person has
   control, -32000: request failed, -32601: unknown method).
3. ``subscribe`` streams session events as ``{"method": "event", "params":
   {"type": ..., ...}}`` notifications.

Methods that act on what the person sees (commands, highlights, narration,
changes) pass through the session's demo gate, which paces them during
demonstrations and pauses them when the person takes control.
"""

from __future__ import annotations

import asyncio
import contextlib
import hmac
import inspect
import json
import os
import re
import secrets
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..core.policy import PolicyError
from ..core.session import ControlError, Session, SessionError
from .registry import SessionInfo, safe_name, sessions_dir, unique_name, write_info

#: method -> (Session attribute, gated by the demo pacing)
METHODS: dict[str, tuple[str, bool]] = {
    "status": ("status", False),
    "sheets": ("sheets", False),
    "open": ("open", True),
    "view": ("view", False),
    "profile": ("profile", False),
    "query": ("query", False),
    "steps": ("steps", False),
    "preview": ("preview", False),
    "propose_step": ("propose_step", True),
    "step_action": ("step_action", True),
    "undo": ("undo", True),
    "redo": ("redo", True),
    "diff": ("diff", False),
    "export": ("export", False),
    "get_selection": ("get_selection", False),
    "highlight": ("highlight", True),
    "clear_highlights": ("clear_highlights", False),
    "narrate": ("narrate", True),
    "screen": ("screen", False),
    "command": ("command", True),
    "set_policy": ("set_policy", False),
    "start_demo": ("start_demo", False),
    "end_demo": ("end_demo", False),
    "add_alert": ("add_alert", False),
    "alerts": ("alerts", False),
    "watch": ("watch", False),
}

ERROR_POLICY, ERROR_CONTROL, ERROR_FAILED, ERROR_METHOD, ERROR_INTERNAL = (
    -32001,
    -32002,
    -32000,
    -32601,
    -32603,
)


def _dumps(obj: Any) -> bytes:
    return (json.dumps(obj, default=str, separators=(",", ":")) + "\n").encode()


class SessionServer:
    """Serves a `Session` to agents. Run `start()` inside an asyncio event loop."""

    def __init__(
        self,
        session: Session,
        *,
        name: str | None = None,
        directory: Path | None = None,
        title: str = "",
    ) -> None:
        self.session = session
        self.directory = directory or sessions_dir()
        self.name = unique_name(name or session.name, self.directory)
        self.title = title
        self.token = secrets.token_urlsafe(24)
        self.info: SessionInfo | None = None
        self._server: asyncio.AbstractServer | None = None
        self._subscribers: set[asyncio.StreamWriter] = set()
        self._unsubscribe = None
        self.clients: dict[asyncio.StreamWriter, str] = {}

    @property
    def socket_path(self) -> Path:
        return self.directory / f"{self.name}.sock"

    async def start(self) -> SessionInfo:
        path = self.socket_path
        path.unlink(missing_ok=True)
        self._server = await asyncio.start_unix_server(self._handle, path=str(path))
        os.chmod(path, 0o600)
        self.info = SessionInfo(
            name=self.name,
            socket=str(path),
            token=self.token,
            pid=os.getpid(),
            started=datetime.now(timezone.utc).isoformat(),
            cwd=os.getcwd(),
            title=self.title,
        )
        write_info(self.info, self.directory)
        self.session.name = self.name
        self._unsubscribe = self.session.subscribe(self._broadcast)
        return self.info

    async def stop(self) -> None:
        if self._unsubscribe:
            self._unsubscribe()
        for writer in list(self.clients):
            with contextlib.suppress(Exception):
                writer.close()
        if self._server is not None:
            self._server.close()
            with contextlib.suppress(Exception):
                await self._server.wait_closed()
        self.socket_path.unlink(missing_ok=True)
        (self.directory / f"{self.name}.json").unlink(missing_ok=True)

    def _broadcast(self, event: str, data: dict[str, Any]) -> None:
        message = _dumps({"jsonrpc": "2.0", "method": "event", "params": {"type": event, **data}})
        for writer in list(self._subscribers):
            try:
                writer.write(message)
            except Exception:
                self._subscribers.discard(writer)

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        author: str | None = None
        try:
            while True:
                line = await reader.readline()
                if not line:
                    break
                try:
                    message = json.loads(line)
                except ValueError:
                    writer.write(
                        _dumps(
                            {
                                "jsonrpc": "2.0",
                                "id": None,
                                "error": {"code": -32700, "message": "Invalid JSON"},
                            }
                        )
                    )
                    continue
                request_id = message.get("id")
                method = message.get("method")
                params = message.get("params") or {}
                if author is None:
                    if method != "hello" or not hmac.compare_digest(
                        str(params.get("token", "")), self.token
                    ):
                        writer.write(
                            _dumps(
                                {
                                    "jsonrpc": "2.0",
                                    "id": request_id,
                                    "error": {
                                        "code": ERROR_POLICY,
                                        "message": "Authentication failed",
                                    },
                                }
                            )
                        )
                        await writer.drain()
                        break
                    client = safe_name(re.sub(r"^agent:", "", str(params.get("client") or "agent")))
                    author = f"agent:{client}"
                    self.clients[writer] = author
                    result = self.session.connect(author, {"client": client})
                    writer.write(_dumps({"jsonrpc": "2.0", "id": request_id, "result": result}))
                    await writer.drain()
                    continue
                if method == "subscribe":
                    self._subscribers.add(writer)
                    writer.write(
                        _dumps({"jsonrpc": "2.0", "id": request_id, "result": {"subscribed": True}})
                    )
                    await writer.drain()
                    continue
                response = await self._dispatch(method, params, author)
                writer.write(_dumps({"jsonrpc": "2.0", "id": request_id, **response}))
                await writer.drain()
        except (ConnectionError, asyncio.IncompleteReadError):
            pass
        finally:
            self._subscribers.discard(writer)
            self.clients.pop(writer, None)
            if author is not None:
                self.session.disconnect(author)
            with contextlib.suppress(Exception):
                writer.close()

    async def _dispatch(
        self, method: str | None, params: dict[str, Any], author: str
    ) -> dict[str, Any]:
        if method not in METHODS:
            return {
                "error": {
                    "code": ERROR_METHOD,
                    "message": f"Unknown method '{method}'. Methods: {', '.join(METHODS)}",
                }
            }
        attribute, gated = METHODS[method]
        params = {k: v for k, v in params.items() if k != "author"}
        try:
            if gated:
                await self.session.gate(author)
            result = getattr(self.session, attribute)(**params, author=author)
            if inspect.isawaitable(result):
                result = await result
            return {"result": result}
        except PolicyError as e:
            return {"error": {"code": ERROR_POLICY, "message": str(e)}}
        except ControlError as e:
            return {"error": {"code": ERROR_CONTROL, "message": str(e)}}
        except (SessionError, ValueError, KeyError) as e:
            return {"error": {"code": ERROR_FAILED, "message": str(e)}}
        except TypeError as e:
            return {"error": {"code": ERROR_FAILED, "message": f"Bad parameters for {method}: {e}"}}
        except Exception as e:  # noqa: BLE001 - report, don't crash the server
            return {"error": {"code": ERROR_INTERNAL, "message": f"{type(e).__name__}: {e}"}}
