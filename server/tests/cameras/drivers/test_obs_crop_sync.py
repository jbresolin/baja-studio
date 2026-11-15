"""Tests for what the OBS crop driver asks OBS (cameras/drivers/obs_crop_sync.py).

Two questions: "is the camera's Crop/Pad filter there and usable?" and
"how big is the source?" (OBS has no request for that; we take a screenshot,
which is the size *after* the filter, and add the filter's edges back). We
ask a fake OBS with one 4K webcam through a real client.
"""

from collections.abc import Iterator

import pytest

from baja_studio.adapters.obs_transport import ObsAddress
from baja_studio.adapters.obs_websocket import ObsWebsocketClient
from baja_studio.agents.contract import Status
from baja_studio.cameras.crop_window import PixelCrop
from baja_studio.cameras.drivers.obs_crop_sync import (
    CropUnavailableError,
    FilterReading,
    measure_source,
    read_filter,
)
from baja_studio.fakes.fake_obs import FakeObs
from baja_studio.fakes.fake_obs_in_process import InProcessObsNetwork
from tests.conftest import FakeClock

CAMERA, FILTER = "Cam 4K", "Baja crop"


@pytest.fixture
def fake_obs(fake_clock: FakeClock) -> FakeObs:
    fake = FakeObs(fake_clock)
    fake.add_video_input(CAMERA, 3840, 2160)
    return fake


@pytest.fixture
def client(fake_clock: FakeClock, fake_obs: FakeObs) -> Iterator[ObsWebsocketClient]:
    network = InProcessObsNetwork()
    network.plug("obs", 4455, fake_obs)
    connected = ObsWebsocketClient(fake_clock, network.connect)
    connected.connect(ObsAddress("obs", 4455), "")
    yield connected
    connected.close()


def test_a_usable_filter_is_read_with_its_edges(client: ObsWebsocketClient) -> None:
    client.create_source_filter(CAMERA, FILTER, "crop_filter", {"left": 10, "bottom": 20})

    reading = read_filter(client, CAMERA, FILTER)

    assert reading == FilterReading(enabled=True, crop=PixelCrop(10, 0, 0, 20))


def test_a_missing_filter_and_a_missing_source_say_different_things(
    client: ObsWebsocketClient,
) -> None:
    # The source is there, the filter not: degraded, and the fix is a button.
    with pytest.raises(CropUnavailableError) as no_filter:
        read_filter(client, CAMERA, FILTER)
    assert no_filter.value.status == Status.DEGRADED
    assert "Create filter" in str(no_filter.value)

    # No such source at all: offline (nothing to crop).
    with pytest.raises(CropUnavailableError) as no_source:
        read_filter(client, "Nope", FILTER)
    assert no_source.value.status == Status.OFFLINE


def test_a_filter_of_another_kind_is_refused(client: ObsWebsocketClient) -> None:
    client.create_source_filter(CAMERA, FILTER, "color_filter", {})

    with pytest.raises(CropUnavailableError, match="is a color_filter, not a Crop/Pad filter"):
        read_filter(client, CAMERA, FILTER)


def test_the_source_size_is_the_screenshot_plus_the_edges_cut_off(
    client: ObsWebsocketClient,
) -> None:
    # The filter cuts 1000 px off the left and 500 off the top.
    client.create_source_filter(CAMERA, FILTER, "crop_filter", {"left": 1000, "top": 500})
    reading = read_filter(client, CAMERA, FILTER)

    # The screenshot is 2840x1660; adding the edges back gives the 4K source.
    assert measure_source(client, CAMERA, FILTER, reading) == (3840, 2160)


def test_a_disabled_filter_cuts_nothing_so_nothing_is_added(
    client: ObsWebsocketClient, fake_obs: FakeObs
) -> None:
    client.create_source_filter(CAMERA, FILTER, "crop_filter", {"left": 1000})
    fake_obs.set_filter_enabled(CAMERA, FILTER, False)
    reading = read_filter(client, CAMERA, FILTER)

    assert measure_source(client, CAMERA, FILTER, reading) == (3840, 2160)


def test_a_measurement_is_thrown_away_if_the_filter_changed_meanwhile(
    client: ObsWebsocketClient,
) -> None:
    client.create_source_filter(CAMERA, FILTER, "crop_filter", {"left": 1000})
    # A reading from *before* someone changed the edges (OBS applies new edges
    # at its next frame, so screenshot and reading would not agree).
    stale = FilterReading(enabled=True, crop=PixelCrop(0, 0, 0, 0))

    assert measure_source(client, CAMERA, FILTER, stale) is None


def test_a_source_with_no_picture_cannot_be_measured(client: ObsWebsocketClient) -> None:
    client.create_source_filter(CAMERA, FILTER, "crop_filter", {"left": 2000, "right": 2000})
    reading = read_filter(client, CAMERA, FILTER)

    with pytest.raises(CropUnavailableError, match="no picture"):
        measure_source(client, CAMERA, FILTER, reading)
