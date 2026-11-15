"""The UVC child's backend: duvc-ctl (DirectShow), or its fake, behind studio control names.

Used only inside the UVC child process, and only from its request thread:
DirectShow objects are COM objects, so every handle is opened, used and
dropped on the one thread that answers requests (there is no watcher thread).

It takes the *library* as an argument (the real `duvc_ctl` module on Windows,
`fakes.fake_duvc` in tests and the rig), so the code that copes with the
library's quirks runs against both:

- reads return a result with `value()`; writes return one with only
  `is_ok()` and `error()`. A write's result is never asked for `value()`.
- every control of a write is written and judged on its own: one refused
  axis never hides the next (the `pixy_ptz` two-axis bug).
- a device is found by its DirectShow path, case-insensitively.
- what never changes while a device stays plugged in is asked once: the
  device object (from the last listing) and which controls it has. A full
  capability scan opens the filter and reads every range, far too slow to do
  on every read (the Pixy's 20 Hz writes would queue behind it).
- an open handle dies when its device is unplugged, even for a moment. A
  failing handle is dropped and the device opened again; that path is then
  reported by `take_reopened`, so the service puts the camera's settings
  back (a replug shorter than the 2 s device look would otherwise go unnoticed).

Values are whole device numbers. Names are the studio's (`white_balance`),
mapped here to the library's (`VidProp.WhiteBalance`).
"""

import contextlib
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

# Studio name -> (domain, member of CamProp or VidProp).
CONTROLS: dict[str, tuple[str, str]] = {
    "pan": ("cam", "Pan"),
    "tilt": ("cam", "Tilt"),
    "zoom": ("cam", "Zoom"),
    "exposure": ("cam", "Exposure"),
    "iris": ("cam", "Iris"),
    "focus": ("cam", "Focus"),
    "brightness": ("vid", "Brightness"),
    "contrast": ("vid", "Contrast"),
    "hue": ("vid", "Hue"),
    "saturation": ("vid", "Saturation"),
    "sharpness": ("vid", "Sharpness"),
    "gamma": ("vid", "Gamma"),
    "white_balance": ("vid", "WhiteBalance"),
    "backlight_compensation": ("vid", "BacklightCompensation"),
    "gain": ("vid", "Gain"),
}


class UvcError(Exception):
    """The library refused or failed; the message is its description."""


class DeviceMissingError(UvcError):
    """No device with that path is plugged in."""


@dataclass(frozen=True)
class ListedDevice:
    """A USB camera as listed: `index` counts devices with the same name (0 first)."""

    name: str
    path: str
    index: int

    def as_json(self) -> dict[str, Any]:
        """The form the child sends to the service."""
        return {"name": self.name, "path": self.path, "index": self.index}


class DuvcBackend:
    """Reads and writes UVC controls by device path, through a duvc-ctl-like library."""

    def __init__(self, library: Any) -> None:
        """`library` is `duvc_ctl` itself, or a `FakeDuvcLibrary`."""
        self._library = library
        # All keyed by the lower-case path.
        self._devices: dict[str, Any] = {}
        self._cameras: dict[str, Any] = {}
        self._supported: dict[str, list[str]] = {}
        # Paths whose handle died; opening one of them again is a reopen.
        self._lost: set[str] = set()
        self._reopened: list[str] = []

    def list_devices(self) -> list[ListedDevice]:
        """The cameras plugged in now; forgets everything about the ones that left."""
        listed: list[ListedDevice] = []
        devices: dict[str, Any] = {}
        seen_names: dict[str, int] = {}
        for device in self._library.list_devices():
            name, path = str(device.name), str(device.path)
            index = seen_names.get(name, 0)
            seen_names[name] = index + 1
            listed.append(ListedDevice(name, path, index))
            devices[path.lower()] = device
        for key in set(self._devices) - set(devices):
            self._lose(key)
            self._supported.pop(key, None)
        self._devices = devices
        return listed

    def take_reopened(self) -> list[str]:
        """Paths whose handle had died and was opened again since the last call."""
        reopened, self._reopened = self._reopened, []
        return reopened

    def probe(self) -> None:
        """Touch every open handle with one read; a dead one is dropped and reopened.

        Called with each 2 s device look, so a camera unplugged and plugged
        back in between two looks is still noticed (its handle died).
        """
        for key in list(self._cameras):
            names = self._supported.get(key) or []
            if not names or self._cameras[key].get(self._prop(names[0])).is_ok():
                continue
            self._lose(key)
            # Still away: it is reopened (and reported) once it is back.
            with contextlib.suppress(UvcError):
                self._camera(key)

    def supported(self, path: str) -> list[str]:
        """The studio names of the controls the device has, in `CONTROLS` order (cached)."""
        key = path.lower()
        if key not in self._supported:
            result = self._library.get_device_capabilities(self._device(path))
            if not result.is_ok():
                raise UvcError(result.error().description())
            capabilities = result.value()
            members = {
                *(("cam", prop.name) for prop in capabilities.supported_camera_properties()),
                *(("vid", prop.name) for prop in capabilities.supported_video_properties()),
            }
            self._supported[key] = [name for name, item in CONTROLS.items() if item in members]
        return self._supported[key]

    def read(self, path: str, name: str) -> dict[str, Any]:
        """One control: value, auto (None if it has no automatic mode), range and default.

        Whether a control has an automatic mode comes from DirectShow's range
        capability flags, which duvc reports as the range's `default_mode`.
        """
        prop = self._prop(name)
        setting = self._read_result(path, lambda camera: camera.get(prop))
        prop_range = self._read_result(path, lambda camera: camera.get_range(prop))
        has_auto = prop_range.default_mode == self._library.CamMode.Auto
        return {
            "value": int(setting.value),
            "auto": (setting.mode == self._library.CamMode.Auto) if has_auto else None,
            "min": int(prop_range.min),
            "max": int(prop_range.max),
            "step": max(1, int(prop_range.step)),
            "default": int(prop_range.default_val),
        }

    def write(self, path: str, name: str, value: int, auto: bool) -> None:
        """Set one control to `value` (manual) or to automatic; raises `UvcError` if refused.

        UVC takes the value and the mode together; on auto the value is ignored.
        A refused write is told from a dead handle by reading the same control:
        a live handle reads fine (the device just said no), a dead one does
        not, and then the device is opened again and the write tried once more.
        """
        prop = self._prop(name)
        mode = self._library.CamMode.Auto if auto else self._library.CamMode.Manual
        setting = self._library.PropSetting(int(value), mode)
        camera = self._camera(path)
        # A write's result has is_ok() and error(), and no value().
        result = camera.set(prop, setting)
        if result.is_ok():
            return
        if camera.get(prop).is_ok():
            raise UvcError(result.error().description())
        self._lose(path.lower())
        result = self._camera(path).set(prop, setting)
        if not result.is_ok():
            raise UvcError(result.error().description())

    def handle_fake(self, method: str, params: dict[str, Any]) -> Any:
        """`fake_*` requests go to the fake library; the real one has none."""
        fake_handler = getattr(self._library, "handle_fake", None)
        if fake_handler is None:
            raise ValueError("fake_* requests need the fake UVC backend")
        return fake_handler(method, params)

    # --- inside ------------------------------------------------------------------------

    def _read_result(self, path: str, call: Callable[[Any], Any]) -> Any:
        result = call(self._camera(path))
        if result.is_ok():
            return result.value()
        # A read only fails on a dead handle (or a device gone): open again, once.
        self._lose(path.lower())
        result = call(self._camera(path))
        if not result.is_ok():
            raise UvcError(result.error().description())
        return result.value()

    def _prop(self, name: str) -> Any:
        if name not in CONTROLS:
            raise UvcError(f"unknown control {name!r}")
        domain, member = CONTROLS[name]
        enum = self._library.CamProp if domain == "cam" else self._library.VidProp
        return getattr(enum, member)

    def _device(self, path: str) -> Any:
        device = self._devices.get(path.lower())
        if device is None:
            self.list_devices()  # plugged in since the last look?
            device = self._devices.get(path.lower())
        if device is None:
            raise DeviceMissingError("the camera is not plugged in")
        return device

    def _camera(self, path: str) -> Any:
        key = path.lower()
        camera = self._cameras.get(key)
        if camera is None:
            result = self._library.open_camera(self._device(path))
            if not result.is_ok():
                raise UvcError(result.error().description())
            camera = self._cameras[key] = result.value()
            if key in self._lost:
                self._lost.discard(key)
                self._reopened.append(path)
        return camera

    def _lose(self, key: str) -> None:
        """Drop a handle; only ever on the request thread, like every other COM call."""
        if self._cameras.pop(key, None) is not None:
            self._lost.add(key)
