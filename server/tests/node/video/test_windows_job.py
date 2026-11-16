"""Tests for tying ffmpeg's life to the node's on Windows (node/video/windows_job.py).

On Windows the node puts each ffmpeg in a "job" that Windows closes when the
node's process ends, killing the ffmpeg with it. These tests start a
harmless child process (Python waiting a few seconds) in its place. On
Linux the functions do nothing and say so.
"""

import subprocess
import sys

import pytest

from baja_studio.node.video.windows_job import is_tied_to_this_process, tie_to_this_process


def start_child() -> "subprocess.Popen[bytes]":
    return subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(30)"],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


@pytest.mark.skipif(sys.platform != "win32", reason="job objects are Windows only")
def test_on_windows_a_child_is_put_in_the_kill_on_close_job() -> None:
    child = start_child()
    try:
        assert not is_tied_to_this_process(child.pid)
        assert tie_to_this_process(child)
        assert is_tied_to_this_process(child.pid)
    finally:
        child.kill()
        child.wait()


@pytest.mark.skipif(sys.platform == "win32", reason="Windows does tie it")
def test_elsewhere_nothing_is_done() -> None:
    child = start_child()
    try:
        assert tie_to_this_process(child) is False
        assert is_tied_to_this_process(child.pid) is False
    finally:
        child.kill()
        child.wait()
