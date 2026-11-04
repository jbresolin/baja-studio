"""Tests for the ffprobe/ffmpeg wrapper (playout/probe.py).

No real ffprobe runs: `MediaProber` takes the function it uses to run a
program (normally `subprocess.run`), and each test hands it a fake that
records the command line and answers what the test wants.
"""

import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from baja_studio.playout.probe import (
    PROBE_TIMEOUT_SECONDS,
    MediaProber,
    ProbeError,
    thumbnail_at,
)


class FakeRun:
    """Stands in for `subprocess.run`: records calls, answers with a set result."""

    def __init__(
        self,
        stdout: str = "",
        stderr: str = "",
        returncode: int = 0,
        raises: Exception | None = None,
        side_effect: Callable[[list[str]], object] | None = None,
    ) -> None:
        self.calls: list[tuple[list[str], dict[str, Any]]] = []
        self._result = (stdout, stderr, returncode)
        self._raises = raises
        self._side_effect = side_effect

    def __call__(self, command: list[str], **options: Any) -> "subprocess.CompletedProcess[str]":
        self.calls.append((command, options))
        if self._raises is not None:
            raise self._raises
        if self._side_effect is not None:
            self._side_effect(command)
        stdout, stderr, returncode = self._result
        return subprocess.CompletedProcess(command, returncode, stdout, stderr)


def test_the_duration_comes_from_this_exact_ffprobe_command() -> None:
    # ffprobe prints the container duration alone on a line.
    run = FakeRun(stdout="12.345000\n")
    prober = MediaProber("C:/tools/ffprobe.exe", "ffmpeg", run)

    duration = prober.probe_duration("D:/clips/enduro/42/3.mp4")

    assert duration == pytest.approx(12.345)
    command, options = run.calls[0]
    assert command == [
        "C:/tools/ffprobe.exe",
        "-v",
        "error",
        "-show_entries",
        "format=duration",
        "-of",
        "default=noprint_wrappers=1:nokey=1",
        "D:/clips/enduro/42/3.mp4",
    ]
    # A timeout, so a file on a dead network share cannot hang the worker.
    assert options["timeout"] == PROBE_TIMEOUT_SECONDS
    assert options["capture_output"] is True


def test_no_duration_in_the_output_is_a_probe_error() -> None:
    # Some broken files make ffprobe print "N/A" and exit 0.
    prober = MediaProber("ffprobe", "ffmpeg", FakeRun(stdout="N/A\n"))

    with pytest.raises(ProbeError, match="no duration"):
        prober.probe_duration("broken.mp4")


def test_a_failing_ffprobe_reports_the_last_line_of_its_error_output() -> None:
    # ffprobe explains itself on stderr; the last line is the useful one.
    run = FakeRun(stderr="first line\nbroken.mp4: Invalid data found\n", returncode=1)
    prober = MediaProber("ffprobe", "ffmpeg", run)

    with pytest.raises(ProbeError, match=r"^broken.mp4: Invalid data found$"):
        prober.probe_duration("broken.mp4")


def test_a_missing_ffprobe_says_to_fix_the_path_in_the_settings() -> None:
    # `subprocess.run` raises FileNotFoundError when the program does not exist.
    prober = MediaProber("ffprobe", "ffmpeg", FakeRun(raises=FileNotFoundError("ffprobe")))

    with pytest.raises(ProbeError, match="set its path in the settings"):
        prober.probe_duration("a.mp4")


def test_a_timeout_is_a_probe_error() -> None:
    timeout = subprocess.TimeoutExpired(["ffprobe"], PROBE_TIMEOUT_SECONDS)
    prober = MediaProber("ffprobe", "ffmpeg", FakeRun(raises=timeout))

    with pytest.raises(ProbeError, match="ffprobe failed"):
        prober.probe_duration("a.mp4")


def test_the_thumbnail_comes_from_this_exact_ffmpeg_command(tmp_path: Path) -> None:
    # The fake "ffmpeg" writes the output file, as the real one would.
    output = tmp_path / "cache" / "7-100.jpg"
    run = FakeRun(side_effect=lambda command: Path(command[-1]).write_bytes(b"jpeg"))
    prober = MediaProber("ffprobe", "/usr/bin/ffmpeg", run)

    prober.make_thumbnail("clip.mp4", output, at_s=1.0)

    # The cache folder was created for it, and one frame scaled to 320 px was asked for.
    assert output.read_bytes() == b"jpeg"
    assert run.calls[0][0] == [
        "/usr/bin/ffmpeg",
        "-v",
        "error",
        "-y",
        "-ss",
        "1.000",
        "-i",
        "clip.mp4",
        "-frames:v",
        "1",
        "-vf",
        "scale=320:-2",
        str(output),
    ]


def test_ffmpeg_writing_no_file_is_a_probe_error(tmp_path: Path) -> None:
    # Exit code 0 but no picture (a clip with no video stream, say).
    prober = MediaProber("ffprobe", "ffmpeg", FakeRun())

    with pytest.raises(ProbeError, match="no thumbnail"):
        prober.make_thumbnail("clip.mp4", tmp_path / "t.jpg", at_s=1.0)


def test_the_thumbnail_is_taken_one_second_in_or_halfway_through_a_short_clip() -> None:
    assert thumbnail_at(30.0) == 1.0
    assert thumbnail_at(1.2) == pytest.approx(0.6)
