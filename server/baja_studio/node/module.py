"""The camera node's one module: its cameras, their image controls, and the agent API (ADR 0023).

The node is the studio's service with a single module (`app.py`), so it
reuses the studio's settings store (secrets masked), supervisor, logging,
state and command log as they are. This module:

- runs the UVC child (`uvc` worker, `imaging/uvc_link.py`) for every
  camera's image controls, in both video modes;
- keeps the served cameras matching the settings (`apply` worker, woken by
  every settings change; `cameras.py`), so a camera's video mode or video
  settings change without restarting the node;
- pushes the agent state four times a second (`push` worker, `agent.py`) and
  samples the laptop's CPU for the page (`system` worker);
- serves the agent contract at the root (`agent_routes.py`);
- answers the page's commands: the local OBS's sources, Test connection,
  Create filter (the studio's own `ObsCameraCommands`), and the capture
  devices and their modes for `node` mode.

A failing camera never takes the API down: drivers run in their own workers,
the supervisor restarts what raises, and the routes only read what the
drivers published.
"""

import threading
from typing import Any

from fastapi import APIRouter

from baja_studio.cameras.config import CameraConfig, CamerasSettings, DriverKind
from baja_studio.cameras.drivers.obs_gateway import NoObsGateway
from baja_studio.cameras.remote_obs import RemoteObsPool
from baja_studio.core.module import CommandError, Module, ModuleContext
from baja_studio.core.supervisor import WorkerContext, WorkerSpec
from baja_studio.core.system_probes import read_system_load
from baja_studio.imaging.usb_devices import UsbDevice
from baja_studio.imaging.uvc_link import UvcChildLink
from baja_studio.modules.cameras import UVC_CHILD_MODULE
from baja_studio.modules.cameras.obs_commands import (
    OBS_COMMANDS,
    UNLOGGED_OBS_COMMANDS,
    ObsCameraCommands,
)
from baja_studio.node import backends
from baja_studio.node.agent import PUSH_INTERVAL_SECONDS, NodeAgent
from baja_studio.node.agent_routes import agent_routes
from baja_studio.node.cameras import NodeCameras
from baja_studio.node.image_controls import NodeImageControls
from baja_studio.node.parts import NodeParts
from baja_studio.node.settings import LOCAL_OBS_HOST, NodeSettings, VideoMode

APPLY_WORKER, PUSH_WORKER, SYSTEM_WORKER = "apply", "push", "system"
# The apply worker also looks again this often, in case a wake was missed.
APPLY_INTERVAL_SECONDS = 5.0
SYSTEM_INTERVAL_SECONDS = 2.0
# The page's per-camera details go out once a second (every fourth push).
DETAILS_EVERY_PUSHES = 4
DEVICE_COMMANDS = frozenset({"list_capture_devices", "capture_modes", "refresh_usb_devices"})


class NodeModule(Module):
    """The camera node: cameras, image controls, agent API."""

    name = "node"
    title = "Camera node"
    SettingsModel = NodeSettings
    can_be_disabled = False

    def __init__(self, parts: NodeParts) -> None:
        """`parts` are what the node is made of where it runs: the video pipeline, the
        capture device list, the UVC child and OBS's transport (tests pass fakes)."""
        super().__init__()
        self._parts = parts
        self._lock = threading.Lock()
        self._subscribed = False
        self.agent: NodeAgent | None = None
        self.cameras: NodeCameras | None = None
        self.image: NodeImageControls | None = None
        self.pool: RemoteObsPool | None = None
        self._uvc: UvcChildLink | None = None
        self._obs_commands: ObsCameraCommands | None = None
        self._apply_worker: WorkerContext | None = None

    def initial_state(self) -> dict[str, Any]:
        """No cameras until the apply worker has run."""
        return {
            "cameras": {},
            "order": [],
            "usb": {"status": "starting", "message": "", "devices": []},
            "system": {"cpu_percent": None, "memory_percent": None},
        }

    def start(self, context: ModuleContext) -> None:
        """Build the node's parts for this start; workers start right after."""
        super().start(context)
        parts = self._parts
        uvc = UvcChildLink(
            lambda on_event: parts.uvc_child(context, UVC_CHILD_MODULE, on_event),
            context.logger,
            on_started=self._usb_changed,
            on_devices=lambda devices, appeared, gone: self._usb_changed(appeared),
        )
        image = NodeImageControls(uvc, lambda: self._settings().served())
        pool = RemoteObsPool.for_module(
            context, NoObsGateway("a camera node has no OBS of its own"), parts.obs_transport
        )
        cameras = NodeCameras(
            context,
            pool,
            lambda spec: parts.pipelines(spec, context.clock, context.logger),
            parts.ffmpeg_path,
            image.names,
        )
        agent = NodeAgent(context.clock, self._settings, cameras.cameras)
        with self._lock:
            self._uvc, self.image, self.pool, self.cameras, self.agent = (
                uvc,
                image,
                pool,
                cameras,
                agent,
            )
            self._obs_commands = ObsCameraCommands(
                pool, self._obs_cameras_settings, context.clock, parts.obs_transport
            )
        if not self._subscribed:
            self._subscribed = True
            context.settings.subscribe(self.name, lambda values: self._wake_apply())

    def stop(self) -> None:
        """Workers have stopped: stop every camera's backend and close the WebSockets."""
        with self._lock:
            cameras, agent = self.cameras, self.agent
            self.cameras, self.agent, self.image, self._uvc = None, None, None, None
        if cameras is not None:
            cameras.stop_all()
        if agent is not None:
            agent.hub.close_all_from_thread()

    def workers(self) -> list[WorkerSpec]:
        """The UVC child, applying settings, the state pushes, and the CPU sample."""
        assert self._uvc is not None
        return [
            self._uvc.worker_spec(),
            WorkerSpec(APPLY_WORKER, self._run_apply),
            WorkerSpec(PUSH_WORKER, self._run_push),
            WorkerSpec(SYSTEM_WORKER, self._run_system),
        ]

    def root_routes(self) -> APIRouter:
        """The agent contract, at the root (its paths are fixed by its clients)."""
        return agent_routes(
            lambda: self.agent,
            lambda: self.image,
            lambda camera_id: None if self.cameras is None else self.cameras.settings_of(camera_id),
        )

    # --- commands from the node's page --------------------------------------------------------

    def handle_command(self, command: str, args: dict[str, Any], source: str) -> Any:
        """OBS helpers for a camera in OBS mode, and the capture devices for node mode."""
        if command in OBS_COMMANDS:
            return self._obs_command(command, args)
        if command == "list_capture_devices":
            devices = self._parts.capture_devices.list_capture_devices()
            return {"devices": [{"name": d.name, "device": d.device} for d in devices]}
        if command == "capture_modes":
            device = str(args.get("device") or "")
            modes = self._parts.capture_devices.capture_modes(device)
            return {
                "modes": [
                    {"width": m.width, "height": m.height, "fps": m.fps, "codec": m.codec}
                    for m in modes
                ]
            }
        if command == "refresh_usb_devices":
            uvc = self._uvc
            if uvc is None:
                raise CommandError("the node is starting")
            uvc.refresh_devices()
            return {"devices": [device.as_json() for device in uvc.devices()]}
        raise CommandError(
            f"the node has no command {command!r}; try list_obs_sources, test_obs_connection, "
            "create_crop_filter, list_capture_devices, capture_modes, refresh_usb_devices"
        )

    def wants_logged(
        self, command: str, args: dict[str, Any], source: str, result: Any, failed: bool
    ) -> bool:
        """Reads and commands carrying a password stay out of the command log."""
        return command not in UNLOGGED_OBS_COMMANDS and command not in DEVICE_COMMANDS

    def _obs_command(self, command: str, args: dict[str, Any]) -> Any:
        commands = self._obs_commands
        if commands is None:
            raise CommandError("the node is starting")
        camera_id = str(args.get("camera") or "")
        camera = next((c for c in self._settings().cameras if c.id == camera_id), None)
        if camera is None:
            raise CommandError(f"no camera called {camera_id!r} (save it first)")
        if command == "create_crop_filter" and not camera.obs.source.strip():
            raise CommandError("choose the camera's OBS source first, and save")
        forwarded = dict(args)
        obs = args.get("obs")
        if isinstance(obs, dict):
            # The page sends the node's form (port, password); the studio's helper wants an
            # `obs_crop` camera's connection: a remote OBS, here always this laptop's.
            forwarded["obs"] = {
                "remote": True,
                "host": LOCAL_OBS_HOST,
                "port": obs.get("port", camera.obs.port),
                "password": obs.get("password", ""),
            }
        return commands.handle(command, forwarded)

    def _obs_cameras_settings(self) -> CamerasSettings:
        """The node's OBS-mode cameras as the studio's camera list (for `ObsCameraCommands`).

        A camera without a source yet is listed only for its OBS password.
        """
        cameras: list[CameraConfig] = []
        for camera in self._settings().cameras:
            if camera.mode != VideoMode.OBS:
                continue
            config = backends.obs_camera_config(camera)
            if config is None:
                crop = backends.crop_settings(camera)
                config = CameraConfig(id=camera.id, driver=DriverKind.NONE, crop=crop)
            cameras.append(config)
        return CamerasSettings(cameras=cameras)

    # --- workers ------------------------------------------------------------------------------

    def _settings(self) -> NodeSettings:
        settings = self.context.current_settings()
        assert isinstance(settings, NodeSettings)
        return settings

    def _wake_apply(self) -> None:
        worker = self._apply_worker
        if worker is not None:
            worker.wake()

    def _run_apply(self, worker: WorkerContext) -> None:
        self._apply_worker = worker
        try:
            while not worker.should_stop:
                cameras = self.cameras
                if cameras is not None:
                    cameras.apply(self._settings())
                worker.sleep(APPLY_INTERVAL_SECONDS)
        finally:
            self._apply_worker = None

    def _run_push(self, worker: WorkerContext) -> None:
        cycle = 0
        while not worker.should_stop:
            agent, cameras = self.agent, self.cameras
            if agent is not None and cameras is not None:
                agent.push()
                if cycle % DETAILS_EVERY_PUSHES == 0:
                    self._publish_details(cameras)
            cycle += 1
            worker.sleep(PUSH_INTERVAL_SECONDS)

    def _publish_details(self, cameras: NodeCameras) -> None:
        """For the page, once a second: each camera's backend details and image controls."""
        image = self.image
        for camera in cameras.cameras():
            self.context.update_state(f"cameras.{camera.id}.details", cameras.details(camera.id))
            names = image.names(camera.id) if image is not None else []
            self.context.update_state(f"cameras.{camera.id}.image_controls", names)

    def _run_system(self, worker: WorkerContext) -> None:
        while not worker.should_stop:
            load = read_system_load(worker.clock)
            self.context.update_state(
                "system", {"cpu_percent": load.cpu_percent, "memory_percent": load.memory_percent}
            )
            worker.sleep(SYSTEM_INTERVAL_SECONDS)

    def _usb_changed(self, appeared: set[str] | None = None) -> None:
        """The USB cameras changed (or the UVC child started): tell the page, reapply settings."""
        uvc, image = self._uvc, self.image
        if uvc is None:
            return
        status, message = uvc.status()
        devices: list[UsbDevice] = uvc.devices()
        self.context.update_state(
            "usb",
            {"status": status, "message": message, "devices": [d.as_json() for d in devices]},
        )
        if appeared and image is not None:
            image.device_returned(appeared)
