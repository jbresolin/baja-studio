"""The loop an adapter child process runs; the other half of `childproc.py`.

Kept separate so a child imports only this (and the standard library), not the
host side and the supervisor. The wire format is described in `childproc.py`.
"""

import json
import sys
from collections.abc import Callable
from typing import IO, Any

ChildHandler = Callable[[str, dict[str, Any]], Any]


def run_child(
    handler: ChildHandler, stdin: IO[str] | None = None, stdout: IO[str] | None = None
) -> None:
    """The loop a child process runs: read requests, call `handler`, write replies.

    A real adapter only writes `handler(method, params) -> result`; pings are
    answered here. The loop ends when the host closes stdin.
    """
    input_stream = stdin if stdin is not None else sys.stdin
    output_stream = stdout if stdout is not None else sys.stdout
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
        output_stream.write(json.dumps(reply) + "\n")
        output_stream.flush()
