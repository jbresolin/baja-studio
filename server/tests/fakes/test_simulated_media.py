"""Tests for the fake OBS's media playback (fakes/simulated_media.py).

The media source is plain code told the time on every call, so these tests
just pass numbers for "now": no clock, no threads.
"""

import pytest

from baja_studio.adapters.obs_protocol import MediaState
from baja_studio.fakes.simulated_media import SimulatedMedia


def playing(duration: float = 10.0, at: float = 0.0) -> SimulatedMedia:
    """A source that loaded a `duration`-second file at time `at`."""
    media = SimulatedMedia()
    media.load("clip.mp4", duration, at)
    return media


def test_loading_a_file_starts_playing_it_from_the_top() -> None:
    # Real OBS starts a media source as soon as its file changes.
    media = playing(at=5.0)

    assert media.state == MediaState.PLAYING
    assert media.cursor(7.0) == pytest.approx(2.0)


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

    assert media.state == MediaState.PLAYING
    assert media.cursor(21.0) == pytest.approx(1.0)


def test_restart_goes_back_to_the_top() -> None:
    media = playing()

    media.restart(4.0)

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

    assert media.advance(9.9) is False
    assert media.advance(10.0) is True
    assert media.advance(11.0) is False
    assert media.state == MediaState.ENDED
    assert media.cursor(50.0) == 10.0


def test_the_position_never_passes_the_end() -> None:
    assert playing().cursor(30.0) == 10.0


def test_actions_on_an_empty_source_do_nothing() -> None:
    media = SimulatedMedia()

    media.play(1.0)
    media.restart(1.0)
    media.stop(1.0)

    assert media.state == MediaState.NONE
