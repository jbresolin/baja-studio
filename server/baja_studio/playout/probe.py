"""ffprobe for durations and frame rates, ffmpeg for thumbnails, run as subprocesses.

Thumbnails go into a cache folder in the service's data directory, never
next to the clips (the clip folder belongs to the receiver and to OBS).
Each call has a timeout, so a file on a dead network share cannot hang the
probe worker.
"""

import json
import subprocess
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path
from typing import Any, Protocol

PROBE_TIMEOUT_SECONDS = 30.0
THUMBNAIL_AT_SECONDS = 1.0
THUMBNAIL_WIDTH = 320

Runner = Callable[..., "subprocess.CompletedProcess[str]"]


class ProbeError(Exception):
    """ffprobe or ffmpeg could not read the file (or could not be started)."""


@dataclass(frozen=True)
class MediaInfo:
    """What ffprobe says about a file: its length, and its video frame rate if it has one."""

    duration_s: float
    fps: float | None = None


class Prober(Protocol):
    """What the probe worker needs; tests hand in a fake."""

    def probe(self, path: str) -> MediaInfo:
        """The file's duration and frame rate, or `ProbeError`."""

    def make_thumbnail(self, path: str, output: Path, at_s: float) -> None:
        """Write a JPEG still of the file at `at_s` seconds to `output`, or `ProbeError`."""


class MediaProber:
    """Runs the real ffprobe/ffmpeg (paths from settings; default: found on PATH)."""

    def __init__(self, ffprobe_path: str, ffmpeg_path: str, run: Runner = subprocess.run) -> None:
        """`run` is `subprocess.run`; tests replace it to check the command lines."""
        self._ffprobe = ffprobe_path
        self._ffmpeg = ffmpeg_path
        self._run = run

    def probe(self, path: str) -> MediaInfo:
        """Ask ffprobe for the container duration and the first video stream's frame rate.

        The frame rate sets seek tolerances and the scrub bar's step (WP5c §3); a
        file without video, or with a rate ffprobe cannot tell ("0/0"), has none.
        """
        output = self._call(
            [
                self._ffprobe,
                "-v",
                "error",
                "-select_streams",
                "v:0",
                "-show_entries",
                "format=duration:stream=avg_frame_rate",
                "-of",
                "json",
                path,
            ]
        )
        try:
            data = json.loads(output)
            duration = float(data["format"]["duration"])
        except (ValueError, KeyError, TypeError) as error:
            raise ProbeError(f"ffprobe gave no duration for {path}") from error
        streams = data.get("streams") or [{}]
        return MediaInfo(duration, _frame_rate(streams[0].get("avg_frame_rate")))

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


def _frame_rate(text: object) -> float | None:
    """ "30000/1001" → 29.97; "0/0", "N/A" or nothing → None."""
    try:
        rate = Fraction(str(text))
    except (ValueError, ZeroDivisionError):
        return None
    return float(rate) if rate > 0 else None
