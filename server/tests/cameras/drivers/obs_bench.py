"""A test bench for the OBS crop driver: a fake OBS, a real client to it, and the driver.

The client reaches the fake OBS over the in-process network (no port). It is
lent to the driver through the same gateway the playout module provides, so
the driver sees exactly what it sees in the service. The driver's worker runs
under a supervisor on the fake clock; `step` lets fake time pass only while
the worker sleeps *and* OBS has answered everything (a request waiting for
its answer also sleeps on the fake clock, and moving time then would time it
out).
"""

import logging
from collections.abc import Callable
from typing import Any

from baja_studio.adapters.obs_transport import ObsAddress
from baja_studio.adapters.obs_websocket import ObsWebsocketClient
from baja_studio.cameras.config import CameraConfig
from baja_studio.cameras.crop_window import CropWindow
from baja_studio.cameras.driver import DriverHost
from baja_studio.cameras.drivers.obs_crop import ObsCropDriver
from baja_studio.core.supervisor import Supervisor
from baja_studio.fakes.fake_obs import FakeObs
from baja_studio.fakes.fake_obs_in_process import InProcessObsNetwork
from baja_studio.modules.playout.obs_gateway import PlayoutObsGateway
from tests.conftest import FakeClock, eventually, module_worker_threads

CAMERA = "Cam 4K"
FILTER = "Baja crop"
OBS_ADDRESS = ObsAddress("obs.test", 4455)
# The driver's worker steps 25 times a second while moving; fake time moves as much.
STEP_SECONDS = 0.04
CAMERA_WORKERS = module_worker_threads("cameras")


def crop_camera(**crop: Any) -> CameraConfig:
    """An `obs_crop` camera on CAMERA, with crop settings changed by `crop`."""
    return CameraConfig.model_validate(
        {"id": "crop", "driver": "obs_crop", "obs_source": CAMERA, "crop": crop}
    )


class ObsBench:
    """The fake OBS (one 4K webcam), the connection to it, and one OBS crop driver."""

    def __init__(self, fake_clock: FakeClock, supervisor: Supervisor) -> None:
        self.clock = fake_clock
        self.obs = FakeObs(fake_clock)
        self.obs.add_video_input(CAMERA, 3840, 2160)
        self.network = InProcessObsNetwork()
        self.network.plug(OBS_ADDRESS.host, OBS_ADDRESS.port, self.obs)
        self.client = ObsWebsocketClient(fake_clock, self.network.connect)
        # What playout would say about OBS while the connection is down.
        self.playout_obs_state = {"connection": "offline", "message": "OBS closed the connection"}
        self.gateway = PlayoutObsGateway(lambda: self.playout_obs_state)
        self.gateway.attach(self.client)
        self.client.connect(OBS_ADDRESS, "")
        self.config = crop_camera()
        self.state: dict[str, Any] = {}
        self.saved_presets: list[tuple[int, CropWindow]] = []
        host = DriverHost(
            camera_id="crop",
            clock=fake_clock,
            logger=logging.getLogger("test"),
            publish=self.state.__setitem__,
            start_worker=lambda spec: supervisor.start_worker("cameras", spec),
            stop_worker=lambda name: supervisor.stop_worker("cameras", name),
        )
        self.driver = ObsCropDriver(
            host,
            self.gateway,
            config=lambda: self.config,
            save_window=lambda n, window: self.saved_presets.append((n, window)),
        )

    # --- OBS set-up -------------------------------------------------------------------------

    def add_filter(self, **settings: Any) -> None:
        """The operator added the Crop/Pad filter in OBS (before the studio looks)."""
        self.client.create_source_filter(CAMERA, FILTER, "crop_filter", settings)

    def crop_in_obs(self) -> dict[str, Any]:
        """The filter's settings as OBS has them now."""
        return self.obs.filter_settings(CAMERA, FILTER)

    def visible_size(self) -> tuple[float, float]:
        """How big the picture after the filter is, in pixels."""
        region = self.obs.visible_region(CAMERA)
        return region.width, region.height

    def count(self, request_type: str, **matching: Any) -> int:
        """How many requests of a type OBS answered (with these fields, if given)."""
        return sum(
            1
            for record in self.obs.requests_of(request_type)
            if all(record.data.get(key) == value for key, value in matching.items())
        )

    def full_size_screenshots(self) -> int:
        """Screenshots taken to measure the source (no width asked for)."""
        return sum(
            1
            for record in self.obs.requests_of("GetSourceScreenshot")
            if "imageWidth" not in record.data
        )

    # --- OBS going and coming back --------------------------------------------------------

    def quit_obs(self) -> None:
        self.network.unplug(OBS_ADDRESS.host, OBS_ADDRESS.port)
        eventually(lambda: not self.client.is_connected)

    def restart_obs(self) -> None:
        """OBS is back with the same scene collection; playout reconnects."""
        self.network.plug(OBS_ADDRESS.host, OBS_ADDRESS.port, self.obs)
        self.client.connect(OBS_ADDRESS, "")

    # --- time ---------------------------------------------------------------------------

    def settle(self) -> None:
        """Wait until the worker sleeps with nothing to do and OBS answered everything.

        A command wakes the worker, but a sleeping thread notices that only a
        few milliseconds later; until then it still counts as asleep. So we
        also wait for the wake-up to be taken (the worker's private flag).
        """
        eventually(
            lambda: (
                self.clock.all_asleep_or_gone(CAMERA_WORKERS())
                and self.client.requests_in_flight == 0
                and not self._wake_pending()
            ),
            "the driver never went back to sleep, or OBS never answered it",
        )

    def _wake_pending(self) -> bool:
        worker = self.driver._worker
        return worker is not None and worker._wake_requested.is_set()

    def step(self, seconds: float) -> None:
        """Let `seconds` of fake time pass in 40 ms steps, the worker running each one."""
        for _ in range(round(seconds / STEP_SECONDS)):
            self.settle()
            self.clock.advance(STEP_SECONDS)
        self.settle()

    def soon(self, condition: Callable[[], bool], within_seconds: float = 3.0) -> None:
        """Let fake time pass until `condition()` holds (checked while the worker sleeps)."""
        for _ in range(round(within_seconds / STEP_SECONDS)):
            self.settle()
            if condition():
                return
            self.clock.advance(STEP_SECONDS)
        self.settle()
        eventually(condition)

    def close(self) -> None:
        self.client.close()
