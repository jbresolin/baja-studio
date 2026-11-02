"""Tests for the child-process host (core/childproc.py).

Some tests start real Python child processes (the echo child, or the fakes in
fake_children.py). That needs no hardware or network: they talk over pipes.
"""

import threading
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from baja_studio.core.childproc import (
    ChildProcessHost,
    ChildRequestError,
    run_child_session,
)
from baja_studio.core.supervisor import DeviceOfflineError, WorkerContext
from tests.conftest import FakeClock, eventually

SERVER_FOLDER = Path(__file__).resolve().parents[2]
ECHO_CHILD = "baja_studio.childprocs.echo_child"
FAKE_CHILDREN = "tests.core.fake_children"


@pytest.fixture
def started_hosts() -> Iterator[list[ChildProcessHost]]:
    """Collects hosts a test starts and stops them afterwards, even if the test fails."""
    hosts: list[ChildProcessHost] = []
    yield hosts
    for host in hosts:
        host.stop()


@pytest.fixture
def fake_children_importable(monkeypatch: pytest.MonkeyPatch) -> None:
    """Let child processes import `tests.core.fake_children` by putting server/ on PYTHONPATH."""
    monkeypatch.setenv("PYTHONPATH", str(SERVER_FOLDER))


def start_host(
    python_module: str, clock: FakeClock, hosts: list[ChildProcessHost], **options: Any
) -> ChildProcessHost:
    host = ChildProcessHost(python_module, clock, owner_module="test", **options)
    host.start()
    hosts.append(host)
    return host


# --- the host side: ChildProcessHost ------------------------------------------


def test_host_round_trips_a_request_through_the_echo_child(
    fake_clock: FakeClock, started_hosts: list[ChildProcessHost]
) -> None:
    host = start_host(ECHO_CHILD, fake_clock, started_hosts)

    assert host.request("upper", {"text": "baja"}) == "BAJA"
    host.ping()  # does not raise: the child is alive


def test_an_error_reply_raises_child_request_error(
    fake_clock: FakeClock, started_hosts: list[ChildProcessHost]
) -> None:
    host = start_host(ECHO_CHILD, fake_clock, started_hosts)

    with pytest.raises(ChildRequestError, match="unknown method"):
        host.request("no-such-method")


def test_a_request_to_a_stopped_child_raises_device_offline(
    fake_clock: FakeClock, started_hosts: list[ChildProcessHost]
) -> None:
    host = start_host(ECHO_CHILD, fake_clock, started_hosts)
    host.stop()

    with pytest.raises(DeviceOfflineError):
        host.ping()


def test_killing_the_child_sets_exited_and_calls_exit_listeners(
    fake_clock: FakeClock, started_hosts: list[ChildProcessHost]
) -> None:
    listener_called = threading.Event()
    host = ChildProcessHost(ECHO_CHILD, fake_clock, owner_module="test")
    host.add_exit_listener(listener_called.set)
    host.start()
    started_hosts.append(host)

    host.kill()

    # The host learns the child died when the child's output pipe ends. On
    # Windows that pipe can end a moment *before* the operating system reports
    # the process as finished, so asking the OS ("poll") right then could still
    # say "running". This test once failed on Windows CI for exactly that
    # reason. Now the host's reader thread waits for the process to really
    # exit, and only then marks the host stopped and calls the listeners. So by
    # the time `exited` is set, `is_running` must already be False.
    assert host.exited.wait(10)
    assert listener_called.wait(10)
    assert not host.is_running


def test_exit_listeners_already_see_the_host_stopped_with_an_exit_code(
    fake_clock: FakeClock, started_hosts: list[ChildProcessHost]
) -> None:
    # The listener writes down what the host looked like at the moment it was
    # called. We check that record afterwards, from the test's own thread,
    # because an assert failing inside the reader thread would go unnoticed.
    seen_by_listener: dict[str, Any] = {}
    listener_called = threading.Event()
    host = ChildProcessHost(ECHO_CHILD, fake_clock, owner_module="test")

    def record_what_the_host_says() -> None:
        seen_by_listener["is_running"] = host.is_running
        seen_by_listener["exit_code"] = host.exit_code
        listener_called.set()

    host.add_exit_listener(record_what_the_host_says)
    host.start()
    started_hosts.append(host)

    host.kill()
    assert listener_called.wait(10)

    # Killing gives a non-zero exit code (its exact value differs between
    # Linux and Windows), so we only check that one was recorded, and that it
    # is the same code the host reports now.
    assert seen_by_listener["is_running"] is False
    assert seen_by_listener["exit_code"] is not None
    assert seen_by_listener == {"is_running": False, "exit_code": host.exit_code}


@pytest.mark.usefixtures("fake_children_importable")
def test_a_child_that_never_answers_counts_as_offline_after_the_timeout(
    fake_clock: FakeClock, started_hosts: list[ChildProcessHost], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FAKE_CHILD_BEHAVIOUR", "silent")
    host = start_host(FAKE_CHILDREN, fake_clock, started_hosts)

    # The ping blocks waiting for an answer, so run it on a helper thread and
    # keep whatever it raises.
    errors: list[Exception] = []

    def ping_and_keep_error() -> None:
        try:
            host.ping(timeout_seconds=2)
        except Exception as error:
            errors.append(error)

    pinger = threading.Thread(target=ping_and_keep_error)
    pinger.start()
    eventually(lambda: fake_clock.sleeping_count == 1)  # the ping is waiting

    # Two fake seconds pass with no answer: the ping gives up.
    fake_clock.advance(2)
    pinger.join(10)

    assert len(errors) == 1
    assert isinstance(errors[0], DeviceOfflineError)
    assert "did not answer" in str(errors[0])


@pytest.mark.usefixtures("fake_children_importable")
def test_unsolicited_events_from_the_child_reach_the_event_callback(
    fake_clock: FakeClock, started_hosts: list[ChildProcessHost], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FAKE_CHILD_BEHAVIOUR", "chatty")
    events: list[tuple[str, Any]] = []
    host = start_host(
        FAKE_CHILDREN,
        fake_clock,
        started_hosts,
        on_event=lambda name, data: events.append((name, data)),
    )

    # The chatty child also prints a non-JSON line, which must simply be skipped.
    assert host.request("anything") == "anything"
    assert events == [("hello", {"from": "chatty"})]


@pytest.mark.usefixtures("fake_children_importable")
def test_run_child_errors_come_back_as_child_request_errors(
    fake_clock: FakeClock, started_hosts: list[ChildProcessHost], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FAKE_CHILD_BEHAVIOUR", "failing")
    host = start_host(FAKE_CHILDREN, fake_clock, started_hosts)

    host.ping()  # pings are answered by run_child itself, so they still work
    with pytest.raises(ChildRequestError, match="cannot do move"):
        host.request("move")


# --- the worker helper: run_child_session --------------------------------------


def test_run_child_session_reports_online_then_raises_offline_when_the_child_dies(
    fake_clock: FakeClock,
) -> None:
    # A WorkerContext normally comes from the supervisor; here we build one by
    # hand and record when the session calls mark_online().
    came_online = threading.Event()
    worker = WorkerContext("test", "child", fake_clock, on_online=came_online.set)
    host = ChildProcessHost(ECHO_CHILD, fake_clock, owner_module="test")
    errors: list[Exception] = []

    def run_session() -> None:
        try:
            run_child_session(host, worker)
        except Exception as error:
            errors.append(error)

    session = threading.Thread(target=run_session)
    session.start()
    assert came_online.wait(10)

    # Kill the child. The host notices the end of its output and wakes the
    # session, which should give up with DeviceOfflineError.
    host.kill()
    session.join(10)

    assert len(errors) == 1
    assert isinstance(errors[0], DeviceOfflineError)


def test_run_child_session_stops_the_child_when_asked_to_stop(fake_clock: FakeClock) -> None:
    came_online = threading.Event()
    worker = WorkerContext("test", "child", fake_clock, on_online=came_online.set)
    host = ChildProcessHost(ECHO_CHILD, fake_clock, owner_module="test")
    session = threading.Thread(target=run_child_session, args=(host, worker))
    session.start()
    assert came_online.wait(10)

    worker.request_stop()
    session.join(10)

    assert not session.is_alive()
    assert not host.is_running


def test_run_child_session_pings_on_every_interval(fake_clock: FakeClock) -> None:
    worker = WorkerContext("test", "child", fake_clock, on_online=lambda: None)
    host = ChildProcessHost(ECHO_CHILD, fake_clock, owner_module="test")
    pings: list[int] = []
    original_ping = host.ping

    def counting_ping(timeout_seconds: float = 5.0) -> None:
        pings.append(1)
        original_ping(timeout_seconds)

    host.ping = counting_ping  # type: ignore[method-assign]
    session = threading.Thread(target=run_child_session, args=(host, worker))
    session.start()
    try:
        # One ping at start-up, then the session sleeps until time 2. (We check
        # the wake-up time, not just "someone is sleeping", because the ping
        # itself also waits on the clock for its answer.)
        eventually(lambda: len(pings) == 1 and fake_clock.wake_up_times() == [2.0])
        fake_clock.advance(2)
        eventually(lambda: len(pings) == 2 and fake_clock.wake_up_times() == [4.0])
    finally:
        worker.request_stop()
        session.join(10)
