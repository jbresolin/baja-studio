"""What a camera pipeline is asked to do, and what it reports back.

`PipelineSpec` is fixed for the life of a pipeline: a different camera mode,
URL, bitrate or encoder means a new pipeline. The window is not in it: it
moves all the time (`CameraPipeline.set_window`).
"""

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum

# What the studio expects from every camera (WP15 brief §1).
OUTPUT_WIDTH = 1920
OUTPUT_HEIGHT = 1080
DEFAULT_BITRATE_KBPS = 8000
DEFAULT_KEYFRAME_SECONDS = 1.0
# "auto" tries the GPU encoders first (encoders.AUTO_ORDER).
AUTO_ENCODER = "auto"
# Decode MJPEG on the CPU; a hardware decoder name (`mjpeg_qsv`, `mjpeg_cuvid`) moves it off.
SOFTWARE_DECODER = "mjpeg"


@dataclass(frozen=True)
class PipelineSpec:
    """One camera's capture mode, where its picture goes, and how it is encoded.

    `device` is a DirectShow device (its "alternative name", the
    `@device_pnp_...` path that tells two cameras of the same model apart,
    or its friendly name) or a synthetic source `lavfi:<name>`
    (`synthetic_sources.py`) for tests and the benchmark.
    """

    device: str
    input_width: int
    input_height: int
    input_fps: float
    # A secret (a Feeds URL holds the feed's publish password): kept out of repr,
    # so a logged spec never shows it.
    output_url: str = field(repr=False)
    output_width: int = OUTPUT_WIDTH
    output_height: int = OUTPUT_HEIGHT
    bitrate_kbps: int = DEFAULT_BITRATE_KBPS
    keyframe_seconds: float = DEFAULT_KEYFRAME_SECONDS
    encoder: str = AUTO_ENCODER
    ffmpeg_path: str = "ffmpeg"
    # Additions to the brief's §4: which MJPEG decoder ffmpeg uses (the bench tells which
    # work, ADR 0023), and the camera's name for messages (its friendly name; `device`
    # can be a long DirectShow path).
    decoder: str = SOFTWARE_DECODER
    label: str = ""

    @property
    def camera_name(self) -> str:
        """How messages name the camera: `label`, or the device when there is none."""
        return self.label or self.device

    @property
    def output_aspect(self) -> float:
        """Width over height of the picture sent (16:9)."""
        return self.output_width / self.output_height


@dataclass(frozen=True)
class SourceWindow:
    """The part of the source frame to show: top-left corner and size, in fractions 0..1.

    Same units as the agent contract's crop rectangle (`docs/agent-api.md`
    §2.3) and `CropFrame.rect`. The pipeline keeps the output's shape: the
    window's width sets the zoom, and its height only places the centre (a
    height that does not match 16:9 never stretches the picture).
    """

    x: float
    y: float
    w: float
    h: float

    @classmethod
    def whole(cls) -> "SourceWindow":
        """The whole frame; where a pipeline starts until it is told otherwise."""
        return cls(0.0, 0.0, 1.0, 1.0)


class PipelineState(StrEnum):
    """Where a pipeline is in its life."""

    # Not started yet, or stopped on purpose (addition to the brief's four states).
    STOPPED = "stopped"
    # ffmpeg launched, no picture out yet.
    STARTING = "starting"
    # Pictures are going out.
    RUNNING = "running"
    # It failed and will start again after a wait; `reason` says why.
    RESTARTING = "restarting"
    # Something a retry alone is unlikely to fix (no ffmpeg, no working encoder).
    # It still tries again every `MAX_RESTART_DELAY_SECONDS`, in case it was fixed.
    FAILED = "failed"


@dataclass(frozen=True)
class PipelineStatus:
    """What the node's page and health show for one camera.

    Rates are over the last second or so. `dropped_frames` counts, since the
    current ffmpeg started, the frames the camera sent that never went out
    (DirectShow's buffer overflowing, or ffmpeg dropping to keep the rate).
    `window_apply_ms` is how long the last window changes took to reach a
    frame (median of recent ones), None before the first.
    """

    state: PipelineState
    reason: str
    fps_in: float
    fps_out: float
    dropped_frames: int
    bitrate_kbps: float
    encoder: str | None
    since: datetime
    restarts: int = 0
    window_apply_ms: float | None = None
