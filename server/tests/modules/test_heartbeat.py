"""Tests for the heartbeat demo module (modules/heartbeat).

Each test starts an app containing only the heartbeat module, on the fake
clock, so "one second" passes exactly when the test says so.
"""

from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient

from baja_studio.core.services import StudioServices
from baja_studio.core.supervisor import HealthState
from baja_studio.modules.heartbeat import HeartbeatModule
from tests.conftest import AppFactory, FakeClock, HealthRecorder, eventually


@pytest.fixture
def recorder() -> HealthRecorder:
    return HealthRecorder()


@pytest.fixture
def services(make_app: AppFactory, recorder: HealthRecorder) -> Iterator[StudioServices]:
    """Start an app with just the heartbeat module and hand back its services."""
    app = make_app(lambda registry: [HeartbeatModule()])
    services: StudioServices = app.state.services
    services.supervisor.add_health_listener(recorder)
    with TestClient(app):
        yield services


def count(services: StudioServices) -> Any:
    """The current value of state.heartbeat.count."""
    return services.state.get("heartbeat", "count")


def ticker_is_sleeping(fake_clock: FakeClock) -> bool:
    """True when the ticker worker is waiting for its next tick."""
    return fake_clock.sleeping_count == 1


def test_the_count_goes_up_by_one_every_interval(
    services: StudioServices, fake_clock: FakeClock
) -> None:
    # On start the ticker counts once straight away, then sleeps one second.
    eventually(lambda: count(services) == 1 and ticker_is_sleeping(fake_clock))

    fake_clock.advance(1)
    eventually(lambda: count(services) == 2 and ticker_is_sleeping(fake_clock))

    fake_clock.advance(1)
    eventually(lambda: count(services) == 3)


def test_reset_sets_the_count_back_to_zero(services: StudioServices, fake_clock: FakeClock) -> None:
    eventually(lambda: count(services) == 1)

    result = services.registry.dispatch_command("heartbeat", "reset", {}, "web:test")

    assert result == {"count": 0}
    assert count(services) == 0


def test_a_new_interval_takes_effect_without_a_restart(
    services: StudioServices, fake_clock: FakeClock
) -> None:
    eventually(lambda: count(services) == 1 and ticker_is_sleeping(fake_clock))

    # Save a five-second interval, as the Settings page would. Saving wakes
    # the ticker, which ticks once and then sleeps for the new interval.
    services.settings.update("heartbeat", {"interval_s": 5.0})
    eventually(lambda: count(services) == 2 and ticker_is_sleeping(fake_clock))
    assert services.state.get("heartbeat", "interval_s") == 5.0

    # One second is no longer enough for a tick...
    fake_clock.advance(1)
    assert count(services) == 2

    # ...but five seconds in total are.
    fake_clock.advance(4)
    eventually(lambda: count(services) == 3)


def test_crash_once_makes_the_worker_fail_and_the_supervisor_recovers_it(
    services: StudioServices, fake_clock: FakeClock, recorder: HealthRecorder
) -> None:
    eventually(lambda: count(services) == 1 and ticker_is_sleeping(fake_clock))

    # Ask the worker to crash. The module should turn "degraded"...
    services.registry.dispatch_command("heartbeat", "crash_once", {}, "web:test")
    recorder.wait_for_sequence("heartbeat", [HealthState.OK, HealthState.DEGRADED])
    assert "crash requested" in services.supervisor.module_health("heartbeat").message

    # ...be restarted after the 1 s backoff, and start counting again...
    eventually(lambda: ticker_is_sleeping(fake_clock))  # the supervisor's backoff wait
    fake_clock.advance(1)
    eventually(lambda: count(services) == 2 and ticker_is_sleeping(fake_clock))

    # ...and be "ok" again once it has run for ten seconds without failing.
    fake_clock.advance(10)
    recorder.wait_for_sequence("heartbeat", [HealthState.OK, HealthState.DEGRADED, HealthState.OK])


def test_disabling_the_module_stops_the_ticker(
    services: StudioServices, fake_clock: FakeClock, recorder: HealthRecorder
) -> None:
    eventually(lambda: count(services) == 1)

    services.settings.update("heartbeat", {"enabled": False})

    assert services.supervisor.module_health("heartbeat").state == HealthState.STOPPED
    fake_clock.advance(5)
    assert count(services) == 1  # nothing ticks while stopped
