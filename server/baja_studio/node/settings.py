"""The camera node's settings (ADR 0023): who it is, its token and port, and the cameras it serves.

Edited on the node's own page (served by the node, `frontend/node/`) through
the core settings API (`/api/settings/node`), which masks the secrets: the
token, each camera's output URL (a Feeds URL carries the feed's publish
password) and its OBS password. Like every settings section, only what an
operator configures lives here; the window and the live status do not.

Each camera has a **video mode** (ADR 0023, brief §9):

- `node`: the node's own video pipeline (ffmpeg) captures, crops, encodes
  and sends SRT to the studio's Feeds URL (`video`);
- `obs`: OBS on the same laptop does the video, and the node moves the
  camera's Crop/Pad filter in it over obs-websocket (`obs`), exactly as the
  studio's `obs_crop` driver does (it *is* that driver).

Both modes share the window's limits and speeds (`min_window`, `update_hz`,
speeds), which mean what they mean for an `obs_crop` camera (ADR 0020), and
the USB device whose image controls the node serves (`usb_device`).
"""

from enum import StrEnum
from typing import ClassVar, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from baja_studio.cameras.config import ID_PATTERN
from baja_studio.cameras.crop_config import DEFAULT_OBS_PORT
from baja_studio.core.settings import ModuleSettings
from baja_studio.imaging.config import UsbDeviceRef

# The node's agent API port: the MV app's zone agent uses 8890, and both may
# run on one laptop (brief §2).
NODE_PORT = 8891
# The local OBS a camera in OBS mode is in: always this laptop's.
LOCAL_OBS_HOST = "127.0.0.1"
ENCODER_PATTERN = r"^(auto|[a-z0-9_]{1,40})$"
# The MJPEG decoders the pipeline knows: auto, the CPU, QuickSync, NVIDIA (the bench says
# which work), and Rockchip's VPU with the whole hardware path (the NanoPi, ADR 0024).
DECODER_PATTERN = r"^(auto|mjpeg|mjpeg_qsv|mjpeg_cuvid|mjpeg_rkmpp)$"


class StrictModel(BaseModel):
    """Refuses unknown keys, so a typo is an error, not a silent default."""

    model_config = ConfigDict(extra="forbid")


class VideoMode(StrEnum):
    """Who does a camera's video: the node's own pipeline, or OBS on this laptop."""

    NODE = "node"
    OBS = "obs"


class PipelineSettings(StrictModel):
    """A camera's video in `node` mode: the node's ffmpeg pipeline (brief §3, §4)."""

    device: str = Field(
        default="",
        max_length=1000,
        description="Capture device, as the device list names it (DirectShow, or V4L2 on Linux).",
    )
    device_name: str = Field(default="", max_length=200, description="Its name, for display.")
    input_width: int = Field(default=3840, ge=160, le=7680)
    input_height: int = Field(default=2160, ge=120, le=4320)
    input_fps: int = Field(default=30, ge=1, le=120)
    output_url: str = Field(
        default="",
        max_length=2000,
        description="Where the picture goes: the camera's SRT URL from the studio's Feeds page.",
        json_schema_extra={"secret": True},
    )
    encoder: str = Field(
        default="auto",
        pattern=ENCODER_PATTERN,
        description=(
            "auto (NVENC, then QuickSync, AMF, x264; Rockchip MPP on a Rockchip board), "
            "or an encoder's name."
        ),
    )
    bitrate_kbps: int = Field(default=8000, ge=500, le=50000)
    decoder: str = Field(
        default="auto",
        pattern=DECODER_PATTERN,
        description=(
            "How the camera's MJPEG is decoded: auto (Rockchip hardware on a Rockchip board, "
            "else the CPU), mjpeg (the CPU), mjpeg_qsv (Intel QuickSync), mjpeg_cuvid (NVIDIA) "
            "or mjpeg_rkmpp (Rockchip: crop, scale and encode in hardware too). On a laptop "
            "keep auto or mjpeg unless the bench showed another works."
        ),
    )


class ObsModeSettings(StrictModel):
    """A camera's video in `obs` mode: OBS on this laptop holds the source and the filter."""

    port: int = Field(default=DEFAULT_OBS_PORT, ge=1, le=65535)
    password: str = Field(
        default="",
        max_length=200,
        description="OBS's WebSocket password (Tools → WebSocket Server Settings).",
        json_schema_extra={"secret": True},
    )
    source: str = Field(default="", max_length=200, description="The camera's source in OBS.")
    filter_name: str = Field(default="Baja crop", min_length=1, max_length=100)
    preview_source: str = Field(
        default="",
        max_length=200,
        description=(
            "An OBS source showing the camera uncropped (optional): snapshots are then the "
            "whole frame; without one they are the window."
        ),
    )


class NodeCameraSettings(StrictModel):
    """One camera the node serves, as the studio sees it: a `kind=crop` agent camera."""

    id: str = Field(pattern=ID_PATTERN, description="The camera's id on this agent, e.g. cam1.")
    label: str = Field(default="", max_length=60)
    enabled: bool = Field(default=True, description="Served to the studio.")
    mode: VideoMode = VideoMode.NODE
    usb_device: UsbDeviceRef | None = Field(
        default=None, description="The USB camera whose image controls the studio may change."
    )
    video: PipelineSettings = Field(default_factory=PipelineSettings)
    obs: ObsModeSettings = Field(default_factory=ObsModeSettings)
    min_window: float = Field(
        default=0.5,
        ge=0.05,
        le=1.0,
        description="Narrowest window, as a fraction of the source's width (0.5: 2x on 4K).",
    )
    update_hz: int = Field(default=25, ge=5, le=60, description="Window updates per second.")
    pan_speed: float = Field(default=0.3, gt=0.0, le=2.0)
    tilt_speed: float = Field(default=0.3, gt=0.0, le=2.0)
    zoom_speed: float = Field(default=0.5, gt=0.0, le=2.0)
    speed_follows_zoom: bool = Field(
        default=True, description="Pan and tilt slow down as the window narrows (ADR 0020)."
    )


class NodeSettings(ModuleSettings):
    """Settings of the camera node's one module."""

    settings_version: ClassVar[int] = 1

    agent_id: str = Field(
        default="",
        max_length=60,
        description="This node's agent id (blank: node-<computer name>).",
    )
    name: str = Field(default="", max_length=60, description="Its name on the studio's pages.")
    token: str = Field(
        default="",
        max_length=200,
        description="The agent token the studio sends (blank: no token; anyone on the LAN).",
        json_schema_extra={"secret": True},
    )
    port: int = Field(
        default=NODE_PORT,
        ge=1024,
        le=65535,
        description="The node's port; applies when the service restarts (firewall: 8891 only).",
    )
    ffmpeg_path: str = Field(
        default="", max_length=1000, description="ffmpeg to run (blank: the bundled one)."
    )
    cameras: list[NodeCameraSettings] = Field(default_factory=list)

    @model_validator(mode="after")
    def _distinct_cameras(self) -> Self:
        ids = [camera.id for camera in self.cameras]
        if len(set(ids)) != len(ids):
            raise ValueError("two cameras have the same id")
        devices = [
            camera.usb_device.path.lower()
            for camera in self.cameras
            if camera.enabled and camera.usb_device is not None and camera.usb_device.path
        ]
        if len(set(devices)) != len(devices):
            raise ValueError("two enabled cameras use the same USB device")
        return self

    def served(self) -> list[NodeCameraSettings]:
        """The enabled cameras, in settings order: the ones `/agent/info` lists."""
        return [camera for camera in self.cameras if camera.enabled]
