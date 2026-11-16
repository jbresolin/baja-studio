"""Running one ffmpeg: start it, read both its outputs all the time, stop it and reap it.

Three rules keep a long-running ffmpeg healthy:

- **Both pipes are read continuously**, each by its own thread, from start
  to end of file. A pipe nobody reads fills up (64 kB on Windows) and ffmpeg
  then stops in the middle of writing a log line or a snapshot: the whole
  pipeline, live feed included, would freeze.
- **Every process is reaped**: `stop()` asks it to end, kills it if it has
  not within `STOP_TIMEOUT_SECONDS`, and always waits for it, so no zombie
  (or, on Windows, no orphan holding a camera) is left behind. On Windows it
  also joins the node's kill-on-close job (`windows_job.py`).
- **`exited` is set only after the readers have finished and the exit code
  is known**, so whoever watches it sees the whole log tail.
"""

import logging
import subprocess
import sys
import threading
from collections.abc import Callable
from typing import IO, Protocol

from baja_studio.node.video.windows_job import tie_to_this_process

logger = logging.getLogger(__name__)

STOP_TIMEOUT_SECONDS = 5.0
# On Windows the node is a service without a console; without this flag every
# ffmpeg it starts would open a console window of its own.
NO_CONSOLE_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0) if sys.platform == "win32" else 0
READ_CHUNK_BYTES = 64 * 1024

LineSink = Callable[[str], None]
BytesSink = Callable[[bytes], None]


class FfmpegNotFoundError(Exception):
    """The ffmpeg program could not be started at all (wrong path, not installed)."""


class RunningFfmpeg(Protocol):
    """What the pipeline needs from one ffmpeg run (the real one, or a test's fake)."""

    @property
    def exited(self) -> threading.Event:
        """Set once the process has ended and its output has been read to the end."""

    @property
    def exit_code(self) -> int | None:
        """The exit code once `exited` is set, otherwise None."""

    def stop(self) -> None:
        """End the process (if still running) and wait until `exited`."""


# How the pipeline starts a run: the command, where stderr lines go, where stdout bytes go.
Launcher = Callable[[list[str], LineSink, BytesSink], RunningFfmpeg]


class FfmpegProcess:
    """One real ffmpeg child process and the two threads reading it."""

    def __init__(
        self, command: list[str], on_stderr_line: LineSink, on_stdout_bytes: BytesSink
    ) -> None:
        """Start ffmpeg now; raises `FfmpegNotFoundError` if it cannot be started."""
        try:
            self._process = subprocess.Popen(
                command,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                creationflags=NO_CONSOLE_WINDOW,
            )
        except OSError as error:
            message = f"ffmpeg could not be started ({command[0]}): {error}"
            raise FfmpegNotFoundError(message) from error
        tie_to_this_process(self._process)
        self._exited = threading.Event()
        self._exit_code: int | None = None
        self._stop_lock = threading.Lock()
        assert self._process.stdout is not None and self._process.stderr is not None
        name = f"ffmpeg-{self._process.pid}"
        self._stdout_thread = threading.Thread(
            target=self._read_stdout,
            args=(self._process.stdout, on_stdout_bytes),
            name=f"{name}.stdout",
            daemon=True,
        )
        self._stderr_thread = threading.Thread(
            target=self._read_stderr,
            args=(self._process.stderr, on_stderr_line),
            name=f"{name}.stderr",
            daemon=True,
        )
        self._stdout_thread.start()
        self._stderr_thread.start()

    @property
    def pid(self) -> int:
        """The process id (for logs and tests)."""
        return self._process.pid

    @property
    def exited(self) -> threading.Event:
        """Set once ffmpeg has ended and both pipes have been read to the end."""
        return self._exited

    @property
    def exit_code(self) -> int | None:
        """ffmpeg's exit code, once `exited` is set."""
        return self._exit_code

    @staticmethod
    def _read_stdout(pipe: IO[bytes], sink: BytesSink) -> None:
        # read1 returns whatever has arrived (up to the size), without waiting for more.
        # A sink that fails must not stop the reading, or ffmpeg would block on a full pipe.
        while chunk := pipe.read1(READ_CHUNK_BYTES):  # type: ignore[attr-defined]
            try:
                sink(chunk)
            except Exception:
                logger.exception("reading ffmpeg's snapshots failed; skipping those bytes")

    def _read_stderr(self, pipe: IO[bytes], sink: LineSink) -> None:
        try:
            for raw_line in pipe:
                try:
                    sink(raw_line.decode("utf-8", errors="replace").rstrip("\r\n"))
                except Exception:
                    logger.exception("reading ffmpeg's log failed; skipping a line")
        finally:
            # stderr is at its end: the process is ending. Collect everything, then tell.
            self._stdout_thread.join()
            self._exit_code = self._process.wait()
            assert self._process.stdout is not None
            self._process.stdout.close()
            pipe.close()
            self._exited.set()

    def stop(self) -> None:
        """Ask ffmpeg to end, kill it if it will not, and wait until it is reaped."""
        with self._stop_lock:
            if not self._exited.is_set() and self._process.poll() is None:
                # On Windows this is TerminateProcess: immediate. Elsewhere SIGTERM,
                # which ffmpeg handles by finishing its outputs and exiting.
                self._process.terminate()
            if not self._exited.wait(STOP_TIMEOUT_SECONDS):
                self._process.kill()
            self._exited.wait()
            self._stderr_thread.join()


def launch_ffmpeg(
    command: list[str], on_stderr_line: LineSink, on_stdout_bytes: BytesSink
) -> RunningFfmpeg:
    """The default `Launcher`: a real ffmpeg process."""
    return FfmpegProcess(command, on_stderr_line, on_stdout_bytes)
