"""The cameras module's live registry: a driver and arbiter per camera, a worker per controller.

`apply(settings)` makes what runs match the settings: a camera that is new
gets a driver (VISCA/IP or agent-proxied) and a `MotionArbiter`; one whose
connection (driver, address, fallback) changed gets a new driver; a removed
one is disconnected and leaves the state. Controllers likewise, by port and
baud. Everything else (limits, curves, labels, controller addresses) is read
on every command, so it needs nothing rebuilt.
"""

import threading
from collections.abc import Callable
from typing import Any

from baja_studio.cameras.config import (
    CameraAddress,
    CameraConfig,
    CamerasSettings,
    ControllerConfig,
    DriverKind,
)
from baja_studio.cameras.driver import CameraDriver, DriverHost
from baja_studio.cameras.drivers.agent_gateway import AgentGateway
from baja_studio.cameras.drivers.agent_proxy import AgentProxyDriver
from baja_studio.cameras.drivers.visca_ip import DatagramOpener, ViscaIpDriver
from baja_studio.cameras.inputs.pelco_d_worker import PelcoDInput, SerialOpener
from baja_studio.cameras.motion.arbiter import MotionArbiter
from baja_studio.core.module import ModuleContext


class CameraRegistry:
    """The running cameras and controllers of one start of the cameras module."""

    def __init__(
        self,
        context: ModuleContext,
        agents: AgentGateway,
        datagram_opener: DatagramOpener,
        serial_opener: SerialOpener,
        wake_motion: Callable[[], None],
    ) -> None:
        """Nothing runs until `apply` is called (by the module's motion worker)."""
        self._context = context
        self._agents = agents
        self._datagram_opener = datagram_opener
        self._serial_opener = serial_opener
        self._wake_motion = wake_motion
        self._lock = threading.RLock()
        self._apply_lock = threading.Lock()
        self._configs: dict[str, CameraConfig] = {}
        self._connections: dict[str, tuple[object, ...]] = {}
        self._arbiters: dict[str, MotionArbiter] = {}
        self._controllers: dict[str, PelcoDInput] = {}

    def arbiters(self) -> dict[str, MotionArbiter]:
        """Every enabled camera's arbiter, by camera id."""
        with self._lock:
            return dict(self._arbiters)

    def apply(self, settings: CamerasSettings) -> None:
        """Make drivers and controller workers match the settings (start, stop, rebuild).

        Stopping a driver or controller waits for its worker, and those workers
        take `_lock` (to publish, to find a camera), so the waiting happens
        outside `_lock`; `_apply_lock` keeps two applies from interleaving.
        """
        with self._apply_lock:
            wanted = {camera.id: camera for camera in settings.cameras if camera.enabled}
            with self._lock:
                leaving = [
                    camera_id
                    for camera_id in self._arbiters
                    if camera_id not in wanted
                    or wanted[camera_id].connection_key() != self._connections[camera_id]
                ]
                removed = [(camera_id, self._arbiters.pop(camera_id)) for camera_id in leaving]
                for camera_id in leaving:
                    self._connections.pop(camera_id, None)
                    self._configs.pop(camera_id, None)
            for camera_id, arbiter in removed:
                arbiter.driver.disconnect()
                if camera_id not in wanted:
                    self._forget_state("cameras", camera_id)
            for camera_id, camera in wanted.items():
                with self._lock:
                    self._configs[camera_id] = camera
                    exists = camera_id in self._arbiters
                if exists:
                    self._publish_camera_details(camera)
                else:
                    self._add_camera(camera)
            self._apply_controllers(settings.controllers)
        self._wake_motion()

    def _add_camera(self, camera: CameraConfig) -> None:
        self._context.update_state(f"cameras.{camera.id}", _initial_camera_state(camera))
        driver = self._build_driver(camera)
        arbiter = MotionArbiter(
            driver,
            # A joystick thread may still hold a just-removed camera's arbiter.
            config=lambda: self._configs.get(camera.id, camera),
            motion=lambda: self._settings().motion,
            clock=self._context.clock,
            publish=self._camera_publisher(camera.id),
            logger=self._context.logger,
            on_activity=self._wake_motion,
        )
        with self._lock:
            self._connections[camera.id] = camera.connection_key()
            self._arbiters[camera.id] = arbiter
        self._context.update_state(
            f"cameras.{camera.id}.capabilities", driver.capabilities().as_state()
        )
        driver.connect()

    def _forget_state(self, subtree: str, item_id: str) -> None:
        """Drop a removed camera's or controller's entry from the state."""
        entries = self._context.state.get(self._context.name, subtree) or {}
        entries.pop(item_id, None)
        self._context.update_state(subtree, entries)

    def _build_driver(self, camera: CameraConfig) -> CameraDriver:
        host = self._driver_host(camera.id)
        if camera.driver == DriverKind.AGENT:
            return AgentProxyDriver(
                host,
                self._agents,
                camera.address.agent_id,
                camera.address.remote_camera_id,
                camera.direct_fallback,
                make_fallback=lambda fallback: self._visca_driver(
                    camera,
                    self._driver_host(camera.id, publishes=False),
                    fallback.address,
                    "fallback",
                ),
            )
        return self._visca_driver(camera, host, camera.address, "visca")

    def _visca_driver(
        self, camera: CameraConfig, host: DriverHost, address: CameraAddress, suffix: str
    ) -> ViscaIpDriver:
        def top_speed_steps() -> tuple[int, int]:
            limits = self._configs.get(camera.id, camera).limits
            return limits.pan_speed_max_step, limits.tilt_speed_max_step

        return ViscaIpDriver(
            host,
            address.host,
            address.port,
            resend_interval_seconds=lambda: self._settings().motion.resend_interval_ms / 1000,
            opener=self._datagram_opener,
            worker_suffix=suffix,
            top_speed_steps=top_speed_steps,
        )

    def _driver_host(self, camera_id: str, publishes: bool = True) -> DriverHost:
        """A fallback driver gets a host that publishes nothing: its agent driver
        reports the camera's state, whichever route is in use."""
        return DriverHost(
            camera_id=camera_id,
            clock=self._context.clock,
            logger=self._context.logger,
            publish=self._camera_publisher(camera_id) if publishes else lambda key, value: None,
            start_worker=self._context.start_worker,
            stop_worker=self._context.stop_worker,
        )

    def _camera_publisher(self, camera_id: str) -> Callable[[str, Any], None]:
        def publish(key: str, value: Any) -> None:
            # No lock (see `_apply`); a late update from a camera just removed is dropped.
            if camera_id in self._configs:
                self._context.update_state(f"cameras.{camera_id}.{key}", value)

        return publish

    def _publish_camera_details(self, camera: CameraConfig) -> None:
        """Fields that may change without a new driver (labels, presets, addresses)."""
        for key, value in _camera_details(camera).items():
            self._context.update_state(f"cameras.{camera.id}.{key}", value)

    def _apply_controllers(self, controllers: list[ControllerConfig]) -> None:
        wanted = {controller.id: controller for controller in controllers if controller.enabled}
        with self._lock:
            leaving = [
                controller_input
                for controller_id, controller_input in self._controllers.items()
                if wanted.get(controller_id) != controller_input.config
            ]
            for controller_input in leaving:
                del self._controllers[controller_input.config.id]
        for controller_input in leaving:
            self._context.stop_worker(controller_input.worker_name)
            if controller_input.config.id not in wanted:
                self._forget_state("controllers", controller_input.config.id)
        for controller_id, controller in wanted.items():
            with self._lock:
                if controller_id in self._controllers:
                    continue
            self._add_controller(controller)

    def _add_controller(self, controller: ControllerConfig) -> None:
        controller_input = PelcoDInput(
            controller,
            arbiter_for_address=self.arbiter_for_address,
            motion=lambda: self._settings().motion,
            publish=self._controller_publisher(controller.id),
            log_command=self._joystick_logger(controller.id),
            clock=self._context.clock,
            logger=self._context.logger,
            opener=self._serial_opener,
        )
        self._context.update_state(
            f"controllers.{controller.id}",
            {
                "port": controller.port,
                "baud": controller.baud,
                "status": "offline",
                "status_reason": "starting",
                "last_call": None,
            },
        )
        with self._lock:
            self._controllers[controller.id] = controller_input
        self._context.start_worker(controller_input.worker_spec())

    def _controller_publisher(self, controller_id: str) -> Callable[[str, Any], None]:
        return lambda key, value: self._context.update_state(
            f"controllers.{controller_id}.{key}", value
        )

    def _joystick_logger(self, controller_id: str) -> Callable[[str, dict[str, Any], str], None]:
        source = f"joystick:{controller_id}"

        def log(command: str, args: dict[str, Any], result: str) -> None:
            self._context.command_log.append(self._context.name, source, command, args, result)

        return log

    def arbiter_for_address(self, address: int) -> MotionArbiter | None:
        with self._lock:
            for camera_id, camera in self._configs.items():
                if camera.controller_address == address:
                    return self._arbiters.get(camera_id)
        return None

    def _settings(self) -> CamerasSettings:
        settings = self._context.current_settings()
        assert isinstance(settings, CamerasSettings)
        return settings


def _camera_details(camera: CameraConfig) -> dict[str, Any]:
    return {
        "label": camera.label or camera.id,
        "owner": camera.owner.value,
        "driver": camera.driver.value,
        "controller_address": camera.controller_address,
        "obs_source": camera.obs_source,
        "presets": [preset.model_dump() for preset in camera.presets],
    }


def _initial_camera_state(camera: CameraConfig) -> dict[str, Any]:
    """The contract's CameraState fields, before the driver has heard anything."""
    direct = camera.driver != DriverKind.AGENT
    return {
        **_camera_details(camera),
        "status": "offline",
        "status_reason": "starting",
        "route": "direct" if direct else "agent",
        "mode": "manual" if direct else None,
        "tracker": {"state": "idle", "target_id": None, "confidence": None},
        "position": None,
        "moving": False,
        "last_command_source": None,
        "last_command_ts": None,
        "last_error": "",
        "control": {
            "velocity_owner": None,
            "velocity": None,
            "latched": False,
            "last_source": None,
            "last_command": None,
            "last_command_ts": None,
        },
    }
