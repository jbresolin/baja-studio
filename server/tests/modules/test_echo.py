"""Tests for the echo demo module (modules/echo).

The echo module's worker runs a real child process
(`baja_studio.childprocs.echo_child`). These tests check the full loop:
start the child, talk to it, kill it, and watch the supervisor bring it back.
"""

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from baja_studio.core.module import CommandError
from baja_studio.core.services import StudioServices
from baja_studio.core.supervisor import HealthState
from baja_studio.modules.echo import EchoModule
from tests.conftest import AppFactory, FakeClock, HealthRecorder, eventually


@pytest.fixture
def recorder() -> HealthRecorder:
    return HealthRecorder()


@pytest.fixture
def services(make_app: AppFactory, recorder: HealthRecorder) -> Iterator[StudioServices]:
    """Start an app with just the echo module and wait until its child answers."""
    app = make_app(lambda registry: [EchoModule()])
    services: StudioServices = app.state.services
    services.supervisor.add_health_listener(recorder)
    with TestClient(app):
        # The module shows "offline" until the child answers its first ping.
        recorder.wait_for_sequence("echo", [HealthState.OFFLINE, HealthState.OK])
        yield services


def test_say_round_trips_through_the_child_process(services: StudioServices) -> None:
    reply = services.registry.dispatch_command("echo", "say", {"text": "hello"}, "web:test")

    assert reply == "HELLO"
    assert services.state.get("echo", "last_reply") == "HELLO"


def test_say_without_text_is_refused(services: StudioServices) -> None:
    with pytest.raises(CommandError, match="needs a 'text'"):
        services.registry.dispatch_command("echo", "say", {}, "web:test")


def test_an_unknown_command_is_refused(services: StudioServices) -> None:
    with pytest.raises(CommandError, match="no command 'shout'"):
        services.registry.dispatch_command("echo", "shout", {}, "web:test")


def test_kill_child_goes_offline_and_the_child_is_restarted(
    services: StudioServices, fake_clock: FakeClock, recorder: HealthRecorder
) -> None:
    # Kill the child process, as the "kill_child" button in the UI does.
    services.registry.dispatch_command("echo", "kill_child", {}, "web:test")

    # The worker notices the child is gone and the module turns "offline".
    recorder.wait_for_sequence("echo", [HealthState.OK, HealthState.OFFLINE])
    assert "exited" in services.supervisor.module_health("echo").message

    # The supervisor waits 1 s (fake time) before restarting. Let that pass.
    eventually(lambda: fake_clock.sleeping_count == 1)
    fake_clock.advance(1)

    # A new child starts, answers its first ping, and the module is "ok" again.
    recorder.wait_for_sequence("echo", [HealthState.OK, HealthState.OFFLINE, HealthState.OK])
    assert services.registry.dispatch_command("echo", "say", {"text": "back"}, "web:t") == "BACK"


def test_say_while_the_child_is_down_explains_the_problem(
    services: StudioServices, recorder: HealthRecorder
) -> None:
    services.registry.dispatch_command("echo", "kill_child", {}, "web:test")
    recorder.wait_for_sequence("echo", [HealthState.OK, HealthState.OFFLINE])

    with pytest.raises(CommandError, match="not running"):
        services.registry.dispatch_command("echo", "say", {"text": "anyone?"}, "web:test")


def test_stopping_the_service_stops_the_child(
    make_app: AppFactory, recorder: HealthRecorder
) -> None:
    module = EchoModule()
    app = make_app(lambda registry: [module])
    app.state.services.supervisor.add_health_listener(recorder)
    with TestClient(app):
        recorder.wait_for_sequence("echo", [HealthState.OK])
        host = module._host
        assert host is not None and host.is_running

    # Leaving the `with` block shut the app down; the child must be gone too.
    assert not host.is_running
