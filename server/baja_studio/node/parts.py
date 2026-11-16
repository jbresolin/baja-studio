"""What a camera node is made of where it runs: real parts on a laptop, fakes in tests and the rig.

- `pipelines`: makes a camera's video pipeline (`node` mode): the ffmpeg
  pipeline (`node/video/pipeline.py`) on a laptop, a fake with `--fake-video`
  (the rig, CI);
- `capture_devices`: the video capture devices and their modes, for the page
  (DirectShow through ffmpeg on a laptop);
- `uvc_child`: starts the UVC child for image controls (`BAJA_STUDIO_UVC_BACKEND=fake`
  makes the child use its fake library, as on the studio's rig);
- `obs_transport`: how OBS-mode cameras reach this laptop's OBS (tests pass
  the fake OBS's in-process network);
- `ffmpeg_path`: the one resolver for the ffmpeg the pipeline and the device
  list run: the setting, else the copy the node installer bundles
  (`<program folder>\\ffmpeg\\ffmpeg.exe`; on Linux the patched
  ffmpeg-rockchip in `<program folder>/ffmpeg/bin/ffmpeg`, ADR 0024), else
  PATH (`playout/tools.resolve_tool`, the studio's own resolver).
"""

import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from baja_studio.adapters.obs_transport import TransportFactory, open_websocket_transport
from baja_studio.core.childproc import EventCallback
from baja_studio.core.module import ModuleContext
from baja_studio.core.system_services import InstallLayout
from baja_studio.imaging.uvc_link import ChildHost
from baja_studio.node.settings import NodeSettings
from baja_studio.node.video import capture_devices as dshow_devices
from baja_studio.node.video.capture_devices import CaptureDevice, CaptureMode
from baja_studio.node.video.fake import FakeCaptureDevices, FakePipelines
from baja_studio.node.video.pipeline import CameraPipeline
from baja_studio.node.video.pipeline_types import CaptureDevices, PipelineFactory
from baja_studio.playout.tools import FFMPEG, resolve_tool

UvcChildFactory = Callable[[ModuleContext, str, EventCallback], ChildHost]


def real_uvc_child(
    context: ModuleContext, python_module: str, on_event: EventCallback
) -> ChildHost:
    """The UVC child process (duvc-ctl, or its fake library with BAJA_STUDIO_UVC_BACKEND=fake)."""
    return context.new_child_process(python_module, on_event)


# Where the Linux installer puts its ffmpeg build (with its own libraries in ../lib).
LINUX_BUNDLED_FFMPEG = Path("ffmpeg") / "bin" / "ffmpeg"


def linux_bundled_ffmpeg(layout: InstallLayout | None) -> str | None:
    """The Linux node installer's ffmpeg (`<program>/ffmpeg/bin/ffmpeg`), None if absent."""
    if layout is None:
        return None
    bundled = layout.program_directory / LINUX_BUNDLED_FFMPEG
    return str(bundled) if bundled.is_file() else None


def bundled_ffmpeg(settings: NodeSettings) -> str:
    """The ffmpeg to run: the setting, else the installer's copy, else PATH."""
    layout = InstallLayout.from_environment()
    if sys.platform != "win32" and not settings.ffmpeg_path.strip():
        bundled = linux_bundled_ffmpeg(layout)
        if bundled is not None:
            return bundled
    return resolve_tool(FFMPEG, settings.ffmpeg_path, layout).command


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
    """A node's parts: the ffmpeg pipeline, and the system's device list."""
    return NodeParts(pipelines=CameraPipeline, capture_devices=DshowCaptureDevices())


class DshowCaptureDevices:
    """The computer's cameras and their modes: DirectShow on Windows, V4L2 on Linux.

    (The name is from WP15, when only laptops ran a node; `capture_devices.py`
    picks the system, synthetic cameras where it has none.)
    """

    def list_capture_devices(self, ffmpeg_path: str) -> list[CaptureDevice]:
        """Every video capture device plugged in now."""
        return dshow_devices.list_capture_devices(ffmpeg_path)

    def capture_modes(self, device: str, ffmpeg_path: str) -> list[CaptureMode]:
        """The modes `device` offers."""
        return dshow_devices.capture_modes(device, ffmpeg_path)
