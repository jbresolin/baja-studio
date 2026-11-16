"""What every agent *server* shares: contract errors, token rule, command log, state socket.

Two programs in this repository serve the agent contract (docs/agent-api.md):
the fake agent (`fakes/fake_agent.py`), and the camera node
(`node/`, ADR 0023), a real agent for a laptop's USB cameras. Both use the
pieces here, so the contract's rules are written once:

- errors always leave as `{error, detail}` with the contract's HTTP status, and
  a malformed body is `400 bad_request`, not FastAPI's 422 (`add_contract_error_handlers`);
- with a token set, everything but `GET /agent/info` needs `X-Agent-Token`
  (`token_refusal`), and so does the WebSocket handshake;
- the agent's own command log, newest first (`AgentCommandLog`);
- one state WebSocket per studio: pushes out, pings in (`StateHub`,
  `serve_state_socket`).
"""

import asyncio
import contextlib
import hmac
import json
from collections import deque
from collections.abc import Callable
from typing import Any

from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.exception_handlers import (
    http_exception_handler,
    request_validation_exception_handler,
)
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from starlette.exceptions import HTTPException as StarletteHTTPException

from baja_studio.agents.contract import (
    DEFAULT_RESUME_AFTER_MS,
    ERROR_HTTP_STATUS,
    TOKEN_HEADER,
    ErrorCode,
    LogEntry,
)

# The command log keeps this many entries (`GET /agent/log` asks for at most 1000).
LOG_LENGTH = 500
# The one path a subnet scan reads without a token (contract §2.2).
OPEN_PATH = "/agent/info"
# A mode profile's `resume_after_ms` is 0 to this many whole milliseconds (contract §6).
MAX_RESUME_AFTER_MS = 600_000

# What a subscriber's queue carries: a message to send, or None for "close now".
SubscriberQueue = asyncio.Queue[dict[str, Any] | None]


class CameraCommandError(Exception):
    """A command the agent refuses; it leaves as `{error, detail}` with the code's HTTP status."""

    def __init__(self, code: ErrorCode, detail: str) -> None:
        """`code` decides the HTTP status (contract.ERROR_HTTP_STATUS)."""
        super().__init__(detail)
        self.code = code
        self.detail = detail


def checked_profile(profile: dict[str, Any]) -> dict[str, Any]:
    """A copy of a mode profile, refused if `resume_after_ms` is not 0-600000 whole ms."""
    resume_after_ms = profile.get("resume_after_ms", DEFAULT_RESUME_AFTER_MS)
    if (
        not isinstance(resume_after_ms, int)
        or isinstance(resume_after_ms, bool)
        or not 0 <= resume_after_ms <= MAX_RESUME_AFTER_MS
    ):
        raise CameraCommandError(
            ErrorCode.BAD_REQUEST,
            f"profile.resume_after_ms must be a whole number of ms from 0 to {MAX_RESUME_AFTER_MS}",
        )
    return dict(profile)


def error_response(status_code: int, code: str, detail: str) -> JSONResponse:
    """The contract's error body."""
    return JSONResponse({"error": code, "detail": detail}, status_code=status_code)


def token_matches(sent: str | None, token: str) -> bool:
    """True when `sent` is `token`, compared in constant time (no timing hints for guessers)."""
    return hmac.compare_digest((sent or "").encode(), token.encode())


def token_refusal(path: str, sent: str | None, token: str) -> JSONResponse | None:
    """The 401 answer for a request without the right token, or None to let it through.

    No token configured lets everything through; `GET /agent/info` is always open.
    The answer never repeats what was sent.
    """
    if not token or path == OPEN_PATH or token_matches(sent, token):
        return None
    return error_response(401, ErrorCode.UNAUTHORIZED, f"missing or wrong {TOKEN_HEADER}")


def add_contract_error_handlers(
    app: FastAPI, applies_to: Callable[[str], bool] = lambda path: True
) -> None:
    """Every error on a contract path leaves as `{error, detail}` with the contract's status.

    `applies_to(path)` says which paths are the contract's (all of them, for
    the fake agent); others keep FastAPI's own answers (the camera node's
    core API under `/api`).
    """

    @app.exception_handler(CameraCommandError)
    async def camera_refused(request: Request, refused: CameraCommandError) -> JSONResponse:
        return error_response(ERROR_HTTP_STATUS[refused.code], refused.code, refused.detail)

    @app.exception_handler(RequestValidationError)
    async def bad_body(request: Request, invalid: RequestValidationError) -> Response:
        if not applies_to(request.url.path):
            return await request_validation_exception_handler(request, invalid)
        problems = "; ".join(
            f"{'.'.join(str(part) for part in problem['loc'])}: {problem['msg']}"
            for problem in invalid.errors()
        )
        return error_response(400, ErrorCode.BAD_REQUEST, problems)

    @app.exception_handler(StarletteHTTPException)
    async def http_error(request: Request, error: StarletteHTTPException) -> Response:
        if not applies_to(request.url.path):
            return await http_exception_handler(request, error)
        code = "not_found" if error.status_code == 404 else "http_error"
        return error_response(error.status_code, code, str(error.detail))


class AgentCommandLog:
    """The agent's own command log (`GET /agent/log`): the newest `LOG_LENGTH` commands."""

    def __init__(self, now_iso: Callable[[], str]) -> None:
        """`now_iso()` stamps each entry (the agent's clock, in UTC)."""
        self._now_iso = now_iso
        self._entries: deque[LogEntry] = deque(maxlen=LOG_LENGTH)

    def record(
        self, camera_id: str, source: str, command: str, args: dict[str, Any], result: str
    ) -> None:
        """Add one entry; `result` is "ok" or "error: <code>"."""
        self._entries.append(
            LogEntry(
                ts=self._now_iso(),
                module=camera_id,
                source=source,
                command=command,
                args=args,
                result=result,
            )
        )

    def tail(self, limit: int) -> list[LogEntry]:
        """The newest `limit` entries, newest first."""
        return list(reversed(self._entries))[:limit]


class StateHub:
    """The open state WebSockets, each a queue of messages; used on the server's event loop."""

    def __init__(self) -> None:
        """No subscribers yet."""
        self._subscribers: set[SubscriberQueue] = set()
        # The server's event loop, once a WebSocket has been accepted on it.
        self.loop: asyncio.AbstractEventLoop | None = None

    @property
    def subscriber_count(self) -> int:
        """How many WebSockets are open."""
        return len(self._subscribers)

    def add_subscriber(self) -> SubscriberQueue:
        """A queue that will receive every message for one new WebSocket."""
        queue: SubscriberQueue = asyncio.Queue()
        self._subscribers.add(queue)
        return queue

    def remove_subscriber(self, queue: SubscriberQueue) -> None:
        """Forget a WebSocket that has closed."""
        self._subscribers.discard(queue)

    def broadcast(self, message: dict[str, Any]) -> None:
        """Queue `message` for every open WebSocket."""
        for queue in list(self._subscribers):
            queue.put_nowait(message)

    def close_all(self) -> None:
        """Close every open WebSocket (an agent restarting, or shutting down)."""
        for queue in list(self._subscribers):
            queue.put_nowait(None)

    def broadcast_from_thread(self, message: dict[str, Any]) -> None:
        """`broadcast` from a thread other than the server's (the node's push worker)."""
        self._call_on_loop(self.broadcast, message)

    def close_all_from_thread(self) -> None:
        """`close_all` from another thread (the node shutting down)."""
        self._call_on_loop(self.close_all)

    def _call_on_loop(self, function: Callable[..., None], *arguments: Any) -> None:
        loop = self.loop
        if loop is None or loop.is_closed():
            return  # no WebSocket was ever accepted: nobody to tell
        with contextlib.suppress(RuntimeError):  # the loop closed just now
            loop.call_soon_threadsafe(function, *arguments)


async def serve_state_socket(
    websocket: WebSocket,
    hub: StateHub,
    token: str,
    greeting: Callable[[], list[dict[str, Any]]],
    answers_pings: Callable[[], bool] = lambda: True,
) -> None:
    """One studio's state WebSocket: pushes out, pings in, until either side closes.

    A wrong token is refused at the handshake with an HTTP 401 (or, where the
    server cannot answer the handshake with HTTP, a close with code 4401).
    `greeting()` is sent at once on acceptance (the state, and any restore
    notices); `answers_pings()` False keeps pings unanswered (the fake's
    "silent" switch).
    """
    if token and not token_matches(websocket.headers.get(TOKEN_HEADER), token):
        refusal = error_response(401, ErrorCode.UNAUTHORIZED, f"missing or wrong {TOKEN_HEADER}")
        try:
            await websocket.send_denial_response(refusal)
        except RuntimeError:  # the server cannot send an HTTP answer; refuse plainly
            await websocket.close(code=4401)
        return
    await websocket.accept()
    hub.loop = asyncio.get_running_loop()
    queue = hub.add_subscriber()
    for message in greeting():
        queue.put_nowait(message)

    async def send_messages() -> None:
        while (message := await queue.get()) is not None:
            await websocket.send_json(message)
        await websocket.close()

    async def answer_pings() -> None:
        while True:
            try:
                message = json.loads(await websocket.receive_text())
            except json.JSONDecodeError:
                continue
            if isinstance(message, dict) and message.get("type") == "ping" and answers_pings():
                queue.put_nowait({"type": "pong"})

    tasks = [asyncio.create_task(send_messages()), asyncio.create_task(answer_pings())]
    try:
        await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
    finally:
        hub.remove_subscriber(queue)
        for task in tasks:
            task.cancel()
        for task in tasks:
            with contextlib.suppress(asyncio.CancelledError, WebSocketDisconnect, RuntimeError):
                await task
