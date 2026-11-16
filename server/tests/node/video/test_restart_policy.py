"""Tests for when a pipeline gives up on ffmpeg, and how long it waits (restart_policy.py)."""

from baja_studio.node.video.restart_policy import (
    STALL_TIMEOUT_SECONDS,
    STARTUP_TIMEOUT_SECONDS,
    RestartBackoff,
    stall_reason,
)


def test_waits_double_from_one_second_up_to_thirty() -> None:
    backoff = RestartBackoff()
    assert [backoff.next_delay() for _ in range(8)] == [1, 2, 4, 8, 16, 30, 30, 30]


def test_after_a_good_run_the_wait_is_short_again() -> None:
    backoff = RestartBackoff()
    for _ in range(5):
        backoff.next_delay()
    backoff.reset()
    assert backoff.next_delay() == 1


def test_a_run_that_never_made_a_picture_is_given_twenty_seconds() -> None:
    # Launched at t=100. Opening a 4K camera and connecting SRT take time.
    assert stall_reason(now=110.0, launched_at=100.0, frames_changed_at=None) is None
    reason = stall_reason(
        now=100.0 + STARTUP_TIMEOUT_SECONDS, launched_at=100.0, frames_changed_at=None
    )
    assert reason == "no picture 20 s after starting"


def test_a_picture_that_stops_for_five_seconds_is_a_stall() -> None:
    # Frames last moved at t=200; at t=204 it is still fine, at t=205 it is stalled.
    assert stall_reason(now=204.0, launched_at=0.0, frames_changed_at=200.0) is None
    reason = stall_reason(
        now=200.0 + STALL_TIMEOUT_SECONDS, launched_at=0.0, frames_changed_at=200.0
    )
    assert reason == "the picture stopped for 5 s"
