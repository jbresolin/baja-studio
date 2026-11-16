"""The interface between the camera node and its video pipeline (brief §4, ADR 0023).

The node and the pipeline (`pipeline.py`) meet here. The node never looks
inside a pipeline: it builds a `PipelineSpec` from a camera's settings,
starts one pipeline per camera in `node` video mode, tells it where the crop
window is (`set_window`, up to 25-30 times a second while the window moves),
asks it for a whole-frame snapshot about once a second, and reads its
`status()` for the health and the node's page. `FakeCameraPipeline`
(`fake.py`) has the same interface.

The data types live next to the code that fills them and are re-exported
here, so the node imports everything about a pipeline from one place:

- `PipelineSpec`, `SourceWindow`, `PipelineState`, `PipelineStatus`: `spec.py`;
- `CaptureDevice`, `CaptureMode`: `capture_devices.py`.

Rules every implementation keeps:

- `set_window` is safe from any thread and never blocks on the child: the
  newest window wins, and is applied at the next frame;
- the window survives the pipeline restarting itself (ffmpeg died, the
  camera was unplugged, the SRT receiver went away): the node does not
  resend it;
- `stop()` stops the child and waits for it (it can take a while, up to the
  encoder test in progress: the node only calls it from a worker thread);
- the output URL is a secret (a Feeds URL holds the feed's publish
  password): it is never logged, never in a `reason` and never in `repr`.
"""

from collections.abc import Callable
from typing import Protocol

from baja_studio.cameras.driver import Logger
from baja_studio.core.clock import Clock
from baja_studio.node.video.capture_devices import CaptureDevice, CaptureMode
from baja_studio.node.video.spec import (
    AUTO_ENCODER,
    OUTPUT_HEIGHT,
    OUTPUT_WIDTH,
    PipelineSpec,
    PipelineState,
    PipelineStatus,
    SourceWindow,
)

__all__ = [
    "AUTO_ENCODER",
    "OUTPUT_HEIGHT",
    "OUTPUT_WIDTH",
    "CaptureDevice",
    "CaptureDevices",
    "CaptureMode",
    "PipelineFactory",
    "PipelineSpec",
    "PipelineState",
    "PipelineStatus",
    "SourceWindow",
    "VideoPipeline",
]


class VideoPipeline(Protocol):
    """One camera's video: capture, crop, scale, encode, SRT out, and snapshots.

    `pipeline.CameraPipeline` (ffmpeg) and `fake.FakeCameraPipeline` are the two.
    """

    def start(self) -> None:
        """Start the child; returns at once (status says `starting`)."""

    def stop(self) -> None:
        """Stop the child and wait for it; the window is kept."""

    def set_window(self, window: SourceWindow) -> None:
        """Crop to `window` from the next frame; coalesced, never blocks."""

    def latest_snapshot(self) -> bytes | None:
        """The newest whole-frame JPEG (about 320 px wide, about one a second), if any."""

    def status(self) -> PipelineStatus:
        """The state, its reason, and the live figures."""

    def log_tail(self) -> list[str]:
        """ffmpeg's recent lines, with passwords removed (for the node's page)."""


# How the node makes a pipeline: (spec, clock, logger) → a pipeline, not started.
PipelineFactory = Callable[[PipelineSpec, Clock, Logger], VideoPipeline]


class CaptureDevices(Protocol):
    """Lists capture devices and their modes (DirectShow on Windows; synthetic ones elsewhere)."""

    def list_capture_devices(self, ffmpeg_path: str) -> list[CaptureDevice]:
        """Every video capture device plugged in now (`ffmpeg_path` lists them)."""

    def capture_modes(self, device: str, ffmpeg_path: str) -> list[CaptureMode]:
        """The modes `device` offers."""
