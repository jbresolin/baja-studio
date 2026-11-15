"""Tests for camera previews (cameras/previews.py): which source, how often OBS is asked.

`PreviewCache` is tested with a stand-in "OBS" that counts screenshots, and
with threads where it matters (several pages asking at once). `CameraPreviews`
decides which source to show for a camera and explains it in headers; it is
tested against a stand-in gateway. The route and the real fake OBS are
exercised in tests/modules/test_cameras_obs.py.
"""

import threading
from typing import Any

import pytest

from baja_studio.adapters.obs_images import ImageData
from baja_studio.adapters.obs_requests import ObsRequestsMixin
from baja_studio.adapters.obs_transport import ObsRequestFailedError
from baja_studio.cameras.config import CamerasSettings
from baja_studio.cameras.drivers.obs_gateway import NoObsGateway, ObsLinkView, ObsListener
from baja_studio.cameras.previews import (
    MAX_PREVIEWS_AT_ONCE,
    MAX_PREVIEWS_PER_OBS,
    SHOWS_FULL,
    SHOWS_WINDOW,
    CameraPreviews,
    PreviewCache,
    PreviewUnavailableError,
)
from tests.conftest import FakeClock, eventually

JPEG = ImageData("image/jpeg", b"\xff\xd8 a picture")


class CountingObs:
    """Stands in for OBS's screenshot request: counts calls, can be held up or fail."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, int]] = []
        self.release = threading.Event()
        self.release.set()  # answers at once unless a test clears this
        self.failure: Exception | None = None

    def fetch(self, source: str, width: int) -> ImageData:
        self.calls.append((source, width))
        self.release.wait(5)
        if self.failure is not None:
            raise self.failure
        return JPEG


def no_window() -> dict[str, Any] | None:
    return None


def test_pages_asking_within_a_second_share_one_screenshot(fake_clock: FakeClock) -> None:
    obs = CountingObs()
    cache = PreviewCache(fake_clock, obs.fetch)

    # Three pages ask in the same second: OBS is asked once.
    for _ in range(3):
        assert cache.get("Cam", 320, no_window)[0] == JPEG
    assert len(obs.calls) == 1

    # A second later the picture is old: the next ask takes a new one.
    fake_clock.advance(1.0)
    cache.get("Cam", 320, no_window)
    assert len(obs.calls) == 2


def test_another_width_or_source_is_a_separate_screenshot(fake_clock: FakeClock) -> None:
    obs = CountingObs()
    cache = PreviewCache(fake_clock, obs.fetch)

    cache.get("Cam", 320, no_window)
    cache.get("Cam", 640, no_window)
    cache.get("Other", 320, no_window)

    assert obs.calls == [("Cam", 320), ("Cam", 640), ("Other", 320)]


def test_only_one_request_per_source_is_in_flight_and_the_others_wait_for_it(
    fake_clock: FakeClock,
) -> None:
    obs = CountingObs()
    obs.release.clear()  # OBS is slow: the first screenshot is still being taken
    cache = PreviewCache(fake_clock, obs.fetch)
    answers: list[ImageData] = []

    def page() -> None:
        answers.append(cache.get("Cam", 320, no_window)[0])

    pages = [threading.Thread(target=page) for _ in range(4)]
    for thread in pages:
        thread.start()
    eventually(lambda: len(obs.calls) == 1)
    obs.release.set()  # OBS answers
    for thread in pages:
        thread.join(5)

    assert len(obs.calls) == 1  # four pages, one request
    assert answers == [JPEG] * 4


def test_a_request_taking_longer_than_a_second_is_still_the_only_one(
    fake_clock: FakeClock,
) -> None:
    # OBS takes 1.5 s to answer: a page asking after 1 s must wait for that
    # answer, not send a second request while the first is still running.
    obs = CountingObs()
    obs.release.clear()
    cache = PreviewCache(fake_clock, obs.fetch)
    first = threading.Thread(target=cache.get, args=("Cam", 320, no_window))
    first.start()
    eventually(lambda: len(obs.calls) == 1)
    fake_clock.advance(1.2)

    second = threading.Thread(target=cache.get, args=("Cam", 320, no_window))
    second.start()
    # The second page is now waiting for the first page's answer (asleep on the clock).
    eventually(lambda: fake_clock.sleeping_count == 1)
    fake_clock.advance(0.3)
    obs.release.set()
    first.join(5)
    second.join(5)

    assert len(obs.calls) == 1
    # The second counts from the answer: just after it, the picture is reused.
    cache.get("Cam", 320, no_window)
    assert len(obs.calls) == 1


def no_state(camera_id: str) -> dict[str, Any] | None:
    """No camera has published anything yet."""
    return None


class HungGateway:
    """A connected OBS that answers no screenshot until `release` is set (or 5 s pass)."""

    def __init__(self) -> None:
        self.release = threading.Event()
        self.asked: list[str] = []

    def link(self) -> ObsLinkView:
        return ObsLinkView(True)

    def client(self) -> ObsRequestsMixin | None:
        gateway = self

        class Client(ObsRequestsMixin):
            def request(
                self,
                request_type: str,
                request_data: dict[str, Any] | None = None,
                timeout_s: float | None = None,
            ) -> dict[str, Any]:
                gateway.asked.append(str((request_data or {}).get("sourceName")))
                gateway.release.wait(5)
                return {"imageData": "data:image/jpg;base64,/9g="}

        return Client()

    def add_listener(self, listener: ObsListener) -> Any:
        return lambda: None


def ptz_cameras(count: int, prefix: str = "cam") -> list[dict[str, Any]]:
    """Cameras on playout's OBS, each with its own source."""
    return [
        {"id": f"{prefix}{n}", "address": {"host": "10.0.0.9"}, "obs_source": f"{prefix} {n}"}
        for n in range(count)
    ]


def laptop_cameras(count: int) -> list[dict[str, Any]]:
    """Crop cameras in a laptop's OBS (WP13), each on its own webcam."""
    obs = {"remote": True, "host": "laptop.test", "password": "pw"}
    return [
        {"id": f"lap{n}", "driver": "obs_crop", "obs_source": f"Lap {n}", "crop": {"obs": obs}}
        for n in range(count)
    ]


def ask_in_background(service: CameraPreviews, camera_ids: list[str]) -> list[threading.Thread]:
    """Pages asking for these previews at once; each waits in its own thread."""
    threads = [
        threading.Thread(target=service.preview, args=(camera, 320)) for camera in camera_ids
    ]
    for thread in threads:
        thread.start()
    return threads


def test_one_hung_obs_takes_only_its_own_share_of_the_previews(fake_clock: FakeClock) -> None:
    # Playout's OBS hangs: every page asking for one of its cameras waits.
    playouts, laptop = HungGateway(), StubGateway()
    cameras = ptz_cameras(MAX_PREVIEWS_PER_OBS + 1) + laptop_cameras(1)
    service = CameraPreviews(
        fake_clock,
        playouts,
        lambda: CamerasSettings.model_validate({"cameras": cameras}),
        no_state,
        gateway_for=lambda connection: laptop,
    )
    waiting = ask_in_background(service, [f"cam{n}" for n in range(MAX_PREVIEWS_PER_OBS)])
    eventually(lambda: len(playouts.asked) == MAX_PREVIEWS_PER_OBS)

    # One more for the hung OBS is told to try again at once, instead of waiting too...
    with pytest.raises(PreviewUnavailableError, match="this OBS is slow to answer"):
        service.preview(f"cam{MAX_PREVIEWS_PER_OBS}", 320)
    # ...while a laptop's camera, in another OBS, is served straight away.
    assert service.preview("lap0", 320).headers["X-Preview-Shows"] == SHOWS_WINDOW
    assert laptop.asked == ["Lap 0"]

    playouts.release.set()
    for thread in waiting:
        thread.join(5)


def test_only_a_few_previews_are_served_at_once_across_all_obses(fake_clock: FakeClock) -> None:
    # Two hung OBSes (this PC's and a laptop's): beyond the overall cap, pages are
    # told to try again instead of taking one more of the web server's threads.
    playouts, laptop = HungGateway(), HungGateway()
    from_laptop = MAX_PREVIEWS_AT_ONCE - MAX_PREVIEWS_PER_OBS
    cameras = ptz_cameras(MAX_PREVIEWS_PER_OBS) + laptop_cameras(from_laptop + 1)
    service = CameraPreviews(
        fake_clock,
        playouts,
        lambda: CamerasSettings.model_validate({"cameras": cameras}),
        no_state,
        gateway_for=lambda connection: laptop,
    )
    camera_ids = [f"cam{n}" for n in range(MAX_PREVIEWS_PER_OBS)]
    camera_ids += [f"lap{n}" for n in range(from_laptop)]
    waiting = ask_in_background(service, camera_ids)
    eventually(lambda: len(playouts.asked) + len(laptop.asked) == MAX_PREVIEWS_AT_ONCE)

    with pytest.raises(PreviewUnavailableError, match="too many previews at once"):
        service.preview(f"lap{from_laptop}", 320)

    playouts.release.set()
    laptop.release.set()
    for thread in waiting:
        thread.join(5)


def test_the_same_source_name_in_two_obses_is_two_screenshots(fake_clock: FakeClock) -> None:
    # Two laptops both call their webcam "Lap 0"; their pictures must not be mixed up.
    first, second = StubGateway(), StubGateway()
    cameras = laptop_cameras(1)
    other = {**cameras[0], "id": "other"}
    other["crop"] = {"obs": {"remote": True, "host": "laptop2.test", "password": "pw"}}
    service = CameraPreviews(
        fake_clock,
        NoObsGateway("not used"),
        lambda: CamerasSettings.model_validate({"cameras": [*cameras, other]}),
        no_state,
        gateway_for=lambda connection: first if connection.host == "laptop.test" else second,
    )

    service.preview("lap0", 320)
    service.preview("other", 320)

    assert (first.asked, second.asked) == (["Lap 0"], ["Lap 0"])


def test_a_remote_obss_refusal_names_that_obs(fake_clock: FakeClock) -> None:
    class Refusing(StubGateway):
        def link(self) -> ObsLinkView:
            return ObsLinkView(True, where="laptop.test:4455")

        def client(self) -> ObsRequestsMixin | None:
            class Client(ObsRequestsMixin):
                def request(
                    self,
                    request_type: str,
                    request_data: dict[str, Any] | None = None,
                    timeout_s: float | None = None,
                ) -> dict[str, Any]:
                    raise ObsRequestFailedError(request_type, 600, "No source was found")

            return Client()

    service = CameraPreviews(
        fake_clock,
        NoObsGateway("not used"),
        lambda: CamerasSettings.model_validate({"cameras": laptop_cameras(1)}),
        no_state,
        gateway_for=lambda connection: Refusing(),
    )

    with pytest.raises(PreviewUnavailableError) as refused:
        service.preview("lap0", 320)

    assert refused.value.detail == (
        "OBS at laptop.test:4455: OBS refused GetSourceScreenshot: No source was found"
    )


def test_a_refusal_is_remembered_for_a_second_too(fake_clock: FakeClock) -> None:
    # A source OBS does not have: asking again every time would only repeat that.
    obs = CountingObs()
    obs.failure = ObsRequestFailedError("GetSourceScreenshot", 600, "No source was found")
    cache = PreviewCache(fake_clock, obs.fetch)

    for _ in range(2):
        with pytest.raises(PreviewUnavailableError) as refused:
            cache.get("Cam", 320, no_window)
        assert refused.value.status_code == 503
        assert "No source was found" in refused.value.detail
    assert len(obs.calls) == 1


# --- which source a camera shows ---------------------------------------------------------


class StubGateway:
    """A connected OBS whose client hands out a fixed screenshot."""

    def __init__(self) -> None:
        self.asked: list[str] = []

    def link(self) -> ObsLinkView:
        return ObsLinkView(True)

    def client(self) -> ObsRequestsMixin | None:
        gateway = self

        class Client(ObsRequestsMixin):
            def request(
                self,
                request_type: str,
                request_data: dict[str, Any] | None = None,
                timeout_s: float | None = None,
            ) -> dict[str, Any]:
                gateway.asked.append(str((request_data or {}).get("sourceName")))
                return {"imageData": "data:image/jpg;base64,/9g="}

        return Client()

    def add_listener(self, listener: ObsListener) -> Any:
        return lambda: None


def previews(
    fake_clock: FakeClock, cameras: list[dict[str, Any]], state: dict[str, Any], gateway: Any
) -> CameraPreviews:
    settings = CamerasSettings.model_validate({"cameras": cameras})
    return CameraPreviews(fake_clock, gateway, lambda: settings, state.get)


CROP_STATE = {"capabilities": {"kind": "crop"}, "crop": {"x": 0.25, "y": 0.0, "w": 0.5, "h": 0.5}}


def test_a_ptz_camera_shows_its_whole_source(fake_clock: FakeClock) -> None:
    gateway = StubGateway()
    camera = {"id": "ptz", "address": {"host": "10.0.0.9"}, "obs_source": "PTZ"}
    service = previews(fake_clock, [camera], {"ptz": {"capabilities": {"kind": "ptz"}}}, gateway)

    preview = service.preview("ptz", 320)

    assert gateway.asked == ["PTZ"]
    assert preview.image.media_type == "image/jpeg"
    assert preview.headers["X-Preview-Shows"] == SHOWS_FULL
    assert "X-Crop-Window" not in preview.headers


def test_an_obs_crop_camera_shows_its_window_and_says_where_it_was(fake_clock: FakeClock) -> None:
    gateway = StubGateway()
    camera = {"id": "crop", "driver": "obs_crop", "obs_source": "Cam 4K"}
    service = previews(fake_clock, [camera], {"crop": CROP_STATE}, gateway)

    preview = service.preview("crop", 320)

    assert preview.headers["X-Preview-Shows"] == SHOWS_WINDOW
    assert preview.headers["X-Crop-Window"] == "0.250000,0.000000,0.500000,0.500000"


def test_an_obs_crop_camera_with_a_preview_source_shows_that_whole_picture(
    fake_clock: FakeClock,
) -> None:
    gateway = StubGateway()
    camera = {
        "id": "crop",
        "driver": "obs_crop",
        "obs_source": "Cam 4K",
        "crop": {"preview_source": "Cam 4K uncropped"},
    }
    service = previews(fake_clock, [camera], {"crop": CROP_STATE}, gateway)

    preview = service.preview("crop", 320)

    assert gateway.asked == ["Cam 4K uncropped"]
    assert preview.headers["X-Preview-Shows"] == SHOWS_FULL
    assert "X-Crop-Window" in preview.headers  # the page draws the window on it


def test_an_agent_crop_cameras_source_is_its_window(fake_clock: FakeClock) -> None:
    gateway = StubGateway()
    camera = {
        "id": "laptop",
        "owner": "agent",
        "driver": "agent",
        "address": {"agent_id": "a", "remote_camera_id": "crop1"},
        "obs_source": "Laptop NDI",
    }
    service = previews(fake_clock, [camera], {"laptop": CROP_STATE}, gateway)

    assert service.preview("laptop", 320).headers["X-Preview-Shows"] == SHOWS_WINDOW


def test_no_source_and_no_obs_are_explained(fake_clock: FakeClock) -> None:
    camera = {"id": "ptz", "address": {"host": "10.0.0.9"}}
    with_source = {**camera, "id": "ptz2", "obs_source": "PTZ"}
    service = previews(fake_clock, [camera, with_source], {}, NoObsGateway("OBS is not running"))

    with pytest.raises(PreviewUnavailableError) as no_source:
        service.preview("ptz", 320)
    assert no_source.value.status_code == 404
    with pytest.raises(PreviewUnavailableError) as no_obs:
        service.preview("ptz2", 320)
    assert (no_obs.value.status_code, no_obs.value.detail) == (
        503,
        "OBS offline: OBS is not running",
    )
    with pytest.raises(PreviewUnavailableError) as unknown:
        service.preview("nope", 320)
    assert unknown.value.status_code == 404
