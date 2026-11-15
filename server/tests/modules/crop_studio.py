"""The cameras and playout modules together with a fake OBS, for OBS crop camera tests (WP6b).

Loaded into every test through `pytest_plugins` in conftest.py. The
`crop_studio` fixture runs the service with the cameras, playout and agents
modules on the fake clock, wired as `main.py` wires them: the cameras module
reaches OBS through playout's one connection. OBS is a `FakeObs` (the
playout scenes plus a 4K webcam called "Cam 4K") on the in-process network,
so no port is opened.
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
STEP_SECONDS = 0.1
CAMERA_WORKERS = module_worker_threads("cameras", skip=("controller:",))
PLAYOUT_WORKERS = module_worker_threads("playout")


@dataclass
class CropStudio:
    """The running service, its HTTP client and the fake OBS."""

    services: StudioServices
    http: TestClient
    obs: FakeObs
    network: InProcessObsNetwork
    clock: FakeClock
    playout: PlayoutModule

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

    def settle(self) -> None:
        """Every worker asleep on the fake clock and no OBS request waiting for its answer."""
        client = self.playout.obs_client
        eventually(
            lambda: (
                self.clock.all_asleep_or_gone(CAMERA_WORKERS() | PLAYOUT_WORKERS())
                and client.requests_in_flight == 0
            ),
            "a worker never went back to sleep, or OBS never answered it",
        )


@pytest.fixture
def crop_studio(
    make_app: AppFactory, transport: InProcessTransport, fake_clock: FakeClock
) -> Iterator[CropStudio]:
    network = InProcessObsNetwork()
    obs = FakeObs.with_playout_scenes(fake_clock, password=OBS_PASSWORD)
    obs.add_video_input(WEBCAM, 3840, 2160)
    network.plug(OBS_HOST, OBS_PORT, obs)
    playouts: list[PlayoutModule] = []
    usb = FakeUsb()  # WP6a's UVC child, in-process (no USB cameras are used here)

    def modules(registry: Any) -> list[Any]:
        agents = AgentsModule(transport, studio_id="test-studio")
        playout = PlayoutModule(obs_transport_factory=network.connect)
        playouts.append(playout)
        cameras = CamerasModule(agents, obs=playout.obs_gateway, uvc_child_factory=usb.new_host)
        return [cameras, playout, agents]

    app = make_app(modules)
    with TestClient(app) as http:
        yield CropStudio(app.state.services, http, obs, network, fake_clock, playouts[0])
