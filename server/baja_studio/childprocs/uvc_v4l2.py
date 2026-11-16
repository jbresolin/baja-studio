"""The UVC child's backend on Linux: a webcam's controls through V4L2, behind the studio's names.

The Linux counterpart of `uvc_duvc.py` (the NanoPi node, ADR 0024), with the
same five calls (`uvc_child.UvcBackend`), so the child, the node's image
controls and the studio's Image page work unchanged. The Linux UVC driver
(uvcvideo) exposes each UVC control as a V4L2 control; this backend maps the
studio's names onto them:

- a value control, and for some an *automatic* partner: exposure has a menu
  (`auto_exposure`: 1 = manual, 3 = aperture priority, the UVC "auto"),
  white balance and focus a switch;
- **exposure is given in DirectShow's units** (whole log2 seconds: -5 is
  1/32 s), as the Windows nodes and the studio PC give it. V4L2 counts
  100 µs steps; converting here keeps one unit across every USB camera, so
  the Image page's "Copy settings from" between a laptop's camera and the
  NanoPi's does not turn 1/32 s into 1/10000 s. The step is one stop, as on
  Windows;
- a value written while its control is on auto is not written (the driver
  refuses it), and turning auto off is written before the value, so the
  camera never holds a value it was told while still deciding by itself.

Devices are found by their `/dev/v4l/by-path` link (stable per USB port, the
same rule as DirectShow's path). Nothing is kept open: each call opens the
node, asks, and closes it, so an unplug never leaves a dead handle. A replug
shows as a new device node (another inode) under the same link; the next
look reports it through `take_reopened`, and the service then writes that
camera's settings again (a webcam forgets them).

The kernel is reached through `adapters/v4l2_ioctl.V4l2System`: the real
one on the board, `fakes/fake_v4l2.py` in tests.
"""

import math
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any

from baja_studio.adapters.v4l2_ioctl import ControlInfo, V4l2Device, V4l2System
from baja_studio.childprocs.uvc_duvc import DeviceMissingError, ListedDevice, UvcError

# V4L2 control ids (include/uapi/linux/v4l2-controls.h).
_USER = 0x00980900
_CAMERA = 0x009A0900
CID_BRIGHTNESS = _USER + 0
CID_CONTRAST = _USER + 1
CID_SATURATION = _USER + 2
CID_HUE = _USER + 3
CID_AUTO_WHITE_BALANCE = _USER + 12
CID_GAMMA = _USER + 16
CID_AUTOGAIN = _USER + 18
CID_GAIN = _USER + 19
CID_HUE_AUTO = _USER + 25
CID_WHITE_BALANCE_TEMPERATURE = _USER + 26
CID_SHARPNESS = _USER + 27
CID_BACKLIGHT_COMPENSATION = _USER + 28
CID_EXPOSURE_AUTO = _CAMERA + 1
CID_EXPOSURE_ABSOLUTE = _CAMERA + 2
CID_PAN_ABSOLUTE = _CAMERA + 8
CID_TILT_ABSOLUTE = _CAMERA + 9
CID_FOCUS_ABSOLUTE = _CAMERA + 10
CID_FOCUS_AUTO = _CAMERA + 12
CID_ZOOM_ABSOLUTE = _CAMERA + 13
CID_IRIS_ABSOLUTE = _CAMERA + 17

# `auto_exposure` menu values (enum v4l2_exposure_auto_type).
EXPOSURE_AUTO = 0
EXPOSURE_MANUAL = 1
EXPOSURE_APERTURE_PRIORITY = 3
# One V4L2 exposure step is 100 µs.
EXPOSURE_UNIT_SECONDS = 1e-4


@dataclass(frozen=True)
class MappedControl:
    """A studio control: its V4L2 value control and, if any, its automatic partner."""

    value_id: int
    auto_id: int | None = None
    # True for exposure: values in DirectShow's log2 seconds, auto through the menu.
    exposure: bool = False


CONTROLS: dict[str, MappedControl] = {
    "pan": MappedControl(CID_PAN_ABSOLUTE),
    "tilt": MappedControl(CID_TILT_ABSOLUTE),
    "zoom": MappedControl(CID_ZOOM_ABSOLUTE),
    "exposure": MappedControl(CID_EXPOSURE_ABSOLUTE, CID_EXPOSURE_AUTO, exposure=True),
    "iris": MappedControl(CID_IRIS_ABSOLUTE),
    "focus": MappedControl(CID_FOCUS_ABSOLUTE, CID_FOCUS_AUTO),
    "brightness": MappedControl(CID_BRIGHTNESS),
    "contrast": MappedControl(CID_CONTRAST),
    "hue": MappedControl(CID_HUE, CID_HUE_AUTO),
    "saturation": MappedControl(CID_SATURATION),
    "sharpness": MappedControl(CID_SHARPNESS),
    "gamma": MappedControl(CID_GAMMA),
    "white_balance": MappedControl(CID_WHITE_BALANCE_TEMPERATURE, CID_AUTO_WHITE_BALANCE),
    "backlight_compensation": MappedControl(CID_BACKLIGHT_COMPENSATION),
    "gain": MappedControl(CID_GAIN, CID_AUTOGAIN),
}


# --- exposure units ----------------------------------------------------------------------------


def exposure_to_log2(units: int) -> int:
    """V4L2 exposure (100 µs steps) -> DirectShow's whole log2 seconds (156 -> -6: 1/64 s)."""
    return round(math.log2(max(1, units) * EXPOSURE_UNIT_SECONDS))


def exposure_from_log2(value: int, control: ControlInfo) -> int:
    """DirectShow's log2 seconds -> V4L2 exposure steps, kept in the control's range."""
    units = round(2.0**value / EXPOSURE_UNIT_SECONDS)
    return min(control.maximum, max(control.minimum, units))


def exposure_range(control: ControlInfo) -> tuple[int, int]:
    """The whole stops that lie inside the control's range (min, max) in log2 seconds."""
    low = math.ceil(math.log2(max(1, control.minimum) * EXPOSURE_UNIT_SECONDS) - 1e-9)
    high = math.floor(math.log2(max(1, control.maximum) * EXPOSURE_UNIT_SECONDS) + 1e-9)
    return low, max(low, high)


class V4l2Backend:
    """Reads and writes a webcam's controls by its by-path link, through a `V4l2System`."""

    def __init__(self, system: V4l2System) -> None:
        """`system` is the kernel (`KernelV4l2System`) or a fake."""
        self._system = system
        # Keyed by the lower-case link: the device node's identity when last listed.
        self._identities: dict[str, tuple[int, int]] = {}
        self._links: dict[str, str] = {}
        self._supported: dict[str, list[str]] = {}
        self._reopened: list[str] = []

    def list_devices(self) -> list[ListedDevice]:
        """The cameras plugged in now; a known one whose node was made again was replugged."""
        listed: list[ListedDevice] = []
        identities: dict[str, tuple[int, int]] = {}
        links: dict[str, str] = {}
        seen_names: dict[str, int] = {}
        for node in self._system.capture_nodes():
            index = seen_names.get(node.name, 0)
            seen_names[node.name] = index + 1
            listed.append(ListedDevice(node.name, node.stable_path, index))
            key = node.stable_path.lower()
            identities[key] = node.identity
            links[key] = node.stable_path
            before = self._identities.get(key)
            if before is not None and before != node.identity:
                # Same port, new device node: it was unplugged and plugged back in.
                self._reopened.append(node.stable_path)
                self._supported.pop(key, None)
        for key in set(self._identities) - set(identities):
            self._supported.pop(key, None)
        self._identities, self._links = identities, links
        return listed

    def probe(self) -> None:
        """Look for replugs (the service's 2 s `check`): listing is the look."""
        self.list_devices()

    def take_reopened(self) -> list[str]:
        """Links whose device was replugged since the last call."""
        reopened, self._reopened = self._reopened, []
        return reopened

    def supported(self, path: str) -> list[str]:
        """The studio names of the controls the device has, in `CONTROLS` order (cached)."""
        key = path.lower()
        if key not in self._supported:
            with self._open(path) as device:
                self._supported[key] = [
                    name
                    for name, mapped in CONTROLS.items()
                    if device.query_control(mapped.value_id) is not None
                ]
        return self._supported[key]

    def read(self, path: str, name: str) -> dict[str, Any]:
        """One control: value, auto (None without an automatic mode), range and default."""
        mapped = _mapped(name)
        with self._open(path) as device:
            info = _require(device, mapped.value_id, name)
            raw = device.get_control(mapped.value_id)
            auto = self._read_auto(device, mapped)
        if mapped.exposure:
            low, high = exposure_range(info)
            return {
                "value": exposure_to_log2(raw),
                "auto": auto,
                "min": low,
                "max": high,
                "step": 1,
                "default": exposure_to_log2(info.default),
            }
        return {
            "value": raw,
            "auto": auto,
            "min": info.minimum,
            "max": info.maximum,
            "step": max(1, info.step),
            "default": info.default,
        }

    def write(self, path: str, name: str, value: int, auto: bool) -> None:
        """Set one control to `value` (manual) or to automatic; raises `UvcError` if refused.

        On auto the value is not written; to manual, auto is turned off first.
        """
        mapped = _mapped(name)
        try:
            with self._open(path) as device:
                info = _require(device, mapped.value_id, name)
                if mapped.auto_id is not None and self._has_auto(device, mapped):
                    self._write_auto(device, mapped, auto)
                    if auto:
                        return
                elif auto:
                    raise UvcError(f"{name} has no automatic mode")
                raw = exposure_from_log2(value, info) if mapped.exposure else int(value)
                device.set_control(mapped.value_id, raw)
        except OSError as error:
            raise UvcError(f"{name}: {error.strerror or error}") from None

    # --- inside ------------------------------------------------------------------------

    @contextmanager
    def _open(self, path: str) -> Iterator[V4l2Device]:
        """`with self._open(path) as device:` by link; `DeviceMissingError` if not plugged in."""
        link = self._links.get(path.lower(), path)
        try:
            device = self._system.open(link)
        except OSError as error:
            raise DeviceMissingError(f"no camera at {path}: {error.strerror or error}") from None
        try:
            yield device
        finally:
            device.close()

    def _has_auto(self, device: V4l2Device, mapped: MappedControl) -> bool:
        if mapped.auto_id is None:
            return False
        control = device.query_control(mapped.auto_id)
        if control is None:
            return False
        if mapped.exposure:
            # A menu with only "manual" is no automatic mode.
            return any(value != EXPOSURE_MANUAL for value in device.menu_values(control))
        return True

    def _read_auto(self, device: V4l2Device, mapped: MappedControl) -> bool | None:
        if not self._has_auto(device, mapped):
            return None
        assert mapped.auto_id is not None
        value = device.get_control(mapped.auto_id)
        return value != EXPOSURE_MANUAL if mapped.exposure else bool(value)

    def _write_auto(self, device: V4l2Device, mapped: MappedControl, auto: bool) -> None:
        assert mapped.auto_id is not None
        if not mapped.exposure:
            device.set_control(mapped.auto_id, int(auto))
            return
        if not auto:
            device.set_control(mapped.auto_id, EXPOSURE_MANUAL)
            return
        control = device.query_control(mapped.auto_id)
        offered = device.menu_values(control) if control is not None else []
        # UVC cameras offer "aperture priority" as their auto; some offer plain "auto".
        chosen = (
            EXPOSURE_APERTURE_PRIORITY if EXPOSURE_APERTURE_PRIORITY in offered else (EXPOSURE_AUTO)
        )
        device.set_control(mapped.auto_id, chosen)


def _mapped(name: str) -> MappedControl:
    if name not in CONTROLS:
        raise UvcError(f"unknown control {name!r}")
    return CONTROLS[name]


def _require(device: V4l2Device, control_id: int, name: str) -> ControlInfo:
    info = device.query_control(control_id)
    if info is None:
        raise UvcError(f"this camera has no {name} control")
    return info
