"""Watching what a pipeline sends, as tiny grey pictures, to time window moves and check them.

A second ffmpeg reads the pipeline's output (from MediaMTX over RTSP, or
from a local UDP sink in the benchmark), decodes it with as little buffering
as it allows, and writes each frame shrunk to `PROBE_WIDTH` x
`PROBE_HEIGHT` grey pixels on stdout. We stamp each one with when we read
it. That is enough to tell where the window is on a test picture (its
brightness), and when a move first shows (the picture changes).

What it measures is the pipeline's latency plus the probe's own decode,
with no network in between: an upper bound of what the node adds.
"""

import statistics
import subprocess
import threading
import time
from collections import deque
from dataclasses import dataclass

from baja_studio.node.video.ffmpeg_process import NO_CONSOLE_WINDOW

PROBE_WIDTH = 32
PROBE_HEIGHT = 18
FRAME_BYTES = PROBE_WIDTH * PROBE_HEIGHT
FRAMES_KEPT = 300


@dataclass(frozen=True)
class ProbeFrame:
    """One frame as the probe saw it: when it was read and its grey pixels, row by row."""

    read_at: float
    pixels: bytes

    def mean(self) -> float:
        """Average brightness, 0..255."""
        return sum(self.pixels) / len(self.pixels)

    def difference(self, other: "ProbeFrame") -> float:
        """Average absolute brightness difference from another frame, 0..255."""
        return sum(abs(a - b) for a, b in zip(self.pixels, other.pixels, strict=True)) / len(
            self.pixels
        )


def probe_command(ffmpeg_path: str, url: str) -> list[str]:
    """The probe's command line for `url` (RTSP is read over TCP)."""
    transport = ["-rtsp_transport", "tcp"] if url.startswith("rtsp://") else []
    return [
        ffmpeg_path, "-hide_banner", "-nostdin", "-loglevel", "error",
        "-fflags", "nobuffer", "-flags", "low_delay",
        "-probesize", "500000", "-analyzeduration", "500000",
        *transport, "-i", url,
        "-vf", f"scale={PROBE_WIDTH}:{PROBE_HEIGHT},format=gray",
        "-f", "rawvideo", "pipe:1",
    ]  # fmt: skip


class PictureProbe:
    """A running probe; `frames()` are the latest ones read, newest last."""

    def __init__(self, ffmpeg_path: str, url: str) -> None:
        """Start reading `url` now."""
        self._process = subprocess.Popen(
            probe_command(ffmpeg_path, url),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            creationflags=NO_CONSOLE_WINDOW,
        )
        self._lock = threading.Lock()
        self._frames: deque[ProbeFrame] = deque(maxlen=FRAMES_KEPT)
        self._reader = threading.Thread(target=self._read, name="probe-reader", daemon=True)
        self._reader.start()

    def _read(self) -> None:
        stdout = self._process.stdout
        assert stdout is not None
        while len(data := stdout.read(FRAME_BYTES)) == FRAME_BYTES:
            frame = ProbeFrame(time.monotonic(), data)
            with self._lock:
                self._frames.append(frame)

    def frames(self) -> list[ProbeFrame]:
        """The frames kept so far (the last `FRAMES_KEPT`), oldest first."""
        with self._lock:
            return list(self._frames)

    def latest(self) -> ProbeFrame | None:
        """The newest frame, or None before the first."""
        with self._lock:
            return self._frames[-1] if self._frames else None

    def frames_after(self, moment: float) -> list[ProbeFrame]:
        """Frames read after `moment` (a `time.monotonic()` value)."""
        return [frame for frame in self.frames() if frame.read_at > moment]

    def close(self) -> None:
        """Stop the probe and reap it."""
        if self._process.poll() is None:
            self._process.terminate()
        try:
            self._process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self._process.kill()
            self._process.wait()
        self._reader.join()
        assert self._process.stdout is not None
        self._process.stdout.close()


def first_changed_frame(
    frames: list[ProbeFrame], reference: ProbeFrame, threshold: float
) -> ProbeFrame | None:
    """The first of `frames` that differs from `reference` by more than `threshold`."""
    for frame in frames:
        if frame.difference(reference) > threshold:
            return frame
    return None


def summarise_ms(values: list[float]) -> str:
    """ "median 62 ms, max 75 ms (20 moves)" for a list of latencies in ms."""
    if not values:
        return "not measured"
    return (
        f"median {statistics.median(values):.0f} ms, max {max(values):.0f} ms ({len(values)} moves)"
    )
