"""The cameras and playout modules together with fake OBSes, for OBS crop camera tests.

Loaded into every test through `pytest_plugins` in conftest.py. The
`crop_studio` fixture runs the service with the cameras, playout and agents
modules on the fake clock, wired as `main.py` wires them: the cameras module
reaches the OBS on this PC through playout's one connection. That OBS is a
`FakeObs` (the playout scenes plus a 4K webcam called "Cam 4K") on the
in-process network, so no port is opened.

A second fake OBS stands for a trackside laptop's (WP13, ADR 0021): at
"laptop.test":4455, with its own password and two 4K webcams ("Laptop cam
A" and "Laptop cam B"), each with its Crop/Pad filter. The cameras module
reaches it over the same in-process network, through its own connection.
"""

from collections.abc import Callable, Iterator
from dataclasses import dataclass
from typing import Any

import pytest
from fastapi.testclient import TestClient

from baja_studio.cameras.config import CameraConfig, CamerasSettings
from baja_studio.core.services import StudioServices
from baja_studio.fakes.fake_obs import FakeObs
from baja_studio.fakes.fake_obs_in_process import InProcessObsNetwork
from baja_studio.fakes.in_process import InProcessTransport
from baja_studio.modules.agents import AgentsModule
from baja_studio.modules.cameras import CamerasModule
from baja_studio.modules.playout import PlayoutModule
from tests.conftest import AppFactory, FakeClock, eventually, module_worker_threads
from tests.imaging.uvc_harness import FakeUsb

OBS_HOST, OBS_PORT = "obs.test", 4455
# Playout never connects without a password, so the fake OBS asks for one.
OBS_PASSWORD = "obs-secret"
WEBCAM = "Cam 4K"
CROP_FILTER = "Baja crop"
LAPTOP_HOST, LAPTOP_PORT = "laptop.test", 4455
LAPTOP_PASSWORD = "laptop-secret"
LAPTOP_CAM_A, LAPTOP_CAM_B = "Laptop cam A", "Laptop cam B"
STEP_SECONDS = 0.1
CAMERA_WORKERS = module_worker_threads("cameras", skip=("controller:",))
PLAYOUT_WORKERS = module_worker_threads("playout")


@dataclass
class CropStudio:
    """The running service, its HTTP client and the fake OBSes (this PC's and a laptop's)."""

    services: StudioServices
    http: TestClient
    obs: FakeObs
    network: InProcessObsNetwork
    clock: FakeClock
    playout: PlayoutModule
    laptop: FakeObs
    cameras: CamerasModule

    def command(self, command: str, source: str = "web:alice", **args: Any) -> Any:
        """Send a cameras command, to the camera "crop" unless another one is named."""
        args.setdefault("camera", "crop")
        return self.services.registry.dispatch_command("cameras", command, args, source)

    def camera(self, camera_id: str = "crop") -> Any:
        return self.services.state.get("cameras", f"cameras.{camera_id}") or {}

    def saved_cameras(self) -> list[CameraConfig]:
        """The cameras as saved in the cameras module's settings."""
        settings = self.services.settings.get("cameras")
        assert isinstance(settings, CamerasSettings)
        return settings.cameras

    def saved_camera(self) -> CameraConfig:
        """The first camera as saved in the cameras module's settings."""
        return self.saved_cameras()[0]

    def crop_settings(self) -> dict[str, Any]:
        """The Crop/Pad filter's settings inside the fake OBS."""
        return self.obs.filter_settings(WEBCAM, CROP_FILTER)

    def screenshots(self, width: int | None = 320) -> int:
        """How many screenshots of the webcam OBS took at that width (None: full size)."""
        return sum(
            1
            for record in self.obs.requests_of("GetSourceScreenshot")
            if record.data.get("sourceName") == WEBCAM and record.data.get("imageWidth") == width
        )

    def connect_obs(self) -> None:
        """Fill in playout's OBS settings and wait for the connection."""
        obs = {"host": OBS_HOST, "port": OBS_PORT, "password": OBS_PASSWORD}
        self.services.settings.update("playout", {"obs": obs})
        self.soon(lambda: self.services.state.get("playout", "obs.connection") == "connected")

    def add_crop_camera(self, **overrides: Any) -> None:
        camera = {"id": "crop", "driver": "obs_crop", "obs_source": WEBCAM, **overrides}
        self.services.settings.update("cameras", {"cameras": [camera]})

    def save_cameras(self, *cameras: dict[str, Any]) -> None:
        """Save exactly these cameras, as Camera setup's Save does."""
        self.services.settings.update("cameras", {"cameras": list(cameras)})

    def laptop_crop(self, source: str) -> dict[str, Any]:
        """The Crop/Pad filter's settings on one of the laptop's webcams."""
        return self.laptop.filter_settings(source, CROP_FILTER)

    def remote_connections(self) -> list[Any]:
        """The cameras module's open connections to remote OBSes."""
        pool = self.cameras.obs_pool
        return pool.open_connections() if pool is not None else []

    def soon(self, condition: Callable[[], bool], within_seconds: float = 5.0) -> None:
        """Let fake time pass in 0.1 s steps until `condition()` holds."""
        for _ in range(round(within_seconds / STEP_SECONDS)):
            self.settle()
            if condition():
                return
            self.clock.advance(STEP_SECONDS)
        self.settle()
        eventually(condition)

    def pass_time(self, seconds: float) -> None:
        for _ in range(round(seconds / STEP_SECONDS)):
            self.settle()
            self.clock.advance(STEP_SECONDS)
        self.settle()

    def settle(self, wait_for_remote_answers: bool = True) -> None:
        """Every worker asleep on the fake clock and no OBS request waiting for its answer.

        `wait_for_remote_answers` False: a test that holds a remote OBS's
        answers lets time pass while its requests are still out.
        """
        client = self.playout.obs_client

        def remote_idle() -> bool:
            if not wait_for_remote_answers:
                return True
            return all(c.obs_client.requests_in_flight == 0 for c in self.remote_connections())

        eventually(
            lambda: (
                self.clock.all_asleep_or_gone(CAMERA_WORKERS() | PLAYOUT_WORKERS())
                and client.requests_in_flight == 0
                and remote_idle()
                and not self._wake_pending(skip_remote=not wait_for_remote_answers)
            ),
            "a worker never went back to sleep, or OBS never answered it",
        )

    def _wake_pending(self, skip_remote: bool) -> bool:
        """True while a worker was woken (a command) but has not noticed yet.

        A sleeping thread notices a wake-up only a few milliseconds later and
        counts as asleep until then; moving time meanwhile would let the
        command wait a whole sleep. `skip_remote`: leave out the workers of
        remote OBS cameras and connections (stuck waiting for held answers,
        they notice a wake-up only once their request times out).
        """
        running = self.services.supervisor._workers
        workers = [*running.get("cameras", {}).items(), *running.get("playout", {}).items()]
        return any(
            worker.context._wake_requested.is_set()
            for name, worker in workers
            if not (skip_remote and self._is_remote_worker(name))
        )

    def _is_remote_worker(self, worker_name: str) -> bool:
        if worker_name.startswith("remote-obs:"):
            return True
        parts = worker_name.split(":")
        if len(parts) == 3 and parts[0] == "camera":
            return bool((self.camera(parts[1]).get("obs") or {}).get("remote"))
        return False


@pytest.fixture
def crop_studio(
    make_app: AppFactory, transport: InProcessTransport, fake_clock: FakeClock
) -> Iterator[CropStudio]:
    network = InProcessObsNetwork()
    obs = FakeObs.with_playout_scenes(fake_clock, password=OBS_PASSWORD)
    obs.add_video_input(WEBCAM, 3840, 2160)
    network.plug(OBS_HOST, OBS_PORT, obs)
    laptop = FakeObs(fake_clock, password=LAPTOP_PASSWORD)
    for webcam in (LAPTOP_CAM_A, LAPTOP_CAM_B):
        laptop.add_video_input(webcam, 3840, 2160)
        laptop.add_crop_filter(webcam, CROP_FILTER)
    network.plug(LAPTOP_HOST, LAPTOP_PORT, laptop)
    playouts: list[PlayoutModule] = []
    camera_modules: list[CamerasModule] = []
    usb = FakeUsb()  # WP6a's UVC child, in-process (no USB cameras are used here)

    def modules(registry: Any) -> list[Any]:
        agents = AgentsModule(transport, studio_id="test-studio")
        playout = PlayoutModule(obs_transport_factory=network.connect)
        playouts.append(playout)
        cameras = CamerasModule(
            agents,
            obs=playout.obs_gateway,
            uvc_child_factory=usb.new_host,
            remote_obs_transport=network.connect,
        )
        camera_modules.append(cameras)
        return [cameras, playout, agents]

    app = make_app(modules)
    with TestClient(app) as http:
        yield CropStudio(
            app.state.services,
            http,
            obs,
            network,
            fake_clock,
            playouts[0],
            laptop,
            camera_modules[0],
        )
