"""Tests for the controller's frame monitor (cameras/inputs/frame_monitor.py).

The monitor remembers the controller's recent frames for the configuration
page: the last 20 decoded (published at most ten times a second) and the
last 100 as raw hex (read on request, to make test fixtures from). Time is
passed in as a plain number, so no clock is needed.
"""

from baja_studio.cameras.inputs.frame_monitor import FrameMonitor, raw_bytes
from baja_studio.cameras.inputs.pelco_d import FrameParser, interpret
from baja_studio.fakes.fake_serial import SNIFFED_FRAMES

TS = "2026-11-03T12:00:00+00:00"


def record(monitor: FrameMonitor, name: str, times: int = 1) -> None:
    """Feed a sniffed frame (by fixture name) into the monitor, `times` times."""
    for _ in range(times):
        for frame in FrameParser().feed(SNIFFED_FRAMES[name]):
            monitor.record(frame, interpret(frame), TS)


def test_a_frame_is_shown_decoded_with_its_raw_bytes() -> None:
    monitor = FrameMonitor()

    record(monitor, "up_right_full")

    assert monitor.due(now=0.0) == [
        {
            "ts": TS,
            "hex": SNIFFED_FRAMES["up_right_full"].hex(" ").upper(),
            "action": "move",
            "address": 1,
            "pan": 1.0,
            "tilt": 1.0,
            "zoom": 0,
        }
    ]


def test_each_kind_of_frame_is_named() -> None:
    monitor = FrameMonitor()

    record(monitor, "stop")
    record(monitor, "recall_preset_1")
    record(monitor, "call_2")

    shown = monitor.due(now=0.0) or []
    # Newest first, as the page lists them.
    assert [frame["action"] for frame in shown] == ["call", "recall_preset", "stop"]
    assert shown[0]["n"] == 2
    assert shown[1]["n"] == 0  # the controller's preset 1 is preset 0


def test_the_raw_bytes_are_rebuilt_exactly_as_they_arrived() -> None:
    for name, data in SNIFFED_FRAMES.items():
        (frame,) = FrameParser().feed(data)
        assert raw_bytes(frame) == data, name


def test_only_the_last_twenty_frames_are_shown_and_the_last_hundred_kept_raw() -> None:
    monitor = FrameMonitor()

    record(monitor, "stop", times=150)

    assert len(monitor.due(now=0.0) or []) == 20
    assert len(monitor.raw_frames()) == 100


def test_the_list_is_published_at_most_ten_times_a_second() -> None:
    monitor = FrameMonitor()
    record(monitor, "stop")
    assert monitor.due(now=0.0) is not None

    # More frames 50 ms later: not yet; at 100 ms: yes.
    record(monitor, "pan_right_full")
    assert monitor.due(now=0.05) is None
    assert monitor.due(now=0.1) is not None


def test_nothing_is_published_when_no_frame_arrived() -> None:
    monitor = FrameMonitor()
    record(monitor, "stop")
    monitor.due(now=0.0)

    assert monitor.due(now=5.0) is None
