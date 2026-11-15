"""A fake of the duvc-ctl library (UVC over DirectShow) with two S600 webcams and a Pixy.

`FakeDuvcLibrary` has the same surface as the parts of the real `duvc_ctl`
module the UVC child uses (`list_devices`, `open_camera`,
`get_device_capabilities`, `CamProp`, `VidProp`, `CamMode`, `PropSetting`),
so `childprocs/uvc_duvc.py` runs unchanged against either. That is the point:
the quirks below are the library's, and only a fake *library* lets the tests
catch code that trips over them.

What it models, and where each behaviour comes from:

- **Result types** (duvc-ctl 2.1 package source, and the `pixy_ptz` work,
  Oct 2026): reads (`get`, `get_range`, `open_camera`) return a result with
  `is_ok()`, `value()` and `error()`. Writes (`set`) return a *different*
  result with only `is_ok()` and `error()`: it has no `value()`, so calling
  it raises `AttributeError`, as the real `VoidResult` does. That once
  silently dropped the second axis of a two-axis move in `pixy_ptz`.
- **Pixy pan/tilt/zoom readback is the last value written through UVC**
  (`pixy_ptz`): moving the camera by hand, by its own AI tracking or from the
  EMEET app changes the real position (`fake_hand_move`) but not what `get`
  answers. Zoom (100-150) only changes the picture at 1080p
  (`capture_height`); the readback is the written value either way.
- **Pixy ranges** (`pixy_ptz`): pan -150..150, tilt -90..90, 1° steps.
- **S600 image ranges are typical UVC webcam values, not measured on an
  S600**; the bench sheet (docs/bench/wp6.md) records the real ones. The
  studio reads every range from the device, so wrong numbers here change
  no code path.
- **DirectShow refuses** a manual value outside the range and an automatic
  mode on a control that has none (E_INVALIDARG; the descriptions are ours).
- **Several devices can share a name** (two S600s); the device path differs
  per USB port. An unplugged device leaves `list_devices`, and a camera
  object opened before the unplug answers every call with an error.
- **Automatic controls drift**: while a control is on auto, its value follows
  the scene (`fake_scene {light}`), as exposure does when a cloud passes.

Run it to see the UVC child answer against it:

    python -m baja_studio.fakes.fake_duvc
"""

import json
import os
import subprocess
import sys
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

# Matches `childprocs.uvc_child.BACKEND_ENVIRONMENT_VARIABLE` (not imported:
# the child imports this module, and this module only runs the child).
BACKEND_ENVIRONMENT_VARIABLE = "BAJA_STUDIO_UVC_BACKEND"

S600_NAME = "EMEET SmartCam S600"
PIXY_NAME = "EMEET PIXY"
# DirectShow paths look like this; the instance part (7&...) differs per USB port.
_VIDEO_CLASS = r"#{65e8773d-8f56-11d0-a3b9-00a0c90629ac}\global"
S600_A_PATH = r"\\?\usb#vid_328f&pid_00c0&mi_00#7&1a2b3c4d&0&0000" + _VIDEO_CLASS
S600_B_PATH = r"\\?\usb#vid_328f&pid_00c0&mi_00#7&5e6f7a8b&0&0000" + _VIDEO_CLASS
PIXY_PATH = r"\\?\usb#vid_328f&pid_00d0&mi_00#7&9c0d1e2f&0&0000" + _VIDEO_CLASS


class CamMode(Enum):
    """`duvc_ctl.CamMode`."""

    Auto = 1
    Manual = 2


class CamProp(Enum):
    """`duvc_ctl.CamProp` (the members the studio uses, plus one it does not)."""

    Pan = 0
    Tilt = 1
    Roll = 2
    Zoom = 3
    Exposure = 4
    Iris = 5
    Focus = 6


class VidProp(Enum):
    """`duvc_ctl.VidProp`."""

    Brightness = 0
    Contrast = 1
    Hue = 2
    Saturation = 3
    Sharpness = 4
    Gamma = 5
    ColorEnable = 6
    WhiteBalance = 7
    BacklightCompensation = 8
    Gain = 9


@dataclass
class PropSetting:
    """`duvc_ctl.PropSetting(value, mode)`."""

    value: int
    mode: CamMode


@dataclass
class PropRange:
    """`duvc_ctl.PropRange`: note `default_val` and `default_mode` are attributes."""

    min: int
    max: int
    step: int
    default_val: int
    default_mode: CamMode


class FakeErrorInfo:
    """`duvc_ctl.ErrorInfo`: what went wrong."""

    def __init__(self, description: str) -> None:
        self._description = description

    def description(self) -> str:
        """The library's message."""
        return self._description


class ReadResult:
    """A read's result (`PropSettingResult`, `PropRangeResult`, `open_camera`'s)."""

    def __init__(self, value: Any = None, error: str | None = None) -> None:
        self._value = value
        self._error = error

    def is_ok(self) -> bool:
        """True when `value()` may be called."""
        return self._error is None

    def value(self) -> Any:
        """The value; the real library raises when the result is an error."""
        if self._error is not None:
            raise RuntimeError(f"value() on an error result: {self._error}")
        return self._value

    def error(self) -> FakeErrorInfo:
        """The error of a failed read."""
        return FakeErrorInfo(self._error or "")


class WriteResult:
    """A write's result (`VoidResult`): `is_ok()` and `error()`, and no `value()`."""

    def __init__(self, error: str | None = None) -> None:
        self._error = error

    def is_ok(self) -> bool:
        """True when the device took the write."""
        return self._error is None

    def error(self) -> FakeErrorInfo:
        """The error of a refused write."""
        return FakeErrorInfo(self._error or "")


@dataclass
class FakeControl:
    """One UVC control of a fake device."""

    range: PropRange
    value: int
    mode: CamMode
    has_auto: bool
    # What the device picks by itself while on auto (follows `fake_scene`).
    auto_value: int | None = None

    def read(self) -> PropSetting:
        """What `get` answers: on auto, the value the device chose."""
        if self.mode == CamMode.Auto and self.auto_value is not None:
            return PropSetting(self.auto_value, self.mode)
        return PropSetting(self.value, self.mode)


def _control(low: int, high: int, default: int, step: int = 1, auto: bool = False) -> FakeControl:
    mode = CamMode.Auto if auto else CamMode.Manual
    return FakeControl(
        range=PropRange(low, high, step, default, mode),
        value=default,
        mode=mode,
        has_auto=auto,
        auto_value=default if auto else None,
    )


def webcam_controls() -> dict[Enum, FakeControl]:
    """Typical UVC webcam image controls (not measured on an S600; see the module docstring)."""
    return {
        CamProp.Exposure: _control(-13, -1, -6, auto=True),
        CamProp.Focus: _control(0, 1023, 0, step=1, auto=True),
        VidProp.Brightness: _control(-64, 64, 0),
        VidProp.Contrast: _control(0, 100, 50),
        VidProp.Saturation: _control(0, 100, 64),
        VidProp.Sharpness: _control(0, 100, 50),
        VidProp.Gamma: _control(100, 500, 300),
        VidProp.WhiteBalance: _control(2800, 6500, 4600, step=10, auto=True),
        VidProp.BacklightCompensation: _control(0, 2, 1),
        VidProp.Gain: _control(0, 100, 0),
    }


def pixy_controls() -> dict[Enum, FakeControl]:
    """The Pixy: a webcam's image controls plus pan, tilt and zoom (ranges from `pixy_ptz`)."""
    return {
        CamProp.Pan: _control(-150, 150, 0),
        CamProp.Tilt: _control(-90, 90, 0),
        CamProp.Zoom: _control(100, 150, 100),
        **webcam_controls(),
    }


@dataclass
class FakeDevice:
    """`duvc_ctl.Device`: a name and a path, plus the fake's own state."""

    name: str
    path: str
    controls: dict[Enum, FakeControl] = field(default_factory=webcam_controls)
    plugged: bool = True
    # The Pixy's real position, which UVC readback does not report.
    real_pan: float = 0.0
    real_tilt: float = 0.0
    capture_height: int = 1080


class FakeCapabilities:
    """`duvc_ctl.DeviceCapabilities`: which properties the device has."""

    def __init__(self, device: FakeDevice) -> None:
        self._device = device

    def supported_camera_properties(self) -> list[CamProp]:
        """The camera-domain properties (pan, tilt, zoom, exposure, focus...)."""
        return [prop for prop in self._device.controls if isinstance(prop, CamProp)]

    def supported_video_properties(self) -> list[VidProp]:
        """The video-domain properties (brightness, white balance, gain...)."""
        return [prop for prop in self._device.controls if isinstance(prop, VidProp)]


class FakeCamera:
    """`duvc_ctl.Camera`, opened with `open_camera`."""

    def __init__(self, library: "FakeDuvcLibrary", device: FakeDevice) -> None:
        self._library = library
        self._device = device

    def is_valid(self) -> bool:
        """False once the device has been unplugged."""
        return self._device.plugged

    def get(self, prop: Enum) -> ReadResult:
        """The control's current setting."""
        control = self._control(prop)
        if isinstance(control, str):
            return ReadResult(error=control)
        return ReadResult(control.read())

    def get_range(self, prop: Enum) -> ReadResult:
        """The control's range, step and defaults."""
        control = self._control(prop)
        if isinstance(control, str):
            return ReadResult(error=control)
        return ReadResult(control.range)

    def set(self, prop: Enum, setting: PropSetting) -> WriteResult:
        """Change a control; refused out of range, or auto where there is none."""
        control = self._control(prop)
        if isinstance(control, str):
            return WriteResult(control)
        self._library.writes.append(
            (self._device.path, prop.name, setting.value, setting.mode.name)
        )
        if setting.mode == CamMode.Auto:
            if not control.has_auto:
                return WriteResult("Invalid argument: the property has no automatic mode")
            control.mode = CamMode.Auto
            return WriteResult()
        if not control.range.min <= setting.value <= control.range.max:
            return WriteResult("Invalid argument: value out of range")
        control.mode, control.value = CamMode.Manual, int(setting.value)
        if prop == CamProp.Pan:
            self._device.real_pan = setting.value
        if prop == CamProp.Tilt:
            self._device.real_tilt = setting.value
        return WriteResult()

    def close(self) -> None:
        """Nothing to release in the fake."""

    def _control(self, prop: Enum) -> FakeControl | str:
        if not self._device.plugged:
            return "Device not connected"
        control = self._device.controls.get(prop)
        return control if control is not None else "Property not supported"


class FakeDuvcLibrary:
    """Stands in for the `duvc_ctl` module, with fake devices plugged in."""

    CamMode = CamMode
    CamProp = CamProp
    VidProp = VidProp
    PropSetting = PropSetting

    def __init__(self, devices: list[FakeDevice] | None = None) -> None:
        """No devices unless given; `studio_devices()` builds the studio's three."""
        self.devices = devices if devices is not None else []
        # Every write that reached a device: (path, property, value, mode), in order.
        self.writes: list[tuple[str, str, int, str]] = []

    def list_devices(self) -> list[FakeDevice]:
        """The plugged-in devices, in the order DirectShow lists them."""
        return [device for device in self.devices if device.plugged]

    def open_camera(self, device: FakeDevice) -> ReadResult:
        """A camera handle, or an error if the device is gone."""
        if not device.plugged:
            return ReadResult(error="Device not found")
        return ReadResult(FakeCamera(self, device))

    def get_device_capabilities(self, device: FakeDevice) -> ReadResult:
        """Which properties the device supports."""
        if not device.plugged:
            return ReadResult(error="Device not found")
        return ReadResult(FakeCapabilities(device))

    def device(self, path: str) -> FakeDevice:
        """The fake device at `path` (plugged in or not)."""
        return next(device for device in self.devices if device.path.lower() == path.lower())

    def handle_fake(self, method: str, params: dict[str, Any]) -> Any:
        """The `fake_*` requests the UVC child forwards (tests, the rig, the demo)."""
        if method in ("fake_unplug", "fake_replug"):
            self.device(str(params["path"])).plugged = method == "fake_replug"
            return None
        if method == "fake_scene":
            self.set_scene(float(params.get("light", 0.5)))
            return None
        if method == "fake_hand_move":
            device = self.device(str(params["path"]))
            device.real_pan = float(params.get("pan", device.real_pan))
            device.real_tilt = float(params.get("tilt", device.real_tilt))
            return None
        if method == "fake_writes":
            writes, self.writes = self.writes, []
            return [list(write) for write in writes]
        raise ValueError(f"unknown fake request {method!r}")

    def set_scene(self, light: float) -> None:
        """Change the light (0 dark .. 1 bright): automatic controls pick new values."""
        for device in self.devices:
            for prop, control in device.controls.items():
                if control.auto_value is None:
                    continue
                low, high = control.range.min, control.range.max
                # Brighter light: shorter exposure, colder white balance.
                share = 1.0 - light if prop in (CamProp.Exposure, VidProp.WhiteBalance) else light
                control.auto_value = round(low + share * (high - low))


def studio_devices() -> list[FakeDevice]:
    """The studio PC's USB cameras on the bench: two S600s (one name) and a Pixy."""
    return [
        FakeDevice(S600_NAME, S600_A_PATH),
        FakeDevice(S600_NAME, S600_B_PATH),
        FakeDevice(PIXY_NAME, PIXY_PATH, controls=pixy_controls()),
    ]


def main() -> None:
    """Run the real UVC child against the fake and print what comes back."""
    environment = {**os.environ, BACKEND_ENVIRONMENT_VARIABLE: "fake", "PYTHONUNBUFFERED": "1"}
    requests = [
        {"id": 1, "method": "list_devices"},
        {"id": 2, "method": "read_controls", "params": {"path": PIXY_PATH, "names": ["pan"]}},
        {
            "id": 3,
            "method": "write_controls",
            "params": {"path": PIXY_PATH, "controls": {"pan": {"value": 30, "auto": False}}},
        },
        {"id": 4, "method": "fake_writes"},
    ]
    stdin = "".join(json.dumps(request) + "\n" for request in requests)
    completed = subprocess.run(
        [sys.executable, "-m", "baja_studio.childprocs.uvc_child"],
        input=stdin,
        capture_output=True,
        text=True,
        env=environment,
        timeout=10,
        check=False,
    )
    print(completed.stdout, end="")
    print(completed.stderr, end="", file=sys.stderr)


if __name__ == "__main__":
    main()
