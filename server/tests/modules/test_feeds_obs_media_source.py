"""Tests for Add to OBS (modules/feeds/obs_media_source.py), against the fake OBS.

The fake OBS answers `CreateInput` as obs-websocket 5.x documents it (a
taken name is refused with 601). The studio reaches it through an
`ObsGateway`, as it reaches playout's connection in the real service; here
the gateway is a small stand-in holding a client connected to the fake over
the in-process network (no port opened).

The rule under test: OBS changes only on a click, and only by *creating*
one media source; an existing source is never touched.
"""

from collections.abc import Iterator
from typing import Any

import pytest

from baja_studio.adapters.obs_transport import ObsAddress
from baja_studio.adapters.obs_websocket import ObsWebsocketClient
from baja_studio.core.module import CommandError
from baja_studio.fakes.fake_obs import FakeObs
from baja_studio.fakes.fake_obs_in_process import InProcessObsNetwork
from baja_studio.modules.feeds.obs_media_source import (
    add_media_source,
    media_source_settings,
    obs_scene_names,
)
from tests.conftest import FakeClock
from tests.modules.feeds_studio import ConnectedGateway

URL = "rtsp://127.0.0.1:8554/drone"


@pytest.fixture
def obs(fake_clock: FakeClock) -> FakeObs:
    """OBS with two scenes and a camera already in "Main"."""
    fake = FakeObs(fake_clock)
    fake.add_video_input("Camera 1", 1920, 1080)
    fake.add_scene("Main", ["Camera 1"])
    fake.add_scene("Drone")
    return fake


@pytest.fixture
def gateway(fake_clock: FakeClock, obs: FakeObs) -> Iterator[ConnectedGateway]:
    network = InProcessObsNetwork()
    network.plug("obs", 4455, obs)
    client = ObsWebsocketClient(fake_clock, network.connect)
    client.connect(ObsAddress("obs", 4455), "")
    yield ConnectedGateway(client)
    client.close()


def test_the_scene_picker_lists_obs_scenes(gateway: ConnectedGateway) -> None:
    assert obs_scene_names(gateway) == ["Main", "Drone"]


def test_add_to_obs_creates_one_media_source_reading_the_local_feed(
    gateway: ConnectedGateway, obs: FakeObs
) -> None:
    result = add_media_source(gateway, "Drone", "Drone feed", URL)

    # One new input, of OBS's media source kind, in the chosen scene, shown.
    assert result["source"] == "Drone feed" and result["scene"] == "Drone"
    created = obs.inputs["Drone feed"]
    assert created.kind == "ffmpeg_source"
    assert [item.source_name for item in obs.scenes["Drone"]] == ["Drone feed"]
    assert obs.scenes["Drone"][0].enabled is True
    # Its settings: a network stream (not a file) read over RTSP/TCP, reconnecting
    # by itself, decoded on the GPU, with the smallest buffer, restarted when shown.
    assert created.settings == media_source_settings(URL)
    assert created.settings["is_local_file"] is False
    assert created.settings["input"] == URL
    assert created.settings["ffmpeg_options"] == "rtsp_transport=tcp"
    assert created.settings["reconnect_delay_sec"] == 2
    assert created.settings["hw_decode"] is True
    assert created.settings["buffering_mb"] == 1
    assert created.settings["restart_on_activate"] is True
    # Nothing else was asked of OBS but reads and this one CreateInput.
    changes = [
        record.request_type for record in obs.requests if not record.request_type.startswith("Get")
    ]
    assert changes == ["CreateInput"]


def test_a_source_name_that_exists_is_refused_and_the_source_left_alone(
    gateway: ConnectedGateway, obs: FakeObs
) -> None:
    before = dict(obs.inputs["Camera 1"].settings)
    with pytest.raises(CommandError, match="OBS already has a source named 'Camera 1'"):
        add_media_source(gateway, "Drone", "Camera 1", URL)
    assert obs.inputs["Camera 1"].settings == before
    assert obs.requests_of("CreateInput") == []


def test_a_name_obs_refuses_as_taken_is_explained_the_same_way(
    gateway: ConnectedGateway, obs: FakeObs
) -> None:
    # A scene called "Drone" is not an input, so the studio's own check lets
    # it through, but OBS refuses (601: names are shared by all sources).
    with pytest.raises(CommandError, match="OBS already has a source named 'Drone'"):
        add_media_source(gateway, "Main", "Drone", URL)


def test_a_scene_that_is_not_in_obs_is_refused(gateway: ConnectedGateway) -> None:
    with pytest.raises(CommandError, match="OBS has no scene named 'Gone'"):
        add_media_source(gateway, "Gone", "Drone feed", URL)


def test_without_obs_nothing_is_tried_and_the_reason_is_shown() -> None:
    away = ConnectedGateway(None, "OBS is reached through the playout module, which is not running")
    with pytest.raises(CommandError, match="OBS is not connected: OBS is reached through"):
        add_media_source(away, "Drone", "Drone feed", URL)
    with pytest.raises(CommandError, match="not connected"):
        obs_scene_names(away)


def test_the_settings_are_plain_json(gateway: ConnectedGateway) -> None:
    # They travel in an obs-websocket request: only JSON types.
    settings: dict[str, Any] = media_source_settings(URL)
    assert all(isinstance(value, str | bool | int) for value in settings.values())
