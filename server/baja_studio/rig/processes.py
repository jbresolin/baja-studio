"""The rig's child processes: started in their own process group, stopped for sure.

Each fake (and the studio) runs as `python -m <module> ...` with its output in
`<rig>/logs/<name>.log`, so the terminal stays readable. Children get their
own process group, so a Ctrl-C in the terminal reaches only the rig, which
then stops them in order: politely first (SIGTERM, or CTRL_BREAK on Windows,
which uvicorn and Python both treat as "shut down"), then for good after a
few seconds. A child with a console (the fake XR18) gets its standard input
as a pipe the scenarios type into.
"""

import contextlib
import os
import signal
import subprocess
import sys
from pathlib import Path
from typing import IO

STOP_GRACE_SECONDS = 8.0
KILL_WAIT_SECONDS = 5.0
# `CREATE_NEW_PROCESS_GROUP` exists on Windows only; elsewhere `start_new_session` does the job.
NEW_PROCESS_GROUP = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)


class ProcessStartError(Exception):
    """A child could not be started, or quit straight away; the message says which."""


class RigProcess:
    """One child process of the rig."""

    def __init__(
        self,
        name: str,
        python_module: str,
        arguments: list[str],
        log_directory: Path,
        environment: dict[str, str] | None = None,
        console: bool = False,
    ) -> None:
        """Nothing runs until `start`."""
        self.name = name
        self.command = [sys.executable, "-m", python_module, *arguments]
        self.log_path = log_directory / f"{name}.log"
        self._environment = environment
        self._console = console
        self._process: subprocess.Popen[str] | None = None
        self._log: IO[str] | None = None

    def start(self) -> None:
        """Start the child (again, after `stop`), its output appended to its log file."""
        if self.running:
            return
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        self._close_log()
        self._log = self.log_path.open("a", encoding="utf-8")
        environment = {**os.environ, **(self._environment or {}), "PYTHONUNBUFFERED": "1"}
        try:
            self._process = subprocess.Popen(
                self.command,
                stdin=subprocess.PIPE if self._console else subprocess.DEVNULL,
                stdout=self._log,
                stderr=subprocess.STDOUT,
                env=environment,
                text=True,
                # Their own process group: a Ctrl-C in the terminal reaches only the rig.
                creationflags=NEW_PROCESS_GROUP,
                start_new_session=sys.platform != "win32",
            )
        except OSError as error:
            self._close_log()
            raise ProcessStartError(f"{self.name} could not start: {error}") from None

    @property
    def running(self) -> bool:
        """True while the child is alive."""
        return self._process is not None and self._process.poll() is None

    @property
    def pid(self) -> int | None:
        """The child's process id while it runs (tests check it is gone afterwards)."""
        return self._process.pid if self._process is not None else None

    def type_line(self, line: str) -> None:
        """Type one line on the child's console (the fake XR18's commands)."""
        if self._process is None or self._process.stdin is None or not self.running:
            raise ProcessStartError(f"{self.name} is not running; cannot type {line!r}")
        self._process.stdin.write(line + "\n")
        self._process.stdin.flush()

    def stop(self) -> None:
        """Ask the child to stop, wait a few seconds, then kill it; closes its pipes."""
        process, self._process = self._process, None
        if process is not None:
            if process.poll() is None:
                _ask_to_stop(process)
                try:
                    process.wait(STOP_GRACE_SECONDS)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(KILL_WAIT_SECONDS)
            if process.stdin is not None:
                # The child is gone; a broken pipe has nothing left to flush.
                with contextlib.suppress(OSError):
                    process.stdin.close()
        self._close_log()

    def _close_log(self) -> None:
        if self._log is not None:
            self._log.close()
            self._log = None


def _ask_to_stop(process: "subprocess.Popen[str]") -> None:
    """The polite stop: CTRL_BREAK on Windows (its group), SIGTERM elsewhere."""
    with contextlib.suppress(OSError):  # it went away meanwhile
        if sys.platform == "win32":
            process.send_signal(signal.CTRL_BREAK_EVENT)
        else:
            process.terminate()
