"""The cameras module running inside the service, with fakes around it, for module tests.

Loaded into every test through `pytest_plugins` in conftest.py, like the fake
agents. The `studio` fixture runs the service with the agents and cameras
modules on the fake clock. Nothing real is opened: VISCA cameras are
`FakeViscaCamera`s on a pretend network, the joystick is a `FakeSerialPort`,
the computer's serial ports are a plain list, and agents are fake agents
reached through the in-process transport.
"""

from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from typing import Any

import pytest
from fastapi.testclient import TestClient

from baja_studio.cameras.inputs.serial_ports import SerialPortInfo
from baja_studio.core.services import StudioServices
from baja_studio.fakes.fake_serial import FakeSerialPorts
from baja_studio.fakes.in_process import InProcessTransport
from baja_studio.modules.agents import AgentsModule
from baja_studio.modules.cameras import CamerasModule
from tests.cameras.visca_network import FakeViscaNetwork
from tests.conftest import (
    AppFactory,
    FakeClock,
    eventually,
    module_worker_threads,
    step_fake_time_until,
)
from tests.fake_agents import AGENT_HOST, StartAgent

SMTAV_HOST = "10.0.0.9"
# The cameras workers that sleep on the fake clock. Controllers block in a
# (real-time) serial read instead, so waiting for them to sleep would never end.
CAMERA_CLOCK_WORKERS = module_worker_threads("cameras", skip=("controller:",))


@dataclass
class Studio:
    """The running service plus the fakes around it."""

    services: StudioServices
    network: FakeViscaNetwork
    ports: FakeSerialPorts
    clock: FakeClock
    # What the module's `list_serial_ports` command finds on this "computer".
    serial_ports: list[SerialPortInfo] = field(default_factory=list)

    def command(self, command: str, source: str = "api:test", **args: Any) -> Any:
        return self.services.registry.dispatch_command("cameras", command, args, source)

    def configure(self, **values: Any) -> None:
        self.services.settings.update("cameras", values)

    def camera(self, camera_id: str) -> Any:
        return self.services.state.get("cameras", f"cameras.{camera_id}")

    def status(self, camera_id: str) -> Any:
        camera = self.camera(camera_id)
        return camera and camera["status"]

    def health(self) -> Any:
        return self.services.supervisor.module_health("cameras")

    def soon(self, condition: Callable[[], bool], within_seconds: float = 3.0) -> None:
        """Let fake time pass in 0.1 s steps until `condition()` holds.

        Agent cameras look at their agent ten times a (fake) second, so their
        state only changes as fake time passes; `eventually` alone would wait
        for a tick that never comes.
        """
        step_fake_time_until(self.clock, condition, within_seconds, CAMERA_CLOCK_WORKERS)

    def log(self, source_prefix: str) -> list[tuple[str, str, dict[str, Any]]]:
        """(command, result, args) of the cameras log entries from a source, newest first."""
        return [
            (entry["command"], entry["result"], entry["args"])
            for entry in self.services.command_log.tail("cameras", 200)
            if entry["source"].startswith(source_prefix)
        ]


@pytest.fixture
def studio(
    make_app: AppFactory,
    transport: InProcessTransport,
    start_agent: StartAgent,
    fake_clock: FakeClock,
) -> Iterator[Studio]:
    network, ports = FakeViscaNetwork(fake_clock), FakeSerialPorts()
    listed: list[SerialPortInfo] = []

    def modules(registry: Any) -> list[Any]:
        agents = AgentsModule(transport, studio_id="test-studio")
        return [CamerasModule(agents, network.open, ports.open, lambda: list(listed)), agents]

    app = make_app(modules)
    with TestClient(app):
        yield Studio(app.state.services, network, ports, fake_clock, listed)


def smtav(**overrides: Any) -> dict[str, Any]:
    """A direct VISCA/IP camera entry, as the Settings page would save it."""
    return {"id": "smtav", "address": {"host": SMTAV_HOST}, "controller_address": 1, **overrides}


def agent_camera(**overrides: Any) -> dict[str, Any]:
    return {
        "id": "trackside",
        "owner": "agent",
        "driver": "agent",
        "address": {"agent_id": "laptop-a", "remote_camera_id": "cam1"},
        **overrides,
    }


def add_agent(studio: Studio) -> None:
    """Configure the fake agent and wait until the agents module's link to it is up.

    The link connects in real time (on the agents module's event loop); letting
    fake time run ahead of it would only send it into reconnect backoff.
    """
    studio.services.settings.update("agents", {"agents": [{"id": "laptop-a", "host": AGENT_HOST}]})
    eventually(lambda: studio.services.state.get("agents", "agents.laptop-a.status") == "ok")
