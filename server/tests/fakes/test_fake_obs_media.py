"""Tests for the fake OBS's media timeline (fakes/fake_obs_media.py), and how the fake uses it.

The timeline alone is plain code given times as numbers. The last tests
drive a whole `FakeObs` with a seed, the way the race tests do: requests
as raw messages, time moved by the fake clock, `tick()` to let OBS work.
"""

from typing import Any

import pytest

from baja_studio.adapters.obs_protocol import MediaState
from baja_studio.fakes.fake_obs import FakeObs
from baja_studio.fakes.fake_obs_media import MediaTimeline
from baja_studio.fakes.obs_timing import ObsTiming, TimingRanges
from tests.conftest import FakeClock
from tests.fakes.test_fake_obs import Peer, ask, connect

SOURCE = "baja_program_a"
# Fixed delays, so the tests can say exactly when things happen.
FIXED = TimingRanges(
    opening_s=(0.02, 0.02), first_frame_s=(0.3, 0.3), seek_s=(0.05, 0.05), event_s=(0.1, 0.1)
)


def test_without_timing_events_are_due_at_once() -> None:
    timeline = MediaTimeline(None)

    timeline.media_event(5.0, SOURCE, "MediaInputPlaybackStarted")

    assert timeline.due_events(5.0) == [("MediaInputPlaybackStarted", {"inputName": SOURCE})]


def test_events_are_held_until_due_and_one_inputs_events_keep_their_order() -> None:
    # Input a's events take 0.1 s; b's are due in between, so they interleave.
    timeline = MediaTimeline(ObsTiming(1, FIXED))
    timeline.media_event(1.0, "a", "MediaInputPlaybackStarted")
    timeline.media_event(1.05, "b", "MediaInputPlaybackStarted")
    timeline.media_event(1.06, "a", "MediaInputPlaybackEnded")

    assert timeline.due_events(1.09) == []
    assert timeline.next_event_at() == pytest.approx(1.1)
    assert [name for name, _ in timeline.due_events(1.2)] == [
        "MediaInputPlaybackStarted",
        "MediaInputPlaybackStarted",
        "MediaInputPlaybackEnded",
    ]


def test_only_black_moments_are_kept() -> None:
    timeline = MediaTimeline(None)

    timeline.look_at_program(1.0, "Playback A", is_black=False)
    timeline.look_at_program(2.0, "Playback A", is_black=True)

    assert timeline.black_on_program == [(2.0, "Playback A")]


@pytest.fixture
def seeded(fake_clock: FakeClock) -> FakeObs:
    return FakeObs.with_playout_scenes(fake_clock, timing=ObsTiming(1, FIXED))


def load(fake: FakeObs, peer: Peer, **settings: Any) -> None:
    ask(fake, peer, "SetInputSettings", inputName=SOURCE, inputSettings=settings)


def test_with_timing_status_says_playing_before_the_first_frame(
    seeded: FakeObs, fake_clock: FakeClock
) -> None:
    # Real OBS: "opening", then "playing" with nothing to show yet, then the
    # first frame and MediaInputPlaybackStarted (sent 0.1 s later here).
    peer = connect(seeded)
    load(seeded, peer, local_file="x.mp4")
    media = seeded.media_of(SOURCE)

    fake_clock.advance(0.02)
    seeded.tick()
    assert (media.state, media.has_frame) == (MediaState.PLAYING, False)

    fake_clock.advance(0.3)
    seeded.tick()
    assert media.has_frame
    assert peer.events() == []
    assert seeded.next_change_at() == pytest.approx(0.42)

    fake_clock.advance(0.1)
    seeded.tick()
    assert peer.events() == ["MediaInputPlaybackStarted"]


def test_a_seek_before_the_first_frame_is_accepted_and_ignored(
    seeded: FakeObs, fake_clock: FakeClock
) -> None:
    peer = connect(seeded)
    load(seeded, peer, local_file="x.mp4")
    fake_clock.advance(0.02)
    seeded.tick()

    status = ask(seeded, peer, "SetMediaInputCursor", inputName=SOURCE, mediaCursor=4000)

    assert status["requestStatus"]["result"] is True
    fake_clock.advance(0.5)
    seeded.tick()
    # It started from the top as if nobody had asked.
    assert seeded.media_of(SOURCE).cursor(fake_clock.monotonic()) < 0.5


def test_a_seek_lands_late_and_status_shows_the_old_position_until_then(
    seeded: FakeObs, fake_clock: FakeClock
) -> None:
    peer = connect(seeded)
    load(seeded, peer, local_file="x.mp4")
    fake_clock.advance(0.32)
    seeded.tick()
    ask(seeded, peer, "TriggerMediaInputAction", inputName=SOURCE, mediaAction="pause")

    ask(seeded, peer, "SetMediaInputCursor", inputName=SOURCE, mediaCursor=4000)
    before = ask(seeded, peer, "GetMediaInputStatus", inputName=SOURCE)["responseData"]
    fake_clock.advance(0.05)
    after = ask(seeded, peer, "GetMediaInputStatus", inputName=SOURCE)["responseData"]

    assert before["mediaCursor"] == 0
    assert after["mediaCursor"] == 4000


def test_identical_settings_reopen_nothing_and_send_no_event(
    seeded: FakeObs, fake_clock: FakeClock
) -> None:
    peer = connect(seeded)
    load(seeded, peer, local_file="x.mp4", speed_percent=50)
    fake_clock.advance(1.0)
    seeded.tick()

    load(seeded, peer, local_file="x.mp4", speed_percent=50)
    fake_clock.advance(1.0)
    seeded.tick()

    assert peer.events() == ["MediaInputPlaybackStarted"]
    assert seeded.media_of(SOURCE).state == MediaState.PLAYING


def test_program_showing_a_source_without_a_frame_is_recorded_as_black(
    seeded: FakeObs, fake_clock: FakeClock
) -> None:
    peer = connect(seeded)
    load(seeded, peer, local_file="x.mp4")

    ask(seeded, peer, "SetCurrentProgramScene", sceneName="Playback A")
    fake_clock.advance(0.5)
    seeded.tick()

    assert seeded.timeline.black_on_program == [(0.0, "Playback A")]
    assert seeded.timeline.program_cuts == [(0.0, "Playback A")]
    assert not seeded.program_is_black()
