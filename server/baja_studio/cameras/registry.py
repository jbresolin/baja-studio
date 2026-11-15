"""The cameras module's live registry: a driver and arbiter per camera, a worker per controller.

`apply(settings)` makes what runs match the settings: a camera that is new
gets a driver (VISCA/IP or agent-proxied) and a `MotionArbiter`; one whose
connection (driver, address, fallback) changed gets a new driver; a removed
one is disconnected and leaves the state. Controllers likewise, by port and
baud. Everything else (limits, curves, labels, controller addresses) is read
on every command, so it needs nothing rebuilt. A controller whose port, baud
and protocol are unchanged keeps its worker (and open port) when only its Call
bindings change.

It also publishes `order` (the enabled cameras' ids in settings order: the
Cameras page's order and its Alt+1-9 keys) and `motion` (the motion
settings the page needs, e.g. the deadband for its on-screen joystick).
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
from baja_studio.cameras.crop_window import CropWindow
from baja_studio.cameras.driver import CameraDriver, DriverHost
from baja_studio.cameras.drivers.agent_gateway import AgentGateway
from baja_studio.cameras.drivers.agent_proxy import AgentProxyDriver
from baja_studio.cameras.drivers.fixed import FixedCameraDriver
from baja_studio.cameras.drivers.obs_crop import ObsCropDriver
from baja_studio.cameras.drivers.obs_gateway import NoObsGateway, ObsGateway
from baja_studio.cameras.drivers.uvc_ptz import UsbPtzAccess, UvcPtzDriver
from baja_studio.cameras.drivers.visca_ip import DatagramOpener, ViscaIpDriver
from baja_studio.cameras.inputs.pelco_d_worker import PelcoDInput, SerialOpener
from baja_studio.cameras.motion.arbiter import MotionArbiter
from baja_studio.core.module import ModuleContext
from baja_studio.imaging.config import StoredPosition


class CameraRegistry:
    """The running cameras and controllers of one start of the cameras module."""

    def __init__(
        self,
        context: ModuleContext,
        agents: AgentGateway,
        datagram_opener: DatagramOpener,
        serial_opener: SerialOpener,
        wake_motion: Callable[[], None],
        usb: UsbPtzAccess | None = None,
        save_position: Callable[[str, StoredPosition], None] | None = None,
        obs: ObsGateway | None = None,
        save_crop_window: Callable[[str, int, CropWindow], None] | None = None,
    ) -> None:
        """Nothing runs until `apply` is called (by the module's motion worker).

        `usb` (the imaging service) reaches USB cameras for the `uvc` and
        `none` drivers; `save_position(camera id, position)` saves a `uvc`
        camera's preset in its settings. `obs` is playout's OBS connection, for
        `obs_crop` cameras; `save_crop_window(camera id, n, window)` saves an
        `obs_crop` camera's preset.
        """
        self._context = context
        self._usb = usb
        self._save_position = save_position
        self._obs = obs or NoObsGateway()
        self._save_crop_window = save_crop_window
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
            self._context.update_state("order", list(wanted))
            self._context.update_state("motion", settings.motion.model_dump(mode="json"))
        self._wake_motion()

    def controller(self, controller_id: str) -> PelcoDInput | None:
        """The running input of an enabled controller."""
        with self._lock:
            return self._controllers.get(controller_id)

    def arbiter_for_camera(self, camera_id: str) -> MotionArbiter | None:
        """The arbiter of an enabled camera."""
        with self._lock:
            return self._arbiters.get(camera_id)

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
        if camera.driver == DriverKind.OBS_CROP:
            save_window = self._save_crop_window
            assert save_window is not None, "the cameras module always passes it"
            return ObsCropDriver(
                host,
                self._obs,
                config=lambda: self._configs.get(camera.id, camera),
                save_window=lambda n, window: save_window(camera.id, n, window),
            )
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
        if camera.driver in (DriverKind.UVC, DriverKind.NONE):
            return self._usb_driver(camera, host)
        return self._visca_driver(camera, host, camera.address, "visca")

    def _usb_driver(self, camera: CameraConfig, host: DriverHost) -> CameraDriver:
        """WP6a: the Pixy (`uvc`), or a camera without motion (`none`)."""
        usb, save = self._usb, self._save_position
        if camera.driver == DriverKind.NONE:
            if usb is None or camera.usb_device is None:
                return FixedCameraDriver(host, None)
            return FixedCameraDriver(host, lambda: usb.locate_camera(camera.id))
        assert usb is not None and save is not None, "the cameras module always passes both"
        return UvcPtzDriver(
            host,
            usb,
            stored_positions=lambda: self._configs.get(camera.id, camera).stored_positions,
            save_position=lambda position: save(camera.id, position),
        )

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
            position_poll_seconds=lambda: (
                self._configs.get(camera.id, camera).position_poll_seconds
            ),
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
            leaving = []
            for controller_id, controller_input in self._controllers.items():
                new = wanted.get(controller_id)
                if new is None or _port_key(new) != _port_key(controller_input.config):
                    leaving.append(controller_input)
                else:
                    controller_input.config = new  # Call bindings are read on every press
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
            arbiter_for_camera=self.arbiter_for_camera,
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
                "selected_camera": None,
                "recent_frames": [],
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


def _port_key(controller: ControllerConfig) -> tuple[object, ...]:
    """What a controller's worker opens; a change here means a new worker."""
    return (controller.port, controller.baud, controller.protocol)


def _camera_details(camera: CameraConfig) -> dict[str, Any]:
    details: dict[str, Any] = {
        "label": camera.label or camera.id,
        "owner": camera.owner.value,
        "driver": camera.driver.value,
        "controller_address": camera.controller_address,
        "obs_source": camera.obs_source,
        "usb_device": camera.usb_device.model_dump() if camera.usb_device else None,
    }
    # An agent camera's presets are the agent's (its driver mirrors them from
    # /agent/info); a direct camera's names live in the registry.
    if camera.driver != DriverKind.AGENT:
        details["presets"] = [preset.model_dump() for preset in camera.presets]
    return details


def _initial_camera_state(camera: CameraConfig) -> dict[str, Any]:
    """The contract's CameraState fields, before the driver has heard anything."""
    direct = camera.driver != DriverKind.AGENT
    return {
        "presets": [],
        **_camera_details(camera),
        "status": "offline",
        "status_reason": "starting",
        "route": "direct" if direct else "agent",
        "mode": "manual" if direct else None,
        "tracker": {"state": "idle", "target_id": None, "confidence": None},
        "position": None,
        # True for a camera that cannot read its position back (the Pixy's
        # `uvc` driver): `position` is then where it was last told to go.
        "position_is_commanded": False,
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
