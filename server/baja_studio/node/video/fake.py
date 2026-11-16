"""Fake video for the node's tests and the rig: a pipeline, its factory, and capture devices.

`FakeCameraPipeline` has the pipeline's interface (`pipeline_types.VideoPipeline`)
and behaves as the real one was measured to (ADR 0023), minus the video:

- `start()` makes it `running` at once, or `starting` for `startup_seconds`
  first (the real one is `starting` for about a second);
- `set_window` never blocks and keeps every window it was given, in order
  (`windows`), and the newest (`window`, `last_window`), which survives a
  failure and `stop`/`start` like the real one;
- `latest_snapshot()` is a 320x180 JPEG of a test card while running, None
  otherwise (the real one clears its snapshot when ffmpeg fails);
- `status()` reports the camera's rate in and out, no drops, the spec's
  bitrate and the encoder (`libx264` for "auto", else the one asked for),
  unless a test scripts something else: `script_status` (any state, with
  counts), or `fail(reason)` / `recover()` (ffmpeg died or the SRT receiver
  went away, then came back).

`FakePipelines` is a factory that keeps every fake it made, and
`FakeCaptureDevices` lists two EMEET S600s, as João's laptops have.
"""

import threading
from dataclasses import replace
from importlib import resources

from baja_studio.cameras.driver import Logger
from baja_studio.core.clock import Clock
from baja_studio.node.video.capture_devices import CaptureDevice, CaptureMode
from baja_studio.node.video.spec import (
    AUTO_ENCODER,
    PipelineSpec,
    PipelineState,
    PipelineStatus,
    SourceWindow,
)

FAKE_ENCODER_FOR_AUTO = "libx264"
# Windows kept for tests to read: a few seconds' worth at 25 updates a second.
WINDOWS_KEPT = 500


def fake_snapshot_jpeg() -> bytes:
    """The fake's whole-frame picture: a 320x180 test card (a real JPEG)."""
    return (resources.files("baja_studio.node.video") / "fake_snapshot.jpg").read_bytes()


class FakeCameraPipeline:
    """Same methods as `CameraPipeline`; see the module docstring."""

    def __init__(
        self, spec: PipelineSpec, clock: Clock, logger: Logger, startup_seconds: float = 0.0
    ) -> None:
        """Stopped, showing the whole frame; once started, `starting` for `startup_seconds`."""
        self.spec = spec
        self._clock = clock
        self._logger = logger
        self._startup_seconds = startup_seconds
        self._lock = threading.Lock()
        self.windows: list[SourceWindow] = []
        self.window = SourceWindow.whole()
        self.starts = 0
        self.stops = 0
        self._snapshot: bytes | None = fake_snapshot_jpeg()
        self._scripted: PipelineStatus | None = None
        self._status = self._stopped_status()
        self._started_at: float | None = None
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

    def _starting_status(self) -> PipelineStatus:
        return replace(self._stopped_status(), state=PipelineState.STARTING)

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
        """Running at once, or after `startup_seconds` (does nothing if already started)."""
        with self._lock:
            if self._status.state != PipelineState.STOPPED:
                return
            self.starts += 1
            self._started_at = self._clock.monotonic()
            self._status = (
                self._starting_status() if self._startup_seconds > 0 else self._running_status()
            )
            self._log_lines.append("--- ffmpeg starting (fake)")
        self._logger.info("fake pipeline started for %s", self.spec.camera_name)

    def stop(self) -> None:
        """Stopped; the window is kept."""
        with self._lock:
            if self._status.state == PipelineState.STOPPED:
                return
            self.stops += 1
            self._status = self._stopped_status()
            self._scripted = None
            self._started_at = None

    def set_window(self, window: SourceWindow) -> None:
        """Record the window; never blocks."""
        with self._lock:
            self.window = window
            self.windows.append(window)
            del self.windows[:-WINDOWS_KEPT]

    def latest_snapshot(self) -> bytes | None:
        """The test card while running, None otherwise."""
        with self._lock:
            state = self._current().state
            return self._snapshot if state == PipelineState.RUNNING else None

    def status(self) -> PipelineStatus:
        """What a test scripted, else stopped / starting / running as above."""
        with self._lock:
            return self._current()

    def log_tail(self) -> list[str]:
        """Lines a test added with `add_log_line`, after the start markers."""
        with self._lock:
            return list(self._log_lines)

    # --- for tests and the rig ---------------------------------------------------------

    @property
    def last_window(self) -> SourceWindow | None:
        """The newest window the node sent, if any."""
        with self._lock:
            return self.windows[-1] if self.windows else None

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

    def fail(self, reason: str) -> None:
        """Behave as if ffmpeg died or the receiver went away: `restarting`, saying why."""
        self.script_status(PipelineState.RESTARTING, reason)

    def recover(self) -> None:
        """Running again (the window it had is still applied)."""
        with self._lock:
            self._scripted = None
            self._status = self._running_status()

    def set_snapshot(self, jpeg: bytes | None) -> None:
        """Serve another picture (None: no picture even while running)."""
        with self._lock:
            self._snapshot = jpeg

    def add_log_line(self, line: str) -> None:
        """Add a line to the log tail (as if ffmpeg had written it)."""
        with self._lock:
            self._log_lines.append(line)

    def _current(self) -> PipelineStatus:
        """The status now (lock held): scripted, else `starting` turning `running` on time."""
        if self._scripted is not None:
            return self._scripted
        started = self._started_at
        starting = self._status.state == PipelineState.STARTING and started is not None
        if starting and self._clock.monotonic() - (started or 0.0) >= self._startup_seconds - 1e-9:
            self._status = self._running_status()
        return self._status


class FakePipelines:
    """A pipeline factory that keeps every fake it made, for tests and the rig to script."""

    def __init__(self, startup_seconds: float = 0.0) -> None:
        """Pipelines made by this factory stay `starting` for `startup_seconds`."""
        self._startup_seconds = startup_seconds
        self._lock = threading.Lock()
        self.made: list[FakeCameraPipeline] = []

    def __call__(self, spec: PipelineSpec, clock: Clock, logger: Logger) -> FakeCameraPipeline:
        """The factory: a new fake pipeline for `spec`."""
        pipeline = FakeCameraPipeline(spec, clock, logger, self._startup_seconds)
        with self._lock:
            self.made.append(pipeline)
        return pipeline

    def for_device(self, device: str) -> FakeCameraPipeline | None:
        """The newest pipeline made for `device`."""
        with self._lock:
            matching = [pipeline for pipeline in self.made if pipeline.spec.device == device]
        return matching[-1] if matching else None


# Two S600s on one laptop: same name, told apart by their DirectShow device path.
FAKE_S600_NAME = "EMEET SmartCam S600"
FAKE_DEVICES = (
    CaptureDevice(FAKE_S600_NAME, "@device_pnp_\\\\?\\usb#vid_328f&pid_00c0&mi_00#fake-a"),
    CaptureDevice(FAKE_S600_NAME, "@device_pnp_\\\\?\\usb#vid_328f&pid_00c0&mi_00#fake-b"),
)
FAKE_MODES = (
    CaptureMode("mjpeg", 3840, 2160, 30.0, 5.0),
    CaptureMode("mjpeg", 2560, 1440, 30.0, 5.0),
    CaptureMode("mjpeg", 1920, 1080, 60.0, 5.0),
    CaptureMode("mjpeg", 1920, 1080, 30.0, 5.0),
)


class FakeCaptureDevices:
    """Capture devices with two S600s; `devices` can be changed to plug and unplug them."""

    def __init__(self) -> None:
        """Both S600s plugged in."""
        self.devices = list(FAKE_DEVICES)

    def list_capture_devices(self, ffmpeg_path: str = "ffmpeg") -> list[CaptureDevice]:
        """The devices plugged in now (`ffmpeg_path` is not used)."""
        return list(self.devices)

    def capture_modes(self, device: str, ffmpeg_path: str = "ffmpeg") -> list[CaptureMode]:
        """An S600's modes (none for a device that is not plugged in)."""
        known = any(listed.device == device for listed in self.devices)
        return list(FAKE_MODES) if known else []
