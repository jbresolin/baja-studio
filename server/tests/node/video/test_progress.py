"""Tests for reading ffmpeg's progress reports (node/video/progress.py).

Every second ffmpeg writes a block of lines like `frame=1503` ending with
`progress=continue`. The tracker turns them into frames per second in and
out, dropped frames and bitrate. Time comes from the fake clock: the test
says how much time passes between blocks.
"""

from baja_studio.node.video.progress import ProgressTracker, is_progress_line
from tests.conftest import FakeClock


def feed_block(
    tracker: ProgressTracker,
    frames: int,
    size: int,
    dropped: int = 0,
    duplicated: int = 0,
) -> None:
    """Feed one progress block, the way ffmpeg 6.1 writes it (with keys we ignore)."""
    for line in (
        f"frame={frames}",
        "fps=30.00",
        "stream_0_0_q=23.0",
        "bitrate=8000.0kbits/s",
        f"total_size={size}",
        "out_time_us=5000000",
        f"dup_frames={duplicated}",
        f"drop_frames={dropped}",
        "speed=1x",
        "progress=continue",
    ):
        assert tracker.feed_line(line)


def test_progress_lines_are_told_apart_from_log_lines() -> None:
    assert is_progress_line("frame=12")
    assert is_progress_line("stream_0_0_q=-1.0")
    assert is_progress_line("progress=end")
    # A warning that happens to contain "=" is not a progress line.
    assert not is_progress_line("[dshow @ 0x1] real-time buffer too full! frame dropped!")
    assert not is_progress_line("Error opening output srt://x?streamid=publish:a")


def test_before_any_block_everything_is_zero(fake_clock: FakeClock) -> None:
    tracker = ProgressTracker(fake_clock)
    numbers = tracker.numbers()
    assert (numbers.fps_in, numbers.fps_out, numbers.bitrate_kbps) == (0.0, 0.0, 0.0)
    assert tracker.frames_changed_at() is None


def test_rates_are_worked_out_between_blocks(fake_clock: FakeClock) -> None:
    tracker = ProgressTracker(fake_clock)
    feed_block(tracker, frames=30, size=1_000_000)
    # One block only: no rate yet (a rate needs two readings).
    assert tracker.numbers().fps_out == 0.0
    fake_clock.advance(1.0)
    # One second later: 30 more frames and 1 MB more written.
    feed_block(tracker, frames=60, size=2_000_000)
    numbers = tracker.numbers()
    assert numbers.fps_out == 30.0
    assert numbers.fps_in == 30.0
    # 1 000 000 bytes in a second = 8 000 kilobits per second.
    assert numbers.bitrate_kbps == 8000.0
    assert numbers.frames_out == 60


def test_rates_are_averaged_over_the_last_few_blocks(fake_clock: FakeClock) -> None:
    # Blocks reach us irregularly (0.8 s, then 1.2 s apart). Averaging over the
    # last four smooths that out: 90 frames in 3 s is 30 fps.
    tracker = ProgressTracker(fake_clock)
    for frames, wait in ((30, 1.0), (54, 0.8), (90, 1.2), (120, 0.0)):
        feed_block(tracker, frames=frames, size=0)
        fake_clock.advance(wait)
    assert tracker.numbers().fps_out == 30.0


def test_frames_in_count_what_the_output_had_to_drop_or_repeat(fake_clock: FakeClock) -> None:
    # The output keeps exactly 30 fps. If the camera sent 33 frames in a second,
    # 3 were dropped; if it sent 27, 3 were repeated. Frames in = out - repeated + dropped.
    tracker = ProgressTracker(fake_clock)
    feed_block(tracker, frames=0, size=0)
    fake_clock.advance(1.0)
    feed_block(tracker, frames=30, size=0, dropped=3)
    assert tracker.numbers().fps_in == 33.0
    fake_clock.advance(1.0)
    feed_block(tracker, frames=60, size=0, dropped=3, duplicated=3)
    # Over the two seconds: 60 out, 3 repeated, 3 dropped -> 60 in.
    assert tracker.numbers().fps_in == 30.0


def test_directshow_drops_are_added_to_the_drop_count(fake_clock: FakeClock) -> None:
    # DirectShow loses frames before ffmpeg sees them; the pipeline counts its
    # warnings and tells the tracker, so the status shows every lost frame.
    tracker = ProgressTracker(fake_clock)
    feed_block(tracker, frames=30, size=0, dropped=2)
    tracker.count_capture_drop()
    tracker.count_capture_drop()
    assert tracker.numbers().dropped_frames == 4


def test_not_yet_known_values_count_as_zero(fake_clock: FakeClock) -> None:
    # At the very start ffmpeg writes N/A for sizes it does not know yet.
    tracker = ProgressTracker(fake_clock)
    for line in ("frame=0", "total_size=N/A", "progress=continue"):
        tracker.feed_line(line)
    assert tracker.numbers().frames_out == 0


def test_the_time_frames_last_moved_is_kept_for_the_stall_watchdog(fake_clock: FakeClock) -> None:
    tracker = ProgressTracker(fake_clock)
    # ffmpeg reports "frame=0" before the first picture is out: not a frame yet.
    feed_block(tracker, frames=0, size=0)
    assert tracker.frames_changed_at() is None
    fake_clock.advance(1.0)
    feed_block(tracker, frames=30, size=0)
    assert tracker.frames_changed_at() == 1.0
    fake_clock.advance(1.0)
    # The count did not move: the time stays where it was (1.0), so a watchdog
    # can see the picture has stopped for a second.
    feed_block(tracker, frames=30, size=0)
    assert tracker.frames_changed_at() == 1.0
