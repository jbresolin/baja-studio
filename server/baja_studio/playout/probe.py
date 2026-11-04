"""ffprobe for durations and ffmpeg for thumbnails, run as subprocesses.

Thumbnails go into a cache folder in the service's data directory, never
next to the clips (the clip folder belongs to the receiver and to OBS).
Each call has a timeout, so a file on a dead network share cannot hang the
probe worker.
"""

import subprocess
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any, Protocol

PROBE_TIMEOUT_SECONDS = 30.0
THUMBNAIL_AT_SECONDS = 1.0
THUMBNAIL_WIDTH = 320

Runner = Callable[..., "subprocess.CompletedProcess[str]"]


class ProbeError(Exception):
    """ffprobe or ffmpeg could not read the file (or could not be started)."""


class Prober(Protocol):
    """What the probe worker needs; tests hand in a fake."""

    def probe_duration(self, path: str) -> float:
        """The file's duration in seconds, or `ProbeError`."""

    def make_thumbnail(self, path: str, output: Path, at_s: float) -> None:
        """Write a JPEG still of the file at `at_s` seconds to `output`, or `ProbeError`."""


class MediaProber:
    """Runs the real ffprobe/ffmpeg (paths from settings; default: found on PATH)."""

    def __init__(self, ffprobe_path: str, ffmpeg_path: str, run: Runner = subprocess.run) -> None:
        """`run` is `subprocess.run`; tests replace it to check the command lines."""
        self._ffprobe = ffprobe_path
        self._ffmpeg = ffmpeg_path
        self._run = run

    def probe_duration(self, path: str) -> float:
        """Ask ffprobe for the container duration."""
        output = self._call(
            [
                self._ffprobe,
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "default=noprint_wrappers=1:nokey=1",
                path,
            ]
        )
        try:
            return float(output.strip().splitlines()[0])
        except (ValueError, IndexError) as error:
            raise ProbeError(f"ffprobe gave no duration for {path}") from error

    def make_thumbnail(self, path: str, output: Path, at_s: float) -> None:
        """Grab one frame at `at_s`, scaled to 320 px wide."""
        output.parent.mkdir(parents=True, exist_ok=True)
        self._call(
            [
                self._ffmpeg,
                "-v",
                "error",
                "-y",
                "-ss",
                f"{at_s:.3f}",
                "-i",
                path,
                "-frames:v",
                "1",
                "-vf",
                f"scale={THUMBNAIL_WIDTH}:-2",
                str(output),
            ]
        )
        if not output.is_file():
            raise ProbeError(f"ffmpeg wrote no thumbnail for {path}")

    def _call(self, command: Sequence[str]) -> str:
        options: dict[str, Any] = {
            "capture_output": True,
            "text": True,
            "timeout": PROBE_TIMEOUT_SECONDS,
            "check": False,
        }
        try:
            completed = self._run(list(command), **options)
        except FileNotFoundError as error:
            raise ProbeError(f"{command[0]} not found; set its path in the settings") from error
        except (subprocess.TimeoutExpired, OSError) as error:
            raise ProbeError(f"{command[0]} failed: {error}") from error
        if completed.returncode != 0:
            message = (completed.stderr or "").strip().splitlines()
            raise ProbeError(message[-1] if message else f"{command[0]} failed")
        return completed.stdout or ""


def thumbnail_at(duration_s: float) -> float:
    """One second in, or halfway through a clip shorter than two seconds."""
    return min(THUMBNAIL_AT_SECONDS, duration_s / 2)
