"""A camera in `node` video mode: the node's own pipeline crops the frame (ADR 0023).

It is a `CameraDriver` like the studio's `obs_crop`, and moves its window
the same way, with the same shared pieces: the held velocity and its
dead-man (`cameras/crop_motion.py`), the window maths (`cameras/crop_window.py`)
and what a crop camera publishes (`drivers/obs_crop_state.py`). Only where
the window goes differs: to the pipeline's `set_window`, not to an OBS
filter.

Its worker (`camera:<id>:pipeline`):

1. starts the pipeline when it starts (only with a device and an output URL:
   without them the camera stays `degraded`, saying what to fill in, and
   its window still moves, so the studio can set it up beforehand);
2. integrates the held velocity at `update_hz` while it moves, and applies
   jumps (`PUT /crop`, reset) at once, sending the window to the pipeline
   only when it changed;
3. reads the pipeline's status for the health: `running` is ok, `starting`
   and `restarting` are degraded with the reason, `failed` is offline;
4. stops the pipeline (and waits for it) when it stops.

The window lives here, not in the pipeline: a pipeline that restarts keeps
the window it was given (§4), and a new pipeline (a settings change) is
given the window at once. A velocity ends by itself when its ttl runs out,
whatever the pipeline is doing: the dead-man needs no message to anyone.
"""

from collections.abc import Callable
from dataclasses import replace

from baja_studio.agents.contract import CameraKind, Position, Status
from baja_studio.cameras.config import CameraConfig
from baja_studio.cameras.crop_motion import CropCommands, next_window
from baja_studio.cameras.crop_window import CropFrame, CropWindow, WindowTarget
from baja_studio.cameras.driver import CameraDriver, DriverCapabilities, DriverHost, Velocity
from baja_studio.cameras.drivers.obs_crop_state import CropPublisher, crop_state
from baja_studio.core.worker import HealthState, WorkerContext, WorkerSpec
from baja_studio.node.redact import scrub, secret_parts
from baja_studio.node.video.pipeline_types import (
    PipelineSpec,
    PipelineState,
    PipelineStatus,
    SourceWindow,
    VideoPipeline,
)

# At rest the worker still looks at the pipeline's status this often.
IDLE_SECONDS = 0.25
OUTPUT_ASPECT = 16 / 9
NO_DEVICE = "no capture device chosen: pick one on this node's page"
NO_OUTPUT = "no output URL: paste this camera's SRT URL from the studio's Feeds page"
_HEALTH_STATE = {
    Status.OK: HealthState.OK,
    Status.DEGRADED: HealthState.DEGRADED,
    Status.OFFLINE: HealthState.OFFLINE,
}

MakePipeline = Callable[[PipelineSpec], VideoPipeline]


def pipeline_health(status: PipelineStatus) -> tuple[Status, str]:
    """A pipeline's status as a camera's health, in operator words."""
    if status.state == PipelineState.RUNNING:
        return Status.OK, ""
    if status.state == PipelineState.FAILED:
        return Status.OFFLINE, f"pipeline failed: {status.reason or 'no reason given'}"
    if status.state == PipelineState.RESTARTING:
        return Status.DEGRADED, f"pipeline restarting: {status.reason or 'no reason given'}"
    if status.state == PipelineState.STARTING:
        return Status.DEGRADED, "pipeline starting"
    return Status.OFFLINE, "pipeline stopped"


class PipelineCropDriver(CameraDriver):
    """One camera whose window is cut by the node's own pipeline."""

    def __init__(
        self,
        host: DriverHost,
        config: Callable[[], CameraConfig],
        spec: PipelineSpec,
        make_pipeline: MakePipeline,
        initial_window: CropWindow | None = None,
    ) -> None:
        """`config` (the camera as a crop camera, read every step) gives the limits and speeds;
        `spec` the video; `initial_window` the window a previous driver of this camera had."""
        self._host = host
        self._config = config
        self.spec = spec
        self._make_pipeline = make_pipeline
        self.worker_name = f"{host.worker_prefix}:pipeline"
        self._commands = CropCommands(host.clock)
        self._publisher = CropPublisher(host.publish)
        frame = self.frame()
        self.window = frame.clamp(initial_window) if initial_window else frame.whole()
        self._sent: SourceWindow | None = None
        self.pipeline: VideoPipeline | None = None
        self._health = (Status.DEGRADED, "starting")
        self._secrets = secret_parts(spec.output_url)
        self._worker: WorkerContext | None = None

    # --- CameraDriver ---------------------------------------------------------------------

    def connect(self) -> None:
        """Start the worker, which starts the pipeline."""
        self._host.start_worker(WorkerSpec(self.worker_name, self._run))

    def disconnect(self) -> None:
        """Stop the worker, which stops the pipeline and waits for it."""
        self._host.stop_worker(self.worker_name)

    def move(self, velocity: Velocity, ttl_ms: int) -> None:
        """Hold `velocity` for `ttl_ms`; the worker moves the window."""
        self._commands.move(velocity, ttl_ms / 1000)
        self._wake()

    def stop(self) -> None:
        """Stop moving the window; it stays where it is."""
        self._commands.stop()
        self._wake()

    def recall_preset(self, n: int) -> None:
        """Agent crop cameras keep no presets (the studio stores windows; contract §6)."""

    def store_preset(self, n: int) -> None:
        """Agent crop cameras keep no presets (the studio stores windows; contract §6)."""

    def place_window(self, target: WindowTarget) -> None:
        """Jump the window (`PUT /crop`, reset); any velocity stops."""
        self._commands.jump(target)
        self._wake()

    def position(self) -> Position | None:
        """The window's centre and zoom (the contract's crop position)."""
        return self._publisher.position

    def capabilities(self) -> DriverCapabilities:
        """A crop camera with position feedback and zoom; no auto, no presets of its own."""
        return DriverCapabilities(CameraKind.CROP, True, True, False, None)

    def health(self) -> Status:
        """ok while the pipeline runs; degraded while it starts or restarts; offline if failed."""
        return self._health[0]

    # --- what the node reads ------------------------------------------------------------------

    def frame(self) -> CropFrame:
        """The frame windows are cut from: the capture mode's size, 16:9 windows."""
        crop = self._config().crop
        return CropFrame(
            self.spec.input_width, self.spec.input_height, OUTPUT_ASPECT, crop.min_window
        )

    def snapshot(self) -> bytes | None:
        """The pipeline's newest whole-frame JPEG, if it has one."""
        pipeline = self.pipeline
        return None if pipeline is None else pipeline.latest_snapshot()

    def log_tail(self) -> list[str]:
        """ffmpeg's recent lines (for the node's page), with any piece of the output URL removed."""
        pipeline = self.pipeline
        if pipeline is None:
            return []
        return [scrub(line, self._secrets) for line in pipeline.log_tail()]

    def pipeline_status(self) -> PipelineStatus | None:
        """The pipeline's own status (for the node's page), None without a pipeline."""
        pipeline = self.pipeline
        return None if pipeline is None else self._scrubbed(pipeline.status())

    # --- the worker -----------------------------------------------------------------------------

    def _wake(self) -> None:
        worker = self._worker
        if worker is not None:
            worker.wake()

    def _run(self, worker: WorkerContext) -> None:
        self._worker = worker
        try:
            self.pipeline = self._start_pipeline()
            while not worker.should_stop:
                pause = self._step(self._host.clock.monotonic())
                status, reason = self._health
                worker.report_health(_HEALTH_STATE[status], reason)
                worker.sleep(pause)
        finally:
            self._worker = None
            pipeline, self.pipeline = self.pipeline, None
            if pipeline is not None:
                pipeline.stop()

    def _start_pipeline(self) -> VideoPipeline | None:
        if not self.spec.device:
            self._health = (Status.DEGRADED, NO_DEVICE)
            return None
        if not self.spec.output_url.strip():
            self._health = (Status.DEGRADED, NO_OUTPUT)
            return None
        pipeline = self._make_pipeline(self.spec)
        pipeline.start()
        self._sent = None  # a new pipeline is given the window at once
        return pipeline

    def _step(self, now: float) -> float:
        """One motion step and one look at the pipeline; returns how long to sleep."""
        frame = self.frame()
        jump, step = self._commands.take(now)
        window, error = next_window(frame, self.window, jump, step, self._config().crop)
        if window is not None:
            self.window = window
        self._send_window(frame)
        pipeline = self.pipeline
        if pipeline is not None:
            self._health = pipeline_health(self._scrubbed(pipeline.status()))
        moving = self._commands.moving
        self._publisher.publish(
            now,
            moving=moving,
            crop=crop_state(frame, self.window, self._config()),
            status=self._health[0],
            reason=self._health[1],
            last_error=error,
        )
        return 1.0 / self._config().crop.update_hz if moving else IDLE_SECONDS

    def _send_window(self, frame: CropFrame) -> None:
        rect = frame.rect(self.window)
        wanted = SourceWindow(rect["x"], rect["y"], rect["w"], rect["h"])
        if self.pipeline is not None and wanted != self._sent:
            self.pipeline.set_window(wanted)
            self._sent = wanted

    def _scrubbed(self, status: PipelineStatus) -> PipelineStatus:
        """The status with any piece of the output URL taken out of its reason."""
        if not status.reason:
            return status
        reason = scrub(status.reason, self._secrets)
        return status if reason == status.reason else _with_reason(status, reason)


def _with_reason(status: PipelineStatus, reason: str) -> PipelineStatus:
    return replace(status, reason=reason)
