"""A node camera's image controls, through the UVC child (ADR 0019), in either video mode.

The studio's Image page changes an agent camera's exposure, white balance,
focus... with `GET`/`PUT /cameras/{id}/image` (contract §6). On the node
they go to the camera's USB device through the same UVC child the studio
uses on its own PC (`childprocs/uvc_child.py`, run by `imaging/uvc_link.py`),
which works while OBS or the node's ffmpeg has the camera open (the studio
PC did so on 15 Nov).

- The controls a camera lists are the ones its device has, among the
  studio's image controls (`imaging/controls.IMAGE_CONTROL_NAMES`); they are
  remembered from the last read, because `/agent/info` must answer at once
  without asking the device. A camera without a USB device has none.
- A write is checked against a fresh read first: an unknown name refuses the
  whole change (`400 unknown_control`); values are clamped to the range and
  put on the step, and a value sent while the control is on auto is ignored,
  as a webcam does; the answer is read back from the device.
- What was written is remembered, and written again when the device comes
  back after an unplug (a webcam forgets its settings): the studio only
  reapplies its saved settings when the agent link comes back, not when a
  USB cable does.
"""

import threading
from collections.abc import Callable
from typing import Any

from baja_studio.agents.contract import ErrorCode, ImageControlChange, ImageControlValue
from baja_studio.agents.server import CameraCommandError
from baja_studio.core.worker import DeviceOfflineError
from baja_studio.imaging.controls import IMAGE_CONTROL_NAMES, ControlState, ordered, snap
from baja_studio.imaging.usb_devices import locate
from baja_studio.imaging.uvc_link import UvcChildLink
from baja_studio.node.settings import NodeCameraSettings


class NodeImageControls:
    """Every node camera's image controls; safe to call from any thread."""

    def __init__(self, uvc: UvcChildLink, cameras: Callable[[], list[NodeCameraSettings]]) -> None:
        """`uvc` is the node's UVC child link; `cameras()` the cameras it serves now."""
        self._uvc = uvc
        self._cameras = cameras
        self._lock = threading.Lock()
        self._names: dict[str, list[str]] = {}
        # Per camera, what was last written: {control: {"value": v, "auto": a}}.
        self._written: dict[str, dict[str, dict[str, Any]]] = {}

    def names(self, camera_id: str) -> list[str]:
        """The controls `camera_id` lists in `/agent/info` (from the last read)."""
        with self._lock:
            return list(self._names.get(camera_id, []))

    def read(self, camera: NodeCameraSettings) -> dict[str, ImageControlValue]:
        """`GET /image`: every control the camera's device has, read now."""
        controls = self._read_states(camera)
        return {name: _contract_value(control) for name, control in controls.items()}

    def write(
        self, camera: NodeCameraSettings, changes: dict[str, ImageControlChange]
    ) -> dict[str, ImageControlValue]:
        """`PUT /image`: apply `changes` (all or nothing), and answer what the device took."""
        current = self._read_states(camera)
        unknown = sorted(set(changes) - set(current))
        if unknown:
            raise CameraCommandError(
                ErrorCode.UNKNOWN_CONTROL,
                f"{camera.id} has no image control {', '.join(unknown)}; "
                f"it has {', '.join(current) or 'none'}",
            )
        writes = {name: _write_of(current[name], change) for name, change in changes.items()}
        if writes:
            path = self._path(camera)
            self._request("write_controls", {"path": path, "controls": writes})
            with self._lock:
                self._written.setdefault(camera.id, {}).update(writes)
        return self.read(camera)

    def device_returned(self, paths: set[str]) -> None:
        """USB devices came back (paths in lower case): write their cameras' settings again.

        Called on the UVC worker's thread; a failure is left for the next read to show.
        """
        for camera in self._cameras():
            ref = camera.usb_device
            with self._lock:
                written = dict(self._written.get(camera.id, {}))
            if ref is None or not written or ref.path.lower() not in paths:
                continue
            try:
                self._request("write_controls", {"path": ref.path, "controls": written})
            except CameraCommandError:
                continue

    # --- internals ----------------------------------------------------------------------

    def _read_states(self, camera: NodeCameraSettings) -> dict[str, ControlState]:
        if camera.usb_device is None:
            with self._lock:
                self._names[camera.id] = []
            return {}
        answer = self._request(
            "read_controls", {"path": self._path(camera), "names": IMAGE_CONTROL_NAMES}
        )
        read = {name: ControlState.from_usb(data) for name, data in answer["controls"].items()}
        controls = {name: read[name] for name in ordered(list(read))}
        with self._lock:
            self._names[camera.id] = list(controls)
        return controls

    def _path(self, camera: NodeCameraSettings) -> str:
        """The camera's device path now, or `503 device_offline` saying it is not plugged in."""
        assert camera.usb_device is not None
        found = locate(camera.usb_device, self._uvc.devices())
        if found.device is None:
            raise CameraCommandError(ErrorCode.DEVICE_OFFLINE, found.problem)
        return found.device.path

    def _request(self, method: str, params: dict[str, Any]) -> Any:
        try:
            return self._uvc.request(method, params)
        except DeviceOfflineError as error:
            raise CameraCommandError(ErrorCode.DEVICE_OFFLINE, str(error)) from None
        except Exception as error:  # the child answered with an error (a device refusing)
            raise CameraCommandError(
                ErrorCode.DEVICE_OFFLINE, f"the USB camera did not answer: {error}"
            ) from None


def _contract_value(control: ControlState) -> ImageControlValue:
    """A control as the contract reports it; the node never changes one by itself (`locked`)."""
    return ImageControlValue(
        value=control.value,
        auto=control.auto,
        locked=False,
        min=control.min,
        max=control.max,
        step=control.step,
    )


def _write_of(control: ControlState, change: ImageControlChange) -> dict[str, Any]:
    """What to write for one change: like a webcam, a value is ignored while auto is on."""
    auto = control.auto
    if change.auto is not None and control.auto is not None:
        auto = change.auto
    value = control.value
    if change.value is not None and not auto:
        value = snap(change.value, control)
    return {"value": value, "auto": bool(auto)}
