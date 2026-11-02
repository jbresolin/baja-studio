"""How the client reaches an agent: real network, or (in tests) an in-process fake.

`AgentClient` never imports httpx2 or websockets itself; it asks a transport
for an HTTP client and a WebSocket. `NetworkTransport` is the real one. The
fake agent's `InProcessTransport` (fakes/in_process.py) is the test one.

Every network wait has an explicit limit here, because an agent laptop that
loses power mid-request never answers at all.
"""

import json
from collections.abc import AsyncIterator, Mapping
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from typing import Any, Protocol

import httpx2
import websockets.asyncio.client
import websockets.exceptions

# Commands and info: an agent on the LAN answers in milliseconds, so two
# seconds means it is not going to.
COMMAND_TIMEOUT = httpx2.Timeout(connect=2.0, read=2.0, write=2.0, pool=2.0)
# Opening the state WebSocket, and closing it politely before giving up.
WEBSOCKET_OPEN_TIMEOUT_SECONDS = 3.0
WEBSOCKET_CLOSE_TIMEOUT_SECONDS = 1.0
# A state message for a dozen cameras is a few kilobytes; this is generous.
WEBSOCKET_MAX_MESSAGE_BYTES = 1024 * 1024


class AgentUnreachableError(Exception):
    """The agent did not answer: refused, timed out, or the connection dropped."""


class AgentSocketClosedError(Exception):
    """The state WebSocket closed (or never opened)."""


class AgentHandshakeRejectedError(AgentSocketClosedError):
    """The agent refused the WebSocket handshake, e.g. HTTP 401 for a bad token."""

    def __init__(self, status_code: int) -> None:
        """Keep the HTTP status so the client can tell a bad token from other refusals."""
        super().__init__(f"the agent refused the WebSocket (HTTP {status_code})")
        self.status_code = status_code


class AgentSocket(Protocol):
    """An open state WebSocket, carrying JSON objects."""

    async def send_json(self, message: dict[str, Any]) -> None:
        """Send one JSON object; raises `AgentSocketClosedError` if the socket is gone."""

    async def receive_json(self) -> Any:
        """Wait for the next JSON message; raises `AgentSocketClosedError` when closed."""


class AgentTransport(Protocol):
    """Makes the HTTP client and WebSocket the `AgentClient` uses."""

    def http_client(
        self, base_url: str, headers: Mapping[str, str], timeout: httpx2.Timeout
    ) -> httpx2.AsyncClient:
        """A new HTTP client for `base_url` (e.g. http://10.0.0.5:8890)."""

    def open_websocket(
        self, url: str, headers: Mapping[str, str]
    ) -> AbstractAsyncContextManager[AgentSocket]:
        """Open the WebSocket at `url` (ws://...), closing it when the `async with` ends.

        Entering raises `AgentHandshakeRejectedError` if the agent refuses, or
        `AgentUnreachableError` if it cannot be reached.
        """


class _NetworkSocket:
    """`AgentSocket` over a `websockets` connection."""

    def __init__(self, connection: websockets.asyncio.client.ClientConnection) -> None:
        self._connection = connection

    async def send_json(self, message: dict[str, Any]) -> None:
        try:
            await self._connection.send(json.dumps(message))
        except websockets.exceptions.ConnectionClosed as closed:
            raise AgentSocketClosedError(str(closed)) from closed

    async def receive_json(self) -> Any:
        try:
            text = await self._connection.recv()
        except websockets.exceptions.ConnectionClosed as closed:
            raise AgentSocketClosedError(str(closed)) from closed
        return json.loads(text)


class NetworkTransport:
    """The real transport: httpx2 for HTTP, `websockets` for the state socket."""

    def http_client(
        self, base_url: str, headers: Mapping[str, str], timeout: httpx2.Timeout
    ) -> httpx2.AsyncClient:
        """An httpx2 client that ignores proxy environment variables (agents are on the LAN)."""
        return httpx2.AsyncClient(
            base_url=base_url, headers=dict(headers), timeout=timeout, trust_env=False
        )

    @asynccontextmanager
    async def open_websocket(
        self, url: str, headers: Mapping[str, str]
    ) -> AsyncIterator[AgentSocket]:
        """Open with explicit open/close timeouts; protocol-level pings are left to the
        contract's own `{type: "ping"}`, so both sides agree on what silence means."""
        try:
            connection = await websockets.asyncio.client.connect(
                url,
                additional_headers=dict(headers),
                open_timeout=WEBSOCKET_OPEN_TIMEOUT_SECONDS,
                close_timeout=WEBSOCKET_CLOSE_TIMEOUT_SECONDS,
                ping_interval=None,
                max_size=WEBSOCKET_MAX_MESSAGE_BYTES,
                proxy=None,
            )
        except websockets.exceptions.InvalidStatus as refused:
            raise AgentHandshakeRejectedError(refused.response.status_code) from refused
        except (OSError, TimeoutError, websockets.exceptions.InvalidHandshake) as error:
            raise AgentUnreachableError(f"cannot open {url}: {error}") from error
        try:
            yield _NetworkSocket(connection)
        finally:
            await connection.close()
