"""What a single worker sees: its context, its spec, its health, and its restart policy.

Split out of `supervisor.py`, which runs the threads; modules only ever need
the names defined here (and `supervisor.py` re-exports them).
"""

import threading
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum

from baja_studio.core.clock import Clock

FIRST_RESTART_DELAY_SECONDS = 1.0
MAX_RESTART_DELAY_SECONDS = 30.0


class HealthState(StrEnum):
    """How a module is doing, worst last except `stopped`, which is deliberate."""

    OK = "ok"
    DEGRADED = "degraded"
    OFFLINE = "offline"
    STOPPED = "stopped"


# Used to pick the most worrying worker state when summarising a module (worst is highest).
HEALTH_SEVERITY = {HealthState.OK: 0, HealthState.DEGRADED: 1, HealthState.OFFLINE: 2}


@dataclass(frozen=True)
class Health:
    """A health state plus the message that explains it (empty when ok)."""

    state: HealthState
    message: str = ""


class DeviceOfflineError(Exception):
    """Raised by a worker when its device or child process is absent.

    The supervisor reports `offline` instead of `degraded` and keeps retrying.
    """


class WorkerContext:
    """What a running worker uses to sleep, notice stop requests and report recovery."""

    def __init__(
        self,
        module: str,
        worker_name: str,
        clock: Clock,
        on_online: Callable[[], None],
        on_report: Callable[[Health], None] | None = None,
    ) -> None:
        """Created by the supervisor for each worker; workers never build one."""
        self.module = module
        self.worker_name = worker_name
        self.clock = clock
        self.stop_requested = threading.Event()
        self._wake_requested = threading.Event()
        self._on_online = on_online
        self._on_report = on_report

    @property
    def should_stop(self) -> bool:
        """True once the supervisor wants this worker to return."""
        return self.stop_requested.is_set()

    def sleep(self, seconds: float) -> None:
        """Pause for `seconds`, returning early if woken or asked to stop."""
        self.clock.wait(self._wake_requested, seconds)
        self._wake_requested.clear()

    def wake(self) -> None:
        """Cut the current `sleep` short, e.g. after a settings change or command."""
        self._wake_requested.set()

    def request_stop(self) -> None:
        """Ask the worker to finish; also wakes it so it notices promptly."""
        self.stop_requested.set()
        self._wake_requested.set()

    def mark_online(self) -> None:
        """Report that the device is reachable again, so `offline` turns back to `ok`."""
        self._on_online()

    def report_health(self, state: HealthState, message: str = "") -> None:
        """Set this worker's health while it keeps running (e.g. 1 of 3 devices away).

        The reported health stays until the worker reports again or fails; the
        ten-second "ran stably" rule does not override it.
        """
        if self._on_report is not None:
            self._on_report(Health(state, message))


WorkerFunction = Callable[[WorkerContext], None]


@dataclass(frozen=True)
class WorkerSpec:
    """A worker a module asks the supervisor to run: a name and a function.

    `starts_offline` is for workers that talk to a device or child process:
    the module shows `offline` until the worker calls `mark_online`, rather
    than claiming `ok` before the device has answered.
    """

    name: str
    run: WorkerFunction
    starts_offline: bool = False


class RestartPolicy:
    """Exponential backoff between restarts: 1 s, 2 s, 4 s ... capped at 30 s."""

    def __init__(
        self,
        first_delay: float = FIRST_RESTART_DELAY_SECONDS,
        max_delay: float = MAX_RESTART_DELAY_SECONDS,
    ) -> None:
        """Start at the shortest delay."""
        self._first_delay = first_delay
        self._max_delay = max_delay
        self._next_delay = first_delay

    def next_delay(self) -> float:
        """The delay to wait before this restart; each call doubles the next one."""
        delay = self._next_delay
        self._next_delay = min(self._next_delay * 2, self._max_delay)
        return delay

    def reset(self) -> None:
        """Go back to the shortest delay after a worker has run stably."""
        self._next_delay = self._first_delay
