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

import asyncio
import contextlib
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

# Fixtures for tests that need fake camera-laptop agents (tests/fake_agents.py),
# and the running cameras module with its fakes (tests/modules/camera_studio.py).
pytest_plugins = [
    "tests.fake_agents",
    "tests.modules.camera_studio",
    "tests.modules.playout_studio",
    "tests.modules.audio_studio",
]

# How long (in real seconds) a helper waits before declaring the test failed.
# It is a safety net against hangs, not a timing assumption: in a passing run
# the thing we wait for happens within milliseconds.
SAFETY_TIMEOUT_SECONDS = 10.0

# Fixed starting date for the fake clock, so timestamps in tests are predictable.
FAKE_CLOCK_START = datetime(2026, 10, 1, 12, 0, 0, tzinfo=UTC)


@dataclass
class _AsyncSleeper:
    """A coroutine paused in `FakeClock.sleep`: when to wake it, and how."""

    due: float
    loop: asyncio.AbstractEventLoop
    future: asyncio.Future[None]


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
    Coroutines that call `await sleep(seconds)` likewise stay paused until the
    test advances time past their deadline (they may live on any event loop).
    """

    def __init__(self) -> None:
        self._now = 0.0
        self._condition = threading.Condition()
        # Deadlines of the threads currently blocked in `wait`, by thread id.
        self._sleeper_deadlines: dict[int, float] = {}
        self._timers: list[_FakeTimer] = []
        self._async_sleepers: list[_AsyncSleeper] = []

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

    async def sleep(self, seconds: float) -> None:
        if seconds <= 0:
            await asyncio.sleep(0)
            return
        loop = asyncio.get_running_loop()
        with self._condition:
            sleeper = _AsyncSleeper(self._now + seconds, loop, loop.create_future())
            self._async_sleepers.append(sleeper)
        try:
            await sleeper.future
        finally:
            with self._condition:
                if sleeper in self._async_sleepers:
                    self._async_sleepers.remove(sleeper)

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

    @property
    def async_sleeping_count(self) -> int:
        """How many coroutines are paused in `sleep` right now."""
        with self._condition:
            return len(self._async_sleepers)

    def sleeping_threads(self) -> set[int]:
        """Ids of the threads blocked in `wait` right now."""
        with self._condition:
            return set(self._sleeper_deadlines)

    def all_asleep_or_gone(self, thread_ids: set[int]) -> bool:
        """True once each of `thread_ids` is blocked in `wait` again, or has ended.

        After `advance`, this says the threads it woke have finished what they
        do between sleeps; a thread that stopped (a worker shut down) counts as done.
        """
        alive = {thread.ident for thread in threading.enumerate()}
        sleeping = self.sleeping_threads()
        return all(ident in sleeping or ident not in alive for ident in thread_ids)

    def wake_up_times(self) -> list[float]:
        """The fake times at which the currently sleeping threads will wake, earliest first."""
        with self._condition:
            return sorted(self._sleeper_deadlines.values())

    def advance(self, seconds: float) -> None:
        """Move time forward, run timers that came due, and let due sleepers wake.

        Returns only once every thread whose deadline has passed has actually
        left `wait`, so the test can safely look at what they did next.
        Coroutines whose deadline has passed are only *scheduled* to resume
        (their event loop runs them soon after), so tests check what they did
        with `eventually` / `eventually_async`.
        """
        with self._condition:
            self._now += seconds
            due_timers = [timer for timer in self._timers if timer.due <= self._now]
            self._timers = [timer for timer in self._timers if timer.due > self._now]
            due_sleepers = [sleeper for sleeper in self._async_sleepers if sleeper.due <= self._now]
            self._async_sleepers = [
                sleeper for sleeper in self._async_sleepers if sleeper.due > self._now
            ]
            for sleeper in due_sleepers:
                _resume_soon(sleeper)
            self._condition.notify_all()
            woke_up = self._condition.wait_for(
                lambda: all(deadline > self._now for deadline in self._sleeper_deadlines.values()),
                timeout=SAFETY_TIMEOUT_SECONDS,
            )
        assert woke_up, "a sleeping thread did not wake up after advancing the fake clock"
        for timer in due_timers:
            if not timer.cancelled:
                timer.callback()


def _resume_soon(sleeper: _AsyncSleeper) -> None:
    """Ask the sleeper's event loop to finish its `sleep` (from whatever thread we are on)."""

    def resume() -> None:
        if not sleeper.future.done():
            sleeper.future.set_result(None)

    # RuntimeError: that event loop has already closed, so nobody is waiting any more.
    with contextlib.suppress(RuntimeError):
        sleeper.loop.call_soon_threadsafe(resume)


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


WorkerThreads = Callable[[], set[int]]


def module_worker_threads(module: str, skip: tuple[str, ...] = ()) -> WorkerThreads:
    """The ids of a module's live worker threads, worked out afresh on every call.

    The supervisor names worker threads `<module>.<worker>`. `skip` lists
    worker-name prefixes to leave out: workers that block on something other
    than the fake clock (a serial-port read, an asyncio loop) never "sleep",
    so waiting for them to would never end.
    """
    prefix = f"{module}."
    skipped = tuple(prefix + name for name in skip)

    def threads() -> set[int]:
        return {
            thread.ident
            for thread in threading.enumerate()
            if thread.ident is not None
            and thread.name.startswith(prefix)
            and not thread.name.startswith(skipped)
        }

    return threads


def wait_until_workers_sleep(fake_clock: FakeClock, workers: WorkerThreads) -> None:
    """Wait until every thread `workers()` names is asleep on the fake clock, or has ended.

    A worker that has just published something, or been woken by a settings
    change or a command, may still be on its way back to `sleep`. Fake time
    moved in that gap wakes nobody: the worker then sleeps from a later time
    than the test expects, and the step is lost. Tests that move time call
    this before every step, and before checking what the workers did.
    """
    eventually(
        lambda: fake_clock.all_asleep_or_gone(workers()),
        "a worker never went back to sleep on the fake clock",
    )


def step_fake_time(
    fake_clock: FakeClock, seconds: float, workers: WorkerThreads, step: float = 0.1
) -> None:
    """Let `seconds` of fake time pass in `step`s, the workers asleep before each step and after.

    So each step wakes every worker that is due, and the test sees what they did.
    """
    for _ in range(round(seconds / step)):
        wait_until_workers_sleep(fake_clock, workers)
        fake_clock.advance(step)
    wait_until_workers_sleep(fake_clock, workers)


def step_fake_time_until(
    fake_clock: FakeClock,
    condition: Callable[[], bool],
    within_seconds: float,
    workers: WorkerThreads,
    step: float = 0.1,
) -> None:
    """Let fake time pass in `step`s until `condition()` holds (checked with the workers asleep).

    Checking only while the workers sleep means the condition never sees one
    halfway through publishing (a new position next to an old remaining time).
    """
    for _ in range(round(within_seconds / step)):
        wait_until_workers_sleep(fake_clock, workers)
        if condition():
            return
        fake_clock.advance(step)
    wait_until_workers_sleep(fake_clock, workers)
    eventually(condition)


async def eventually_async(
    condition: Callable[[], bool], message: str = "condition never became true"
) -> None:
    """`eventually` for async tests: polls without blocking the test's own event loop.

    Other coroutines on the same loop (the agent client, say) keep running
    while this waits, which a plain `eventually` would prevent.
    """
    give_up_at = time.monotonic() + SAFETY_TIMEOUT_SECONDS
    while not condition():
        assert time.monotonic() < give_up_at, message
        await asyncio.sleep(0.005)


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


def pytest_addoption(parser: pytest.Parser) -> None:
    """Options of the agent conformance suite (tests/agent_contract; see docs/agent-api.md)."""
    group = parser.getgroup("agent contract")
    group.addoption(
        "--agent-url",
        default=None,
        help="check a real agent, e.g. http://laptop:8890 (default: an in-process fake)",
    )
    group.addoption("--agent-token", default="", help="the X-Agent-Token the agent expects")
    group.addoption(
        "--agent-camera", default=None, help="id of the PTZ to test (default: the first PTZ)"
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
