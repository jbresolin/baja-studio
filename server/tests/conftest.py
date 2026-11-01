"""Shared test helpers and fixtures.

A "fixture" is something pytest builds for a test when the test names it as
a parameter. For example, a test written as `def test_x(fake_clock): ...`
receives a fresh `FakeClock` built by the `fake_clock` function below.

Why a fake clock? The service waits a lot: workers sleep between ticks, the
supervisor waits before restarting a worker, child processes are pinged every
two seconds. If tests used real time they would be slow and flaky. Instead,
all service code asks a `Clock` object for the time and for waiting, and tests
hand it this fake one, where time only moves when the test calls `advance()`.
"""

import threading
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from baja_studio.core.module import Module
from baja_studio.core.registry import ModuleRegistry
from baja_studio.core.supervisor import Health, HealthState
from baja_studio.main import build_module_list, create_app

# How long (in real seconds) a helper waits before declaring the test failed.
# It is a safety net against hangs, not a timing assumption: in a passing run
# the thing we wait for happens within milliseconds.
SAFETY_TIMEOUT_SECONDS = 10.0

# Fixed starting date for the fake clock, so timestamps in tests are predictable.
FAKE_CLOCK_START = datetime(2026, 10, 1, 12, 0, 0, tzinfo=UTC)


@dataclass
class _FakeTimer:
    due: float
    callback: Callable[[], None]
    cancelled: bool = False

    def cancel(self) -> None:
        self.cancelled = True


class FakeClock:
    """A clock whose time only moves when a test calls `advance(seconds)`.

    Threads that call `wait(event, seconds)` stay blocked until either the
    event is set or the test has advanced fake time past their deadline.
    """

    def __init__(self) -> None:
        self._now = 0.0
        self._condition = threading.Condition()
        # Deadlines of the threads currently blocked in `wait`, by thread id.
        self._sleeper_deadlines: dict[int, float] = {}
        self._timers: list[_FakeTimer] = []

    def monotonic(self) -> float:
        with self._condition:
            return self._now

    def now_utc(self) -> datetime:
        return FAKE_CLOCK_START + timedelta(seconds=self.monotonic())

    def wait(self, event: threading.Event, seconds: float) -> bool:
        with self._condition:
            deadline = self._now + seconds
            self._sleeper_deadlines[threading.get_ident()] = deadline
            self._condition.notify_all()
            try:
                while not event.is_set() and self._now < deadline:
                    # Events cannot notify our condition, so look again every few ms.
                    self._condition.wait(timeout=0.005)
                return event.is_set()
            finally:
                del self._sleeper_deadlines[threading.get_ident()]
                self._condition.notify_all()

    def call_later(self, seconds: float, callback: Callable[[], None]) -> _FakeTimer:
        timer = _FakeTimer(self.monotonic() + seconds, callback)
        with self._condition:
            self._timers.append(timer)
        return timer

    @property
    def sleeping_count(self) -> int:
        """How many threads are blocked in `wait` right now."""
        with self._condition:
            return len(self._sleeper_deadlines)

    def wake_up_times(self) -> list[float]:
        """The fake times at which the currently sleeping threads will wake, earliest first."""
        with self._condition:
            return sorted(self._sleeper_deadlines.values())

    def advance(self, seconds: float) -> None:
        """Move time forward, run timers that came due, and let due sleepers wake.

        Returns only once every thread whose deadline has passed has actually
        left `wait`, so the test can safely look at what they did next.
        """
        with self._condition:
            self._now += seconds
            due_timers = [timer for timer in self._timers if timer.due <= self._now]
            self._timers = [timer for timer in self._timers if timer.due > self._now]
            self._condition.notify_all()
            woke_up = self._condition.wait_for(
                lambda: all(deadline > self._now for deadline in self._sleeper_deadlines.values()),
                timeout=SAFETY_TIMEOUT_SECONDS,
            )
        assert woke_up, "a sleeping thread did not wake up after advancing the fake clock"
        for timer in due_timers:
            if not timer.cancelled:
                timer.callback()


def eventually(condition: Callable[[], bool], message: str = "condition never became true") -> None:
    """Wait until `condition()` returns True, failing the test after the safety timeout.

    Used when another thread (a worker, a child process) has to do something
    before the test can check the result.
    """
    give_up_at = time.monotonic() + SAFETY_TIMEOUT_SECONDS
    pause = threading.Event()  # never set; only used for its short wait
    while not condition():
        assert time.monotonic() < give_up_at, message
        pause.wait(0.005)


class HealthRecorder:
    """Remembers every health change the supervisor reports, in order.

    Tests use it to say things like "echo went offline and then came back ok".
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.changes: list[tuple[str, HealthState]] = []

    def __call__(self, module: str, health: Health) -> None:
        with self._lock:
            self.changes.append((module, health.state))

    def states_of(self, module: str) -> list[HealthState]:
        with self._lock:
            return [state for name, state in self.changes if name == module]

    def wait_for_sequence(self, module: str, expected: list[HealthState]) -> None:
        """Wait until the module's history contains `expected` in that order (gaps allowed)."""

        def contains_in_order() -> bool:
            remaining = iter(self.states_of(module))
            return all(state in remaining for state in expected)

        eventually(
            contains_in_order,
            f"{module} never went through {expected}; saw {self.states_of(module)}",
        )


@pytest.fixture
def fake_clock() -> FakeClock:
    """A fresh fake clock starting at time zero."""
    return FakeClock()


@pytest.fixture
def data_directory(tmp_path: Path) -> Path:
    """An empty temporary folder used as the service's data directory."""
    return tmp_path / "data"


@pytest.fixture
def frontend_directory(tmp_path: Path) -> Path:
    """A folder with no built frontend in it (tests that need one create files)."""
    folder = tmp_path / "frontend-dist"
    folder.mkdir()
    return folder


AppFactory = Callable[[Callable[[ModuleRegistry], list[Module]] | None], FastAPI]


@pytest.fixture
def make_app(fake_clock: FakeClock, data_directory: Path, frontend_directory: Path) -> AppFactory:
    """Build an app on the fake clock and temp data directory.

    Pass a function returning a module list to run only those modules;
    pass None for the real module list from `main.py`.
    """

    def build(module_list_builder: Callable[[ModuleRegistry], list[Module]] | None) -> FastAPI:
        return create_app(
            data_directory=data_directory,
            clock=fake_clock,
            frontend_directory=frontend_directory,
            # Zero: send state patches immediately instead of batching for 30 ms.
            coalesce_seconds=0.0,
            log_to_file=False,
            module_list_builder=module_list_builder or build_module_list,
        )

    return build


@pytest.fixture
def app(make_app: AppFactory) -> FastAPI:
    """The real app with all its modules, on the fake clock."""
    return make_app(None)


@pytest.fixture
def client(app: FastAPI) -> Iterator[TestClient]:
    """An HTTP/WebSocket client talking to `app` in-process (no network).

    Entering the `with` block runs the app's startup (modules start);
    leaving it runs shutdown (workers stop, child processes end).
    """
    with TestClient(app) as test_client:
        yield test_client
