"""A fake video pipeline and fake capture devices, for the node's tests and the rig (ADR 0023).

`FakeCameraPipeline` has `CameraPipeline`'s interface (pipeline_types.py)
and does no video at all:

- it records every window it is given (`windows`, the newest last), so a
  test can see what the node sent and how often;
- its snapshot is a JPEG test card of the whole 16:9 frame
  (`fakes/blocky_jpeg.py`), a new one each second, once it is running;
- its status is scripted: `starting` until `startup_seconds` have passed
  (0 by default: running at once), then `running` with the spec's frame
  rate and a chosen encoder; `fail(reason)` puts it in `restarting` with that
  reason until `recover()`, as the real one does when ffmpeg dies or the
  SRT receiver goes away. The window it was given stays across a failure.

It models the contract of §4, not measured behaviour (there is no real
pipeline to measure yet); Part B's pipeline replaces it outside tests and
the rig. `FakeCaptureDevices` lists two EMEET S600s, as João's laptops have.
"""

import threading
from collections import deque

from baja_studio.cameras.driver import Logger
from baja_studio.core.clock import Clock
from baja_studio.fakes.blocky_jpeg import card_jpeg
from baja_studio.node.video.pipeline_types import (
    ENCODER_AUTO,
    CaptureDevice,
    CaptureMode,
    PipelineSpec,
    PipelineState,
    PipelineStatus,
    PipelineWindow,
)

SNAPSHOT_WIDTH = 320
# The encoder a fake "auto" settles on (what the CI runner's ffmpeg has).
FAKE_AUTO_ENCODER = "libx264"
# Windows kept for tests to read: a few seconds' worth at 25 updates a second.
WINDOWS_KEPT = 500


class FakeCameraPipeline:
    """One fake camera pipeline."""

    def __init__(
        self, spec: PipelineSpec, clock: Clock, logger: Logger, startup_seconds: float = 0.0
    ) -> None:
        """Not started; `startup_seconds` is how long it stays `starting`."""
        self.spec = spec
        self._clock = clock
        self._logger = logger
        self._startup_seconds = startup_seconds
        self._lock = threading.Lock()
        self._state = PipelineState.STOPPED
        self._reason = ""
        self._since = clock.monotonic()
        self._started_at: float | None = None
        self.windows: deque[PipelineWindow] = deque(maxlen=WINDOWS_KEPT)
        self.starts = 0
        self.stops = 0

    # --- CameraPipeline --------------------------------------------------------------

    def start(self) -> None:
        """Start "capturing"; running once `startup_seconds` have passed."""
        with self._lock:
            self.starts += 1
            self._started_at = self._clock.monotonic()
            self._set(PipelineState.STARTING, "")
        self._logger.info("fake pipeline started for %s", self.spec.device)

    def stop(self) -> None:
        """Stop at once (there is no child to wait for)."""
        with self._lock:
            self.stops += 1
            self._started_at = None
            self._set(PipelineState.STOPPED, "")

    def set_window(self, window: PipelineWindow) -> None:
        """Record the window; never blocks."""
        with self._lock:
            self.windows.append(window)

    def latest_snapshot(self) -> bytes | None:
        """A test card of the whole frame while running (a new shade every second), else None."""
        if self.status().state != PipelineState.RUNNING:
            return None
        height = SNAPSHOT_WIDTH * self.spec.input_height // self.spec.input_width
        second = int(self._clock.monotonic())
        return card_jpeg(SNAPSHOT_WIDTH, height, shade=second * 16)

    def status(self) -> PipelineStatus:
        """The scripted state; while running, the spec's frame rate in and out."""
        with self._lock:
            self._finish_starting()
            running = self._state == PipelineState.RUNNING
            fps = float(self.spec.input_fps) if running else 0.0
            encoder = self.spec.encoder
            return PipelineStatus(
                state=self._state,
                reason=self._reason,
                input_fps=fps,
                output_fps=fps,
                dropped_frames=0,
                bitrate_kbps=float(self.spec.bitrate_kbps) if running else 0.0,
                encoder=(FAKE_AUTO_ENCODER if encoder == ENCODER_AUTO else encoder),
                since=self._since,
            )

    # --- scripting (tests, the rig) ------------------------------------------------------

    def fail(self, reason: str) -> None:
        """Behave as if ffmpeg died or the receiver went away: `restarting`, saying why."""
        with self._lock:
            self._set(PipelineState.RESTARTING, reason)

    def recover(self) -> None:
        """Running again (the window it had is still applied)."""
        with self._lock:
            self._set(PipelineState.RUNNING, "")

    @property
    def last_window(self) -> PipelineWindow | None:
        """The newest window the node sent, if any."""
        with self._lock:
            return self.windows[-1] if self.windows else None

    def _finish_starting(self) -> None:
        if self._state != PipelineState.STARTING or self._started_at is None:
            return
        if self._clock.monotonic() - self._started_at >= self._startup_seconds - 1e-9:
            self._set(PipelineState.RUNNING, "")

    def _set(self, state: PipelineState, reason: str) -> None:
        if state != self._state:
            self._since = self._clock.monotonic()
        self._state, self._reason = state, reason


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


# Two S600s on one laptop: same name, told apart by their device path.
FAKE_S600_NAME = "EMEET SmartCam S600"
FAKE_DEVICES = (
    CaptureDevice(FAKE_S600_NAME, "@device_pnp_\\\\?\\usb#vid_328f&pid_00c0&mi_00#fake-a"),
    CaptureDevice(FAKE_S600_NAME, "@device_pnp_\\\\?\\usb#vid_328f&pid_00c0&mi_00#fake-b"),
)
FAKE_MODES = (
    CaptureMode(3840, 2160, 30.0),
    CaptureMode(2560, 1440, 30.0),
    CaptureMode(1920, 1080, 60.0),
    CaptureMode(1920, 1080, 30.0),
)


class FakeCaptureDevices:
    """`CaptureDevices` with two S600s; `devices` can be changed to plug and unplug them."""

    def __init__(self) -> None:
        """Both S600s plugged in."""
        self.devices = list(FAKE_DEVICES)

    def list_capture_devices(self) -> list[CaptureDevice]:
        """The devices plugged in now."""
        return list(self.devices)

    def capture_modes(self, device: str) -> list[CaptureMode]:
        """An S600's modes (none for a device that is not plugged in)."""
        known = any(listed.device == device for listed in self.devices)
        return list(FAKE_MODES) if known else []
