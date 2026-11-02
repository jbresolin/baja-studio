"""Host side of adapters that run as child processes.

Adapters built on native libraries that can crash the interpreter (MIDI, UVC)
run in a separate Python process so a crash only takes the child down. Host and
child talk JSON lines over the child's stdin/stdout:

    host -> child   {"id": 7, "method": "upper", "params": {"text": "hi"}}
    child -> host   {"id": 7, "result": "HI"}     or   {"id": 7, "error": "..."}
    child -> host   {"event": "button", "data": {...}}   (unsolicited)

The child's stderr is forwarded to the service log. The host pings the child
every two seconds; a missed ping or an exit counts as the device being offline.
The loop a child runs lives in `childproc_child.py`, so children do not import
the host side (or the supervisor) at all.
"""

import json
import logging
import os
import subprocess
import sys
import threading
from collections.abc import Callable
from typing import IO, Any

from baja_studio.core.clock import Clock
from baja_studio.core.supervisor import DeviceOfflineError, WorkerContext

logger = logging.getLogger(__name__)

PING_INTERVAL_SECONDS = 2.0
REQUEST_TIMEOUT_SECONDS = 5.0
STOP_TIMEOUT_SECONDS = 2.0
EXIT_WAIT_SECONDS = 5.0

EventCallback = Callable[[str, Any], None]


class ChildRequestError(Exception):
    """The child answered a request with an error message."""


class _PendingRequest:
    def __init__(self) -> None:
        self.answered = threading.Event()
        self.reply: dict[str, Any] = {}


class ChildProcessHost:
    """Starts one child process and exchanges requests and events with it."""

    def __init__(
        self,
        python_module: str,
        clock: Clock,
        owner_module: str,
        on_event: EventCallback | None = None,
    ) -> None:
        """Prepare to run `python -m <python_module>`; nothing starts until `start`.

        `owner_module` is the studio module this child belongs to, used to tag logs.
        """
        self.python_module = python_module
        self._clock = clock
        self._owner_module = owner_module
        self._on_event = on_event
        self._process: subprocess.Popen[str] | None = None
        self._write_lock = threading.Lock()
        self._pending_lock = threading.Lock()
        self._pending: dict[int, _PendingRequest] = {}
        self._next_request_id = 1
        self._exit_listeners: list[Callable[[], None]] = []
        # Lifecycle state. Only `start` and the stdout reader thread write these,
        # so every caller sees the same answer (see `_read_replies`).
        self._running = False
        self._exit_code: int | None = None
        self.exited = threading.Event()

    def add_exit_listener(self, listener: Callable[[], None]) -> None:
        """Call `listener()` from the reader thread when the child's output ends."""
        self._exit_listeners.append(listener)

    def start(self) -> None:
        """Spawn the child and start threads reading its stdout and stderr."""
        self.exited.clear()
        self._exit_code = None
        environment = {**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUNBUFFERED": "1"}
        # On Windows the service has no console; without this each child would open one.
        creation_flags = (
            getattr(subprocess, "CREATE_NO_WINDOW", 0) if sys.platform == "win32" else 0
        )
        process = subprocess.Popen(
            [sys.executable, "-m", self.python_module],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            bufsize=1,
            env=environment,
            creationflags=creation_flags,
        )
        self._process = process
        self._running = True
        logger.info(
            "child process started (pid %s)",
            process.pid,
            extra={"studio_module": self._owner_module, "child": self.python_module},
        )
        assert process.stdout is not None and process.stderr is not None
        threading.Thread(
            target=self._read_replies,
            args=(process, process.stdout),
            name=f"{self.python_module}.stdout",
            daemon=True,
        ).start()
        threading.Thread(
            target=self._forward_stderr,
            args=(process.stderr,),
            name=f"{self.python_module}.stderr",
            daemon=True,
        ).start()

    @property
    def is_running(self) -> bool:
        """True from `start` until the reader thread has seen the child exit.

        This is the host's own flag, not `poll()`: on Windows the child's stdout
        can reach end-of-file before `poll()` reports the exit, and the two
        must never disagree.
        """
        return self._running

    @property
    def exit_code(self) -> int | None:
        """The child's exit code once the reader thread has reaped it, otherwise None."""
        return self._exit_code

    def request(
        self,
        method: str,
        params: dict[str, Any] | None = None,
        timeout_seconds: float = REQUEST_TIMEOUT_SECONDS,
    ) -> Any:
        """Send a request and wait for its answer.

        Raises `DeviceOfflineError` if the child is not running, exits, or does
        not answer in time; `ChildRequestError` if it answers with an error.
        """
        if not self.is_running:
            raise DeviceOfflineError(f"child {self.python_module} is not running")
        pending = _PendingRequest()
        with self._pending_lock:
            request_id = self._next_request_id
            self._next_request_id += 1
            self._pending[request_id] = pending
        try:
            self._write({"id": request_id, "method": method, "params": params or {}})
            if not self._clock.wait(pending.answered, timeout_seconds):
                raise DeviceOfflineError(
                    f"child {self.python_module} did not answer {method!r} "
                    f"within {timeout_seconds:.0f} s"
                )
        finally:
            with self._pending_lock:
                self._pending.pop(request_id, None)
        if "error" in pending.reply:
            if pending.reply.get("offline"):
                raise DeviceOfflineError(pending.reply["error"])
            raise ChildRequestError(pending.reply["error"])
        return pending.reply.get("result")

    def ping(self, timeout_seconds: float = REQUEST_TIMEOUT_SECONDS) -> None:
        """Check the child is alive and responsive; raises `DeviceOfflineError` if not."""
        self.request("ping", timeout_seconds=timeout_seconds)

    def kill(self) -> None:
        """Terminate the child abruptly; used to test and demonstrate recovery."""
        if self._process is not None and self.is_running:
            self._process.kill()

    def stop(self, timeout_seconds: float = STOP_TIMEOUT_SECONDS) -> None:
        """Close the child's stdin (its signal to exit), then terminate if it lingers."""
        process = self._process
        if process is None:
            return
        try:
            if process.stdin is not None:
                process.stdin.close()
        except OSError:
            pass
        try:
            process.wait(timeout_seconds)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout_seconds)
        # The reader thread records the exit (flag, exit code, log line); wait
        # for it so that `is_running` is already False when `stop` returns.
        self.exited.wait(timeout_seconds)

    def _write(self, message: dict[str, Any]) -> None:
        assert self._process is not None and self._process.stdin is not None
        line = json.dumps(message) + "\n"
        try:
            with self._write_lock:
                self._process.stdin.write(line)
                self._process.stdin.flush()
        except (OSError, ValueError) as error:
            raise DeviceOfflineError(
                f"cannot write to child {self.python_module}: {error}"
            ) from error

    def _read_replies(self, process: subprocess.Popen[str], stream: IO[str]) -> None:
        """Reader thread: route each line from the child to its waiting request or as an event.

        When the output ends, this thread is the one place that records the
        exit, in a fixed order: reap the process for its exit code, clear the
        running flag, log, set `exited`, then call the exit listeners. So a
        listener always sees `is_running` False and `exit_code` set.
        """
        for line in stream:
            self._handle_line(line)
        self._exit_code = self._reap(process)
        self._running = False
        logger.info(
            "child process stopped (exit code %s)",
            self._exit_code,
            extra={"studio_module": self._owner_module, "child": self.python_module},
        )
        self._fail_all_pending("child exited")
        self.exited.set()
        for listener in self._exit_listeners:
            listener()

    def _reap(self, process: subprocess.Popen[str]) -> int:
        """Wait for a child whose output has ended to exit, and return its exit code.

        End-of-file usually means the process is gone or about to be (on Windows
        the pipe can close slightly before the exit is visible). A child that
        closes stdout but keeps running is killed.
        """
        try:
            return process.wait(timeout=EXIT_WAIT_SECONDS)
        except subprocess.TimeoutExpired:
            logger.warning(
                "child closed its output but did not exit; killing it",
                extra={"studio_module": self._owner_module, "child": self.python_module},
            )
            process.kill()
            return process.wait()

    def _handle_line(self, line: str) -> None:
        try:
            message = json.loads(line)
        except json.JSONDecodeError:
            logger.warning(
                "child wrote a non-JSON line: %r",
                line.rstrip(),
                extra={"studio_module": self._owner_module},
            )
            return
        if "event" in message:
            if self._on_event is not None:
                try:
                    self._on_event(message["event"], message.get("data"))
                except Exception:
                    logger.exception(
                        "child event handler failed", extra={"studio_module": self._owner_module}
                    )
            return
        with self._pending_lock:
            pending = self._pending.get(message.get("id", -1))
        if pending is not None:
            pending.reply = message
            pending.answered.set()

    def _fail_all_pending(self, reason: str) -> None:
        with self._pending_lock:
            waiting = list(self._pending.values())
        for pending in waiting:
            pending.reply = {"error": reason, "offline": True}
            pending.answered.set()

    def _forward_stderr(self, stream: IO[str]) -> None:
        """Reader thread: whatever the child prints to stderr goes to the service log."""
        for line in stream:
            logger.info(
                "child: %s",
                line.rstrip(),
                extra={"studio_module": self._owner_module, "child": self.python_module},
            )


def run_child_session(
    host: ChildProcessHost,
    context: WorkerContext,
    ping_interval_seconds: float = PING_INTERVAL_SECONDS,
) -> None:
    """Worker body for a module backed by a child process.

    Starts the child, reports the module online once it answers a ping, then
    pings it every interval. When the child dies or stops answering this
    raises `DeviceOfflineError`, and the supervisor restarts the worker
    (and so the child) with its usual backoff.
    """
    host.add_exit_listener(context.wake)
    host.start()
    try:
        host.ping()
        context.mark_online()
        while not context.should_stop:
            context.sleep(ping_interval_seconds)
            if context.should_stop:
                break
            if not host.is_running:
                raise DeviceOfflineError(
                    f"child {host.python_module} exited (code {host.exit_code})"
                )
            host.ping()
    finally:
        host.stop()
