"""What a camera node is made of where it runs: real parts on a laptop, fakes in tests and the rig.

- `pipelines`: makes a camera's video pipeline (`node` mode). On a laptop
  that is Part B's `node/video/pipeline.py` (ffmpeg); with `--fake-video`
  (the rig, CI) or before Part B is merged it is a fake, or a pipeline that
  only says it is missing;
- `capture_devices`: the video capture devices and their modes, for the page;
- `uvc_child`: starts the UVC child for image controls (`BAJA_STUDIO_UVC_BACKEND=fake`
  makes the child use its fake library, as on the studio's rig);
- `obs_transport`: how OBS-mode cameras reach this laptop's OBS (tests pass
  the fake OBS's in-process network);
- `ffmpeg_path`: the one resolver for the ffmpeg the pipeline runs: the
  setting, else the copy the node installer bundles, else PATH
  (`playout/tools.resolve_tool`, the studio's own resolver).
"""

import importlib
import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from baja_studio.adapters.obs_transport import TransportFactory, open_websocket_transport
from baja_studio.cameras.driver import Logger
from baja_studio.core.childproc import EventCallback
from baja_studio.core.clock import Clock
from baja_studio.core.module import ModuleContext
from baja_studio.core.system_services import InstallLayout
from baja_studio.imaging.uvc_link import ChildHost
from baja_studio.node.settings import NodeSettings
from baja_studio.node.video.fake_pipeline import FakeCaptureDevices, FakePipelines
from baja_studio.node.video.pipeline_types import (
    CaptureDevice,
    CaptureDevices,
    CaptureMode,
    PipelineFactory,
    PipelineSpec,
    PipelineState,
    PipelineStatus,
    PipelineWindow,
)
from baja_studio.playout.tools import FFMPEG, resolve_tool

logger = logging.getLogger(__name__)

# Part B's module: `CameraPipeline`, `list_capture_devices()`, `capture_modes(device)`.
PIPELINE_MODULE = "baja_studio.node.video.pipeline"
MISSING_PIPELINE = "the node's video pipeline is not in this build: use OBS mode"

UvcChildFactory = Callable[[ModuleContext, str, EventCallback], ChildHost]


def real_uvc_child(
    context: ModuleContext, python_module: str, on_event: EventCallback
) -> ChildHost:
    """The UVC child process (duvc-ctl, or its fake library with BAJA_STUDIO_UVC_BACKEND=fake)."""
    return context.new_child_process(python_module, on_event)


def bundled_ffmpeg(settings: NodeSettings) -> str:
    """The ffmpeg to run: the setting, else the installer's copy, else PATH."""
    return resolve_tool(FFMPEG, settings.ffmpeg_path, InstallLayout.from_environment()).command


@dataclass
class NodeParts:
    """Everything the node module is made of, chosen once at startup."""

    pipelines: PipelineFactory
    capture_devices: CaptureDevices
    uvc_child: UvcChildFactory = real_uvc_child
    obs_transport: TransportFactory = open_websocket_transport
    ffmpeg_path: Callable[[NodeSettings], str] = field(default=bundled_ffmpeg)


def fake_parts(
    pipelines: FakePipelines | None = None, capture_devices: FakeCaptureDevices | None = None
) -> NodeParts:
    """Fake video (`--fake-video`: the rig, CI's conformance run): no camera is opened."""
    return NodeParts(
        pipelines=pipelines or FakePipelines(),
        capture_devices=capture_devices or FakeCaptureDevices(),
        ffmpeg_path=lambda settings: "ffmpeg",
    )


def real_parts() -> NodeParts:
    """A laptop's parts: Part B's pipeline when this build has it, else a stand-in that says so."""
    try:
        video: Any = importlib.import_module(PIPELINE_MODULE)
    except ImportError:
        logger.warning("%s", MISSING_PIPELINE)
        return NodeParts(pipelines=MissingPipeline, capture_devices=NoCaptureDevices())
    return NodeParts(pipelines=video.CameraPipeline, capture_devices=_ModuleDevices(video))


class _ModuleDevices:
    """`CaptureDevices` from the pipeline module's functions."""

    def __init__(self, video: Any) -> None:
        self._video = video

    def list_capture_devices(self) -> list[CaptureDevice]:
        devices: list[CaptureDevice] = self._video.list_capture_devices()
        return devices

    def capture_modes(self, device: str) -> list[CaptureMode]:
        modes: list[CaptureMode] = self._video.capture_modes(device)
        return modes


class NoCaptureDevices:
    """No pipeline, so no device list."""

    def list_capture_devices(self) -> list[CaptureDevice]:
        return []

    def capture_modes(self, device: str) -> list[CaptureMode]:
        return []


class MissingPipeline:
    """A pipeline that cannot run, and says why (a build without Part B's pipeline)."""

    def __init__(self, spec: PipelineSpec, clock: Clock, logger: Logger) -> None:
        self._since = clock.monotonic()

    def start(self) -> None:
        """Nothing to start."""

    def stop(self) -> None:
        """Nothing to stop."""

    def set_window(self, window: PipelineWindow) -> None:
        """Nowhere to send it."""

    def latest_snapshot(self) -> bytes | None:
        """No picture."""
        return None

    def status(self) -> PipelineStatus:
        """Failed, with the reason."""
        return PipelineStatus(PipelineState.FAILED, MISSING_PIPELINE, since=self._since)
