"""Tests for the supervisor (core/supervisor.py).

The supervisor runs each module's workers in threads. These tests use small
hand-written worker functions and the fake clock, so restarts and backoff
delays can be checked without waiting in real time.
"""

import logging
import threading
from collections.abc import Callable, Iterator

import pytest

from baja_studio.core.logging import LogHub
from baja_studio.core.supervisor import (
    DeviceOfflineError,
    Health,
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


def test_a_running_worker_can_report_its_own_health_and_it_sticks(
    supervisor: Supervisor, fake_clock: FakeClock, recorder: HealthRecorder
) -> None:
    # A worker that looks after several devices keeps running when one of
    # them goes away, so it cannot raise. Instead it reports "degraded" itself.
    def multi_device_worker(worker: WorkerContext) -> None:
        worker.report_health(HealthState.DEGRADED, "1 of 2 agents offline")
        sleep_until_stopped(worker)

    supervisor.register_module("demo")
    supervisor.start_module("demo", [WorkerSpec("devices", multi_device_worker)])
    recorder.wait_for_sequence("demo", [HealthState.DEGRADED])
    assert supervisor.module_health("demo").message == "1 of 2 agents offline"
    eventually(lambda: fake_clock.sleeping_count == 1)

    # After ten seconds the "ran stably" rule would normally flip a degraded
    # worker back to ok. It must not override a health the worker reported.
    fake_clock.advance(11)

    assert supervisor.module_health("demo").state == HealthState.DEGRADED


def test_a_reported_health_is_replaced_by_the_next_report(
    supervisor: Supervisor, recorder: HealthRecorder
) -> None:
    def recovering_worker(worker: WorkerContext) -> None:
        worker.report_health(HealthState.OFFLINE, "all agents offline")
        worker.report_health(HealthState.OK, "2 agents ok")
        sleep_until_stopped(worker)

    supervisor.register_module("demo")
    supervisor.start_module("demo", [WorkerSpec("devices", recovering_worker)])

    recorder.wait_for_sequence("demo", [HealthState.OFFLINE, HealthState.OK])
    assert supervisor.module_health("demo").message == "2 agents ok"


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


# --- workers added and removed one at a time (devices that come and go) -----------


def test_start_worker_adds_a_worker_to_a_running_module(supervisor: Supervisor) -> None:
    # A module starts with one worker; later (say, a camera was added in
    # settings) it asks for a second one by name.
    supervisor.register_module("demo")
    supervisor.start_module("demo", [WorkerSpec("first", sleep_until_stopped)])
    started = threading.Event()

    def second(worker: WorkerContext) -> None:
        started.set()
        sleep_until_stopped(worker)

    supervisor.start_worker("demo", WorkerSpec("second", second))

    # The new worker really runs, and the supervisor lists both.
    assert started.wait(5)
    assert supervisor.worker_names("demo") == ["first", "second"]


def test_start_worker_refuses_a_name_that_is_already_running(supervisor: Supervisor) -> None:
    supervisor.register_module("demo")
    supervisor.start_module("demo", [WorkerSpec("camera-cam1", sleep_until_stopped)])

    # Two workers with one name would make "stop camera-cam1" ambiguous.
    with pytest.raises(ValueError, match="already running"):
        supervisor.start_worker("demo", WorkerSpec("camera-cam1", sleep_until_stopped))


def test_start_worker_on_a_stopped_module_does_nothing(supervisor: Supervisor) -> None:
    # A disabled module must stay quiet even if a settings change asks for a worker.
    supervisor.register_module("demo")

    supervisor.start_worker("demo", WorkerSpec("late", sleep_until_stopped))

    assert supervisor.worker_names("demo") == []
    assert supervisor.module_health("demo").state == HealthState.STOPPED


def test_stop_worker_stops_only_that_worker_and_its_health_stops_counting(
    supervisor: Supervisor,
) -> None:
    # Two workers: one healthy, one whose device is away (it reports offline).
    supervisor.register_module("demo")

    def away(worker: WorkerContext) -> None:
        worker.report_health(HealthState.OFFLINE, "unplugged")
        sleep_until_stopped(worker)

    supervisor.start_module(
        "demo", [WorkerSpec("healthy", sleep_until_stopped), WorkerSpec("away", away)]
    )
    eventually(lambda: supervisor.module_health("demo").state == HealthState.OFFLINE)

    # Removing the away device's worker: the module is judged on what is left.
    supervisor.stop_worker("demo", "away")

    assert supervisor.worker_names("demo") == ["healthy"]
    assert supervisor.module_health("demo").state == HealthState.OK


def test_a_worker_restarted_under_the_same_name_ignores_reports_from_the_old_one(
    supervisor: Supervisor,
) -> None:
    # The first worker keeps its context after being stopped, so it could still
    # call report_health late (a thread that was slow to notice). That late
    # report must not change the health of the new worker with the same name.
    supervisor.register_module("demo")
    old_contexts: list[WorkerContext] = []

    def remembers_context(worker: WorkerContext) -> None:
        old_contexts.append(worker)
        sleep_until_stopped(worker)

    supervisor.start_module("demo", [WorkerSpec("cam", remembers_context)])
    eventually(lambda: len(old_contexts) == 1)
    supervisor.stop_worker("demo", "cam")
    supervisor.start_worker("demo", WorkerSpec("cam", sleep_until_stopped))

    old_contexts[0].report_health(HealthState.OFFLINE, "late report from the old worker")

    assert supervisor.module_health("demo").state == HealthState.OK


def test_a_health_summary_decides_the_module_health_from_every_worker(
    supervisor: Supervisor, recorder: HealthRecorder
) -> None:
    # Without a summary, one offline worker makes the whole module offline.
    # This summary says "degraded" when only some workers are away, and names them.
    def some_away_is_degraded(healths: dict[str, Health]) -> Health:
        away = sorted(name for name, health in healths.items() if health.state != HealthState.OK)
        if not away:
            return Health(HealthState.OK)
        state = HealthState.OFFLINE if len(away) == len(healths) else HealthState.DEGRADED
        return Health(state, "away: " + ", ".join(away))

    supervisor.register_module("demo")
    supervisor.set_health_summary("demo", some_away_is_degraded)

    def away(worker: WorkerContext) -> None:
        worker.report_health(HealthState.OFFLINE, "unplugged")
        sleep_until_stopped(worker)

    supervisor.start_module(
        "demo", [WorkerSpec("cam1", sleep_until_stopped), WorkerSpec("cam2", away)]
    )

    eventually(
        lambda: supervisor.module_health("demo") == Health(HealthState.DEGRADED, "away: cam2")
    )
    # The listeners (the home page) heard the summarised health, not "offline".
    assert HealthState.OFFLINE not in recorder.states_of("demo")


# --- repeated identical failures are logged once (WP10) -------------------------

SUPERVISOR_LOGGER = "baja_studio.core.supervisor"


def records_at(caplog: pytest.LogCaptureFixture, level: int) -> list[str]:
    """The supervisor's messages logged at exactly `level`."""
    return [
        record.getMessage()
        for record in caplog.records
        if record.name == SUPERVISOR_LOGGER and record.levelno == level
    ]


def backing_off_after(runs: list[int], run: int, fake_clock: FakeClock) -> Callable[[], bool]:
    """True once run number `run` has failed and the supervisor waits to restart it."""
    return lambda: len(runs) == run and fake_clock.sleeping_count == 1


def fail_n_times(
    supervisor: Supervisor,
    fake_clock: FakeClock,
    failures: int,
    error_for_run: Callable[[int], Exception],
) -> list[int]:
    """Run a worker that fails `failures` times, then stays up; returns its runs.

    `error_for_run(n)` is the error raised on run n (1-based). Fake time is
    moved past each backoff wait (30 s covers the longest) once the worker is
    in it, so the next run starts.
    """
    runs: list[int] = []

    def flaky(worker: WorkerContext) -> None:
        runs.append(len(runs) + 1)
        if len(runs) <= failures:
            raise error_for_run(len(runs))
        sleep_until_stopped(worker)

    supervisor.register_module("demo")
    supervisor.start_module("demo", [WorkerSpec("device", flaky)])
    for run in range(1, failures + 1):
        # Wait until run `run` failed and the supervisor is in its backoff wait.
        eventually(backing_off_after(runs, run, fake_clock))
        fake_clock.advance(30)
    eventually(lambda: len(runs) == failures + 1)
    return runs


def test_fifty_identical_failures_log_one_warning(
    supervisor: Supervisor, fake_clock: FakeClock, caplog: pytest.LogCaptureFixture
) -> None:
    # A dev PC without the gateway: the worker fails the same way 50 times.
    # caplog (a pytest fixture) collects log records; DEBUG captures everything.
    caplog.set_level(logging.DEBUG, logger=SUPERVISOR_LOGGER)

    fail_n_times(supervisor, fake_clock, 50, lambda run: DeviceOfflineError("no answer from Caddy"))

    # One warning for the first failure, nothing more at WARNING or above...
    assert records_at(caplog, logging.WARNING) == ["device offline: no answer from Caddy"]
    assert records_at(caplog, logging.ERROR) == []
    # ...and only the first "restarting" line at INFO.
    assert [m for m in records_at(caplog, logging.INFO) if m.startswith("restarting")] == [
        "restarting worker in 1 s"
    ]
    # The other 49 are at DEBUG, with a running count.
    repeats = [m for m in records_at(caplog, logging.DEBUG) if "again" in m]
    assert len(repeats) == 49
    assert repeats[0] == "device offline again (2 in a row): no answer from Caddy"
    assert repeats[-1] == "device offline again (50 in a row): no answer from Caddy"


def test_the_health_still_follows_every_repeated_failure(
    supervisor: Supervisor, fake_clock: FakeClock, recorder: HealthRecorder
) -> None:
    # Logging less must not hide anything from the health: still offline.
    fail_n_times(supervisor, fake_clock, 5, lambda run: DeviceOfflineError("COM3 not found"))

    assert supervisor.module_health("demo") == Health(HealthState.OFFLINE, "COM3 not found")


def test_a_different_error_is_logged_again(
    supervisor: Supervisor, fake_clock: FakeClock, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG, logger=SUPERVISOR_LOGGER)

    # Runs 1-3: cable unplugged. Runs 4-6: the port is there but busy.
    def error_for_run(run: int) -> Exception:
        return DeviceOfflineError("COM3 not found" if run <= 3 else "COM3 busy")

    fail_n_times(supervisor, fake_clock, 6, error_for_run)

    assert records_at(caplog, logging.WARNING) == [
        "device offline: COM3 not found",
        "device offline: COM3 busy",
    ]


def test_the_same_class_with_a_new_message_is_a_different_error(
    supervisor: Supervisor, fake_clock: FakeClock, caplog: pytest.LogCaptureFixture
) -> None:
    # Unexpected exceptions (bugs) are logged at ERROR with the traceback, once
    # per distinct message: here two messages, two error lines.
    caplog.set_level(logging.DEBUG, logger=SUPERVISOR_LOGGER)

    fail_n_times(supervisor, fake_clock, 4, lambda run: ValueError(f"bad frame {run // 3}"))

    errors = [r for r in caplog.records if r.levelno == logging.ERROR]
    assert [r.getMessage() for r in errors] == ["worker failed", "worker failed"]
    assert all(r.exc_info is not None for r in errors)
    assert "bad frame 0" in str(errors[0].exc_info[1])  # type: ignore[index]
    assert "bad frame 1" in str(errors[1].exc_info[1])  # type: ignore[index]


def test_recovery_is_logged_once_with_the_number_of_failures(
    supervisor: Supervisor, fake_clock: FakeClock, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG, logger=SUPERVISOR_LOGGER)
    fail_n_times(supervisor, fake_clock, 7, lambda run: RuntimeError("socket closed"))

    # The 8th run stays up; after ten stable seconds the worker counts as recovered.
    eventually(lambda: fake_clock.sleeping_count == 1)
    fake_clock.advance(10)

    eventually(lambda: "recovered after 7 failures" in records_at(caplog, logging.INFO))
    assert supervisor.module_health("demo").state == HealthState.OK


def test_after_recovery_the_same_error_is_news_again(
    supervisor: Supervisor, fake_clock: FakeClock, caplog: pytest.LogCaptureFixture
) -> None:
    # The device answers between two outages (mark_online), so the second
    # outage, though worded the same, is warned about again.
    caplog.set_level(logging.DEBUG, logger=SUPERVISOR_LOGGER)
    runs: list[int] = []

    def outage_online_outage(worker: WorkerContext) -> None:
        runs.append(len(runs) + 1)
        if len(runs) == 2:
            worker.mark_online()
        if len(runs) <= 3:
            raise DeviceOfflineError("XR18 not answering")
        sleep_until_stopped(worker)

    supervisor.register_module("demo")
    supervisor.start_module("demo", [WorkerSpec("mixer", outage_online_outage)])
    for run in (1, 2, 3):
        eventually(backing_off_after(runs, run, fake_clock))
        fake_clock.advance(30)
    eventually(lambda: len(runs) == 4)

    assert records_at(caplog, logging.WARNING) == ["device offline: XR18 not answering"] * 2
    assert "device back online: recovered after 1 failure" in records_at(caplog, logging.INFO)


def test_the_live_log_stream_shows_the_repeated_failure_once(
    supervisor: Supervisor, fake_clock: FakeClock
) -> None:
    # The Logs page streams what the LogHub receives. With the service's
    # usual INFO level, the repeats (DEBUG) never reach it.
    hub = LogHub()
    studio_logger = logging.getLogger("baja_studio")
    previous_level = studio_logger.level
    studio_logger.setLevel(logging.INFO)
    studio_logger.addHandler(hub)
    try:
        fail_n_times(supervisor, fake_clock, 10, lambda run: DeviceOfflineError("no gateway"))
    finally:
        studio_logger.removeHandler(hub)
        studio_logger.setLevel(previous_level)

    messages = [(entry["level"], entry["message"]) for entry in hub.recent()]
    assert messages == [
        ("WARNING", "device offline: no gateway"),
        ("INFO", "restarting worker in 1 s"),
    ]
