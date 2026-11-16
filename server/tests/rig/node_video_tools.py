"""Shared helpers for the camera node's real-ffmpeg tests (`test_node_video_real.py`).

They need an ffmpeg with the zmq filter, SRT and x264:

- in CI, the pinned node ffmpeg (`installer/ffmpeg-node/pin.json`; its Linux
  build on Linux, its Windows build on Windows), whose path CI puts in
  `BAJA_STUDIO_TEST_NODE_FFMPEG`. If that ffmpeg lacks a feature the tests
  fail: CI must never skip them quietly;
- elsewhere, `ffmpeg` on PATH if it has them (Ubuntu's 6.1 does), else
  the tests are skipped.

Everything runs in real time on loopback, waiting with deadlines.
"""

import os
import shutil
import subprocess
import time
from collections.abc import Callable

import pytest

NODE_FFMPEG_VARIABLE = "BAJA_STUDIO_TEST_NODE_FFMPEG"
DEADLINE_SECONDS = 40.0


def _listing(ffmpeg: str, what: str) -> list[str]:
    """The words of `ffmpeg -<what>` (filters, protocols, encoders...)."""
    finished = subprocess.run(
        [ffmpeg, "-hide_banner", f"-{what}"], capture_output=True, text=True, check=False
    )
    return finished.stdout.split()


def missing_features(ffmpeg: str) -> list[str]:
    """What this ffmpeg lacks of what the pipeline tests use (empty: it has everything)."""
    missing = []
    if "zmq" not in _listing(ffmpeg, "filters"):
        missing.append("the zmq filter")
    if "srt" not in _listing(ffmpeg, "protocols"):
        missing.append("SRT")
    if "libx264" not in _listing(ffmpeg, "encoders"):
        missing.append("libx264")
    return missing


def find_node_ffmpeg() -> tuple[str | None, str]:
    """(the ffmpeg to test with, or None) and why not."""
    pinned = os.environ.get(NODE_FFMPEG_VARIABLE)
    if pinned:
        # Used whatever it has: if it lacks something, the tests fail (and
        # test_node_ffmpeg_pin.py says what is missing).
        return pinned, ""
    on_path = shutil.which("ffmpeg")
    if on_path is None:
        return None, "no ffmpeg on PATH"
    missing = missing_features(on_path)
    if missing:
        return None, f"the ffmpeg on PATH lacks {', '.join(missing)}"
    return on_path, ""


NODE_FFMPEG, WHY_NOT = find_node_ffmpeg()
needs_node_ffmpeg = pytest.mark.skipif(NODE_FFMPEG is None, reason=WHY_NOT)


def ffmpeg_path() -> str:
    """The ffmpeg to use (tests marked `needs_node_ffmpeg` only)."""
    assert NODE_FFMPEG is not None
    return NODE_FFMPEG


def wait_until(condition: Callable[[], bool], what: str, seconds: float = DEADLINE_SECONDS) -> None:
    """Wait (real time) until `condition()` holds, or fail naming `what`."""
    deadline = time.monotonic() + seconds
    while not condition():
        if time.monotonic() > deadline:
            pytest.fail(f"never happened within {seconds:.0f} s: {what}")
        time.sleep(0.1)
