"""The cameras module: the camera registry, a driver and arbiter per camera, the joysticks.

Settings (`CamerasSettings`) list the cameras, the serial controllers and the
motion rules. Each enabled camera gets a driver (VISCA/IP or agent-proxied)
with its own supervised worker and a `MotionArbiter`; each enabled controller
gets a Pelco-D worker. Settings edits are applied live: a camera whose
connection changed gets a new driver, a controller whose port changed a new
worker, and everything else (limits, curves, labels, addresses) is read on
every command.

State: `state.cameras.cameras.<id>` (the contract's `CameraState` fields plus
label, owner, route, capabilities, control) and `state.cameras.controllers.<id>`.
Commands: see `commands.py`. Readiness checks: `cameras/readiness.py`.
"""

import threading
from typing import Any

from pydantic import BaseModel

from baja_studio.cameras import health
from baja_studio.cameras.config import CamerasSettings
from baja_studio.cameras.drivers.agent_gateway import AgentGateway
from baja_studio.cameras.drivers.visca_ip import DatagramOpener, UdpTransport
from baja_studio.cameras.inputs.pelco_d_worker import SerialOpener, open_serial_port
from baja_studio.cameras.inputs.serial_ports import SerialPortLister, list_serial_ports
from baja_studio.cameras.readiness import camera_checks
from baja_studio.cameras.registry import CameraRegistry
from baja_studio.core.module import Module, ModuleContext
from baja_studio.core.readiness import ReadinessCheck
from baja_studio.core.supervisor import WorkerContext, WorkerSpec
from baja_studio.modules.cameras.commands import CameraCommands


class CamerasModule(Module):
    """Camera registry, drivers, Pelco-D input and motion arbitration."""

    name = "cameras"
    title = "Cameras"
    SettingsModel = CamerasSettings

    def __init__(
        self,
        agents: AgentGateway,
        datagram_opener: DatagramOpener = UdpTransport,
        serial_opener: SerialOpener = open_serial_port,
        list_ports: SerialPortLister = list_serial_ports,
    ) -> None:
        """`agents` is the agents module; tests pass fake openers for UDP and serial,
        and a fake list of serial ports."""
        super().__init__()
        self._agents = agents
        self._datagram_opener = datagram_opener
        self._serial_opener = serial_opener
        self._lock = threading.Lock()
        self._registry: CameraRegistry | None = None
        self._running = False
        self._subscribed = False
        self._commands = CameraCommands(
            self._current_registry, self._settings, self._save_cameras, list_ports
        )

    def initial_state(self) -> dict[str, Any]:
        """No cameras or controllers until the motion worker applies the settings."""
        return {"cameras": {}, "controllers": {}, "order": [], "motion": {}}

    def start(self, context: ModuleContext) -> None:
        """Follow settings edits and summarise health per device."""
        super().start(context)
        with self._lock:
            self._registry = CameraRegistry(
                context, self._agents, self._datagram_opener, self._serial_opener, self._wake_motion
            )
        context.supervisor.set_health_summary(self.name, health.summarise)
        if not self._subscribed:
            self._subscribed = True
            context.settings.subscribe(self.name, self._apply_new_settings)

    def stop(self) -> None:
        """Workers are already stopped; forget the drivers so a restart builds fresh ones."""
        with self._lock:
            self._running, self._registry = False, None

    def workers(self) -> list[WorkerSpec]:
        """The motion loop; it also connects the configured devices when it starts."""
        return [WorkerSpec(health.MOTION_WORKER, self._run_motion)]

    def readiness_checks(self) -> list[ReadinessCheck]:
        """Every enabled camera and controller, from what their workers publish."""
        state = self.context.state.get(self.name) or {}
        return camera_checks(self._settings(), state, self.context.clock)

    # --- the motion worker ------------------------------------------------------------

    def _run_motion(self, worker: WorkerContext) -> None:
        with self._lock:
            self._running = True
            registry = self._registry
        assert registry is not None
        registry.apply(self._settings())
        while not worker.should_stop:
            pauses = [arbiter.tick() for arbiter in registry.arbiters().values()]
            worker.sleep(min(pauses, default=1.0))

    def _wake_motion(self) -> None:
        self.context.supervisor.wake(self.name, health.MOTION_WORKER)

    # --- settings ------------------------------------------------------------------------

    def _apply_new_settings(self, values: BaseModel) -> None:
        """Settings subscriber: apply the edit while the module runs."""
        assert isinstance(values, CamerasSettings)
        with self._lock:
            registry = self._registry if self._running else None
        if registry is not None:
            registry.apply(values)

    def _settings(self) -> CamerasSettings:
        settings = self.context.current_settings()
        assert isinstance(settings, CamerasSettings)
        return settings

    # --- commands ------------------------------------------------------------------------

    def handle_command(self, command: str, args: dict[str, Any], source: str) -> Any:
        """See `commands.py`: motion, presets, modes, serial ports and raw frames."""
        result = self._commands.handle(command, args, source)
        self._wake_motion()
        return result

    def wants_logged(
        self, command: str, args: dict[str, Any], source: str, result: Any, failed: bool
    ) -> bool:
        """Keep the start of a browser's move stream, not each of its ten-a-second repeats."""
        return self._commands.wants_logged(command, args, source, result, failed)

    def _current_registry(self) -> CameraRegistry | None:
        with self._lock:
            return self._registry

    def _save_cameras(self, cameras: list[dict[str, Any]]) -> None:
        self.context.settings.update(self.name, {"cameras": cameras})
