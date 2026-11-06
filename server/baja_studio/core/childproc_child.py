"""The loop an adapter child process runs; the other half of `childproc.py`.

Kept separate so a child imports only this (and the standard library), not the
host side and the supervisor. The wire format is described in `childproc.py`.
"""

import json
import sys
import threading
from collections.abc import Callable
from typing import IO, Any

ChildHandler = Callable[[str, dict[str, Any]], Any]


class ChildChannel:
    """The child's half of the pipe to the service: replies and unsolicited events.

    Replies are written by `run_child`; events (a MIDI message arrived, a port
    went away) by whichever thread notices them, typically a device library's
    callback thread. A lock keeps the two from interleaving within a line.
    """

    def __init__(self, stdout: IO[str] | None = None) -> None:
        """Write to `stdout`, or to the process's real stdout when not given."""
        self._stream = stdout if stdout is not None else sys.stdout
        self._lock = threading.Lock()

    def send_event(self, event: str, data: Any = None) -> None:
        """Tell the service something happened; safe from any thread."""
        self.write({"event": event, "data": data})

    def write(self, message: dict[str, Any]) -> None:
        """Write one JSON line and flush it, so the service sees it at once."""
        line = json.dumps(message) + "\n"
        with self._lock:
            self._stream.write(line)
            self._stream.flush()


def run_child(
    handler: ChildHandler,
    stdin: IO[str] | None = None,
    stdout: IO[str] | None = None,
    channel: ChildChannel | None = None,
) -> None:
    """The loop a child process runs: read requests, call `handler`, write replies.

    A real adapter only writes `handler(method, params) -> result`; pings are
    answered here. An adapter that also sends events creates a `ChildChannel`
    first and passes it here, so replies and events share its lock. The loop
    ends when the host closes stdin.
    """
    input_stream = stdin if stdin is not None else sys.stdin
    output = channel if channel is not None else ChildChannel(stdout)
    for line in input_stream:
        if not line.strip():
            continue
        try:
            request = json.loads(line)
        except json.JSONDecodeError:
            print(f"ignoring non-JSON line: {line!r}", file=sys.stderr)
            continue
        reply: dict[str, Any] = {"id": request.get("id")}
        try:
            if request.get("method") == "ping":
                reply["result"] = "pong"
            else:
                reply["result"] = handler(request.get("method", ""), request.get("params") or {})
        except Exception as error:
            reply["error"] = f"{type(error).__name__}: {error}"
        output.write(reply)
