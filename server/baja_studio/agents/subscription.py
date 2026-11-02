"""The state subscription behind `AgentClient.subscribe`: reconnects and the offline rule.

Two tasks run side by side:

- the *connection loop*: read `/agent/info` (the camera list may have changed),
  open the WebSocket, ping every two seconds, pass every message on; when
  anything fails, wait (0.5 s, 1 s, 2 s, then every 4 s) and start over.
- the *silence watchdog*: if nothing at all has been heard from the agent for
  five seconds, report `offline` and abandon the current attempt, however it
  is stuck (a laptop that loses power never closes its socket).
"""

import asyncio
import contextlib
import logging
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, Any

from pydantic import ValidationError

from baja_studio.agents.contract import (
    OFFLINE_AFTER_SILENCE_SECONDS,
    AgentInfo,
    EventMessage,
    StateMessage,
)
from baja_studio.agents.transport import (
    AgentHandshakeRejectedError,
    AgentSocket,
    AgentSocketClosedError,
    AgentUnreachableError,
)
from baja_studio.core.clock import Clock

if TYPE_CHECKING:
    from baja_studio.agents.client import AgentClient

logger = logging.getLogger(__name__)

PING_INTERVAL_SECONDS = 2.0
FIRST_RECONNECT_DELAY_SECONDS = 0.5
# Below the five-second offline rule on purpose: an agent that answers /agent/info
# but cannot be used (wrong version, wrong token) is heard from on every attempt,
# so it stays "degraded" instead of flickering to "offline".
MAX_RECONNECT_DELAY_SECONDS = 4.0


class LinkStatus(StrEnum):
    """How the studio's link to an agent is doing."""

    CONNECTING = "connecting"
    OK = "ok"
    DEGRADED = "degraded"  # reachable, but unusable: wrong contract version or token
    OFFLINE = "offline"


@dataclass(frozen=True)
class StatusUpdate:
    """The link status changed; `info` is the latest `/agent/info` (None if never read)."""

    status: LinkStatus
    reason: str
    info: AgentInfo | None


@dataclass(frozen=True)
class StateUpdate:
    """The agent pushed the state of its cameras."""

    message: StateMessage


@dataclass(frozen=True)
class EventUpdate:
    """The agent reported an event (mode change, tracker lost, ...)."""

    message: EventMessage


AgentUpdate = StatusUpdate | StateUpdate | EventUpdate
UpdateCallback = Callable[[AgentUpdate], None]


class ReconnectBackoff:
    """Delays between reconnect attempts: 0.5 s doubling up to 4 s."""

    def __init__(self) -> None:
        """Start at the shortest delay."""
        self._next = FIRST_RECONNECT_DELAY_SECONDS

    def next_delay(self) -> float:
        """The delay before this attempt; each call doubles the next one."""
        delay = self._next
        self._next = min(self._next * 2, MAX_RECONNECT_DELAY_SECONDS)
        return delay

    def reset(self) -> None:
        """Back to the shortest delay, after a connection that worked."""
        self._next = FIRST_RECONNECT_DELAY_SECONDS


class Subscription:
    """One run of `AgentClient.subscribe`."""

    def __init__(self, client: "AgentClient", clock: Clock, on_update: UpdateCallback) -> None:
        """Created by `AgentClient.subscribe`."""
        self._client = client
        self._clock = clock
        self._on_update = on_update
        self._status: LinkStatus | None = None
        self._info: AgentInfo | None = None
        self._last_problem = "no answer yet"
        self._attempt: asyncio.Task[bool] | None = None

    async def run(self) -> None:
        """Run both tasks until cancelled."""
        self._client.heard_from_agent()  # the five seconds of patience start now
        self._set_status(LinkStatus.CONNECTING, "connecting")
        watchdog = asyncio.create_task(self._watch_for_silence())
        try:
            await self._keep_connected()
        finally:
            watchdog.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await watchdog

    async def _keep_connected(self) -> None:
        backoff = ReconnectBackoff()
        while True:
            self._attempt = asyncio.create_task(self._connect_once())
            try:
                was_connected = await self._attempt
            except asyncio.CancelledError:
                current = asyncio.current_task()
                if current is not None and current.cancelling():
                    raise  # we are being stopped, not just the attempt
                was_connected = False  # the watchdog gave up on a stuck attempt
            except (AgentUnreachableError, AgentSocketClosedError, ValidationError) as problem:
                self._note_problem(problem)
                was_connected = False
            except Exception as problem:  # e.g. an AgentError answer to /agent/info
                logger.warning("agent %s: %s", self._client.base_url, problem)
                self._note_problem(problem)
                was_connected = False
            finally:
                self._attempt = None
            if was_connected:
                backoff.reset()
            await self._clock.sleep(backoff.next_delay())

    async def _connect_once(self) -> bool:
        """One attempt: info, then the socket until it closes. True if the socket opened."""
        info = await self._client.info()
        self._info = info
        reason = self._client.incompatible_reason
        if reason:
            self._set_status(LinkStatus.DEGRADED, reason)
            return False
        ws_url = "ws" + self._client.base_url.removeprefix("http") + "/agent/ws"
        opened = False
        try:
            async with self._client.transport.open_websocket(
                ws_url, self._client.headers
            ) as socket:
                opened = True
                self._client.heard_from_agent()
                await self._listen(socket)
        except AgentHandshakeRejectedError as refused:
            self._client.heard_from_agent()  # a refusal is still an answer
            if refused.status_code in (401, 403):
                self._set_status(LinkStatus.DEGRADED, "the agent refused our token")
                return False
            raise
        except AgentSocketClosedError as closed:
            if not opened:
                raise
            self._note_problem(closed)
        return opened

    async def _listen(self, socket: AgentSocket) -> None:
        """Pass on messages until the socket closes (raises `AgentSocketClosedError`).

        The link is `ok` only once a message has actually arrived on this
        socket (agents send their state as soon as they accept it): an agent
        that accepts connections but has hung must not look healthy.
        """
        pinger = asyncio.create_task(self._send_pings(socket))
        announced = False
        try:
            while True:
                message = await socket.receive_json()
                self._client.heard_from_agent()
                if not announced:
                    announced = True
                    self._set_status(LinkStatus.OK, "")  # with the info just read
                self._pass_on(message)
        finally:
            pinger.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await pinger

    async def _send_pings(self, socket: AgentSocket) -> None:
        with contextlib.suppress(AgentSocketClosedError):
            while True:
                await self._clock.sleep(PING_INTERVAL_SECONDS)
                await socket.send_json({"type": "ping"})

    def _pass_on(self, message: Any) -> None:
        kind = message.get("type") if isinstance(message, dict) else None
        try:
            if kind == "state":
                self._on_update(StateUpdate(StateMessage.model_validate(message)))
            elif kind == "event":
                self._on_update(EventUpdate(EventMessage.model_validate(message)))
        except ValidationError as invalid:
            logger.warning(
                "agent %s sent a malformed %s message: %s", self._client.base_url, kind, invalid
            )

    async def _watch_for_silence(self) -> None:
        while True:
            silent_for = self._clock.monotonic() - self._client.last_heard
            if silent_for < OFFLINE_AFTER_SILENCE_SECONDS:
                await self._clock.sleep(OFFLINE_AFTER_SILENCE_SECONDS - silent_for)
                continue
            if self._status != LinkStatus.OFFLINE:
                self._set_status(
                    LinkStatus.OFFLINE,
                    f"nothing heard for {OFFLINE_AFTER_SILENCE_SECONDS:.0f} s "
                    f"({self._last_problem})",
                )
                if self._attempt is not None:
                    self._attempt.cancel()
            await self._clock.sleep(OFFLINE_AFTER_SILENCE_SECONDS)

    def _note_problem(self, problem: Exception) -> None:
        self._last_problem = str(problem) or type(problem).__name__

    def _set_status(self, status: LinkStatus, reason: str) -> None:
        if status == self._status and status != LinkStatus.OK:
            return
        self._status = status
        self._on_update(StatusUpdate(status, reason, self._info))
