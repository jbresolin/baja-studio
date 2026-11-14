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
from baja_studio.playout.parking import POLL_SECONDS, MediaParker
from tests.conftest import FakeClock, eventually

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


def test_a_slow_clip_gets_its_speed_and_is_muted_before_it_is_loaded(rig: Rig) -> None:
    # The speed goes in with the file, so the clip opens at that speed; the
    # mute comes before the load, so not one slowed-down sound is heard.
    rig.parker.park(SOURCE, CLIP, speed_percent=50)

    assert rig.asked() == [
        # Is it stopped? (Then it would need PLAY: see the stopped-source test.)
        ("GetMediaInputStatus", None),
        ("GetInputSettings", None),
        ("SetInputMute", True),
        ("SetInputSettings", 50),
        ("GetMediaInputStatus", None),
        ("TriggerMediaInputAction", MediaAction.PAUSE),
        ("GetMediaInputStatus", None),
        ("SetMediaInputCursor", None),
        ("GetMediaInputStatus", None),
    ]
    media = rig.fake.media_of(SOURCE)
    assert media.state == MediaState.PAUSED
    assert media.speed == 0.5


def test_a_clip_can_be_parked_on_a_keyframe(rig: Rig) -> None:
    # The fake OBS, like the pipeline's clips since LIVESTACK-56, has a
    # keyframe every 0.5 s; 4.0 s is one, so the seek lands exactly there.
    rig.parker.park(SOURCE, CLIP, speed_percent=100, at_s=4.0)

    media = rig.fake.media_of(SOURCE)
    assert media.cursor(rig.clock.monotonic()) == pytest.approx(4.0)
    # At normal speed nothing about the sound is touched.
    assert rig.fake.requests_of("SetInputMute") == []


def test_a_clip_loaded_into_a_stopped_source_is_sent_play(rig: Rig) -> None:
    # After a take, playout stops the source it left. Real OBS does not
    # start a file loaded into a stopped source (the probe on the studio
    # PC, LIVESTACK-56): without PLAY the cue would wait 3 s and fail with
    # "did not start ... OBS_MEDIA_STATE_STOPPED".
    rig.parker.park(SOURCE, CLIP, speed_percent=100)
    rig.client.trigger_media_input_action(SOURCE, MediaAction.STOP)
    eventually(lambda: rig.fake.media_of(SOURCE).state == MediaState.STOPPED)
    before = len(rig.fake.requests)

    rig.parker.park(SOURCE, {**CLIP, "file_path": "/clips/2.mp4"}, speed_percent=100)

    # PLAY right after the new file, then the usual pause on the first frame.
    actions = [
        record.data["mediaAction"]
        for record in rig.fake.requests[before:]
        if record.request_type == "TriggerMediaInputAction"
    ]
    assert actions == [MediaAction.PLAY, MediaAction.PAUSE]
    media = rig.fake.media_of(SOURCE)
    assert media.state == MediaState.PAUSED
    assert media.file == "/clips/2.mp4"


def test_a_playing_source_is_not_sent_play_after_loading(rig: Rig) -> None:
    # A source that is playing (or empty) starts by itself on a new file;
    # PLAY is only for one that was stopped.
    rig.parker.park(SOURCE, CLIP, speed_percent=100)

    actions = [
        record.data["mediaAction"] for record in rig.fake.requests_of("TriggerMediaInputAction")
    ]
    assert MediaAction.PLAY not in actions


def test_a_source_playout_muted_is_unmuted_at_normal_speed_again(rig: Rig) -> None:
    rig.parker.park(SOURCE, CLIP, speed_percent=25)

    rig.parker.park(SOURCE, CLIP, speed_percent=100)

    assert rig.fake.inputs[SOURCE].muted is False


def test_a_source_the_operator_muted_is_left_muted(rig: Rig) -> None:
    # Muted in OBS's mixer by someone; playout never muted it, so it never unmutes it.
    rig.fake.inputs[SOURCE].muted = True

    rig.parker.park(SOURCE, CLIP, speed_percent=100)

    assert rig.fake.inputs[SOURCE].muted is True


def test_a_source_already_holding_the_file_is_emptied_first_so_obs_opens_it_again(
    rig: Rig,
) -> None:
    # OBS reopens a source only when its settings change: loading the same
    # file at the same speed again would open nothing and send no start.
    rig.parker.park(SOURCE, CLIP, speed_percent=100)
    before = len(rig.fake.requests)

    rig.parker.park(SOURCE, CLIP, speed_percent=100, at_s=2.0)

    loads = [
        record.data["inputSettings"].get("local_file")
        for record in rig.fake.requests[before:]
        if record.request_type == "SetInputSettings"
    ]
    assert loads == ["", CLIP["file_path"]]
    assert rig.fake.media_of(SOURCE).cursor(rig.clock.monotonic()) == pytest.approx(2.0)


def test_a_seek_obs_has_not_carried_out_is_sent_again(rig: Rig) -> None:
    # OBS accepts a seek but has not moved yet (or ignored it): the position
    # read back is wrong, so the seek goes again (here OBS obeys the second).
    rig.parker.park(SOURCE, CLIP, speed_percent=100)
    media = rig.fake.media_of(SOURCE)
    ignored = {"count": 0}
    real_seek = media.seek

    def ignore_first(cursor_s: float, now: float, seek_s: float = 0.0) -> None:
        ignored["count"] += 1
        if ignored["count"] > 1:
            real_seek(cursor_s, now, seek_s)

    media.seek = ignore_first
    finished = threading.Event()
    result: list[float] = []

    def seek_in_background() -> None:
        result.append(rig.parker.seek_confirmed(SOURCE, CLIP, 3.0, rig.clock.monotonic() + 2.0))
        finished.set()

    def between_polls() -> bool:
        # Asleep in the short wait between two status reads (not inside a
        # request, which waits up to 2 s for OBS's answer).
        wake_ups = rig.clock.wake_up_times()
        return bool(wake_ups) and wake_ups[0] <= rig.clock.monotonic() + POLL_SECONDS + 1e-6

    # The seek waits on the fake clock, so it runs on its own thread while
    # the test moves time, one poll at a time.
    worker = threading.Thread(target=seek_in_background)
    worker.start()
    while not finished.is_set():
        eventually(lambda: between_polls() or finished.is_set())
        if not finished.is_set():
            rig.clock.advance(POLL_SECONDS)
    worker.join(timeout=5)

    assert ignored["count"] == 2
    assert result == [pytest.approx(3.0)]


# --- seeking a playing source (slow-motion replays, LIVESTACK-56) ------------------------------


def seek_while_playing(rig: Rig, at_s: float) -> float:
    """Load CLIP, let it play, then `seek_playing` to `at_s`; returns where it landed.

    The seek polls OBS on the fake clock, so it runs on its own thread while
    the test moves time forward one poll at a time.
    """
    rig.parker.load(SOURCE, CLIP, 100, rig.clock.monotonic() + 3.0)
    rig.clock.advance(1.0)
    result: list[float] = []
    finished = threading.Event()

    def seek_in_background() -> None:
        result.append(rig.parker.seek_playing(SOURCE, CLIP, at_s, 1.0, rig.clock.monotonic() + 3.0))
        finished.set()

    def between_polls() -> bool:
        # Asleep in the short wait between two status reads, not inside a
        # request (which waits up to 2 s for OBS's answer and must not see
        # time move).
        wake_ups = rig.clock.wake_up_times()
        return bool(wake_ups) and wake_ups[0] <= rig.clock.monotonic() + POLL_SECONDS + 1e-6

    worker = threading.Thread(target=seek_in_background)
    worker.start()
    while not finished.is_set():
        eventually(lambda: between_polls() or finished.is_set())
        if not finished.is_set():
            rig.clock.advance(POLL_SECONDS)
    worker.join(timeout=5)
    (landed,) = result
    return landed


def test_a_seek_while_playing_lands_on_the_keyframe_before_and_says_where(rig: Rig) -> None:
    # Asked for 6.3 s, OBS goes to the keyframe at 6.0 s (one every 0.5 s).
    # That is a landing, not a failure: the replay starts there.
    landed = seek_while_playing(rig, 6.3)

    assert landed == pytest.approx(6.0, abs=0.001)
    assert rig.fake.media_of(SOURCE).state == MediaState.PLAYING


def test_a_seek_to_an_old_clip_with_far_keyframes_lands_seconds_early(
    fake_clock: FakeClock,
) -> None:
    # Clips rendered before LIVESTACK-56 have a keyframe every 8.333 s. A
    # replay aimed at 10 s starts at 8.333 s: early, but on air, not an error.
    rig = Rig(fake_clock)
    rig.fake.media_of(SOURCE).keyframe_interval_s = 8.333
    try:
        landed = seek_while_playing(rig, 10.0)
    finally:
        rig.client.close()

    assert landed == pytest.approx(8.333, abs=0.001)


def test_a_seek_to_a_keyframe_close_to_where_it_plays_counts_once_settled(rig: Rig) -> None:
    # The source plays at about 1 s; the keyframe before 1.2 s is 1.0 s, so
    # no jump shows. It counts as landed after SEEK_SETTLE_SECONDS.
    landed = seek_while_playing(rig, 1.2)

    assert landed == pytest.approx(1.0, abs=0.35)
