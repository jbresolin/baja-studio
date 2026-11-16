"""One camera's video pipeline: the interface the node uses (WP15 brief §4), and its supervisor.

`CameraPipeline` keeps one ffmpeg running for its camera (command line in
`ffmpeg_command.py`) on a supervisor thread of its own:

    test the encoder (once) ─► launch ffmpeg ─► start a window sender
          ▲                          │
          │                watch it: frames moving? still alive?
          │                          │ died / stalled
          └── wait (backoff) ◄── stop it, reap it, say why in operator words

Meanwhile, from any thread and without ever waiting for ffmpeg:

- `set_window(window)` stores the newest window and hands it to the current
  run's sender (coalesced: only the newest is sent, at the next frame);
  the window survives restarts (each run starts at it);
- `latest_snapshot()` is the newest whole-frame JPEG (None while not running);
- `status()` is the state, its reason, rates, drops and the encoder.
"""

import logging
import threading
from collections import deque

from baja_studio.core.clock import Clock
from baja_studio.core.worker import MAX_RESTART_DELAY_SECONDS, FailureStreak
from baja_studio.node.video.crop_commands import CropPixels, crop_pixels
from baja_studio.node.video.encoders import PROFILES, choose_encoder
from baja_studio.node.video.ffmpeg_command import build_ffmpeg_command
from baja_studio.node.video.ffmpeg_log import (
    CAPTURE_DROP_MARKER,
    LOG_TAIL_LINES,
    Failure,
    FailureKind,
    LogTail,
    explain_failure,
    live_output_failed,
    url_secrets,
)
from baja_studio.node.video.ffmpeg_process import FfmpegNotFoundError, RunningFfmpeg
from baja_studio.node.video.pipeline_parts import (
    PipelineParts,
    SenderFactory,
    WindowSending,
    ZmqSenders,
)
from baja_studio.node.video.progress import ProgressTracker
from baja_studio.node.video.restart_policy import STABLE_RUN_SECONDS, RestartBackoff, stall_reason
from baja_studio.node.video.snapshots import MultipartJpegReader
from baja_studio.node.video.spec import (
    AUTO_ENCODER,
    PipelineSpec,
    PipelineState,
    PipelineStatus,
    SourceWindow,
)

__all__ = ["CameraPipeline", "PipelineSpec", "PipelineState", "PipelineStatus", "SourceWindow"]

# ffmpeg warnings that mean nothing for us, kept out of the tail. swscale says
# this of the JPEG formats (yuvj*) that MJPEG cameras and snapshots use.
HARMLESS_LOG_MARKERS = ("deprecated pixel format used",)
# How often the supervisor looks at a running ffmpeg (exit, frames, stall).
WATCH_INTERVAL_SECONDS = 0.25
Logger = logging.Logger | logging.LoggerAdapter[logging.Logger]


class _Run:
    """One ffmpeg run's own pieces: its progress, its last log lines, and the process."""

    def __init__(self, clock: Clock) -> None:
        self.progress = ProgressTracker(clock)
        self.process: RunningFfmpeg | None = None
        self.sender: WindowSending | None = None
        # Written by the stderr thread, read by the supervisor: a deque must not
        # be copied while another thread appends to it, hence the lock.
        self._lines_lock = threading.Lock()
        self._lines: deque[str] = deque(maxlen=LOG_TAIL_LINES)

    def add_line(self, line: str) -> None:
        with self._lines_lock:
            self._lines.append(line)

    def lines(self) -> list[str]:
        with self._lines_lock:
            return list(self._lines)


class CameraPipeline:
    """Keeps one camera's ffmpeg running; see the module docstring."""

    def __init__(
        self,
        spec: PipelineSpec,
        clock: Clock,
        logger: Logger,
        parts: PipelineParts | None = None,
    ) -> None:
        """Ready to run `spec`; nothing starts until `start()`."""
        self.spec = spec
        self._clock = clock
        self._logger = logger
        self._parts = parts or PipelineParts()
        self._lock = threading.Lock()
        self._stop_requested = threading.Event()
        self._thread: threading.Thread | None = None
        self._zmq_senders: ZmqSenders | None = None
        # Everything below is guarded by `_lock`.
        self._window = SourceWindow.whole()
        self._run: _Run | None = None
        self._snapshot: bytes | None = None
        self._state = PipelineState.STOPPED
        self._reason = ""
        self._since = clock.now_utc()
        self._encoder: str | None = None
        self._restarts = 0
        self._log = LogTail(url_secrets(spec.output_url))

    # --- the interface -----------------------------------------------------------------

    def start(self) -> None:
        """Start the supervisor thread (it launches ffmpeg); does nothing if already started."""
        if self._thread is not None:
            return
        self._stop_requested.clear()
        self._thread = threading.Thread(
            target=self._supervise, name=f"node-video.{self.spec.camera_name}", daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
        """Stop ffmpeg and the supervisor, and wait for both; the window is kept."""
        thread = self._thread
        if thread is None:
            return
        self._stop_requested.set()
        thread.join()
        self._thread = None
        if self._zmq_senders is not None:
            self._zmq_senders.close()
            self._zmq_senders = None
        self._set_state(PipelineState.STOPPED, "")

    def set_window(self, window: SourceWindow) -> None:
        """Show `window` from the next frame on. Any thread; never waits for ffmpeg."""
        pixels = self._pixels(window)
        with self._lock:
            self._window = window
            sender = self._run.sender if self._run is not None else None
        if sender is not None:
            sender.offer(pixels)

    def latest_snapshot(self) -> bytes | None:
        """The newest JPEG of the whole frame (about 320 px wide, about 1/s); None if none."""
        with self._lock:
            return self._snapshot

    def status(self) -> PipelineStatus:
        """The state, its reason, and the latest numbers."""
        with self._lock:
            run, state = self._run, self._state
            reason, since, encoder = self._reason, self._since, self._encoder
            restarts = self._restarts
        running = run is not None and state == PipelineState.RUNNING
        numbers = run.progress.numbers() if run is not None else None
        sender = run.sender if run is not None else None
        return PipelineStatus(
            state=state,
            reason=reason,
            fps_in=numbers.fps_in if numbers and running else 0.0,
            fps_out=numbers.fps_out if numbers and running else 0.0,
            dropped_frames=numbers.dropped_frames if numbers else 0,
            bitrate_kbps=numbers.bitrate_kbps if numbers and running else 0.0,
            encoder=encoder,
            since=since,
            restarts=restarts,
            window_apply_ms=sender.median_apply_ms() if sender is not None else None,
        )

    def log_tail(self) -> list[str]:
        """ffmpeg's recent warnings and errors (no progress lines, no secrets), oldest first."""
        return self._log.lines()

    # --- the supervisor thread ------------------------------------------------------------

    def _pixels(self, window: SourceWindow) -> CropPixels:
        spec = self.spec
        return crop_pixels(window, spec.input_width, spec.input_height, spec.output_aspect)

    def _set_state(self, state: PipelineState, reason: str) -> None:
        with self._lock:
            if state != self._state:
                self._since = self._clock.now_utc()
            self._state, self._reason = state, reason

    def _supervise(self) -> None:
        backoff = RestartBackoff()
        failures = FailureStreak()
        while not self._stop_requested.is_set():
            try:
                failure, fatal = self._one_run(backoff, failures)
            except Exception as error:
                # A bug of ours must not leave the camera dead with a stale status:
                # it is logged with its traceback and handled like a failed run.
                self._logger.exception("camera %s: pipeline error", self.spec.camera_name)
                failure, fatal = Failure(FailureKind.OTHER, f"internal error: {error}"), False
            if failure is None:
                return
            with self._lock:
                self._snapshot = None
                self._restarts += 1
            if failure.kind == FailureKind.ENCODER and self.spec.encoder == AUTO_ENCODER:
                with self._lock:
                    self._encoder = None  # test the encoders again before the next run
            if failures.record(RuntimeError(failure.reason)):
                self._logger.warning("camera %s: %s", self.spec.camera_name, failure.reason)
            else:
                self._logger.debug(
                    "camera %s: %s (%d in a row)",
                    self.spec.camera_name,
                    failure.reason,
                    failures.same_in_a_row,
                )
            delay = MAX_RESTART_DELAY_SECONDS if fatal else backoff.next_delay()
            state = PipelineState.FAILED if fatal else PipelineState.RESTARTING
            self._set_state(state, f"{failure.reason} — trying again in {delay:g} s")
            self._clock.wait(self._stop_requested, delay)

    def _choose_encoder(self) -> tuple[str | None, str]:
        """The encoder to run with (tested once, again after an encoder failure)."""
        with self._lock:
            if self._encoder is not None:
                return self._encoder, ""
        choice = choose_encoder(self.spec.encoder, self.spec.ffmpeg_path, self._parts.probe_encoder)
        if choice.encoder is None:
            return None, choice.reason()
        for name, message in choice.failures:
            self._logger.info("camera %s: %s not usable: %s", self.spec.camera_name, name, message)
        with self._lock:
            self._encoder = choice.encoder
        return choice.encoder, ""

    def _sender_factory(self) -> SenderFactory:
        if self._parts.make_sender is not None:
            return self._parts.make_sender
        if self._zmq_senders is None:
            self._zmq_senders = ZmqSenders(self._clock)
        return self._zmq_senders

    def _one_run(
        self, backoff: RestartBackoff, failures: FailureStreak
    ) -> tuple[Failure | None, bool]:
        """Launch, watch and stop one ffmpeg. Returns why it ended (None: asked to stop)
        and whether that is fatal (a retry alone will not fix it)."""
        spec = self.spec
        self._set_state(PipelineState.STARTING, "")
        if not self._parts.ffmpeg_exists(spec.ffmpeg_path):
            return Failure(FailureKind.OTHER, f"ffmpeg not found: {spec.ffmpeg_path}"), True
        encoder, why_not = self._choose_encoder()
        if encoder is None:
            return Failure(FailureKind.ENCODER, why_not), True
        if self._stop_requested.is_set():
            return None, False
        port = self._parts.free_port()
        with self._lock:
            window = self._window
        pixels = self._pixels(window)
        run = _Run(self._clock)
        reader = MultipartJpegReader(self._keep_snapshot)
        self._log.add(f"--- ffmpeg starting ({PROFILES[encoder].label}, {spec.camera_name})")
        try:
            run.process = self._parts.launch(
                build_ffmpeg_command(spec, encoder, pixels, port),
                lambda line: self._take_line(run, line),
                reader.feed,
            )
        except FfmpegNotFoundError as error:
            return Failure(FailureKind.OTHER, str(error)), True
        # From here on ffmpeg runs: whatever happens, it is stopped and reaped below.
        try:
            run.sender = self._sender_factory()(port, pixels, self._logger)
            run.sender.start()
            with self._lock:
                self._run = run
                newest = self._window
            if newest != window:
                run.sender.offer(self._pixels(newest))
            failure = self._watch(run, backoff, failures)
        finally:
            with self._lock:
                self._run = None
            if run.sender is not None:
                run.sender.stop()
            run.process.stop()
        return failure, False

    def _take_line(self, run: _Run, line: str) -> None:
        """One stderr line of a run: progress, a capture drop, or a log line."""
        if run.progress.feed_line(line):
            return
        if any(marker in line for marker in HARMLESS_LOG_MARKERS):
            return
        if CAPTURE_DROP_MARKER in line:
            # One per lost frame: counted, not kept (it would push everything else out).
            run.progress.count_capture_drop()
            return
        run.add_line(self._log.add(line))

    def _keep_snapshot(self, jpeg: bytes) -> None:
        with self._lock:
            self._snapshot = jpeg

    def _watch(self, run: _Run, backoff: RestartBackoff, failures: FailureStreak) -> Failure | None:
        """Wait while the run is fine; return why it is not (None if asked to stop)."""
        assert run.process is not None
        launched_at = self._clock.monotonic()
        running_since: float | None = None
        while not self._clock.wait(self._stop_requested, WATCH_INTERVAL_SECONDS):
            if run.process.exited.is_set() or live_output_failed(run.lines()):
                return explain_failure(
                    run.process.exit_code,
                    run.lines(),
                    self.spec.camera_name,
                    self.spec.output_url,
                )
            now = self._clock.monotonic()
            frames_changed_at = run.progress.frames_changed_at()
            if frames_changed_at is not None and running_since is None:
                running_since = now
                self._set_state(PipelineState.RUNNING, "")
                recovered = failures.recover()
                if recovered:
                    self._logger.info(
                        "camera %s: running again after %d failures",
                        self.spec.camera_name,
                        recovered,
                    )
            if running_since is not None and now - running_since >= STABLE_RUN_SECONDS:
                backoff.reset()
            stalled = stall_reason(now, launched_at, frames_changed_at)
            if stalled is not None:
                return Failure(FailureKind.OTHER, stalled)
        return None
