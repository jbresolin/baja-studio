"""Tests for the fake OBS's video sources, filters and screenshots (fakes/fake_obs_sources.py).

The OBS crop driver is only as right as this fake: it must crop the way
OBS's Crop/Pad filter does, report sizes *after* filters the way
obs-websocket does, and send the events real OBS sends. We talk to the
fake with a real client over the in-process network (no port), the way the
driver will.
"""

from collections.abc import Iterator
from typing import Any

import pytest

from baja_studio.adapters.obs_images import decode_data_uri, image_size
from baja_studio.adapters.obs_protocol import RequestStatus
from baja_studio.adapters.obs_transport import ObsAddress, ObsRequestFailedError
from baja_studio.adapters.obs_websocket import ObsWebsocketClient
from baja_studio.fakes.fake_obs import FakeObs, parse_video_source
from baja_studio.fakes.fake_obs_in_process import InProcessObsNetwork
from baja_studio.fakes.fake_obs_video import FRAME_SECONDS, REOPEN_SECONDS
from tests.conftest import FakeClock, eventually

CAMERA = "Cam 4K"
FILTER = "Baja crop"


@pytest.fixture
def fake_obs(fake_clock: FakeClock) -> FakeObs:
    """An OBS with one 4K webcam input and nothing else."""
    fake = FakeObs(fake_clock)
    fake.add_video_input(CAMERA, 3840, 2160)
    return fake


@pytest.fixture
def events(fake_obs: FakeObs) -> list[tuple[str, dict[str, Any]]]:
    """Every event the client hears, in order (filled by the `client` fixture)."""
    return []


@pytest.fixture
def client(
    fake_clock: FakeClock, fake_obs: FakeObs, events: list[tuple[str, dict[str, Any]]]
) -> Iterator[ObsWebsocketClient]:
    network = InProcessObsNetwork()
    network.plug("obs", 4455, fake_obs)
    connected = ObsWebsocketClient(fake_clock, network.connect)
    connected.add_event_listener(lambda kind, data: events.append((kind, data)))
    connected.connect(ObsAddress("obs", 4455), "")
    yield connected
    connected.close()


def next_frame(fake_clock: FakeClock) -> None:
    """Let OBS render its next video frame (30 a second): new filter edges show from then."""
    fake_clock.advance(FRAME_SECONDS)


def screenshot_size(client: ObsWebsocketClient, width: int | None = None) -> tuple[int, int]:
    """Ask for a screenshot of the camera and read its size in pixels."""
    return image_size(decode_data_uri(client.get_source_screenshot(CAMERA, "jpg", width)).content)


def test_a_screenshot_without_a_width_has_the_sources_full_size(client: ObsWebsocketClient) -> None:
    assert screenshot_size(client) == (3840, 2160)


def test_a_relative_crop_filter_cuts_its_edges_and_the_screenshot_shrinks(
    client: ObsWebsocketClient, fake_clock: FakeClock
) -> None:
    # Cut 960 px from left and right and 540 from top and bottom: a 1920x1080 middle.
    edges = {"left": 960, "right": 960, "top": 540, "bottom": 540}
    client.create_source_filter(CAMERA, FILTER, "crop_filter", {"relative": True, **edges})
    next_frame(fake_clock)

    # As in obs-websocket, the screenshot is the source *after* its filters.
    assert screenshot_size(client) == (1920, 1080)


def test_an_absolute_crop_filter_uses_its_corner_and_size(
    client: ObsWebsocketClient, fake_clock: FakeClock
) -> None:
    settings = {"relative": False, "left": 10, "top": 10, "cx": 640, "cy": 360}
    client.create_source_filter(CAMERA, FILTER, "crop_filter", settings)
    next_frame(fake_clock)

    assert screenshot_size(client) == (640, 360)


def test_a_disabled_filter_lets_the_whole_picture_through(
    client: ObsWebsocketClient, fake_obs: FakeObs
) -> None:
    client.create_source_filter(CAMERA, FILTER, "crop_filter", {"left": 1000})
    fake_obs.set_filter_enabled(CAMERA, FILTER, False)

    assert screenshot_size(client) == (3840, 2160)


def test_a_width_scales_the_screenshot_keeping_its_shape(client: ObsWebsocketClient) -> None:
    assert screenshot_size(client, width=320) == (320, 180)


def test_a_width_outside_obs_limits_is_refused(client: ObsWebsocketClient) -> None:
    with pytest.raises(ObsRequestFailedError) as refused:
        client.get_source_screenshot(CAMERA, "jpg", 5000)
    assert refused.value.code == RequestStatus.REQUEST_FIELD_OUT_OF_RANGE


def test_a_crop_larger_than_the_picture_leaves_one_pixel(
    client: ObsWebsocketClient, fake_clock: FakeClock
) -> None:
    # Edges wider than the picture: OBS's crop filter clamps its output to 1x1.
    client.create_source_filter(CAMERA, FILTER, "crop_filter", {"left": 2000, "right": 2000})
    next_frame(fake_clock)

    assert screenshot_size(client)[0] == 1


def test_new_edges_show_in_the_picture_only_from_the_next_video_frame(
    client: ObsWebsocketClient, fake_clock: FakeClock
) -> None:
    client.create_source_filter(CAMERA, FILTER, "crop_filter", {})
    next_frame(fake_clock)

    # OBS stores the new edges at once (and reports them)...
    client.set_source_filter_settings(CAMERA, FILTER, {"left": 1920})
    assert client.get_source_filter(CAMERA, FILTER).settings["left"] == 1920
    # ...but the picture still has the old size until it renders the next frame.
    assert screenshot_size(client) == (3840, 2160)
    next_frame(fake_clock)
    assert screenshot_size(client) == (1920, 2160)


def test_a_camera_switched_to_another_resolution_has_it_only_once_reopened(
    client: ObsWebsocketClient, fake_obs: FakeObs, fake_clock: FakeClock
) -> None:
    fake_obs.change_video_size(CAMERA, 1920, 1080)

    # The event came; the device is still reopening, so the picture is still 4K.
    assert screenshot_size(client) == (3840, 2160)
    fake_clock.advance(REOPEN_SECONDS)
    assert screenshot_size(client) == (1920, 1080)


def test_filter_requests_answer_with_obs_errors_for_missing_and_duplicate_things(
    client: ObsWebsocketClient,
) -> None:
    # No such filter yet: 600 (not found), with OBS's words.
    with pytest.raises(ObsRequestFailedError) as missing:
        client.get_source_filter(CAMERA, FILTER)
    assert missing.value.code == RequestStatus.RESOURCE_NOT_FOUND
    assert "No filter was found" in missing.value.comment

    # Creating it twice: 601 (already exists); an unknown kind: 607.
    client.create_source_filter(CAMERA, FILTER, "crop_filter", {})
    with pytest.raises(ObsRequestFailedError) as duplicate:
        client.create_source_filter(CAMERA, FILTER, "crop_filter", {})
    assert duplicate.value.code == RequestStatus.RESOURCE_ALREADY_EXISTS
    with pytest.raises(ObsRequestFailedError) as bad_kind:
        client.create_source_filter(CAMERA, "Other", "no_such_filter", {})
    assert bad_kind.value.code == RequestStatus.INVALID_FILTER_KIND


def test_filter_changes_send_the_events_obs_sends(
    client: ObsWebsocketClient,
    fake_obs: FakeObs,
    events: list[tuple[str, dict[str, Any]]],
) -> None:
    # Create, change (even our own change is announced, as since obs-websocket
    # 5.4), switch off, and remove the filter; then change the camera's resolution.
    client.create_source_filter(CAMERA, FILTER, "crop_filter", {})
    client.set_source_filter_settings(CAMERA, FILTER, {"left": 8})
    fake_obs.set_filter_enabled(CAMERA, FILTER, False)
    fake_obs.remove_filter(CAMERA, FILTER)
    fake_obs.change_video_size(CAMERA, 1920, 1080)

    eventually(lambda: len(events) == 5)
    assert [kind for kind, _ in events] == [
        "SourceFilterCreated",
        "SourceFilterSettingsChanged",
        "SourceFilterEnableStateChanged",
        "SourceFilterRemoved",
        "InputSettingsChanged",
    ]
    assert events[1][1]["filterSettings"]["left"] == 8
    assert events[4][1]["inputSettings"]["resolution"] == "1920x1080"


def test_the_command_line_names_a_video_source_with_its_size() -> None:
    assert parse_video_source("Cam 4K=3840x2160") == ("Cam 4K", 3840, 2160)


def test_an_empty_crop_filter_can_be_there_from_the_start(
    client: ObsWebsocketClient, fake_obs: FakeObs
) -> None:
    # What `--crop-filter` does for the rehearsal rig: the camera is ready to move.
    fake_obs.add_crop_filter(CAMERA, FILTER)

    found = client.get_source_filter(CAMERA, FILTER)
    assert found.kind == "crop_filter"
    assert screenshot_size(client) == (3840, 2160)  # nothing cut yet
