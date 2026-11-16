"""`FakeCameraPipeline`: the `CameraPipeline` interface without ffmpeg, for tests and the rig.

It behaves as the real pipeline was measured to (ADR 0023), minus the video:

- `start()` makes it `running` at once (the real one is `starting` for about
  a second first: a test that needs that sets it with `script_status`);
- `set_window` never blocks and keeps every window it was given, in order
  (`windows`), and the latest (`window`), which survives `stop`/`start`
  like the real one;
- `latest_snapshot()` is a 320x180 JPEG of a test card while running, None
  otherwise (the real one clears its snapshot when ffmpeg fails);
- `status()` reports the camera's rate in and out, no drops, 8 Mb/s and the
  encoder (`libx264` for "auto", else the one asked for), unless a test
  scripts something else with `script_status` (a failure, a restart).
"""

import threading
from dataclasses import replace
from importlib import resources

from baja_studio.core.clock import Clock
from baja_studio.node.video.pipeline import Logger
from baja_studio.node.video.spec import (
    AUTO_ENCODER,
    PipelineSpec,
    PipelineState,
    PipelineStatus,
    SourceWindow,
)

FAKE_ENCODER_FOR_AUTO = "libx264"


def fake_snapshot_jpeg() -> bytes:
    """The fake's whole-frame picture: a 320x180 test card (a real JPEG)."""
    return (resources.files("baja_studio.node.video") / "fake_snapshot.jpg").read_bytes()


class FakeCameraPipeline:
    """Same methods as `CameraPipeline`; see the module docstring."""

    def __init__(self, spec: PipelineSpec, clock: Clock, logger: Logger) -> None:
        """Stopped, showing the whole frame."""
        self.spec = spec
        self._clock = clock
        self._logger = logger
        self._lock = threading.Lock()
        self.windows: list[SourceWindow] = []
        self.window = SourceWindow.whole()
        self.starts = 0
        self.stops = 0
        self._snapshot: bytes | None = fake_snapshot_jpeg()
        self._scripted: PipelineStatus | None = None
        self._status = self._stopped_status()
        self._log_lines: list[str] = []

    def _stopped_status(self) -> PipelineStatus:
        return PipelineStatus(
            state=PipelineState.STOPPED,
            reason="",
            fps_in=0.0,
            fps_out=0.0,
            dropped_frames=0,
            bitrate_kbps=0.0,
            encoder=None,
            since=self._clock.now_utc(),
        )

    def _running_status(self) -> PipelineStatus:
        encoder = FAKE_ENCODER_FOR_AUTO if self.spec.encoder == AUTO_ENCODER else self.spec.encoder
        return PipelineStatus(
            state=PipelineState.RUNNING,
            reason="",
            fps_in=self.spec.input_fps,
            fps_out=self.spec.input_fps,
            dropped_frames=0,
            bitrate_kbps=float(self.spec.bitrate_kbps),
            encoder=encoder,
            since=self._clock.now_utc(),
            window_apply_ms=20.0,
        )

    # --- the interface -----------------------------------------------------------------

    def start(self) -> None:
        """Running at once (does nothing if already running)."""
        with self._lock:
            if self._status.state != PipelineState.STOPPED:
                return
            self.starts += 1
            self._status = self._running_status()
            self._log_lines.append("--- ffmpeg starting (fake)")

    def stop(self) -> None:
        """Stopped; the window is kept."""
        with self._lock:
            if self._status.state == PipelineState.STOPPED:
                return
            self.stops += 1
            self._status = self._stopped_status()
            self._scripted = None

    def set_window(self, window: SourceWindow) -> None:
        """Record the window; never blocks."""
        with self._lock:
            self.window = window
            self.windows.append(window)

    def latest_snapshot(self) -> bytes | None:
        """The test card while running, None otherwise."""
        with self._lock:
            state = (self._scripted or self._status).state
            return self._snapshot if state == PipelineState.RUNNING else None

    def status(self) -> PipelineStatus:
        """What a test scripted, else running/stopped as above."""
        with self._lock:
            return self._scripted or self._status

    def log_tail(self) -> list[str]:
        """Lines a test added with `add_log_line`, after the start markers."""
        with self._lock:
            return list(self._log_lines)

    # --- for tests and the rig ---------------------------------------------------------

    def script_status(
        self, state: PipelineState, reason: str = "", restarts: int = 0, dropped_frames: int = 0
    ) -> None:
        """Report `state` with `reason` (and these counts) until the next start or stop."""
        with self._lock:
            base = self._running_status() if state == PipelineState.RUNNING else self._status
            self._scripted = replace(
                base,
                state=state,
                reason=reason,
                since=self._clock.now_utc(),
                restarts=restarts,
                dropped_frames=dropped_frames,
            )

    def set_snapshot(self, jpeg: bytes | None) -> None:
        """Serve another picture (None: no picture even while running)."""
        with self._lock:
            self._snapshot = jpeg

    def add_log_line(self, line: str) -> None:
        """Add a line to the log tail (as if ffmpeg had written it)."""
        with self._lock:
            self._log_lines.append(line)
