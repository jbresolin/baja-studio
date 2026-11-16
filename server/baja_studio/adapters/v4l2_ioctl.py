"""Linux video devices (V4L2) asked directly with ioctls: which cameras, their modes, controls.

The camera node on a Linux board (the NanoPi R3S, ADR 0024) needs three things
from a USB webcam that DirectShow gives on Windows: which cameras are plugged
in, which modes each offers (for the node's page), and its image controls
(exposure, white balance... for the studio's Image page). V4L2 answers all
three with a handful of ioctls on `/dev/videoN`, so this module speaks them
itself (`fcntl.ioctl` with the ctypes structures of `v4l2_structures.py`) instead of
parsing the text of a
tool such as `v4l2-ctl`, whose output changes between versions. The structure
layouts are the kernel's stable ABI (`include/uapi/linux/videodev2.h`), the
same on arm64 and x86-64.

Two layers, so tests can replace the kernel:

- `V4l2Device` (`Protocol`): one opened device; `KernelV4l2Device` is the real
  one (a file descriptor and ioctls), `fakes/fake_v4l2.py` a fake;
- `V4l2System` (`Protocol`): lists the capture nodes and opens them;
  `KernelV4l2System` reads `/dev/v4l/by-path` (one link per USB port, the
  same identity rule as DirectShow's device path, `imaging/usb_devices.py`).

Nothing here keeps a device open between calls: a camera may be unplugged at
any moment, and a fresh open per question never holds a stale handle.
"""

import ctypes
import errno
import os
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from baja_studio.adapters.v4l2_structures import (
    BUFFER_TYPE_VIDEO_CAPTURE,
    CAP_DEVICE_CAPS,
    CAP_VIDEO_CAPTURE,
    CONTROL_FLAG_DISABLED,
    FRAME_INTERVAL_DISCRETE,
    FRAME_SIZE_DISCRETE,
    VIDIOC_ENUM_FMT,
    VIDIOC_ENUM_FRAMEINTERVALS,
    VIDIOC_ENUM_FRAMESIZES,
    VIDIOC_G_CTRL,
    VIDIOC_QUERYCAP,
    VIDIOC_QUERYCTRL,
    VIDIOC_QUERYMENU,
    VIDIOC_S_CTRL,
    Capability,
    Control,
    FormatDescription,
    FrameIntervalEnum,
    FrameSizeEnum,
    QueryControl,
    QueryMenu,
)

# Imported by the laptop's node too (capture_devices.py), where Windows has no fcntl:
# only the kernel classes below use it, and only on Linux.
if sys.platform != "win32":
    import fcntl

# Where a by-path link names the capture node of a UVC camera (index1 is its metadata node).
BY_PATH_DIRECTORY = Path("/dev/v4l/by-path")


# --- what the rest of the code sees ---------------------------------------------------------


@dataclass(frozen=True)
class ControlInfo:
    """One control as VIDIOC_QUERYCTRL describes it (a menu's items are asked separately)."""

    control_id: int
    name: str
    type: int
    minimum: int
    maximum: int
    step: int
    default: int
    flags: int

    @property
    def disabled(self) -> bool:
        """True for a control the driver lists but does not have."""
        return bool(self.flags & CONTROL_FLAG_DISABLED)


@dataclass(frozen=True)
class DeviceNode:
    """A video capture node: its stable path (per USB port), the node it points at, its name."""

    stable_path: str
    node_path: str
    name: str
    # Changes when the node is created again (an unplug and replug), so a replug is noticed.
    identity: tuple[int, int]


class V4l2Device(Protocol):
    """One opened video device."""

    def card_name(self) -> str:
        """The device's name (`card`), e.g. "EMEET SmartCam S600"."""

    def is_video_capture(self) -> bool:
        """True for a node that captures video (a UVC camera's metadata node does not)."""

    def pixel_formats(self) -> list[int]:
        """The capture formats it offers (fourcc numbers)."""

    def frame_sizes(self, pixel_format: int) -> list[tuple[int, int]]:
        """The sizes it offers in that format (discrete sizes only, as webcams list them)."""

    def frame_rates(self, pixel_format: int, width: int, height: int) -> list[float]:
        """The frame rates it offers at that format and size."""

    def query_control(self, control_id: int) -> ControlInfo | None:
        """The control's description, or None if the device has no such control."""

    def menu_values(self, control: ControlInfo) -> list[int]:
        """The values a menu control accepts."""

    def get_control(self, control_id: int) -> int:
        """The control's current value."""

    def set_control(self, control_id: int, value: int) -> None:
        """Set the control; raises `OSError` if the device refuses."""

    def close(self) -> None:
        """Close the device."""


class V4l2System(Protocol):
    """The computer's video capture nodes."""

    def capture_nodes(self) -> list[DeviceNode]:
        """Every video capture node plugged in now, by stable path."""

    def open(self, path: str) -> V4l2Device:
        """Open a node by its stable path (or `/dev/videoN`); raises `OSError` if it cannot."""


@contextmanager
def opened(system: V4l2System, path: str) -> Iterator[V4l2Device]:
    """`with opened(system, path) as device:` opens and always closes it."""
    device = system.open(path)
    try:
        yield device
    finally:
        device.close()


# --- the real kernel ----------------------------------------------------------------------


class KernelV4l2Device:
    """A `/dev/videoN` opened for ioctls (non-blocking: it never waits for a frame)."""

    def __init__(self, path: str) -> None:
        """Open `path`; raises `OSError` (missing, no permission)."""
        # Non-blocking: no question here ever waits for a frame (getattr: Windows lacks it).
        self._fd = os.open(path, os.O_RDWR | getattr(os, "O_NONBLOCK", 0))

    def _ioctl(self, request: int, structure: ctypes.Structure) -> None:
        if sys.platform != "win32":
            fcntl.ioctl(self._fd, request, structure)
        else:
            raise OSError(errno.ENOSYS, "V4L2 is Linux only")

    def _capability(self) -> Capability:
        capability = Capability()
        self._ioctl(VIDIOC_QUERYCAP, capability)
        return capability

    def card_name(self) -> str:
        """The device's name."""
        card: bytes = self._capability().card
        return card.decode("utf-8", errors="replace").strip()

    def is_video_capture(self) -> bool:
        """True for a node that captures video."""
        capability = self._capability()
        caps = capability.capabilities
        if caps & CAP_DEVICE_CAPS:
            caps = capability.device_caps
        return bool(caps & CAP_VIDEO_CAPTURE)

    def pixel_formats(self) -> list[int]:
        """The capture formats it offers."""
        formats = []
        for index in range(64):
            description = FormatDescription(index=index, type=BUFFER_TYPE_VIDEO_CAPTURE)
            if not self._enumerate(VIDIOC_ENUM_FMT, description):
                break
            formats.append(int(description.pixelformat))
        return formats

    def frame_sizes(self, pixel_format: int) -> list[tuple[int, int]]:
        """Discrete sizes in that format."""
        sizes = []
        for index in range(256):
            entry = FrameSizeEnum(index=index, pixel_format=pixel_format)
            if not self._enumerate(VIDIOC_ENUM_FRAMESIZES, entry):
                break
            if entry.type != FRAME_SIZE_DISCRETE:
                break
            sizes.append((int(entry.size.discrete.width), int(entry.size.discrete.height)))
        return sizes

    def frame_rates(self, pixel_format: int, width: int, height: int) -> list[float]:
        """Discrete frame rates (frames per second) at that format and size."""
        rates = []
        for index in range(64):
            entry = FrameIntervalEnum(
                index=index, pixel_format=pixel_format, width=width, height=height
            )
            if not self._enumerate(VIDIOC_ENUM_FRAMEINTERVALS, entry):
                break
            if entry.type != FRAME_INTERVAL_DISCRETE:
                break
            interval = entry.interval.discrete
            if interval.numerator:
                rates.append(interval.denominator / interval.numerator)
        return rates

    def query_control(self, control_id: int) -> ControlInfo | None:
        """The control's description, None if the device has none."""
        query = QueryControl(id=control_id)
        try:
            self._ioctl(VIDIOC_QUERYCTRL, query)
        except OSError as error:
            if error.errno == errno.EINVAL:
                return None
            raise
        info = ControlInfo(
            control_id=control_id,
            name=query.name.decode("utf-8", errors="replace"),
            type=int(query.type),
            minimum=int(query.minimum),
            maximum=int(query.maximum),
            step=int(query.step),
            default=int(query.default_value),
            flags=int(query.flags),
        )
        return None if info.disabled else info

    def menu_values(self, control: ControlInfo) -> list[int]:
        """The values of a menu control that the device accepts."""
        values = []
        for index in range(control.minimum, control.maximum + 1):
            if self._enumerate(VIDIOC_QUERYMENU, QueryMenu(id=control.control_id, index=index)):
                values.append(index)
        return values

    def get_control(self, control_id: int) -> int:
        """The control's value."""
        control = Control(id=control_id)
        self._ioctl(VIDIOC_G_CTRL, control)
        return int(control.value)

    def set_control(self, control_id: int, value: int) -> None:
        """Set the control."""
        self._ioctl(VIDIOC_S_CTRL, Control(id=control_id, value=value))

    def close(self) -> None:
        """Close the descriptor."""
        os.close(self._fd)

    def _enumerate(self, request: int, structure: ctypes.Structure) -> bool:
        """One step of an enumeration: False when the index is past the end (EINVAL)."""
        try:
            self._ioctl(request, structure)
        except OSError as error:
            if error.errno == errno.EINVAL:
                return False
            raise
        return True


class KernelV4l2System:
    """The computer's real video nodes, found through udev's `/dev/v4l/by-path` links."""

    def __init__(self, by_path: Path = BY_PATH_DIRECTORY) -> None:
        """`by_path` is the folder of per-port links (a test can point it elsewhere)."""
        self._by_path = by_path

    def capture_nodes(self) -> list[DeviceNode]:
        """Every capture node, by its per-port link, sorted by that link."""
        try:
            links = sorted(self._by_path.iterdir())
        except OSError:
            return []
        nodes = []
        for link in links:
            try:
                node = os.path.realpath(link)
                status = os.stat(node)
                with opened(self, node) as device:
                    if not device.is_video_capture():
                        continue
                    name = device.card_name()
            except OSError:
                continue  # unplugged while we looked, or not ours to open
            identity = (getattr(status, "st_rdev", 0), status.st_ino)
            nodes.append(DeviceNode(str(link), node, name, identity))
        return nodes

    def open(self, path: str) -> V4l2Device:
        """Open a node (its link or its `/dev/videoN`)."""
        return KernelV4l2Device(path)
