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
Commands: `move`, `stop`, `recall_preset`, `store_preset`, `set_mode`.
"""

import threading
from concurrent.futures import TimeoutError as FutureTimeoutError
from typing import Any

from pydantic import BaseModel

from baja_studio.agents.client import AgentError
from baja_studio.agents.contract import Mode
from baja_studio.cameras import health
from baja_studio.cameras.config import CamerasSettings
from baja_studio.cameras.driver import Velocity
from baja_studio.cameras.drivers.agent_gateway import AgentGateway
from baja_studio.cameras.drivers.visca_ip import DatagramOpener, UdpTransport
from baja_studio.cameras.inputs.pelco_d_worker import SerialOpener, open_serial_port
from baja_studio.cameras.motion.arbiter import MotionArbiter, MotionRefusedError
from baja_studio.cameras.registry import CameraRegistry
from baja_studio.core.module import CommandError, Module, ModuleContext
from baja_studio.core.supervisor import WorkerContext, WorkerSpec

SET_MODE_TIMEOUT_SECONDS = 3.0


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
    ) -> None:
        """`agents` is the agents module; tests pass fake openers for UDP and serial."""
        super().__init__()
        self._agents = agents
        self._datagram_opener = datagram_opener
        self._serial_opener = serial_opener
        self._lock = threading.Lock()
        self._registry: CameraRegistry | None = None
        self._running = False
        self._subscribed = False

    def initial_state(self) -> dict[str, Any]:
        """No cameras or controllers until the motion worker applies the settings."""
        return {"cameras": {}, "controllers": {}}

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
        """`move {camera, pan, tilt, zoom, ttl_ms?}`, `stop {camera}`,
        `recall_preset {camera, n}`, `store_preset {camera, n}`,
        `set_mode {camera, mode, profile?}` (agent-owned cameras)."""
        arbiter = self._arbiter(args)
        try:
            if command == "move":
                velocity = Velocity(
                    _number(args, "pan"), _number(args, "tilt"), _number(args, "zoom")
                )
                ttl_ms = args.get("ttl_ms")
                result = arbiter.move(source, velocity, None if ttl_ms is None else int(ttl_ms))
            elif command == "stop":
                arbiter.stop(source)
                result = {"stopped": True}
            elif command == "recall_preset":
                result = arbiter.recall_preset(source, _preset(args))
            elif command == "store_preset":
                arbiter.store_preset(source, _preset(args))
                result = {"stored": _preset(args)}
            elif command == "set_mode":
                return self._set_mode(arbiter, args, source)
            else:
                raise CommandError(
                    f"cameras has no command {command!r}; try move, stop, recall_preset, "
                    "store_preset or set_mode"
                )
        except MotionRefusedError as refused:
            raise CommandError(str(refused)) from None
        self._wake_motion()
        return result

    def _set_mode(self, arbiter: MotionArbiter, args: dict[str, Any], source: str) -> Any:
        try:
            mode = Mode(str(args.get("mode")))
        except ValueError:
            raise CommandError("mode must be manual, auto or hold") from None
        if mode == Mode.AUTO_PAUSED:
            raise CommandError("auto_paused cannot be asked for; it follows studio motion")
        profile = args.get("profile")
        if profile is not None and not isinstance(profile, dict):
            raise CommandError("profile must be an object")
        try:
            reply = arbiter.set_mode(source, mode, profile).result(SET_MODE_TIMEOUT_SECONDS)
        except MotionRefusedError as refused:
            raise CommandError(str(refused)) from None
        except AgentError as error:
            raise CommandError(f"the agent refused: {error}") from None
        except FutureTimeoutError:
            raise CommandError("the agent did not answer in time") from None
        except Exception as error:
            raise CommandError(str(error)) from None
        return reply.model_dump(mode="json")

    def _arbiter(self, args: dict[str, Any]) -> MotionArbiter:
        camera_id = str(args.get("camera") or "")
        with self._lock:
            registry = self._registry
        arbiters = registry.arbiters() if registry is not None else {}
        arbiter, known = arbiters.get(camera_id), sorted(arbiters)
        if arbiter is None:
            hint = f"; cameras: {', '.join(known)}" if known else "; none is enabled"
            raise CommandError(f"no enabled camera called {camera_id!r}{hint}")
        return arbiter


def _number(args: dict[str, Any], key: str) -> float:
    value = args.get(key, 0.0)
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise CommandError(f"{key} must be a number from -1 to 1")
    if not -1.0 <= value <= 1.0:
        raise CommandError(f"{key} must be from -1 to 1")
    return float(value)


def _preset(args: dict[str, Any]) -> int:
    value = args.get("n")
    if isinstance(value, bool) or not isinstance(value, int):
        raise CommandError("n must be a whole preset number (0 is the first)")
    return value
