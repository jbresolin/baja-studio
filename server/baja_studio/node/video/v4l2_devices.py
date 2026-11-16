"""Which cameras a Linux node has, and the modes each offers (the NanoPi, ADR 0024).

The Linux counterpart of the DirectShow listing in `capture_devices.py`, and
it answers in the same types, so the node's page, the bench and the pipeline
do not care which one ran:

- a camera is a `CaptureDevice` whose `alternative_name` is its
  `/dev/v4l/by-path/...-video-index0` link: stable per USB port, and it tells
  two S600s apart, exactly as DirectShow's device path does. ffmpeg opens
  that link (`-f v4l2 -i <link>`), and the UVC child finds the same camera's
  controls by it;
- a mode is a `CaptureMode` per format and size, with the fastest and
  slowest rates the camera lists for it. Formats are named as ffmpeg names
  them (`mjpeg`, `yuyv422`), so `CaptureMode.is_mjpeg` holds as on Windows.

The kernel is reached through `adapters/v4l2_ioctl.V4l2System`, so the tests
run this against the fake camera of `fakes/fake_v4l2.py`.
"""

from baja_studio.adapters.v4l2_ioctl import V4l2System, opened
from baja_studio.adapters.v4l2_structures import fourcc_text
from baja_studio.node.video.capture_devices import CaptureDevice, CaptureMode

# V4L2's pixel format codes -> ffmpeg's names for them (`-input_format`).
FFMPEG_FORMAT_NAMES = {
    "MJPG": "mjpeg",
    "JPEG": "mjpeg",
    "YUYV": "yuyv422",
    "UYVY": "uyvy422",
    "NV12": "nv12",
    "YU12": "yuv420p",
    "H264": "h264",
    "HEVC": "hevc",
}


def ffmpeg_format_name(pixel_format: int) -> str:
    """A V4L2 pixel format as ffmpeg's v4l2 input names it (`MJPG` -> `mjpeg`)."""
    code = fourcc_text(pixel_format)
    return FFMPEG_FORMAT_NAMES.get(code, code.strip().lower())


def list_v4l2_devices(system: V4l2System) -> list[CaptureDevice]:
    """The video capture devices plugged in now: their name and their per-port link."""
    return [CaptureDevice(node.name, node.stable_path) for node in system.capture_nodes()]


def v4l2_modes(device: str, system: V4l2System) -> list[CaptureMode]:
    """The modes `device` offers, biggest and fastest first (none if it cannot be opened)."""
    modes: set[CaptureMode] = set()
    try:
        with opened(system, device) as camera:
            for pixel_format in camera.pixel_formats():
                name = ffmpeg_format_name(pixel_format)
                for width, height in camera.frame_sizes(pixel_format):
                    rates = camera.frame_rates(pixel_format, width, height)
                    if not rates:
                        continue
                    modes.add(CaptureMode(name, width, height, max(rates), min(rates)))
    except OSError:
        return []
    return sorted(modes, key=lambda mode: (-mode.width * mode.height, -mode.max_fps, mode.format))
