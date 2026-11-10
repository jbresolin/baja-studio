"""Tests for loading and parking clips in media sources (playout/parking.py).

Like the switcher tests, the parker drives a real `ObsWebsocketClient`
connected to the fake OBS through the in-process network (no port opened).
The fake records each request it answered, so the tests check what was
asked of OBS and in which order.
"""

import threading
from collections.abc import Iterator
from typing import Any

import pytest

from baja_studio.adapters.obs_protocol import MediaAction, MediaState
from baja_studio.adapters.obs_transport import ObsAddress
from baja_studio.adapters.obs_websocket import ObsWebsocketClient
from baja_studio.fakes.fake_obs import FakeObs
from baja_studio.fakes.fake_obs_in_process import InProcessObsNetwork
from baja_studio.playout.parking import MediaParker
from tests.conftest import FakeClock

HOST, PORT = "obs.test", 4455
SOURCE = "baja_program_a"
CLIP = {"id": 1, "title": "clip 1", "file_path": "/clips/1.mp4"}


class Rig:
    """The fake OBS, a connected client and a parker."""

    def __init__(self, clock: FakeClock) -> None:
        self.clock = clock
        self.fake = FakeObs.with_playout_scenes(clock)
        network = InProcessObsNetwork()
        network.plug(HOST, PORT, self.fake)
        self.client = ObsWebsocketClient(clock, network.connect)
        self.client.connect(ObsAddress(HOST, PORT), "")
        self.parker = MediaParker(self.client, clock)

    def asked(self) -> list[tuple[str, Any]]:
        """Each request with the one field that tells it apart."""
        result = []
        for record in self.fake.requests:
            data = record.data
            detail = data.get("mediaAction") or data.get("inputMuted")
            if record.request_type == "SetInputSettings":
                detail = data["inputSettings"].get("speed_percent")
            result.append((record.request_type, detail))
        return result


@pytest.fixture
def rig(fake_clock: FakeClock) -> Iterator[Rig]:
    rig = Rig(fake_clock)
    yield rig
    rig.client.close()


def test_a_slow_clip_gets_its_speed_and_is_muted_before_it_is_restarted(rig: Rig) -> None:
    # The speed goes in with the file, so the clip opens at that speed; the
    # mute comes before the restart, so not one slowed-down sound is heard.
    rig.parker.park(SOURCE, CLIP, speed_percent=50)

    assert rig.asked() == [
        ("SetInputSettings", 50),
        ("SetInputMute", True),
        ("TriggerMediaInputAction", MediaAction.RESTART),
        ("GetMediaInputStatus", None),
        ("TriggerMediaInputAction", MediaAction.PAUSE),
        ("SetMediaInputCursor", None),
    ]
    media = rig.fake.media_of(SOURCE)
    assert media.state == MediaState.PAUSED
    assert media.speed == 0.5


def test_a_clip_can_be_parked_on_any_frame(rig: Rig) -> None:
    rig.parker.park(SOURCE, CLIP, speed_percent=100, at_s=4.2)

    media = rig.fake.media_of(SOURCE)
    assert media.cursor(rig.clock.monotonic()) == pytest.approx(4.2)
    # At normal speed nothing about the sound is touched.
    assert rig.fake.requests_of("SetInputMute") == []


def test_a_source_playout_muted_is_unmuted_at_normal_speed_again(rig: Rig) -> None:
    rig.parker.park(SOURCE, CLIP, speed_percent=25)

    rig.parker.park(SOURCE, CLIP, speed_percent=100)

    assert rig.fake.inputs[SOURCE].muted is False


def test_a_source_the_operator_muted_is_left_muted(rig: Rig) -> None:
    # Muted in OBS's mixer by someone; playout never muted it, so it never unmutes it.
    rig.fake.inputs[SOURCE].muted = True

    rig.parker.park(SOURCE, CLIP, speed_percent=100)

    assert rig.fake.inputs[SOURCE].muted is True


def test_waiting_for_a_paused_source_can_accept_it_paused(rig: Rig) -> None:
    # A live speed change on a paused clip: if OBS applies the speed without
    # reopening the file, the source stays paused, and that is settled too.
    rig.parker.park(SOURCE, CLIP, speed_percent=100)
    started = threading.Event()

    rig.parker.wait_until_playing(SOURCE, started, "clip 1", paused_is_fine=True)

    assert rig.fake.media_of(SOURCE).state == MediaState.PAUSED
