"""Tests for the module registry (core/registry.py).

The registry starts and stops modules, follows their `enabled` setting, and
routes commands to them while recording each one in the command log.
"""

import threading
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from baja_studio.core.commandlog import CommandLog
from baja_studio.core.database import Database
from baja_studio.core.module import CommandError, Module, ModuleContext
from baja_studio.core.registry import ModuleRegistry, UnknownModuleError
from baja_studio.core.settings import SettingsStore
from baja_studio.core.state import StateStore
from baja_studio.core.supervisor import HealthState, Supervisor, WorkerContext, WorkerSpec
from tests.conftest import FakeClock


class LampModule(Module):
    """A tiny module for these tests: a lamp that can be switched, with one idle worker."""

    name = "lamp"
    title = "Lamp"

    def __init__(self) -> None:
        super().__init__()
        self.start_calls = 0
        self.stop_calls = 0
        self.worker_running = threading.Event()

    def initial_state(self) -> dict[str, Any]:
        return {"on": False}

    def start(self, context: ModuleContext) -> None:
        super().start(context)
        self.start_calls += 1

    def stop(self) -> None:
        self.stop_calls += 1

    def workers(self) -> list[WorkerSpec]:
        return [WorkerSpec("idle", self._idle)]

    def handle_command(self, command: str, args: dict[str, Any], source: str) -> Any:
        if command == "switch":
            self.context.update_state("on", bool(args["on"]))
            return {"on": bool(args["on"])}
        if command == "explode":
            raise RuntimeError("bulb blew")
        raise CommandError(f"no command {command!r}")

    def _idle(self, worker: WorkerContext) -> None:
        self.worker_running.set()
        while not worker.should_stop:
            worker.sleep(60)
        self.worker_running.clear()


class PermanentModule(LampModule):
    """Like the lamp, but marked as impossible to disable (as `home` is)."""

    name = "permanent"
    can_be_disabled = False


class BrokenStartModule(Module):
    name = "broken"
    title = "Broken"

    def start(self, context: ModuleContext) -> None:
        raise RuntimeError("cannot start")


@pytest.fixture
def parts(tmp_path: Path, fake_clock: FakeClock) -> Iterator[dict[str, Any]]:
    """The shared services a registry needs, built on a temporary database."""
    database = Database(tmp_path / "registry-test.sqlite3")
    supervisor = Supervisor(fake_clock)
    settings = SettingsStore(database, fake_clock)
    command_log = CommandLog(database, fake_clock)
    state = StateStore()
    registry = ModuleRegistry(settings, state, command_log, supervisor, fake_clock)
    yield {
        "registry": registry,
        "settings": settings,
        "state": state,
        "supervisor": supervisor,
        "command_log": command_log,
    }
    registry.stop_all()


def test_registering_sets_up_settings_state_and_a_stopped_health_entry(
    parts: dict[str, Any],
) -> None:
    parts["registry"].register(LampModule())

    assert parts["settings"].record("lamp").version == 1
    assert parts["state"].get("lamp") == {"on": False}
    assert parts["supervisor"].module_health("lamp").state == HealthState.STOPPED


def test_registering_the_same_name_twice_is_an_error(parts: dict[str, Any]) -> None:
    parts["registry"].register(LampModule())

    with pytest.raises(ValueError, match="registered twice"):
        parts["registry"].register(LampModule())


def test_start_all_starts_modules_and_their_workers(parts: dict[str, Any]) -> None:
    lamp = LampModule()
    parts["registry"].register(lamp)

    parts["registry"].start_all()

    assert lamp.start_calls == 1
    assert lamp.worker_running.wait(10)
    assert parts["registry"].is_running("lamp")
    assert parts["supervisor"].module_health("lamp").state == HealthState.OK


def test_a_module_disabled_in_settings_is_not_started(parts: dict[str, Any]) -> None:
    lamp = LampModule()
    parts["registry"].register(lamp)
    parts["settings"].update("lamp", {"enabled": False})

    parts["registry"].start_all()

    assert lamp.start_calls == 0
    assert parts["supervisor"].module_health("lamp").state == HealthState.STOPPED


def test_turning_enabled_off_and_on_stops_and_restarts_the_module(parts: dict[str, Any]) -> None:
    lamp = LampModule()
    parts["registry"].register(lamp)
    parts["registry"].start_all()

    # Switching the setting off is all it takes: no restart of the service.
    parts["settings"].update("lamp", {"enabled": False})
    assert lamp.stop_calls == 1
    assert parts["supervisor"].module_health("lamp").state == HealthState.STOPPED

    parts["settings"].update("lamp", {"enabled": True})
    assert lamp.start_calls == 2
    assert parts["supervisor"].module_health("lamp").state == HealthState.OK


def test_a_module_that_cannot_be_disabled_keeps_running(parts: dict[str, Any]) -> None:
    permanent = PermanentModule()
    parts["registry"].register(permanent)
    parts["settings"].update("permanent", {"enabled": False})

    parts["registry"].start_all()

    assert parts["registry"].is_running("permanent")


def test_a_module_whose_start_fails_stays_stopped_and_others_still_start(
    parts: dict[str, Any],
) -> None:
    lamp = LampModule()
    parts["registry"].register(BrokenStartModule())
    parts["registry"].register(lamp)

    parts["registry"].start_all()

    assert not parts["registry"].is_running("broken")
    assert parts["registry"].is_running("lamp")


def test_a_successful_command_returns_its_result_and_is_logged(parts: dict[str, Any]) -> None:
    parts["registry"].register(LampModule())
    parts["registry"].start_all()

    result = parts["registry"].dispatch_command("lamp", "switch", {"on": True}, "web:abc")

    assert result == {"on": True}
    assert parts["state"].get("lamp", "on") is True
    entry = parts["command_log"].tail()[0]
    assert (entry["module"], entry["command"], entry["source"], entry["result"]) == (
        "lamp",
        "switch",
        "web:abc",
        "ok",
    )


def test_a_failing_command_is_logged_with_its_error_and_re_raised(parts: dict[str, Any]) -> None:
    parts["registry"].register(LampModule())
    parts["registry"].start_all()

    with pytest.raises(RuntimeError, match="bulb blew"):
        parts["registry"].dispatch_command("lamp", "explode", {}, "api:test")

    assert parts["command_log"].tail()[0]["result"] == "error: bulb blew"


def test_commands_to_a_stopped_module_are_refused(parts: dict[str, Any]) -> None:
    parts["registry"].register(LampModule())  # registered but never started

    with pytest.raises(CommandError, match="stopped"):
        parts["registry"].dispatch_command("lamp", "switch", {"on": True}, "web:abc")


def test_commands_to_an_unknown_module_raise_unknown_module(parts: dict[str, Any]) -> None:
    with pytest.raises(UnknownModuleError):
        parts["registry"].dispatch_command("ghost", "boo", {}, "web:abc")


def test_stop_all_stops_every_running_module(parts: dict[str, Any]) -> None:
    lamp = LampModule()
    parts["registry"].register(lamp)
    parts["registry"].start_all()

    parts["registry"].stop_all()

    assert lamp.stop_calls == 1
    assert not lamp.worker_running.is_set()
    assert not parts["registry"].is_running("lamp")
