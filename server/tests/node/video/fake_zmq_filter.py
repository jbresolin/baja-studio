"""A stand-in for ffmpeg's `zmq` filter, for the window sender's tests.

What it copies from the real filter (`libavfilter/f_zmq.c`, and measured
with ffmpeg 6.1 in the WP15 prototype):

- it is a ZeroMQ REP socket;
- it looks at its socket **only when a frame passes**, and then takes
  *every* command waiting, answering each `0 Success` (or an error code and
  its text, like `22 Invalid argument`);
- between frames, commands simply wait in the socket.

The test calls `frame()` to let one frame pass. It uses ZeroMQ's in-process
transport (`inproc://`), so no network port is opened.
"""

import itertools

import zmq

_names = itertools.count()


class FakeZmqFilter:
    """A REP socket on `endpoint`; `frame()` processes what is waiting, like the real filter."""

    def __init__(self, context: zmq.Context[zmq.Socket[bytes]]) -> None:
        self.endpoint = f"inproc://fake-zmq-filter-{next(_names)}"
        self._socket = context.socket(zmq.REP)
        self._socket.setsockopt(zmq.LINGER, 0)
        self._socket.bind(self.endpoint)
        # Every command received, in order, and grouped by the frame that took it.
        self.commands: list[str] = []
        self.frames: list[list[str]] = []
        # Set to make the next answers errors (ffmpeg refusing a value).
        self.refuse = False

    def frame(self, wait_ms: int = 200) -> list[str]:
        """One frame passes: take every command waiting (waiting up to `wait_ms` for the first).

        Like the real filter, it takes what is there and no more: a command
        sent after it answered waits for the next frame. Tests wait until the
        sender has sent what they expect before letting a frame pass.
        """
        taken: list[str] = []
        timeout = wait_ms
        while self._socket.poll(timeout):
            command = self._socket.recv().decode("ascii")
            taken.append(command)
            answer = b"22 Invalid argument" if self.refuse else b"0 Success"
            self._socket.send(answer)
            timeout = 0
        self.commands.extend(taken)
        self.frames.append(taken)
        return taken

    def close(self) -> None:
        self._socket.close()
