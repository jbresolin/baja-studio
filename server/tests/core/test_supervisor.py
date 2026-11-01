"""Tests for the supervisor (core/supervisor.py).

The supervisor runs each module's workers in threads. These tests use small
hand-written worker functions and the fake clock, so restarts and backoff
delays can be checked without waiting in real time.
"""

import threading
from collections.abc import Iterator

import pytest

from baja_studio.core.supervisor import (
    DeviceOfflineError,
    HealthState,
    RestartPolicy,
    Supervisor,
    WorkerContext,
    WorkerSpec,
)
from tests.conftest import FakeClock, HealthRecorder, eventually


@pytest.fixture
def recorder() -> HealthRecorder:
    return HealthRecorder()


@pytest.fixture
def supervisor(fake_clock: FakeClock, recorder: HealthRecorder) -> Iterator[Supervisor]:
    """A supervisor that reports health changes to `recorder`, stopped after the test."""
    supervisor = Supervisor(fake_clock, join_timeout_seconds=5)
    supervisor.add_health_listener(recorder)
    yield supervisor
    supervisor.stop_all()


def sleep_until_stopped(worker: WorkerContext) -> None:
    """A well-behaved worker that does nothing until asked to stop."""
    while not worker.should_stop:
        worker.sleep(60)


def test_restart_delays_double_from_one_second_up_to_thirty() -> None:
    policy = RestartPolicy()

    delays = [policy.next_delay() for _ in range(7)]

    assert delays == [1, 2, 4, 8, 16, 30, 30]


def test_restart_delay_goes_back_to_one_second_after_reset() -> None:
    policy = RestartPolicy()
    policy.next_delay()
    policy.next_delay()

    policy.reset()

    assert policy.next_delay() == 1


def test_a_registered_module_is_stopped_until_started(supervisor: Supervisor) -> None:
    supervisor.register_module("demo")

    assert supervisor.module_health("demo").state == HealthState.STOPPED


def test_a_started_module_with_healthy_workers_is_ok(supervisor: Supervisor) -> None:
    supervisor.register_module("demo")

    supervisor.start_module("demo", [WorkerSpec("idle", sleep_until_stopped)])

    assert supervisor.module_health("demo").state == HealthState.OK


def test_worker_is_restarted_after_exception(
    supervisor: Supervisor, fake_clock: FakeClock, recorder: HealthRecorder
) -> None:
    # This worker crashes on its first run and behaves on the second.
    runs: list[int] = []
    second_run_started = threading.Event()

    def crashes_once(worker: WorkerContext) -> None:
        runs.append(len(runs) + 1)
        if len(runs) == 1:
            raise RuntimeError("first run fails")
        second_run_started.set()
        sleep_until_stopped(worker)

    supervisor.register_module("demo")
    supervisor.start_module("demo", [WorkerSpec("flaky", crashes_once)])

    # After the crash the module is "degraded" and the supervisor waits 1 s
    # (fake time) before restarting. Wait until it is in that wait...
    recorder.wait_for_sequence("demo", [HealthState.OK, HealthState.DEGRADED])
    assert "first run fails" in supervisor.module_health("demo").message
    eventually(lambda: fake_clock.sleeping_count == 1)

    # ...then let one fake second pass. The worker should run again.
    fake_clock.advance(1)

    assert second_run_started.wait(5)
    assert runs == [1, 2]


def test_restart_waits_longer_after_each_consecutive_failure(
    supervisor: Supervisor, fake_clock: FakeClock
) -> None:
    # A worker that always fails. We count its runs as fake time passes.
    runs: list[float] = []

    def always_fails(worker: WorkerContext) -> None:
        runs.append(fake_clock.monotonic())
        raise RuntimeError("still broken")

    supervisor.register_module("demo")
    supervisor.start_module("demo", [WorkerSpec("broken", always_fails)])
    eventually(lambda: len(runs) == 1 and fake_clock.sleeping_count == 1)

    # First wait is 1 s: advancing 1 s gives the second run.
    fake_clock.advance(1)
    eventually(lambda: len(runs) == 2 and fake_clock.sleeping_count == 1)

    # Second wait is 2 s: after only 1 s, nothing happens yet...
    fake_clock.advance(1)
    assert len(runs) == 2
    # ...and after one more second the third run starts.
    fake_clock.advance(1)
    eventually(lambda: len(runs) == 3)

    assert runs == [0, 1, 3]


def test_health_returns_to_ok_after_ten_seconds_without_error(
    supervisor: Supervisor, fake_clock: FakeClock, recorder: HealthRecorder
) -> None:
    def crashes_once(worker: WorkerContext) -> None:
        if not recorder.states_of("demo").count(HealthState.DEGRADED):
            raise RuntimeError("first run fails")
        sleep_until_stopped(worker)

    supervisor.register_module("demo")
    supervisor.start_module("demo", [WorkerSpec("flaky", crashes_once)])
    recorder.wait_for_sequence("demo", [HealthState.DEGRADED])
    eventually(lambda: fake_clock.sleeping_count == 1)

    # Restart after 1 s. The worker now runs fine, but the module stays
    # "degraded" until it has proven itself for 10 seconds.
    fake_clock.advance(1)
    eventually(lambda: fake_clock.sleeping_count == 1)  # the restarted worker is sleeping
    fake_clock.advance(9)
    assert supervisor.module_health("demo").state == HealthState.DEGRADED

    fake_clock.advance(1)

    assert supervisor.module_health("demo").state == HealthState.OK


def test_device_offline_error_reports_offline_and_mark_online_restores_ok(
    supervisor: Supervisor, fake_clock: FakeClock, recorder: HealthRecorder
) -> None:
    # First run: the "device" is missing. Second run: it is there, and the
    # worker says so with mark_online().
    attempts: list[int] = []

    def device_worker(worker: WorkerContext) -> None:
        attempts.append(1)
        if len(attempts) == 1:
            raise DeviceOfflineError("COM3 not found")
        worker.mark_online()
        sleep_until_stopped(worker)

    supervisor.register_module("demo")
    supervisor.start_module("demo", [WorkerSpec("device", device_worker, starts_offline=True)])
    recorder.wait_for_sequence("demo", [HealthState.OFFLINE])
    assert supervisor.module_health("demo").message in ("starting", "COM3 not found")
    eventually(lambda: fake_clock.sleeping_count == 1)

    fake_clock.advance(1)

    recorder.wait_for_sequence("demo", [HealthState.OFFLINE, HealthState.OK])


def test_a_worker_that_returns_on_its_own_is_treated_as_a_failure(
    supervisor: Supervisor, recorder: HealthRecorder
) -> None:
    # Workers are supposed to loop until told to stop. Returning early is a bug.
    supervisor.register_module("demo")
    supervisor.start_module("demo", [WorkerSpec("quitter", lambda worker: None)])

    recorder.wait_for_sequence("demo", [HealthState.DEGRADED])
    assert "returned without being asked" in supervisor.module_health("demo").message


def test_the_worst_worker_decides_the_module_health(
    supervisor: Supervisor, recorder: HealthRecorder
) -> None:
    def offline_forever(worker: WorkerContext) -> None:
        raise DeviceOfflineError("unplugged")

    supervisor.register_module("demo")
    supervisor.start_module(
        "demo", [WorkerSpec("fine", sleep_until_stopped), WorkerSpec("gone", offline_forever)]
    )

    recorder.wait_for_sequence("demo", [HealthState.OFFLINE])
    assert supervisor.module_health("demo").message == "unplugged"


def test_stop_module_wakes_and_joins_workers_and_marks_the_module_stopped(
    supervisor: Supervisor, fake_clock: FakeClock
) -> None:
    finished = threading.Event()

    def worker_that_notes_when_it_ends(worker: WorkerContext) -> None:
        sleep_until_stopped(worker)
        finished.set()

    supervisor.register_module("demo")
    supervisor.start_module("demo", [WorkerSpec("idle", worker_that_notes_when_it_ends)])
    eventually(lambda: fake_clock.sleeping_count == 1)

    # The worker is sleeping for 60 fake seconds, but stop must not wait for that.
    supervisor.stop_module("demo")

    assert finished.is_set()
    assert supervisor.module_health("demo").state == HealthState.STOPPED


def test_wake_cuts_a_worker_sleep_short(supervisor: Supervisor, fake_clock: FakeClock) -> None:
    loops: list[int] = []

    def counting_worker(worker: WorkerContext) -> None:
        while not worker.should_stop:
            loops.append(1)
            worker.sleep(60)

    supervisor.register_module("demo")
    supervisor.start_module("demo", [WorkerSpec("counter", counting_worker)])
    eventually(lambda: len(loops) == 1 and fake_clock.sleeping_count == 1)

    # No fake time passes, but waking the worker makes it loop again.
    supervisor.wake("demo", "counter")

    eventually(lambda: len(loops) == 2)


def test_stopping_right_after_starting_is_safe(supervisor: Supervisor) -> None:
    # Stop before the worker thread has had a chance to run at all. This used
    # to crash the thread; now the worker simply never starts its loop.
    supervisor.register_module("demo")
    supervisor.start_module("demo", [WorkerSpec("idle", sleep_until_stopped)])

    supervisor.stop_module("demo")

    assert supervisor.module_health("demo").state == HealthState.STOPPED


def test_all_health_lists_every_registered_module(supervisor: Supervisor) -> None:
    supervisor.register_module("first")
    supervisor.register_module("second")
    supervisor.start_module("second", [])

    health = supervisor.all_health()

    assert health["first"].state == HealthState.STOPPED
    assert health["second"].state == HealthState.OK


def test_listeners_hear_only_about_real_changes(
    supervisor: Supervisor, recorder: HealthRecorder
) -> None:
    supervisor.register_module("demo")
    supervisor.start_module("demo", [])
    supervisor.start_module("demo", [])  # already ok: no new report expected

    assert recorder.states_of("demo") == [HealthState.STOPPED, HealthState.OK]
