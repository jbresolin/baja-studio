"""Tests for the rig's child processes (rig/processes.py).

Real Python child processes over pipes, which the conventions allow (no
network, no hardware). The echo child is used because it is small and
waits on its standard input like the fake XR18's console.
"""

from pathlib import Path

import pytest

from baja_studio.rig.processes import ProcessStartError, RigProcess


def echo_child(log_directory: Path, console: bool = True) -> RigProcess:
    return RigProcess(
        "echo", "baja_studio.childprocs.echo_child", [], log_directory, console=console
    )


def test_a_started_child_runs_and_a_stopped_one_is_gone(tmp_path: Path) -> None:
    child = echo_child(tmp_path)
    child.start()
    try:
        assert child.running and child.pid is not None
    finally:
        child.stop()
    # Stopped and waited for: nothing is left running and the log file is closed.
    assert not child.running
    assert child.log_path.is_file()


def test_a_stopped_child_can_be_started_again(tmp_path: Path) -> None:
    # How the obs-restart scenario brings OBS back on the same port.
    child = echo_child(tmp_path)
    child.start()
    first_pid = child.pid
    child.stop()
    child.start()
    try:
        assert child.running and child.pid != first_pid
    finally:
        child.stop()


def test_typing_on_a_child_that_is_not_running_says_so(tmp_path: Path) -> None:
    child = echo_child(tmp_path)
    with pytest.raises(ProcessStartError, match="echo is not running"):
        child.type_line("drop on")


def test_stopping_twice_or_never_started_is_harmless(tmp_path: Path) -> None:
    child = echo_child(tmp_path, console=False)
    child.stop()
    child.start()
    child.stop()
    child.stop()
    assert not child.running
