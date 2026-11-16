"""Tests for the picture probe's arithmetic (node/video/probe_reader.py).

The probe itself runs ffmpeg and is used by the rig tests and the bench;
here only the parts that need no ffmpeg: comparing tiny grey pictures,
finding the first one that changed, and the command line.
"""

from baja_studio.node.video.probe_reader import (
    FRAME_BYTES,
    ProbeFrame,
    first_changed_frame,
    probe_command,
    summarise_ms,
)


def flat(level: int, at: float = 0.0) -> ProbeFrame:
    """A probe frame of one grey level everywhere, read at time `at`."""
    return ProbeFrame(at, bytes([level]) * FRAME_BYTES)


def test_mean_and_difference_are_in_grey_levels() -> None:
    assert flat(100).mean() == 100.0
    assert flat(100).difference(flat(130)) == 30.0


def test_the_first_frame_that_differs_enough_is_found() -> None:
    reference = flat(100)
    # Small noise (3 levels) is not a move; the jump to 200 is.
    frames = [flat(103, at=1.0), flat(200, at=2.0), flat(201, at=3.0)]
    found = first_changed_frame(frames, reference, threshold=8.0)
    assert found is not None and found.read_at == 2.0
    assert first_changed_frame(frames[:1], reference, threshold=8.0) is None


def test_rtsp_is_read_over_tcp_and_output_is_tiny_grey_frames() -> None:
    command = probe_command("ffmpeg", "rtsp://127.0.0.1:8554/laptop1")
    assert command[command.index("-rtsp_transport") + 1] == "tcp"
    assert "scale=32:18,format=gray" in command
    assert "-rtsp_transport" not in probe_command("ffmpeg", "udp://127.0.0.1:5000")


def test_latencies_are_summarised_as_median_and_max() -> None:
    assert summarise_ms([50.0, 70.0, 60.0]) == "median 60 ms, max 70 ms (3 moves)"
    assert summarise_ms([]) == "not measured"
