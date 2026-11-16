"""The interface between the camera node and its video pipeline (brief §4, ADR 0023).

The node (Part A) and the pipeline (Part B, `node/video/pipeline.py`) meet
here. The node never looks inside a pipeline: it builds a `PipelineSpec`
from a camera's settings, starts one `CameraPipeline` per camera in `node`
video mode, tells it where the crop window is (`set_window`, up to 25-30
times a second while the window moves), asks it for a whole-frame snapshot
about once a second, and reads its `status()` for the health and the node's
page. `FakeCameraPipeline` (`fake_pipeline.py`) has the same interface.

Rules every implementation keeps:

- `set_window` is safe from any thread and never blocks on the child: the
  newest window wins, and is applied at the next frame;
- the window survives the pipeline restarting itself (ffmpeg died, the
  camera was unplugged, the SRT receiver went away): the node does not
  resend it;
- `stop()` stops the child and waits for it;
- the output URL is a secret (a Feeds URL holds the feed's publish
  password): it is never logged, never in a `reason` and never in `repr`.
"""

from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Protocol

from baja_studio.cameras.driver import Logger
from baja_studio.core.clock import Clock

OUTPUT_WIDTH = 1920
OUTPUT_HEIGHT = 1080
DEFAULT_BITRATE_KBPS = 8000
KEYFRAME_SECONDS = 1
ENCODER_AUTO = "auto"


@dataclass(frozen=True)
class PipelineSpec:
    """Everything one camera's pipeline needs to run; a change means a new pipeline."""

    device: str  # the capture device as the device list names it, or a test source
    input_width: int
    input_height: int
    input_fps: int
    # A secret: kept out of repr, so a logged spec never shows it.
    output_url: str = field(repr=False)
    output_width: int = OUTPUT_WIDTH
    output_height: int = OUTPUT_HEIGHT
    bitrate_kbps: int = DEFAULT_BITRATE_KBPS
    keyframe_seconds: int = KEYFRAME_SECONDS
    encoder: str = ENCODER_AUTO  # "auto" or an encoder's name (h264_nvenc, h264_qsv, ...)
    ffmpeg_path: str = "ffmpeg"


@dataclass(frozen=True)
class PipelineWindow:
    """The crop window, as fractions 0..1 of the source frame (the contract's `CropRect`)."""

    x: float
    y: float
    w: float
    h: float


class PipelineState(StrEnum):
    """Where a pipeline is in its life."""

    STARTING = "starting"
    RUNNING = "running"
    RESTARTING = "restarting"  # waiting (1 s doubling to 30 s) before trying again
    FAILED = "failed"  # cannot run at all until its spec changes
    STOPPED = "stopped"


@dataclass(frozen=True)
class PipelineStatus:
    """What a pipeline reports; `reason` is in operator words, without secrets."""

    state: PipelineState
    reason: str = ""
    input_fps: float = 0.0
    output_fps: float = 0.0
    dropped_frames: int = 0
    bitrate_kbps: float = 0.0
    encoder: str = ""  # the encoder in use ("h264_nvenc"), "" until one is chosen
    since: float = 0.0  # clock.monotonic() when `state` began


class CameraPipeline(Protocol):
    """One camera's video: capture, crop, scale, encode, SRT out, and snapshots."""

    def start(self) -> None:
        """Start the child; returns at once (status says `starting`)."""

    def stop(self) -> None:
        """Stop the child and wait for it."""

    def set_window(self, window: PipelineWindow) -> None:
        """Crop to `window` from the next frame; coalesced, never blocks."""

    def latest_snapshot(self) -> bytes | None:
        """The newest whole-frame JPEG (about 320 px wide, about one a second), if any."""

    def status(self) -> PipelineStatus:
        """The state, its reason, and the live figures."""


# How the node makes a pipeline: (spec, clock, logger) → a pipeline, not started.
PipelineFactory = Callable[[PipelineSpec, Clock, Logger], CameraPipeline]


@dataclass(frozen=True)
class CaptureDevice:
    """A video capture device on this computer, for the node's page."""

    name: str  # e.g. "EMEET SmartCam S600"
    device: str  # what `PipelineSpec.device` takes for it (unique, even for two S600s)


@dataclass(frozen=True)
class CaptureMode:
    """One mode a capture device offers."""

    width: int
    height: int
    fps: float
    codec: str = "mjpeg"


class CaptureDevices(Protocol):
    """Lists capture devices and their modes (DirectShow on Windows; a fake list elsewhere)."""

    def list_capture_devices(self) -> list[CaptureDevice]:
        """Every video capture device plugged in now."""

    def capture_modes(self, device: str) -> list[CaptureMode]:
        """The modes `device` offers."""
