"""The UVC child's backend: duvc-ctl (DirectShow), or its fake, behind studio control names.

Used only inside the UVC child process. It takes the *library* as an
argument (the real `duvc_ctl` module on Windows, `fakes.fake_duvc` in tests
and the rig), so the code that copes with the library's quirks runs against
both:

- reads return a result with `value()`; writes return one with only
  `is_ok()` and `error()`. A write's result is never asked for `value()`.
- every control of a write is written and judged on its own: one refused
  axis never hides the next (the `pixy_ptz` two-axis bug).
- a device is found by its DirectShow path, case-insensitively.

Values are whole device numbers. Names are the studio's (`white_balance`),
mapped here to the library's (`VidProp.WhiteBalance`).
"""

import sys
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
# Controls that UVC defines an automatic mode for. DirectShow's range does not
# say whether a control has one, so a control has `auto` when it is listed
# here or the device reports it on auto (now or by default). The bench sheet
# checks this on the S600 and the Pixy.
AUTO_CAPABLE = {"exposure", "focus", "iris", "white_balance", "hue"}


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
        self._cameras: dict[str, Any] = {}

    def list_devices(self) -> list[ListedDevice]:
        """The cameras plugged in now; forgets open handles of the ones that left."""
        listed: list[ListedDevice] = []
        seen_names: dict[str, int] = {}
        for device in self._library.list_devices():
            name, path = str(device.name), str(device.path)
            index = seen_names.get(name, 0)
            seen_names[name] = index + 1
            listed.append(ListedDevice(name, path, index))
        present = {device.path.lower() for device in listed}
        for path in list(self._cameras):
            if path not in present:
                self._forget(path)
        return listed

    def supported(self, path: str) -> list[str]:
        """The studio names of the controls the device has, in `CONTROLS` order."""
        device = self._device(path)
        result = self._library.get_device_capabilities(device)
        if not result.is_ok():
            raise UvcError(result.error().description())
        capabilities = result.value()
        members = {
            *(("cam", prop.name) for prop in capabilities.supported_camera_properties()),
            *(("vid", prop.name) for prop in capabilities.supported_video_properties()),
        }
        return [name for name, key in CONTROLS.items() if key in members]

    def read(self, path: str, name: str) -> dict[str, Any]:
        """One control: value, auto (None if it has no automatic mode), range and defaults."""
        prop = self._prop(name)
        setting = self._read_result(path, lambda camera: camera.get(prop))
        prop_range = self._read_result(path, lambda camera: camera.get_range(prop))
        auto_mode = self._library.CamMode.Auto
        has_auto = (
            name in AUTO_CAPABLE
            or setting.mode == auto_mode
            or prop_range.default_mode == auto_mode
        )
        return {
            "value": int(setting.value),
            "auto": (setting.mode == auto_mode) if has_auto else None,
            "min": int(prop_range.min),
            "max": int(prop_range.max),
            "step": max(1, int(prop_range.step)),
            "default": int(prop_range.default_val),
            "default_auto": (prop_range.default_mode == auto_mode) if has_auto else None,
        }

    def write(self, path: str, name: str, value: int, auto: bool) -> None:
        """Set one control to `value` (manual) or to automatic; raises `UvcError` if refused.

        UVC takes the value and the mode together; on auto the value is ignored.
        """
        prop = self._prop(name)
        mode = self._library.CamMode.Auto if auto else self._library.CamMode.Manual
        setting = self._library.PropSetting(int(value), mode)
        for attempt in range(2):
            camera = self._camera(path)
            # A write's result has is_ok() and error(), and no value().
            result = camera.set(prop, setting)
            if result.is_ok():
                return
            # A handle opened before an unplug/replug is stale: reopen once.
            self._forget(path)
            if attempt == 1:
                raise UvcError(result.error().description())

    def handle_fake(self, method: str, params: dict[str, Any]) -> Any:
        """`fake_*` requests go to the fake library; the real one has none."""
        fake_handler = getattr(self._library, "handle_fake", None)
        if fake_handler is None:
            raise ValueError("fake_* requests need the fake UVC backend")
        return fake_handler(method, params)

    # --- inside ------------------------------------------------------------------------

    def _read_result(self, path: str, call: Any) -> Any:
        for attempt in range(2):
            result = call(self._camera(path))
            if result.is_ok():
                return result.value()
            self._forget(path)
            if attempt == 1:
                raise UvcError(result.error().description())
        raise AssertionError("unreachable")

    def _prop(self, name: str) -> Any:
        if name not in CONTROLS:
            raise UvcError(f"unknown control {name!r}")
        domain, member = CONTROLS[name]
        enum = self._library.CamProp if domain == "cam" else self._library.VidProp
        return getattr(enum, member)

    def _device(self, path: str) -> Any:
        for device in self._library.list_devices():
            if str(device.path).lower() == path.lower():
                return device
        raise DeviceMissingError("the camera is not plugged in")

    def _camera(self, path: str) -> Any:
        key = path.lower()
        camera = self._cameras.get(key)
        if camera is None:
            result = self._library.open_camera(self._device(path))
            if not result.is_ok():
                raise UvcError(result.error().description())
            camera = self._cameras[key] = result.value()
        return camera

    def _forget(self, path: str) -> None:
        camera = self._cameras.pop(path.lower(), None)
        if camera is not None:
            try:
                camera.close()
            except Exception as error:  # a stale handle may refuse to close; it is dropped anyway
                print(f"closing a camera handle failed: {error}", file=sys.stderr)
