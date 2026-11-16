"""Reading ffmpeg's progress reports: frames in and out, drops, bitrate.

With `-progress pipe:2 -stats_period 1` ffmpeg writes a block of
`key=value` lines every second, ending with `progress=continue` (or `=end`):

    frame=1503
    fps=30.00
    total_size=50331648
    dup_frames=0
    drop_frames=2
    progress=continue

`frame` and `total_size` are the live output's (it is the first output);
`dup_frames`/`drop_frames` are what the constant-rate output had to repeat
or skip to keep exactly the camera's rate. So the frames ffmpeg got from the
camera are `frame - dup_frames + drop_frames`. DirectShow's own drops (its
buffer overflowing) never reach ffmpeg's counters; they are counted from
its warnings (`ffmpeg_log.py`) and added here with `count_capture_drop`.

Rates are worked out over the latest `RATE_WINDOW_BLOCKS` blocks (about
three seconds), by the time *we* read them, from the `Clock`: blocks reach
us a little irregularly, and one second alone would make the rates jitter.
"""

import threading
from collections import deque
from dataclasses import dataclass

from baja_studio.core.clock import Clock

RATE_WINDOW_BLOCKS = 4
# The keys we use; any other `key=value` line of a block is read and ignored.
PROGRESS_KEYS = frozenset({"frame", "total_size", "dup_frames", "drop_frames"})
# Every key ffmpeg 6.1-9.0 writes in a progress block, so those lines are not taken for log.
_ALL_PROGRESS_KEYS = PROGRESS_KEYS | {
    "fps", "bitrate", "out_time_us", "out_time_ms", "out_time", "speed", "progress",
}  # fmt: skip


@dataclass(frozen=True)
class ProgressSample:
    """One progress block, stamped with when we read it."""

    at: float
    frames_out: int
    duplicated: int
    dropped: int
    total_bytes: int

    @property
    def frames_in(self) -> int:
        """Frames ffmpeg got from the camera (see the module docstring)."""
        return self.frames_out - self.duplicated + self.dropped


@dataclass(frozen=True)
class ProgressNumbers:
    """What the status shows, from the latest blocks."""

    fps_in: float
    fps_out: float
    dropped_frames: int
    bitrate_kbps: float
    frames_out: int


def is_progress_line(line: str) -> bool:
    """True for a `key=value` line of a progress block (they are not log lines)."""
    key, separator, _ = line.partition("=")
    return bool(separator) and (key in _ALL_PROGRESS_KEYS or key.startswith("stream_"))


def _number(text: str) -> int:
    """ffmpeg writes N/A before it knows a value; that counts as 0."""
    try:
        return int(text)
    except ValueError:
        return 0


class ProgressTracker:
    """Collects one ffmpeg run's progress blocks; fed by its stderr thread, read by anyone."""

    def __init__(self, clock: Clock) -> None:
        """No blocks yet: all rates 0."""
        self._clock = clock
        self._lock = threading.Lock()
        self._block: dict[str, str] = {}
        self._samples: deque[ProgressSample] = deque(maxlen=RATE_WINDOW_BLOCKS)
        self._capture_drops = 0
        self._frames_changed_at: float | None = None

    def feed_line(self, line: str) -> bool:
        """Take one stderr line; True if it was part of a progress block."""
        if not is_progress_line(line):
            return False
        key, _, value = line.partition("=")
        if key in PROGRESS_KEYS:
            self._block[key] = value.strip()
        if key == "progress":
            self._finish_block()
        return True

    def count_capture_drop(self) -> None:
        """DirectShow dropped a frame before ffmpeg saw it."""
        with self._lock:
            self._capture_drops += 1

    def _finish_block(self) -> None:
        block, self._block = self._block, {}
        sample = ProgressSample(
            at=self._clock.monotonic(),
            frames_out=_number(block.get("frame", "")),
            duplicated=_number(block.get("dup_frames", "")),
            dropped=_number(block.get("drop_frames", "")),
            total_bytes=_number(block.get("total_size", "")),
        )
        with self._lock:
            frames_before = self._samples[-1].frames_out if self._samples else 0
            if sample.frames_out > frames_before:
                self._frames_changed_at = sample.at
            self._samples.append(sample)

    def frames_changed_at(self) -> float | None:
        """When (clock time) the output frame count last went up; None before the first frame.

        ffmpeg reports `frame=0` before anything has come out: that is not a frame.
        """
        with self._lock:
            return self._frames_changed_at

    def numbers(self) -> ProgressNumbers:
        """Rates over the latest blocks, and the drop count so far."""
        with self._lock:
            samples, capture_drops = list(self._samples), self._capture_drops
        if not samples:
            return ProgressNumbers(0.0, 0.0, capture_drops, 0.0, 0)
        previous, latest = samples[0], samples[-1]
        dropped = latest.dropped + capture_drops
        if latest.at <= previous.at:
            return ProgressNumbers(0.0, 0.0, dropped, 0.0, latest.frames_out)
        seconds = latest.at - previous.at
        return ProgressNumbers(
            fps_in=(latest.frames_in - previous.frames_in) / seconds,
            fps_out=(latest.frames_out - previous.frames_out) / seconds,
            dropped_frames=dropped,
            bitrate_kbps=(latest.total_bytes - previous.total_bytes) * 8 / 1000 / seconds,
            frames_out=latest.frames_out,
        )
