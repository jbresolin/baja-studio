"""Reach fake agents without a network: an `AgentTransport` for tests.

Each fake agent runs in a `FakeAgentRunner`: its own thread and event loop,
like a separate laptop. `InProcessTransport` routes the client's HTTP
requests and WebSocket to whichever runner is "plugged in" at an address,
so tests can unplug an agent (requests are refused, sockets close) and plug
a new one in (an agent restart) without opening a single port.
"""

import asyncio
import contextlib
import json
import threading
from collections.abc import AsyncIterator, Callable, Coroutine, Mapping, MutableMapping
from contextlib import asynccontextmanager
from typing import Any, TypeVar
from urllib.parse import urlsplit

import httpx2

from baja_studio.agents.transport import (
    AgentHandshakeRejectedError,
    AgentSocket,
    AgentSocketClosedError,
)
from baja_studio.fakes.fake_agent import FakeAgent, create_fake_agent_app

Result = TypeVar("Result")
# Generous: only reached if a test is broken, never in a passing run.
RUNNER_CALL_TIMEOUT_SECONDS = 10.0


class FakeAgentGoneError(Exception):
    """The fake agent was unplugged or stopped while something waited on it."""


class FakeAgentRunner:
    """Runs one fake agent's app on a private thread and event loop."""

    def __init__(self, agent: FakeAgent) -> None:
        """Build the app; nothing runs until `start`."""
        self.agent = agent
        self.app = create_fake_agent_app(agent)
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(
            target=self._loop.run_forever, name=f"fake-agent-{agent.agent_id}", daemon=True
        )

    def start(self) -> "FakeAgentRunner":
        """Start the loop and the agent's state pushes."""
        self._thread.start()
        self.call(self.agent.start())
        return self

    def stop(self) -> None:
        """Stop the agent, cancel whatever still runs on its loop, and end the thread."""
        if not self._thread.is_alive():
            return
        self.call(self.agent.stop())
        self.call(_cancel_other_tasks())
        self._loop.call_soon_threadsafe(self._loop.stop)
        self._thread.join(RUNNER_CALL_TIMEOUT_SECONDS)
        self._loop.close()

    def call(self, coroutine: Coroutine[Any, Any, Result]) -> Result:
        """Run `coroutine` on the agent's loop and wait for it (from a plain thread)."""
        future = asyncio.run_coroutine_threadsafe(coroutine, self._loop)
        return future.result(RUNNER_CALL_TIMEOUT_SECONDS)

    def run(self, function: Callable[[], Result]) -> Result:
        """Run a plain function on the agent's loop, e.g. to flip a fake switch safely."""

        async def wrapper() -> Result:
            return function()

        return self.call(wrapper())

    async def call_async(self, coroutine: Coroutine[Any, Any, Result]) -> Result:
        """Run `coroutine` on the agent's loop and await it from another loop."""
        if not self._loop.is_running():
            coroutine.close()
            raise FakeAgentGoneError("the fake agent has stopped")
        try:
            future = asyncio.run_coroutine_threadsafe(coroutine, self._loop)
        except RuntimeError:  # the loop closed just now
            coroutine.close()
            raise FakeAgentGoneError("the fake agent has stopped") from None
        try:
            return await asyncio.wrap_future(future)
        except asyncio.CancelledError:
            current = asyncio.current_task()
            if current is not None and current.cancelling():
                raise  # our caller is being cancelled; let it be
            raise FakeAgentGoneError("the fake agent stopped mid-call") from None


async def _cancel_other_tasks() -> None:
    current = asyncio.current_task()
    tasks = [task for task in asyncio.all_tasks() if task is not current]
    for task in tasks:
        task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)


class InProcessTransport:
    """An `AgentTransport` whose "network" is a dict of address → `FakeAgentRunner`."""

    def __init__(self) -> None:
        """No agents plugged in yet: every address refuses."""
        self._lock = threading.Lock()
        self._runners: dict[str, FakeAgentRunner] = {}
        self._sockets: dict[str, set[_InProcessSocket]] = {}
        # Counted so a test can check that a scan shares one client.
        self.http_clients_made = 0

    def plug(self, host: str, port: int, runner: FakeAgentRunner) -> None:
        """Make `runner` answer at host:port (an agent coming up)."""
        with self._lock:
            self._runners[f"{host}:{port}"] = runner

    def unplug(self, host: str, port: int) -> FakeAgentRunner | None:
        """The agent at host:port vanishes: new requests are refused, open sockets close."""
        address = f"{host}:{port}"
        with self._lock:
            runner = self._runners.pop(address, None)
            sockets = self._sockets.pop(address, set())
        for socket in sockets:
            socket.force_close()
        return runner

    def runner_at(self, address: str) -> FakeAgentRunner:
        """The runner plugged in at "host:port", or `FakeAgentGoneError`."""
        with self._lock:
            runner = self._runners.get(address)
        if runner is None:
            raise FakeAgentGoneError(f"nothing is listening at {address}")
        return runner

    def http_client(
        self, base_url: str, headers: Mapping[str, str], timeout: httpx2.Timeout
    ) -> httpx2.AsyncClient:
        """A real httpx2 client whose requests are carried to the fake agent's loop.

        Each request goes to whichever agent is plugged in at *its* host and
        port, so one client can probe many addresses, as a subnet scan does.
        """
        self.http_clients_made += 1
        return httpx2.AsyncClient(
            base_url=base_url,
            headers=dict(headers),
            timeout=timeout,
            transport=_BridgeTransport(self),
        )

    @asynccontextmanager
    async def open_websocket(
        self, url: str, headers: Mapping[str, str]
    ) -> AsyncIterator[AgentSocket]:
        """Open the fake agent's state WebSocket by calling its ASGI app directly."""
        parts = urlsplit(url)
        try:
            runner = self.runner_at(parts.netloc)
        except FakeAgentGoneError as gone:
            raise AgentSocketClosedError(str(gone)) from None
        socket = _InProcessSocket(runner, parts.netloc, parts.path, parts.query, headers)
        await socket.open()
        with self._lock:
            self._sockets.setdefault(parts.netloc, set()).add(socket)
        try:
            yield socket
        finally:
            with self._lock:
                self._sockets.get(parts.netloc, set()).discard(socket)
            await socket.close()


class _BridgeTransport(httpx2.AsyncBaseTransport):
    """Carries each httpx2 request to its agent's loop and ASGI app, and back."""

    def __init__(self, transport: InProcessTransport) -> None:
        self._transport = transport

    async def handle_async_request(self, request: httpx2.Request) -> httpx2.Response:
        address = f"{request.url.host}:{request.url.port or 80}"
        try:
            runner = self._transport.runner_at(address)
        except FakeAgentGoneError as gone:
            raise httpx2.ConnectError(str(gone), request=request) from None
        body = await request.aread()

        async def on_agent_loop() -> tuple[int, list[tuple[bytes, bytes]], bytes]:
            asgi = httpx2.ASGITransport(app=runner.app)
            inner = httpx2.Request(
                request.method, request.url, headers=request.headers, content=body
            )
            response = await asgi.handle_async_request(inner)
            content = await response.aread()
            return response.status_code, response.headers.raw, content

        try:
            status, headers, content = await runner.call_async(on_agent_loop())
        except FakeAgentGoneError as gone:
            raise httpx2.ConnectError(str(gone), request=request) from None
        return httpx2.Response(status, headers=headers, content=content)


class _InProcessSocket:
    """An `AgentSocket` that talks to the agent app's WebSocket route through queues."""

    def __init__(
        self,
        runner: FakeAgentRunner,
        address: str,
        path: str,
        query: str,
        headers: Mapping[str, str],
    ) -> None:
        self._runner = runner
        self._address = address
        self._path = path
        self._query = query
        self._headers = headers
        # Created on the agent's loop in `_open_on_agent_loop`.
        self._to_app: asyncio.Queue[dict[str, Any]] | None = None
        self._from_app: asyncio.Queue[dict[str, Any]] | None = None
        self._app_task: asyncio.Task[None] | None = None
        self._closed = False

    async def open(self) -> None:
        """Run the handshake; `AgentHandshakeRejectedError` if the app refuses."""
        try:
            await self._runner.call_async(self._open_on_agent_loop())
        except FakeAgentGoneError as gone:
            raise AgentSocketClosedError(str(gone)) from None

    async def send_json(self, message: dict[str, Any]) -> None:
        if self._closed or self._to_app is None:
            raise AgentSocketClosedError("the socket is closed")
        frame = {"type": "websocket.receive", "text": json.dumps(message)}
        try:
            await self._runner.call_async(self._to_app.put(frame))
        except FakeAgentGoneError as gone:
            raise AgentSocketClosedError(str(gone)) from None

    async def receive_json(self) -> Any:
        if self._closed or self._from_app is None:
            raise AgentSocketClosedError("the socket is closed")
        try:
            frame = await self._runner.call_async(self._from_app.get())
        except FakeAgentGoneError as gone:
            self._closed = True
            raise AgentSocketClosedError(str(gone)) from None
        if frame["type"] != "websocket.send":
            self._closed = True
            raise AgentSocketClosedError(f"the agent closed the socket ({frame.get('code')})")
        return json.loads(frame.get("text") or frame.get("bytes", b"").decode())

    def force_close(self) -> None:
        """The agent vanished: make the next (or current) receive fail."""
        from_app = self._from_app
        if from_app is not None:
            with contextlib.suppress(FakeAgentGoneError, RuntimeError):
                self._runner.run(lambda: from_app.put_nowait({"type": "websocket.close"}))

    async def close(self) -> None:
        """Tell the app the client went away and wait for its handler to finish."""
        self._closed = True
        with contextlib.suppress(FakeAgentGoneError):
            await self._runner.call_async(self._close_on_agent_loop())

    async def _open_on_agent_loop(self) -> None:
        self._to_app, self._from_app = asyncio.Queue(), asyncio.Queue()
        host, _, port = self._address.partition(":")
        scope = {
            "type": "websocket",
            "asgi": {"version": "3.0", "spec_version": "2.4"},
            "http_version": "1.1",
            "scheme": "ws",
            "server": (host, int(port or 80)),
            "client": ("127.0.0.1", 50000),
            "root_path": "",
            "path": self._path,
            "raw_path": self._path.encode(),
            "query_string": self._query.encode(),
            "headers": [(b"host", self._address.encode())]
            + [(key.lower().encode(), value.encode()) for key, value in self._headers.items()],
            "subprotocols": [],
            "state": {},
            "extensions": {"websocket.http.response": {}},
        }
        await self._to_app.put({"type": "websocket.connect"})
        self._app_task = asyncio.create_task(self._run_app(scope))
        first = await self._from_app.get()
        if first["type"] == "websocket.accept":
            return
        self._closed = True
        if first["type"] == "websocket.http.response.start":
            raise AgentHandshakeRejectedError(int(first["status"]))
        raise AgentHandshakeRejectedError(403)  # closed before accepting, as servers report it

    async def _run_app(self, scope: dict[str, Any]) -> None:
        assert self._to_app is not None and self._from_app is not None
        from_app = self._from_app

        async def send(message: MutableMapping[str, Any]) -> None:
            await from_app.put(dict(message))

        try:
            await self._runner.app(scope, self._to_app.get, send)
        finally:
            from_app.put_nowait({"type": "websocket.close", "code": 1006})

    async def _close_on_agent_loop(self) -> None:
        if self._app_task is None or self._to_app is None or self._app_task.done():
            return
        await self._to_app.put({"type": "websocket.disconnect", "code": 1000})
        try:
            await asyncio.wait_for(self._app_task, RUNNER_CALL_TIMEOUT_SECONDS)
        except Exception:
            self._app_task.cancel()
