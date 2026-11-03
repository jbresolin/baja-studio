"""The agent driver's outgoing commands: one at a time, in order, moves coalesced.

HTTP requests sent side by side may arrive in any order, and a stop that
overtakes the move before it would leave the camera moving until the move's
ttl ran out. So each camera's commands wait for the one before. A move still
waiting behind another is replaced by the newer one (only the latest velocity
matters), and a stop drops the moves waiting before it.
"""

import threading
from collections import deque
from collections.abc import Callable
from concurrent.futures import Future
from dataclasses import dataclass
from typing import Any

from baja_studio.cameras.drivers.agent_gateway import AgentCall, AgentGateway

MOVE, STOP = "move", "stop"


@dataclass
class _Queued:
    kind: str
    call: AgentCall
    result: "Future[Any] | None"


class AgentCommandQueue:
    """Serialises one camera's commands to its agent."""

    def __init__(
        self,
        gateway: AgentGateway,
        agent_id: str,
        on_failure: Callable[[str, BaseException], None],
    ) -> None:
        """`on_failure(kind, error)` is told about every command the agent refused or missed."""
        self._gateway = gateway
        self._agent_id = agent_id
        self._on_failure = on_failure
        self._lock = threading.Lock()
        self._queue: deque[_Queued] = deque()
        self._busy = False

    def send(self, kind: str, call: AgentCall, result: "Future[Any] | None" = None) -> None:
        """Queue `call`; `result`, if given, receives its answer or error."""
        with self._lock:
            if kind == STOP:
                self._queue = deque(item for item in self._queue if item.kind != MOVE)
            if kind == MOVE and self._queue and self._queue[-1].kind == MOVE:
                self._queue[-1] = _Queued(kind, call, result)
            else:
                self._queue.append(_Queued(kind, call, result))
            if self._busy:
                return
            self._busy = True
        self._send_next()

    def clear(self) -> None:
        """Forget everything not yet sent (the route changed to the direct fallback)."""
        with self._lock:
            dropped = list(self._queue)
            self._queue.clear()
        for item in dropped:
            if item.result is not None:
                item.result.set_exception(
                    RuntimeError("cancelled: the route to the camera changed")
                )

    def _send_next(self) -> None:
        with self._lock:
            if not self._queue:
                self._busy = False
                return
            item = self._queue.popleft()
        future = self._gateway.submit(self._agent_id, item.call)
        future.add_done_callback(lambda done: self._sent(item, done))

    def _sent(self, item: _Queued, done: "Future[Any]") -> None:
        error = done.exception()
        if item.result is not None:
            if error is not None:
                item.result.set_exception(error)
            else:
                item.result.set_result(done.result())
        if error is not None:
            self._on_failure(item.kind, error)
        self._send_next()
