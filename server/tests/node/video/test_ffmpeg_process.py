"""Tests for running one ffmpeg process (node/video/ffmpeg_process.py).

Instead of ffmpeg, these start a small Python program that behaves like it
on its pipes (`fake_ffmpeg_child.py`): it writes log lines on stderr and
bytes on stdout, exits, floods its pipes, or refuses to stop. Starting real
child processes needs no hardware or network, so this is allowed in the
normal test run.
"""

import sys
import threading
from pathlib import Path

import pytest

from baja_studio.node.video.ffmpeg_process import FfmpegNotFoundError, FfmpegProcess
from tests.conftest import SAFETY_TIMEOUT_SECONDS, eventually

CHILD = str(Path(__file__).with_name("fake_ffmpeg_child.py"))


class Collected:
    """What a process wrote: stderr lines and stdout bytes, gathered from the reader threads."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.lines: list[str] = []
        self.stdout = bytearray()

    def line(self, text: str) -> None:
        with self._lock:
            self.lines.append(text)

    def data(self, chunk: bytes) -> None:
        with self._lock:
            self.stdout.extend(chunk)


def start(*arguments: str) -> tuple[FfmpegProcess, Collected]:
    collected = Collected()
    process = FfmpegProcess([sys.executable, CHILD, *arguments], collected.line, collected.data)
    return process, collected


def test_everything_written_is_read_before_exited_is_set() -> None:
    process, collected = start("talk", "251")
    assert process.exited.wait(SAFETY_TIMEOUT_SECONDS)
    # By the time `exited` is set, every line and byte has been handed over,
    # and the exit code is known: whoever reacts to `exited` sees the whole story.
    assert collected.lines == [
        "[dshow @ 0x1] first warning",
        "frame=30",
        "progress=continue",
        "[srt @ 0x2] Connection to srt://x failed",
    ]
    assert bytes(collected.stdout) == b"snapshot-bytes"
    assert process.exit_code == 251
    process.stop()  # stopping an ended process is harmless


def test_both_pipes_are_read_so_a_chatty_process_never_blocks() -> None:
    # 2 MB on each pipe: far more than a pipe holds (64 kB). If either pipe were
    # left unread, the child would block forever on its write and never exit.
    size = 2_000_000
    process, collected = start("flood", str(size))
    assert process.exited.wait(SAFETY_TIMEOUT_SECONDS)
    assert len(collected.stdout) == size
    assert len(collected.lines) == size // 100
    assert process.exit_code == 0


def test_stop_ends_a_running_process_and_reaps_it() -> None:
    process, collected = start("sleep")
    # Wait until it is really running (it says "ready").
    eventually(lambda: collected.lines == ["ready"], "the child never said it was ready")
    process.stop()
    assert process.exited.is_set()
    # An exit code means the process was reaped: no zombie is left.
    assert process.exit_code is not None


@pytest.mark.skipif(sys.platform == "win32", reason="Windows has no polite stop to ignore")
def test_a_process_that_ignores_the_polite_stop_is_killed(monkeypatch: pytest.MonkeyPatch) -> None:
    # Make the wait before killing short, so the test is quick.
    monkeypatch.setattr("baja_studio.node.video.ffmpeg_process.STOP_TIMEOUT_SECONDS", 0.5)
    process, collected = start("ignore-term")
    eventually(lambda: collected.lines == ["ready"], "the child never said it was ready")
    process.stop()
    assert process.exited.is_set()
    # Killed: on Linux a process killed by signal 9 exits with -9.
    assert process.exit_code == -9


def test_a_missing_program_raises_a_clear_error(tmp_path: Path) -> None:
    with pytest.raises(FfmpegNotFoundError, match="ffmpeg could not be started"):
        FfmpegProcess([str(tmp_path / "ffmpeg.exe")], lambda line: None, lambda data: None)


def test_a_failing_reader_does_not_stop_the_reading() -> None:
    # If whoever receives the lines raises, the next lines are still read
    # (otherwise the pipe would fill and ffmpeg would freeze).
    received: list[str] = []

    def fussy(line: str) -> None:
        received.append(line)
        if len(received) == 1:
            raise ValueError("cannot handle the first line")

    process = FfmpegProcess([sys.executable, CHILD, "talk", "0"], fussy, lambda data: None)
    assert process.exited.wait(SAFETY_TIMEOUT_SECONDS)
    assert len(received) == 4
