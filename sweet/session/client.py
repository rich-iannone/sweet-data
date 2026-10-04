"""An asyncio client for the session server."""

from __future__ import annotations

import asyncio
import contextlib
import itertools
import json
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

from .registry import SessionInfo, find_session


class RemoteError(Exception):
    """The session refused or failed a request."""

    def __init__(self, code: int, message: str) -> None:
        super().__init__(message)
        self.code = code


class SessionClient:
    """Connects to a running session as an agent.

    Example:
        client = await SessionClient.connect(client="claude")
        print(await client.call("view", limit=5))
    """

    def __init__(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter, info: SessionInfo
    ):
        self.reader, self.writer, self.info = reader, writer, info
        self._ids = itertools.count(1)
        self._pending: dict[int, asyncio.Future] = {}
        self.events: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self._task = asyncio.create_task(self._read())
        self.status: dict[str, Any] = {}

    @classmethod
    async def connect(
        cls,
        name: str | None = None,
        *,
        client: str = "agent",
        info: SessionInfo | None = None,
        directory: Path | None = None,
    ) -> SessionClient:
        """Connect to a session (by name, or the most recent) and authenticate."""
        info = info or find_session(name, directory)
        if info is None:
            raise ConnectionError(
                "No running Sweet session found"
                + (f" named '{name}'" if name else "")
                + ". Start one with `sweet <data>`."
            )
        reader, writer = await asyncio.open_unix_connection(info.socket, limit=2**24)
        self = cls(reader, writer, info)
        self.status = await self.call("hello", token=info.token, client=client)
        return self

    async def _read(self) -> None:
        try:
            while line := await self.reader.readline():
                message = json.loads(line)
                if message.get("method") == "event":
                    await self.events.put(message["params"])
                    continue
                future = self._pending.pop(message.get("id"), None)
                if future is not None and not future.done():
                    if "error" in message:
                        future.set_exception(
                            RemoteError(message["error"]["code"], message["error"]["message"])
                        )
                    else:
                        future.set_result(message.get("result"))
        except (ConnectionError, asyncio.CancelledError):
            pass
        finally:
            for future in self._pending.values():
                if not future.done():
                    future.set_exception(ConnectionError("Session closed"))

    async def call(self, method: str, **params: Any) -> Any:
        request_id = next(self._ids)
        future = asyncio.get_running_loop().create_future()
        self._pending[request_id] = future
        payload = {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params}
        self.writer.write((json.dumps(payload, default=str) + "\n").encode())
        await self.writer.drain()
        return await future

    async def subscribe(self) -> AsyncIterator[dict[str, Any]]:
        await self.call("subscribe")
        while True:
            yield await self.events.get()

    async def close(self) -> None:
        self._task.cancel()
        with contextlib.suppress(Exception):
            self.writer.close()
            await self.writer.wait_closed()
