"""A camera node running in the test process, with fakes all around it.

`running_node(...)` starts the node's real app (`node/app.py`) with:

- fake video pipelines and two fake S600 capture devices (`node/video/fake_pipeline.py`);
- the UVC child called in-process over the fake duvc library
  (`tests/imaging/uvc_harness.py`), so image controls really go through
  the child's code;
- a fake OBS on the laptop (`fakes/fake_obs.py`, at 127.0.0.1:4455 on the
  in-process OBS network), with two 4K webcams that each have a "Baja crop"
  filter, for cameras in OBS mode.

Nothing opens a port: HTTP and the WebSockets go through Starlette's test
client. The node runs on the real clock (`SystemClock`), like the agent
conformance suite needs, or on a test's fake clock.
"""

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from baja_studio.agents.contract import STUDIO_ID_HEADER, TOKEN_HEADER
from baja_studio.core.clock import Clock, SystemClock
from baja_studio.fakes.fake_duvc import S600_A_PATH, S600_NAME
from baja_studio.fakes.fake_obs import FakeObs
from baja_studio.fakes.fake_obs_in_process import InProcessObsNetwork
from baja_studio.node.app import create_node_app
from baja_studio.node.module import NodeModule
from baja_studio.node.parts import NodeParts
from baja_studio.node.settings import LOCAL_OBS_HOST
from baja_studio.node.test_cameras import fake_output_url
from baja_studio.node.video.fake_pipeline import (
    FAKE_DEVICES,
    FakeCaptureDevices,
    FakePipelines,
)
from tests.conftest import eventually
from tests.imaging.uvc_harness import FakeUsb

OBS_PORT = 4455
# The laptop's OBS password: built from parts, so it reads as the placeholder it is.
OBS_PASSWORD = "-".join(("laptop", "obs", "test"))
OBS_WEBCAMS = ("S600 A", "S600 B")
# A third webcam in the laptop's OBS with no crop filter yet (for Create filter).
OBS_BARE_WEBCAM = "S600 bare"
CROP_FILTER = "Baja crop"
NODE_TOKEN = "-".join(("node", "test", "token"))
STUDIO_ID = "node-tests"


def node_mode_camera(camera_id: str = "cam1", **overrides: Any) -> dict[str, Any]:
    """A camera in `node` mode on the first fake S600, sending to a pretend Feeds URL."""
    device = FAKE_DEVICES[0]
    camera: dict[str, Any] = {
        "id": camera_id,
        "label": "Node S600",
        "mode": "node",
        "video": {
            "device": device.device,
            "device_name": device.name,
            "output_url": fake_output_url(camera_id),
        },
    }
    return {**camera, **overrides}


def obs_mode_camera(camera_id: str = "cam2", **overrides: Any) -> dict[str, Any]:
    """A camera in `obs` mode: the laptop OBS's first webcam and its crop filter."""
    camera: dict[str, Any] = {
        "id": camera_id,
        "label": "OBS S600",
        "mode": "obs",
        "obs": {"port": OBS_PORT, "password": OBS_PASSWORD, "source": OBS_WEBCAM_A},
    }
    return {**camera, **overrides}


OBS_WEBCAM_A, OBS_WEBCAM_B = OBS_WEBCAMS


def s600_usb_device() -> dict[str, Any]:
    """The first fake S600 as the UVC child lists it (for image controls)."""
    return {"path": S600_A_PATH, "name": S600_NAME, "index": 0}


@dataclass
class RunningNode:
    """The node app, its HTTP client and the fakes around it."""

    http: TestClient
    module: NodeModule
    pipelines: FakePipelines
    devices: FakeCaptureDevices
    obs: FakeObs
    obs_network: InProcessObsNetwork
    usb: FakeUsb
    app: Any

    def settings(self) -> Any:
        return self.app.state.services.settings.get("node")

    def configure(self, **values: Any) -> None:
        """Save node settings, as the node's page does."""
        self.app.state.services.settings.update("node", values)

    def state(self, path: str = "") -> Any:
        """The node module's state at `path` (what the page shows)."""
        return self.app.state.services.state.get("node", path)

    def wait_until_ready(self) -> None:
        """Wait until every served camera has published its window (OBS mode measures first)."""

        def ready() -> bool:
            served = self.settings().served()
            return all((self.state(f"cameras.{camera.id}") or {}).get("crop") for camera in served)

        eventually(ready, "a camera of the node never published its window")

    def command(self, command: str, **args: Any) -> Any:
        return self.app.state.services.registry.dispatch_command("node", command, args, "api:test")


def laptop_obs(clock: Clock) -> FakeObs:
    """The laptop's OBS: two 4K webcams, each with its crop filter."""
    obs = FakeObs(clock, password=OBS_PASSWORD)
    for webcam in OBS_WEBCAMS:
        obs.add_video_input(webcam, 3840, 2160)
        obs.add_crop_filter(webcam, CROP_FILTER)
    obs.add_video_input(OBS_BARE_WEBCAM, 3840, 2160)
    return obs


@contextmanager
def running_node(
    data_directory: Path,
    cameras: list[dict[str, Any]],
    clock: Clock | None = None,
    token: str = NODE_TOKEN,
    studio_id: str = STUDIO_ID,
) -> Iterator[RunningNode]:
    """Start a node with `cameras` (settings dicts) and the fakes; stop everything after."""
    clock = clock or SystemClock()
    pipelines, devices, usb = FakePipelines(), FakeCaptureDevices(), FakeUsb()
    network = InProcessObsNetwork()
    obs = laptop_obs(clock)
    network.plug(LOCAL_OBS_HOST, OBS_PORT, obs)
    parts = NodeParts(
        pipelines=pipelines,
        capture_devices=devices,
        uvc_child=lambda context, python_module, on_event: usb.new_host(context, on_event),
        obs_transport=network.connect,
        ffmpeg_path=lambda settings: "ffmpeg",
    )
    app = create_node_app(
        parts,
        data_directory=data_directory,
        clock=clock,
        log_to_file=False,
        frontend_directory=data_directory / "no-frontend",
    )
    app.state.services.settings.update("node", {"token": token, "cameras": cameras})
    headers = {STUDIO_ID_HEADER: studio_id, **({TOKEN_HEADER: token} if token else {})}
    module = next(m for m in app.state.services.registry.modules() if m.name == "node")
    assert isinstance(module, NodeModule)
    with TestClient(app, headers=headers) as http:
        yield RunningNode(http, module, pipelines, devices, obs, network, usb, app)
