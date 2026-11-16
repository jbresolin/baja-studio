"""A fake Linux video system (V4L2) with S600-like webcams, for the NanoPi node's tests.

    python -m baja_studio.fakes.fake_v4l2          # prints the fake cameras, modes and controls

It copies the surface of `adapters/v4l2_ioctl.py` (`V4l2System`,
`V4l2Device`), so the code that lists cameras and modes
(`node/video/v4l2_devices.py`) and the UVC child's Linux backend
(`childprocs/uvc_v4l2.py`) run their own code against it. The UVC child
uses it with `BAJA_STUDIO_UVC_BACKEND=fake-v4l2`.

What it models, and where from (no S600 has been measured on Linux yet: the
NanoPi bench sheet, `docs/bench/nanopi.md`, checks each and this fake follows
what it finds):

- **Nodes and links**: each camera has a capture node and a metadata node
  (uvcvideo makes both since Linux 5.0); only the capture node has a
  `by-path` link ending in `-video-index0`, as udev's rule names it. An
  unplug removes both; a replug makes new nodes (a new identity) under the
  same link (udev's `60-persistent-v4l.rules`).
- **Modes**: what EMEET lists for the S600 (MJPEG 4K30, 1440p30, 1080p60/30,
  720p; YUYV 640x480 only), as the Windows bench listed them through DirectShow.
- **Controls**: the UVC controls a webcam of this class has, with uvcvideo's
  V4L2 names, ranges typical of the class, and the driver's rules:
  a value control whose automatic partner is on is *inactive* and a write
  to it is refused with EACCES (uvc_ctrl.c, `uvc_ctrl_set` since Linux 5.13);
  `auto_exposure` is a menu offering manual (1) and aperture priority (3);
  unknown ids are EINVAL.
"""

import errno
import itertools
from dataclasses import dataclass, field

from baja_studio.adapters.v4l2_ioctl import ControlInfo, DeviceNode
from baja_studio.adapters.v4l2_structures import CONTROL_TYPE_MENU, fourcc
from baja_studio.childprocs import uvc_v4l2 as ids

BOOLEAN = 2
INTEGER = 1
FLAG_INACTIVE = 0x0010

# (value id, name, type, min, max, step, default)
_S600_CONTROLS = [
    (ids.CID_BRIGHTNESS, "Brightness", INTEGER, -64, 64, 1, 0),
    (ids.CID_CONTRAST, "Contrast", INTEGER, 0, 64, 1, 32),
    (ids.CID_SATURATION, "Saturation", INTEGER, 0, 128, 1, 64),
    (ids.CID_HUE, "Hue", INTEGER, -40, 40, 1, 0),
    (ids.CID_AUTO_WHITE_BALANCE, "White Balance, Automatic", BOOLEAN, 0, 1, 1, 1),
    (ids.CID_GAMMA, "Gamma", INTEGER, 72, 500, 1, 100),
    (ids.CID_GAIN, "Gain", INTEGER, 0, 100, 1, 0),
    (ids.CID_WHITE_BALANCE_TEMPERATURE, "White Balance Temperature", INTEGER, 2800, 6500, 1, 4600),
    (ids.CID_SHARPNESS, "Sharpness", INTEGER, 0, 6, 1, 3),
    (ids.CID_BACKLIGHT_COMPENSATION, "Backlight Compensation", INTEGER, 0, 2, 1, 1),
    (ids.CID_EXPOSURE_AUTO, "Auto Exposure", CONTROL_TYPE_MENU, 0, 3, 1, 3),
    (ids.CID_EXPOSURE_ABSOLUTE, "Exposure Time, Absolute", INTEGER, 1, 5000, 1, 156),
    (ids.CID_FOCUS_ABSOLUTE, "Focus, Absolute", INTEGER, 0, 1023, 1, 68),
    (ids.CID_FOCUS_AUTO, "Focus, Automatic Continuous", BOOLEAN, 0, 1, 1, 1),
]
# The menu entries of auto_exposure the S600 class offers.
_EXPOSURE_MENU = [ids.EXPOSURE_MANUAL, ids.EXPOSURE_APERTURE_PRIORITY]
# value id -> its automatic partner, and the partner value that makes the value inactive.
_AUTO_PARTNERS = {
    ids.CID_WHITE_BALANCE_TEMPERATURE: ids.CID_AUTO_WHITE_BALANCE,
    ids.CID_EXPOSURE_ABSOLUTE: ids.CID_EXPOSURE_AUTO,
    ids.CID_FOCUS_ABSOLUTE: ids.CID_FOCUS_AUTO,
}
_S600_MODES = {
    "MJPG": {
        (3840, 2160): [30.0, 15.0],
        (2560, 1440): [30.0, 15.0],
        (1920, 1080): [60.0, 30.0, 15.0],
        (1280, 720): [60.0, 30.0],
    },
    "YUYV": {(640, 480): [30.0]},
}
# What udev names an S600 in the NanoPi R3S's USB 3 port (and behind a hub's ports).
R3S_USB3_LINK = "/dev/v4l/by-path/platform-xhci-hcd.0.auto-usb-0:1:1.0-video-index0"
HUB_LINK_PATTERN = "/dev/v4l/by-path/platform-xhci-hcd.0.auto-usb-0:1.{port}:1.0-video-index0"
S600_NAME = "EMEET SmartCam S600: EMEET Sma"


@dataclass
class FakeCamera:
    """One plugged-in webcam: its controls' values and its node's identity."""

    link: str
    name: str = S600_NAME
    node_number: int = 0
    identity: tuple[int, int] = (0, 0)
    plugged: bool = True
    values: dict[int, int] = field(default_factory=dict)
    # Every (control id, value) written, in order, for tests to read.
    writes: list[tuple[int, int]] = field(default_factory=list)

    def reset(self) -> None:
        """Back to the camera's defaults, as a webcam is after a replug."""
        self.values = {control[0]: control[6] for control in _S600_CONTROLS}


class FakeV4l2Device:
    """A camera opened through the fake system."""

    def __init__(self, camera: FakeCamera) -> None:
        """Opened on `camera` (which must be plugged in)."""
        self._camera = camera
        self.closed = False

    def card_name(self) -> str:
        """The camera's name."""
        return self._camera.name

    def is_video_capture(self) -> bool:
        """Only capture nodes are opened by link."""
        return True

    def pixel_formats(self) -> list[int]:
        """MJPEG, then YUYV."""
        self._check()
        return [fourcc(code) for code in _S600_MODES]

    def frame_sizes(self, pixel_format: int) -> list[tuple[int, int]]:
        """The sizes of that format."""
        self._check()
        for code, sizes in _S600_MODES.items():
            if fourcc(code) == pixel_format:
                return list(sizes)
        return []

    def frame_rates(self, pixel_format: int, width: int, height: int) -> list[float]:
        """The rates at that format and size."""
        self._check()
        for code, sizes in _S600_MODES.items():
            if fourcc(code) == pixel_format:
                return list(sizes.get((width, height), []))
        return []

    def query_control(self, control_id: int) -> ControlInfo | None:
        """The control's description (inactive while its automatic partner is on)."""
        self._check()
        for value_id, name, kind, low, high, step, default in _S600_CONTROLS:
            if value_id == control_id:
                flags = FLAG_INACTIVE if self._inactive(control_id) else 0
                return ControlInfo(control_id, name, kind, low, high, step, default, flags)
        return None

    def menu_values(self, control: ControlInfo) -> list[int]:
        """auto_exposure's entries."""
        return list(_EXPOSURE_MENU) if control.control_id == ids.CID_EXPOSURE_AUTO else []

    def get_control(self, control_id: int) -> int:
        """The control's value; EINVAL for an id the camera does not have."""
        self._check()
        if control_id not in self._camera.values:
            raise OSError(errno.EINVAL, "Invalid argument")
        return self._camera.values[control_id]

    def set_control(self, control_id: int, value: int) -> None:
        """Set a control, refusing what uvcvideo refuses (see the module docstring)."""
        self._check()
        info = self.query_control(control_id)
        if info is None:
            raise OSError(errno.EINVAL, "Invalid argument")
        if info.flags & FLAG_INACTIVE:
            raise OSError(errno.EACCES, "Permission denied")
        if info.type == CONTROL_TYPE_MENU and value not in _EXPOSURE_MENU:
            raise OSError(errno.EINVAL, "Invalid argument")
        self._camera.values[control_id] = min(info.maximum, max(info.minimum, value))
        self._camera.writes.append((control_id, value))

    def close(self) -> None:
        """Closed (tests check nothing stays open)."""
        self.closed = True

    def _inactive(self, control_id: int) -> bool:
        partner = _AUTO_PARTNERS.get(control_id)
        if partner is None:
            return False
        partner_value = self._camera.values.get(partner, 0)
        if partner == ids.CID_EXPOSURE_AUTO:
            return partner_value != ids.EXPOSURE_MANUAL
        return bool(partner_value)

    def _check(self) -> None:
        if not self._camera.plugged:
            raise OSError(errno.ENODEV, "No such device")


class FakeV4l2System:
    """The fake computer: cameras by link, plugged in or not."""

    def __init__(self, links: list[str] | None = None) -> None:
        """One S600 per link (default: one in the R3S's USB 3 port)."""
        self._numbers = itertools.count(0, 2)  # each camera takes a capture and a metadata node
        self.cameras: dict[str, FakeCamera] = {}
        self.opened: list[FakeV4l2Device] = []
        for link in links or [R3S_USB3_LINK]:
            self.plug(link)

    def plug(self, link: str) -> FakeCamera:
        """Plug a camera in at `link` (again, if it was unplugged): new nodes, defaults back."""
        number = next(self._numbers)
        camera = self.cameras.get(link) or FakeCamera(link)
        camera.plugged = True
        camera.node_number = number
        camera.identity = (81 * 256 + number, 1000 + number)
        camera.reset()
        self.cameras[link] = camera
        return camera

    def unplug(self, link: str) -> None:
        """Pull the camera at `link` out."""
        self.cameras[link].plugged = False

    def capture_nodes(self) -> list[DeviceNode]:
        """The plugged-in cameras' capture nodes, by link."""
        return [
            DeviceNode(link, f"/dev/video{camera.node_number}", camera.name, camera.identity)
            for link, camera in sorted(self.cameras.items())
            if camera.plugged
        ]

    def open(self, path: str) -> FakeV4l2Device:
        """Open by link or by node; ENOENT when nothing is there."""
        for link, camera in self.cameras.items():
            node = f"/dev/video{camera.node_number}"
            if camera.plugged and path in (link, node):
                device = FakeV4l2Device(camera)
                self.opened.append(device)
                return device
        raise OSError(errno.ENOENT, "No such file or directory")


def main() -> None:
    """Print what the fake system holds (a quick look, no options)."""
    from baja_studio.node.video.v4l2_devices import list_v4l2_devices, v4l2_modes

    system = FakeV4l2System([R3S_USB3_LINK, HUB_LINK_PATTERN.format(port=2)])
    for device in list_v4l2_devices(system):
        print(f"{device.name}  {device.device}")
        for mode in v4l2_modes(device.device, system):
            print(f"    {mode.describe()}")


if __name__ == "__main__":
    main()
