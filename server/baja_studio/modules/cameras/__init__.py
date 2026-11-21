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

OBS crop cameras (WP6b) reach playout's OBS through playout's connection,
or a trackside laptop's OBS through a connection this module opens and
shares per laptop (`cameras/remote_obs.py`, WP13, ADR 0021).

Image settings (WP6a, `baja_studio/imaging/`) live here too, because they
share the registry: the `uvc` worker runs the UVC child (USB cameras), the
`imaging` worker reapplies saved settings, and `state.cameras.image.<id>`
and `state.cameras.usb` show them. Commands: `image_commands.py`.
"""

import threading
from collections.abc import Callable
from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel

from baja_studio.adapters.obs_transport import TransportFactory, open_websocket_transport
from baja_studio.cameras import health
from baja_studio.cameras.agent_previews import AgentSnapshots
from baja_studio.cameras.config import CamerasSettings
from baja_studio.cameras.crop_config import with_crop_window
from baja_studio.cameras.crop_window import CropWindow
from baja_studio.cameras.drivers.agent_gateway import AgentGateway
from baja_studio.cameras.drivers.obs_gateway import NoObsGateway, ObsGateway
from baja_studio.cameras.drivers.visca_ip import DatagramOpener, UdpTransport
from baja_studio.cameras.inputs.pelco_d_worker import SerialOpener, open_serial_port
from baja_studio.cameras.inputs.serial_ports import SerialPortLister, list_serial_ports
from baja_studio.cameras.previews import CameraPreviews
from baja_studio.cameras.readiness import camera_checks
from baja_studio.cameras.registry import CameraRegistry
from baja_studio.cameras.remote_obs import RemoteObsPool
from baja_studio.core.childproc import EventCallback
from baja_studio.core.module import Module, ModuleContext
from baja_studio.core.readiness import ReadinessCheck
from baja_studio.core.supervisor import Health, WorkerContext, WorkerSpec
from baja_studio.imaging.config import StoredPosition
from baja_studio.imaging.service import CameraEdit, ImagingHost, ImagingService
from baja_studio.imaging.uvc_link import ChildHost, UvcChildLink
from baja_studio.imaging.watch import ImagingWatch
from baja_studio.modules.cameras.commands import CameraCommands
from baja_studio.modules.cameras.d200_preset_route import d200_preset_routes
from baja_studio.modules.cameras.image_commands import IMAGE_COMMANDS, ImageCommands
from baja_studio.modules.cameras.obs_commands import (
    OBS_COMMANDS,
    UNLOGGED_OBS_COMMANDS,
    ObsCameraCommands,
)
from baja_studio.modules.cameras.preview_routes import camera_routes

UVC_CHILD_MODULE = "baja_studio.childprocs.uvc_child"
UvcChildFactory = Callable[[ModuleContext, EventCallback], ChildHost]


def real_uvc_child(context: ModuleContext, on_event: EventCallback) -> ChildHost:
    """The UVC child process (duvc-ctl, or its fake with BAJA_STUDIO_UVC_BACKEND=fake)."""
    return context.new_child_process(UVC_CHILD_MODULE, on_event)


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
        uvc_child_factory: UvcChildFactory = real_uvc_child,
        obs: ObsGateway | None = None,
        remote_obs_transport: TransportFactory = open_websocket_transport,
    ) -> None:
        """`agents` is the agents module and `obs` playout's OBS connection (crop
        cameras, previews); `remote_obs_transport` opens connections to remote
        OBSes (ADR 0021). Tests pass fake openers for UDP, serial and OBS, a
        fake list of serial ports, and an in-process UVC child."""
        super().__init__()
        self._uvc_child_factory = uvc_child_factory
        self._imaging: ImagingService | None = None
        self._image_commands: ImageCommands | None = None
        self._imaging_watch: ImagingWatch | None = None
        self._uvc: UvcChildLink | None = None
        self._agents = agents
        self._obs = obs or NoObsGateway()
        self._remote_obs_transport = remote_obs_transport
        self._obs_pool: RemoteObsPool | None = None
        self._datagram_opener = datagram_opener
        self._serial_opener = serial_opener
        self._lock = threading.Lock()
        self._registry: CameraRegistry | None = None
        self._running = False
        self._subscribed = False
        self._commands = CameraCommands(
            self._current_registry, self._settings, self.edit_camera, list_ports
        )
        self._obs_commands: ObsCameraCommands | None = None
        self._previews: CameraPreviews | None = None

    def initial_state(self) -> dict[str, Any]:
        """No cameras or controllers until the motion worker applies the settings."""
        return {
            "cameras": {},
            "controllers": {},
            "order": [],
            "motion": {},
            "image": {},
            "usb": {"status": "starting", "message": "", "devices": []},
        }

    def start(self, context: ModuleContext) -> None:
        """Follow settings edits and summarise health per device."""
        super().start(context)
        imaging = self._start_imaging(context)
        pool = RemoteObsPool.for_module(context, self._obs, self._remote_obs_transport)
        with self._lock:
            self._obs_pool = pool
            self._registry = CameraRegistry(
                context,
                self._agents,
                self._datagram_opener,
                self._serial_opener,
                self._wake_motion,
                usb=imaging,
                save_position=self._save_position,
                obs_pool=pool,
                save_crop_window=self._save_crop_window,
            )
            self._previews = CameraPreviews(
                context.clock,
                self._obs,
                self._settings,
                lambda camera_id: context.state.get(self.name, f"cameras.{camera_id}"),
                gateway_for=pool.gateway_for,
                agent_snapshots=AgentSnapshots(context.clock, self._agents),
            )
            self._obs_commands = ObsCameraCommands(
                pool, self._settings, context.clock, self._remote_obs_transport
            )
        context.supervisor.set_health_summary(self.name, self._summarise_health)
        if not self._subscribed:
            self._subscribed = True
            context.settings.subscribe(self.name, self._apply_new_settings)

    def stop(self) -> None:
        """Workers are already stopped; forget the drivers so a restart builds fresh ones."""
        with self._lock:
            self._running, self._registry, self._previews = False, None, None
            self._obs_pool = None  # its connections' workers stopped with the module's
        if self._imaging is not None:
            self._imaging.flush_due(everything=True)  # the last slider drag, not saved yet

    @property
    def obs_pool(self) -> RemoteObsPool | None:
        """While running: which OBS each crop camera uses, and the remote connections."""
        with self._lock:
            return self._obs_pool

    def workers(self) -> list[WorkerSpec]:
        """The motion loop (it also connects the configured devices when it starts),
        the UVC child and the imaging worker."""
        assert self._uvc is not None and self._imaging_watch is not None
        return [
            WorkerSpec(health.MOTION_WORKER, self._run_motion),
            self._uvc.worker_spec(),
            self._imaging_watch.worker_spec(),
        ]

    def routes(self) -> APIRouter:
        """Camera previews (`preview_routes.py`)."""
        return camera_routes(lambda: self._previews)

    def root_routes(self) -> APIRouter:
        """`/action/preset/{camera}/{number}`: a preset from a URL, for the D200."""
        return d200_preset_routes()

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
        """See `commands.py` (motion, presets, modes, serial ports, raw frames),
        `obs_commands.py` (OBS sources, creating a crop filter) and
        `image_commands.py` (image settings and USB devices)."""
        if command in OBS_COMMANDS:
            assert self._obs_commands is not None, "commands only reach a running module"
            return self._obs_commands.handle(command, args)
        if command in IMAGE_COMMANDS:
            assert self._image_commands is not None
            return self._image_commands.handle(command, args, source)
        result = self._commands.handle(command, args, source)
        self._wake_motion()
        return result

    def wants_logged(
        self, command: str, args: dict[str, Any], source: str, result: Any, failed: bool
    ) -> bool:
        """Keep the start of a browser's move stream, not each of its ten-a-second repeats
        (and likewise the start of a slider drag's image writes)."""
        if command in UNLOGGED_OBS_COMMANDS:
            return False
        if command in IMAGE_COMMANDS:
            assert self._image_commands is not None
            return self._image_commands.wants_logged(command, args, source)
        return self._commands.wants_logged(command, args, source, result, failed)

    def _current_registry(self) -> CameraRegistry | None:
        with self._lock:
            return self._registry

    def edit_camera(self, camera_id: str, edit: CameraEdit) -> None:
        """Save an edited copy of one camera's settings entry, atomically.

        `edit(entry) -> entry` gets the camera's entry (JSON form) as it is
        *now*, under the settings store's lock, so two writers (a preset
        rename, image settings, a stored position, a crop preset) never undo
        each other. Every writer inside the service goes through here.
        """

        def change(values: dict[str, Any]) -> dict[str, Any]:
            cameras = values["cameras"]
            return {"cameras": [edit(c) if c["id"] == camera_id else c for c in cameras]}

        self.context.settings.edit(self.name, change)

    # --- image settings and USB cameras (WP6a) ------------------------------------------

    def _start_imaging(self, context: ModuleContext) -> ImagingService:
        """Build the UVC link, the imaging service and its worker for this start."""
        host = ImagingHost(
            publish=context.update_state,
            read_state=lambda path: context.state.get(self.name, path),
            settings=self._settings,
            edit_camera=self.edit_camera,
            logger=context.logger,
            monotonic=context.clock.monotonic,
        )
        watch_holder: list[ImagingWatch] = []
        uvc = UvcChildLink(
            lambda on_event: self._uvc_child_factory(context, on_event),
            context.logger,
            on_started=lambda: watch_holder[0].uvc_started(),
            on_devices=lambda *changes: watch_holder[0].usb_devices_changed(*changes),
            on_reopened=lambda path: watch_holder[0].device_reopened(path),
        )
        imaging = ImagingService(host, uvc, self._agents)
        watch = ImagingWatch(imaging)
        watch_holder.append(watch)
        self._uvc, self._imaging, self._imaging_watch = uvc, imaging, watch
        self._image_commands = ImageCommands(imaging, watch, context.clock)
        return imaging

    def _summarise_health(self, healths: dict[str, Health]) -> Health:
        """Per device, the UVC child included while some enabled camera uses USB."""
        usb_in_use = any(
            camera.enabled and camera.usb_device is not None for camera in self._settings().cameras
        )
        return health.summarise(healths, usb_in_use=usb_in_use)

    def _save_crop_window(self, camera_id: str, n: int, window: CropWindow) -> None:
        """An `obs_crop` camera's preset `n`: its window, kept in its settings (WP6b)."""
        self.edit_camera(camera_id, lambda entry: with_crop_window(entry, n, window))

    def _save_position(self, camera_id: str, position: StoredPosition) -> None:
        """A `uvc` camera's preset, kept in its settings (the Pixy's own slots lose zoom)."""

        def edit(entry: dict[str, Any]) -> dict[str, Any]:
            kept = [p for p in entry.get("stored_positions", []) if p["n"] != position.n]
            stored = sorted([*kept, position.model_dump()], key=lambda p: p["n"])
            return {**entry, "stored_positions": stored}

        self.edit_camera(camera_id, edit)
