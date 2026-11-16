"""Which cameras the laptop has, and the modes each one offers (for the node's settings page).

On Windows, ffmpeg's DirectShow input lists them (it prints to stderr and
then fails on purpose, since `dummy` is not a device):

    ffmpeg -hide_banner -list_devices true -f dshow -i dummy
        [dshow @ 0000020f] "EMEET SmartCam S600" (video)
        [dshow @ 0000020f]   Alternative name "@device_pnp_\\\\?\\usb#vid_328f&...\\global"

    ffmpeg -hide_banner -list_options true -f dshow -i video=<device>
        [dshow @ 0000020f]   vcodec=mjpeg  min s=3840x2160 fps=30 max s=3840x2160 fps=30

The line prefix is `[dshow @ …]` in ffmpeg 6 and `[in#0 @ …]` in later
versions, and newer ones add colour details after a mode, so the parsers
look only at the parts that matter. Two cameras of the same model have the
same name; the alternative name (a USB path) tells them apart, so it is
what a pipeline is given. The samples in the tests follow ffmpeg's source;
the bench tool prints the raw listing so a laptop's real output can be
checked against them (`--raw`).

On Linux the cameras come from V4L2 (`v4l2_devices.py`, the NanoPi node of
ADR 0024) wherever there are cameras to list: on a Rockchip board always,
elsewhere once udev has made `/dev/v4l/by-path`. Where there is neither
(Linux CI, a dev machine without a webcam) the synthetic cameras are listed
instead, so the settings page and the rig have something to show.
"""

import re
import subprocess
import sys
from dataclasses import dataclass

from baja_studio.adapters.v4l2_ioctl import BY_PATH_DIRECTORY, KernelV4l2System
from baja_studio.node.video.ffmpeg_process import NO_CONSOLE_WINDOW
from baja_studio.node.video.rockchip import is_rockchip_board
from baja_studio.node.video.synthetic_sources import QUADRANTS, SYNTHETIC_PREFIX, TESTSRC2

LIST_TIMEOUT_SECONDS = 15.0

_DEVICE_LINE = re.compile(r'"(?P<name>[^"]+)"\s+\((?P<kinds>[^)]*)\)\s*$')
_ALTERNATIVE_LINE = re.compile(r'Alternative name\s+"(?P<name>[^"]+)"')
_MODE_LINE = re.compile(
    r"(?P<kind>vcodec|pixel_format)=(?P<format>\S+)\s+"
    r"min s=(?P<min_w>\d+)x(?P<min_h>\d+) fps=(?P<min_fps>[\d.]+)\s+"
    r"max s=(?P<max_w>\d+)x(?P<max_h>\d+) fps=(?P<max_fps>[\d.]+)"
)


@dataclass(frozen=True)
class CaptureDevice:
    """A video capture device: its friendly name and, when DirectShow has one, its USB path."""

    name: str
    alternative_name: str | None = None

    @property
    def device(self) -> str:
        """What to put in `PipelineSpec.device`: the USB path when there is one."""
        return self.alternative_name or self.name


@dataclass(frozen=True)
class CaptureMode:
    """One mode a camera offers: its format (`mjpeg`, or a raw pixel format) and size and rate."""

    format: str
    width: int
    height: int
    max_fps: float
    min_fps: float

    @property
    def is_mjpeg(self) -> bool:
        """True for MJPEG, the only format a webcam sends 4K at 30 fps in."""
        return self.format == "mjpeg"

    def describe(self) -> str:
        """E.g. `mjpeg 3840x2160 @ 30 fps`."""
        return f"{self.format} {self.width}x{self.height} @ {self.max_fps:g} fps"


def parse_device_list(text: str) -> list[CaptureDevice]:
    """The video devices in `-list_devices` output, with their alternative names."""
    devices: list[CaptureDevice] = []
    last_was_video = False
    for line in text.splitlines():
        alternative = _ALTERNATIVE_LINE.search(line)
        if alternative is not None:
            if last_was_video and devices and devices[-1].alternative_name is None:
                devices[-1] = CaptureDevice(devices[-1].name, alternative["name"])
            continue
        found = _DEVICE_LINE.search(line)
        if found is None:
            continue
        last_was_video = "video" in found["kinds"]
        if last_was_video:
            devices.append(CaptureDevice(found["name"]))
    return devices


def parse_mode_list(text: str) -> list[CaptureMode]:
    """The modes in `-list_options` output, without repeats, biggest and fastest first.

    A line with a range ("min s=640x480 … max s=1920x1080") gives its
    largest size; webcams list every size on its own line anyway.
    """
    modes: set[CaptureMode] = set()
    for line in text.splitlines():
        found = _MODE_LINE.search(line)
        if found is None:
            continue
        modes.add(
            CaptureMode(
                format=found["format"],
                width=int(found["max_w"]),
                height=int(found["max_h"]),
                max_fps=float(found["max_fps"]),
                min_fps=float(found["min_fps"]),
            )
        )
    return sorted(modes, key=lambda mode: (-mode.width * mode.height, -mode.max_fps, mode.format))


SYNTHETIC_DEVICES = [
    CaptureDevice("Test card (synthetic)", SYNTHETIC_PREFIX + TESTSRC2),
    CaptureDevice("Grey quadrants (synthetic)", SYNTHETIC_PREFIX + QUADRANTS),
]
SYNTHETIC_MODES = [
    CaptureMode("mjpeg", 3840, 2160, 30.0, 30.0),
    CaptureMode("mjpeg", 1920, 1080, 30.0, 30.0),
]


def _dshow_listing(ffmpeg_path: str, arguments: list[str]) -> str:
    """ffmpeg's stderr for a DirectShow listing (ffmpeg exits with an error; that is normal)."""
    try:
        finished = subprocess.run(
            [ffmpeg_path, "-hide_banner", *arguments],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            timeout=LIST_TIMEOUT_SECONDS,
            check=False,
            creationflags=NO_CONSOLE_WINDOW,
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return finished.stderr.decode("utf-8", errors="replace")


def raw_device_listing(ffmpeg_path: str = "ffmpeg") -> str:
    """ffmpeg's own words listing the devices (the bench prints them with `--raw`).

    ffmpeg cannot list V4L2 devices; on Linux the bench shows the by-path links instead.
    """
    if uses_v4l2():
        links = sorted(BY_PATH_DIRECTORY.glob("*")) if BY_PATH_DIRECTORY.is_dir() else []
        return "\n".join(f"{link} -> {link.resolve()}" for link in links) or "no /dev/v4l links"
    return _dshow_listing(ffmpeg_path, ["-list_devices", "true", "-f", "dshow", "-i", "dummy"])


def raw_mode_listing(device: str, ffmpeg_path: str = "ffmpeg") -> str:
    """ffmpeg's own words listing one device's modes."""
    if uses_v4l2():
        return _dshow_listing(ffmpeg_path, ["-f", "v4l2", "-list_formats", "all", "-i", device])
    return _dshow_listing(
        ffmpeg_path, ["-list_options", "true", "-f", "dshow", "-i", f"video={device}"]
    )


def uses_v4l2() -> bool:
    """True on a Linux computer whose cameras should be listed from V4L2 (see the docstring)."""
    return sys.platform.startswith("linux") and (is_rockchip_board() or BY_PATH_DIRECTORY.is_dir())


def list_capture_devices(ffmpeg_path: str = "ffmpeg") -> list[CaptureDevice]:
    """The cameras (DirectShow on Windows, V4L2 on Linux), or the synthetic ones elsewhere."""
    if sys.platform == "win32":
        return parse_device_list(raw_device_listing(ffmpeg_path))
    if uses_v4l2():
        # Imported here: v4l2_devices.py answers in this module's types.
        from baja_studio.node.video.v4l2_devices import list_v4l2_devices

        return list_v4l2_devices(KernelV4l2System())
    return list(SYNTHETIC_DEVICES)


def capture_modes(device: str, ffmpeg_path: str = "ffmpeg") -> list[CaptureMode]:
    """The modes `device` offers (DirectShow, V4L2), or the synthetic ones."""
    if device.startswith(SYNTHETIC_PREFIX):
        return list(SYNTHETIC_MODES)
    if sys.platform == "win32":
        return parse_mode_list(raw_mode_listing(device, ffmpeg_path))
    if uses_v4l2():
        # Imported here: v4l2_devices.py answers in this module's types.
        from baja_studio.node.video.v4l2_devices import v4l2_modes

        return v4l2_modes(device, KernelV4l2System())
    return list(SYNTHETIC_MODES)
