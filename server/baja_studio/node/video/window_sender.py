"""Sending the crop window to a running ffmpeg, as fast as frames go and never in the caller's way.

ffmpeg's `zmq` filter is a ZeroMQ REP socket: it takes commands like
`crop@window x 1700-ow/2` and answers each with `0 Success` (or an error
code). It looks for commands only when a frame passes, and then takes every
command waiting. So:

- we use a **DEALER** socket, which may send several commands without
  waiting for each answer (a REQ socket could send one per frame, and a
  window change can be three);
- `offer()` only stores the newest window and wakes our thread: callers
  (the motion loop, at 25-30 updates a second, from any thread) never wait
  for ffmpeg, even a hung one;
- the thread sends a window only when the previous one has been answered,
  so at most one change waits for each frame and a burst of updates
  collapses into the newest (an answer that never comes is given up after
  `ANSWER_TIMEOUT_SECONDS`);
- the time from sending a change to its answer is when the change reached
  a frame: we keep the recent ones for the status (`apply_times_ms`).

One sender serves one ffmpeg run; the pipeline starts a new one with each run
(the zmq port changes with it).
"""

import logging
import statistics
import threading
from collections import deque

import zmq

from baja_studio.core.clock import Clock
from baja_studio.node.video.crop_commands import CropPixels, window_commands

ANSWER_TIMEOUT_SECONDS = 1.0
# How long the thread waits for an answer before looking at new windows again.
ANSWER_POLL_MILLISECONDS = 5
APPLY_TIMES_KEPT = 30
Logger = logging.Logger | logging.LoggerAdapter[logging.Logger]


class WindowSender:
    """Owns one DEALER socket connected to one ffmpeg's zmq filter, on its own thread."""

    def __init__(
        self,
        context: zmq.Context[zmq.Socket[bytes]],
        endpoint: str,
        starting_window: CropPixels,
        clock: Clock,
        logger: Logger,
        name: str = "window-sender",
    ) -> None:
        """Ready to send to `endpoint`, where ffmpeg already shows `starting_window`."""
        self._context = context
        self._endpoint = endpoint
        self._clock = clock
        self._logger = logger
        self._name = name
        self._lock = threading.Lock()
        self._pending: CropPixels | None = None
        self._last_sent = starting_window
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        # Send time of the change waiting for answers, and how many answers it waits for.
        self._waiting_since: float | None = None
        self._answers_due = 0
        self._apply_times_ms: deque[float] = deque(maxlen=APPLY_TIMES_KEPT)
        self._refused_logged = False
        self.commands_sent = 0
        self.answers = 0

    def start(self) -> None:
        """Start the thread (it connects; ZeroMQ queues until ffmpeg's filter is listening)."""
        self._thread = threading.Thread(target=self._run, name=self._name, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        """Stop the thread and close the socket; returns once both are done."""
        self._stop.set()
        self._wake.set()
        if self._thread is not None:
            self._thread.join()

    def offer(self, window: CropPixels) -> None:
        """Make `window` the next one to send, replacing any not yet sent. Never blocks."""
        with self._lock:
            self._pending = window
        self._wake.set()

    def apply_times_ms(self) -> list[float]:
        """Recent send-to-answer times, oldest first."""
        with self._lock:
            return list(self._apply_times_ms)

    def median_apply_ms(self) -> float | None:
        """The median of the recent send-to-answer times, None before the first."""
        times = self.apply_times_ms()
        return statistics.median(times) if times else None

    # --- the thread ----------------------------------------------------------------------

    def _run(self) -> None:
        socket = self._context.socket(zmq.DEALER)
        # Closing must never wait for unsent commands to a dead ffmpeg.
        socket.setsockopt(zmq.LINGER, 0)
        try:
            socket.connect(self._endpoint)
            while not self._stop.is_set():
                self._step(socket)
        except zmq.ZMQError as error:
            # The window stops following until the next run; the run itself goes on.
            self._logger.error("window sender stopped: %s", error)
        finally:
            socket.close()

    def _step(self, socket: zmq.Socket[bytes]) -> None:
        self._read_answers(socket)
        if self._answers_due and not self._answer_overdue():
            # A change is waiting for its frame: wait for the answer, briefly.
            socket.poll(ANSWER_POLL_MILLISECONDS)
            return
        if self._answers_due:
            self._logger.debug(
                "window change unanswered for %.1f s; sending the next", ANSWER_TIMEOUT_SECONDS
            )
            self._answers_due = 0
            self._waiting_since = None
        with self._lock:
            window, self._pending = self._pending, None
        if window is None:
            self._wake.wait(0.1)
            self._wake.clear()
            return
        commands = window_commands(self._last_sent, window)
        self._last_sent = window
        if not commands:
            return
        for command in commands:
            socket.send_multipart([b"", command.encode("ascii")])
        self.commands_sent += len(commands)
        self._answers_due = len(commands)
        self._waiting_since = self._clock.monotonic()

    def _answer_overdue(self) -> bool:
        return (
            self._waiting_since is not None
            and self._clock.monotonic() - self._waiting_since > ANSWER_TIMEOUT_SECONDS
        )

    def _read_answers(self, socket: zmq.Socket[bytes]) -> None:
        while socket.poll(0):
            # A DEALER sees the REP's answer as [empty delimiter, answer].
            answer = socket.recv_multipart()[-1]
            self.answers += 1
            if not answer.startswith(b"0 ") and not self._refused_logged:
                # Logged once per run: a refused window repeats with every update.
                self._refused_logged = True
                text = answer.decode("utf-8", errors="replace")
                self._logger.warning("ffmpeg refused a window change: %s", text)
            if self._answers_due == 0:
                continue  # a late answer to a change already given up on
            self._answers_due -= 1
            if self._answers_due == 0 and self._waiting_since is not None:
                elapsed_ms = (self._clock.monotonic() - self._waiting_since) * 1000
                with self._lock:
                    self._apply_times_ms.append(elapsed_ms)
                self._waiting_since = None
