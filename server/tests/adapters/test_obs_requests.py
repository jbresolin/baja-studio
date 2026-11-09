"""Tests for the typed OBS request helpers (adapters/obs_requests.py).

Each helper is one request. We run them against the fake OBS over the
in-process network and check both what came back and what the fake
recorded, so field names and units (OBS speaks milliseconds) are right.
"""

from collections.abc import Iterator

import pytest

from baja_studio.adapters.obs_protocol import MediaAction, MediaState
from baja_studio.adapters.obs_requests import MediaStatus
from baja_studio.adapters.obs_transport import ObsAddress
from baja_studio.adapters.obs_websocket import ObsWebsocketClient
from baja_studio.fakes.fake_obs import FakeObs
from baja_studio.fakes.fake_obs_in_process import InProcessObsNetwork
from tests.conftest import FakeClock


@pytest.fixture
def fake_obs(fake_clock: FakeClock) -> FakeObs:
    """The playout scenes ("Main" on program); every file plays for 10 s."""
    return FakeObs.with_playout_scenes(fake_clock)


@pytest.fixture
def client(fake_clock: FakeClock, fake_obs: FakeObs) -> Iterator[ObsWebsocketClient]:
    """A client connected to the fake, closed after the test."""
    network = InProcessObsNetwork()
    network.plug("obs", 4455, fake_obs)
    connected = ObsWebsocketClient(fake_clock, network.connect)
    connected.connect(ObsAddress("obs", 4455), "")
    yield connected
    connected.close()


def test_the_scene_list_starts_at_the_top_of_the_obs_scenes_dock(
    client: ObsWebsocketClient,
) -> None:
    # OBS sends scenes bottom-up; the helper reverses them. The fake was given
    # Main, Playback A, Playback B in that order (top to bottom).
    scenes = client.get_scene_list()

    assert scenes.scene_names == ["Main", "Playback A", "Playback B"]
    assert scenes.current_program_scene == "Main"


def test_setting_the_program_scene_changes_it(
    client: ObsWebsocketClient, fake_obs: FakeObs
) -> None:
    client.set_current_program_scene("Playback B")

    assert fake_obs.program_scene == "Playback B"
    assert client.get_current_program_scene() == "Playback B"


def test_input_settings_overlay_keeps_settings_not_mentioned(client: ObsWebsocketClient) -> None:
    # overlay=True (the default) merges; overlay=False replaces everything.
    client.set_input_settings("baja_program_a", {"local_file": "a.mp4", "looping": False})
    client.set_input_settings("baja_program_a", {"local_file": "b.mp4"})
    assert client.get_input_settings("baja_program_a") == {"local_file": "b.mp4", "looping": False}

    client.set_input_settings("baja_program_a", {"local_file": "c.mp4"}, overlay=False)
    assert client.get_input_settings("baja_program_a") == {"local_file": "c.mp4"}


def test_media_status_is_reported_in_seconds(
    client: ObsWebsocketClient, fake_clock: FakeClock
) -> None:
    # Load a file; like real OBS the fake opens it and starts it a moment later
    # (on the next request). Let 2.5 s pass after that, then ask.
    client.set_input_settings("baja_program_a", {"local_file": "clip.mp4"})
    client.get_media_input_status("baja_program_a")
    fake_clock.advance(2.5)

    status = client.get_media_input_status("baja_program_a")

    assert status == MediaStatus(MediaState.PLAYING, 2.5, 10.0)
    assert not status.has_ended


def test_an_empty_media_source_has_no_position_or_duration(client: ObsWebsocketClient) -> None:
    status = client.get_media_input_status("baja_program_b")

    assert status == MediaStatus(MediaState.NONE, None, None)


def test_media_actions_and_cursor_drive_playback(
    client: ObsWebsocketClient, fake_clock: FakeClock
) -> None:
    # Restart, pause and seek to 0 is how playout cues a clip on its first frame.
    client.set_input_settings("baja_program_a", {"local_file": "clip.mp4"})
    fake_clock.advance(1.0)
    client.trigger_media_input_action("baja_program_a", MediaAction.RESTART)
    client.trigger_media_input_action("baja_program_a", MediaAction.PAUSE)
    client.set_media_input_cursor("baja_program_a", 0.0)
    fake_clock.advance(3.0)

    assert client.get_media_input_status("baja_program_a") == MediaStatus(
        MediaState.PAUSED, 0.0, 10.0
    )


def test_a_finished_file_reports_ended(client: ObsWebsocketClient, fake_clock: FakeClock) -> None:
    client.set_input_settings("baja_program_a", {"local_file": "clip.mp4"})
    client.get_media_input_status("baja_program_a")  # it starts here
    fake_clock.advance(10.0)

    assert client.get_media_input_status("baja_program_a").has_ended


def test_the_cursor_is_sent_in_milliseconds(client: ObsWebsocketClient, fake_obs: FakeObs) -> None:
    client.set_input_settings("baja_program_a", {"local_file": "clip.mp4"})

    client.set_media_input_cursor("baja_program_a", 1.25)

    assert fake_obs.requests_of("SetMediaInputCursor")[-1].data["mediaCursor"] == 1250


def test_a_scene_item_can_be_found_and_hidden(
    client: ObsWebsocketClient, fake_obs: FakeObs
) -> None:
    item_id = client.get_scene_item_id("Playback A", "baja_program_a")

    client.set_scene_item_enabled("Playback A", item_id, False)

    assert fake_obs.scenes["Playback A"][0].enabled is False


def test_a_transition_override_is_set_and_cleared(
    client: ObsWebsocketClient, fake_obs: FakeObs
) -> None:
    client.set_scene_transition_override("Main", "Fade", 800)
    set_override = fake_obs.transition_overrides["Main"]
    client.set_scene_transition_override("Main", None)
    cleared_override = fake_obs.transition_overrides["Main"]

    assert set_override == ("Fade", 800)
    assert cleared_override == (None, None)


def test_a_screenshot_comes_back_as_a_data_uri(
    client: ObsWebsocketClient, fake_obs: FakeObs
) -> None:
    image = client.get_source_screenshot("baja_program_a", width=320)

    assert image.startswith("data:image/")
    assert fake_obs.requests_of("GetSourceScreenshot")[-1].data["imageWidth"] == 320


def test_the_version_names_the_rpc_version(client: ObsWebsocketClient) -> None:
    assert client.get_version()["rpcVersion"] == 1
