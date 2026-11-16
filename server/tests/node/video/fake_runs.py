"""Fakes for `CameraPipeline`'s unit tests: a scripted ffmpeg run, its launcher, and senders.

`FakeRun` is one "ffmpeg" the test drives by hand: it writes progress
blocks, log lines and snapshots through the same callbacks the real
process's reader threads use, and exits when told to. The lines it writes
are the real ones (see `test_progress.py` and `test_ffmpeg_log.py` for
where they come from).
"""

import threading

from baja_studio.node.video.crop_commands import CropPixels
from baja_studio.node.video.encoders import ProbeResult
from baja_studio.node.video.ffmpeg_process import BytesSink, FfmpegNotFoundError, LineSink


class FakeRun:
    """One scripted ffmpeg run."""

    def __init__(self, command: list[str], on_line: LineSink, on_bytes: BytesSink) -> None:
        self.command = command
        self._on_line = on_line
        self._on_bytes = on_bytes
        self._exited = threading.Event()
        self._exit_code: int | None = None
        self.stopped = False
        self.frames = 0

    @property
    def exited(self) -> threading.Event:
        return self._exited

    @property
    def exit_code(self) -> int | None:
        return self._exit_code

    def stop(self) -> None:
        """What the pipeline calls; a run still going ends as if terminated."""
        self.stopped = True
        if not self._exited.is_set():
            self._exit_code = -15
            self._exited.set()

    # --- what the test makes "ffmpeg" do ---------------------------------------------

    def progress(self, new_frames: int = 30, bytes_per_frame: int = 33_000) -> None:
        """One progress block: `new_frames` more frames out (30 = one second at 30 fps)."""
        self.frames += new_frames
        for line in (
            f"frame={self.frames}",
            f"total_size={self.frames * bytes_per_frame}",
            "dup_frames=0",
            "drop_frames=0",
            "progress=continue",
        ):
            self._on_line(line)

    def log(self, line: str) -> None:
        """One warning or error line on stderr."""
        self._on_line(line)

    def snapshot(self, jpeg: bytes) -> None:
        """One snapshot on stdout, as the mpjpeg muxer writes it."""
        header = f"--ffmpeg\r\nContent-type: image/jpeg\r\nContent-length: {len(jpeg)}\r\n\r\n"
        self._on_bytes(header.encode("ascii") + jpeg + b"\r\n")

    def exit(self, code: int, *last_lines: str) -> None:
        """ffmpeg writes its last lines and exits with `code`."""
        for line in last_lines:
            self._on_line(line)
        self._exit_code = code
        self._exited.set()


class FakeLauncher:
    """Starts `FakeRun`s and keeps them, newest last; can pretend ffmpeg is missing."""

    def __init__(self) -> None:
        self.runs: list[FakeRun] = []
        self.missing = False
        self.bug: Exception | None = None

    def __call__(self, command: list[str], on_line: LineSink, on_bytes: BytesSink) -> FakeRun:
        if self.bug is not None:
            raise self.bug
        if self.missing:
            raise FfmpegNotFoundError("ffmpeg could not be started (ffmpeg): no such file")
        run = FakeRun(command, on_line, on_bytes)
        self.runs.append(run)
        return run

    @property
    def latest(self) -> FakeRun:
        return self.runs[-1]


class FakeProber:
    """Answers test encodes from a set of working encoders, and counts the questions."""

    def __init__(self, working: set[str]) -> None:
        self.working = working
        self.asked: list[str] = []

    def __call__(self, ffmpeg_path: str, encoder: str) -> ProbeResult:
        self.asked.append(encoder)
        return ProbeResult(encoder in self.working, "" if encoder in self.working else "no device")


class RecordingSender:
    """A window sender that only records."""

    def __init__(self, port: int, starting_window: CropPixels) -> None:
        self.port = port
        self.starting_window = starting_window
        self.offered: list[CropPixels] = []
        self.started = False
        self.stopped = False

    def start(self) -> None:
        self.started = True

    def stop(self) -> None:
        self.stopped = True

    def offer(self, window: CropPixels) -> None:
        self.offered.append(window)

    def median_apply_ms(self) -> float | None:
        return 25.0


class RecordingSenders:
    """The sender factory: makes `RecordingSender`s and keeps them, newest last."""

    def __init__(self) -> None:
        self.made: list[RecordingSender] = []

    def __call__(self, port: int, starting_window: CropPixels, logger: object) -> RecordingSender:
        sender = RecordingSender(port, starting_window)
        self.made.append(sender)
        return sender
