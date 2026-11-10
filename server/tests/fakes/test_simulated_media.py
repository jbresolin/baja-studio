"""Tests for the fake OBS's media playback (fakes/simulated_media.py).

The media source is plain code told the time on every call, so these tests
just pass numbers for "now": no clock, no threads.
"""

import pytest

from baja_studio.adapters.obs_protocol import MediaState
from baja_studio.fakes.simulated_media import SimulatedMedia


def playing(duration: float = 10.0, at: float = 0.0) -> SimulatedMedia:
    """A source that loaded a `duration`-second file and started it at time `at`."""
    media = SimulatedMedia()
    media.load("clip.mp4", duration, at)
    media.advance(at)
    return media


def test_loading_a_file_opens_it_and_the_next_advance_starts_it_from_the_top() -> None:
    # Real OBS opens a new file first and starts it a moment later.
    media = SimulatedMedia()
    media.load("clip.mp4", 10.0, 5.0)
    assert media.state == MediaState.OPENING
    assert not media.is_seekable

    assert media.advance(5.0) == "started"

    assert media.state == MediaState.PLAYING
    assert media.cursor(7.0) == pytest.approx(2.0)


def test_a_stuck_source_never_finishes_opening() -> None:
    media = SimulatedMedia(stuck=True)
    media.load("clip.mp4", 10.0, 0.0)

    assert media.advance(100.0) is None
    assert media.state == MediaState.OPENING


def test_pausing_an_opening_source_does_nothing() -> None:
    # There is no frame to freeze yet; OBS leaves it opening.
    media = SimulatedMedia()
    media.load("clip.mp4", 10.0, 0.0)

    media.pause(0.0)

    assert media.state == MediaState.OPENING


def test_loading_no_file_empties_the_source() -> None:
    media = playing()

    media.load("", None, 1.0)

    assert media.state == MediaState.NONE


def test_pausing_freezes_the_position() -> None:
    media = playing()

    media.pause(3.0)

    assert media.state == MediaState.PAUSED
    assert media.cursor(100.0) == pytest.approx(3.0)


def test_playing_a_paused_file_resumes_where_it_was() -> None:
    media = playing()
    media.pause(3.0)

    media.play(10.0)

    assert media.cursor(11.0) == pytest.approx(4.0)


def test_playing_an_ended_file_starts_it_again() -> None:
    media = playing()
    media.advance(10.0)

    media.play(20.0)
    media.advance(20.0)

    assert media.state == MediaState.PLAYING
    assert media.cursor(21.0) == pytest.approx(1.0)


def test_restart_goes_back_to_the_top() -> None:
    media = playing()

    media.restart(4.0)

    assert media.state == MediaState.OPENING
    assert media.cursor(4.0) == 0.0


def test_stop_rewinds_and_stops() -> None:
    media = playing()

    media.stop(4.0)

    assert media.state == MediaState.STOPPED
    assert media.cursor(9.0) == 0.0


def test_seeking_keeps_the_play_state_and_stays_inside_the_file() -> None:
    media = playing()
    media.pause(1.0)

    media.seek(25.0, 2.0)

    assert media.state == MediaState.PAUSED
    assert media.cursor(2.0) == 10.0


def test_the_end_is_noticed_exactly_once() -> None:
    # advance() says "it just ended" once; later calls stay quiet so the fake
    # sends a single MediaInputPlaybackEnded.
    media = playing()

    assert media.advance(9.9) is None
    assert media.advance(10.0) == "ended"
    assert media.advance(11.0) is None
    assert media.state == MediaState.ENDED
    assert media.cursor(50.0) == 10.0


def test_the_position_never_passes_the_end() -> None:
    assert playing().cursor(30.0) == 10.0


def test_at_half_speed_the_position_moves_half_a_second_per_second() -> None:
    # speed_percent 50: the position is in the file's own seconds.
    media = playing()
    media.speed = 0.5

    assert media.cursor(4.0) == pytest.approx(2.0)
    # A 10 s file at half speed ends after 20 s.
    assert media.advance(19.0) is None
    assert media.advance(20.0) == "ended"


def test_a_reload_opens_the_file_again_and_starts_it_from_the_top() -> None:
    media = playing()

    media.reload(3.0)

    assert media.state == MediaState.OPENING
    assert media.advance(3.0) == "started"
    assert media.cursor(3.0) == 0.0


def test_actions_on_an_empty_source_do_nothing() -> None:
    media = SimulatedMedia()

    media.play(1.0)
    media.restart(1.0)
    media.stop(1.0)

    assert media.state == MediaState.NONE
