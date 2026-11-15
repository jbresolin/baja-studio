"""The imaging service: every camera's image controls, read, written, saved and reapplied.

A camera has image settings when it has a `usb_device` (through the UVC
child) or is an agent camera whose agent lists `image_controls`. For each,
`state.cameras.image.<camera id>` holds its controls (value, auto, range,
default, the error of the last write), its status and where it lives.

Operator actions (`set`, `lock`, `reset`, `copy`) run on the command thread.
A write is applied at once and what the device took is saved in the
camera's `image` settings; a refused value is shown on its control and not
saved. The `imaging` worker (`watch.py`) reapplies the saved settings by
itself: when the UVC child (re)starts, when a USB camera reappears, and when
an agent link comes back `ok`.
"""

import threading
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from baja_studio.cameras.config import CameraConfig, CamerasSettings, DriverKind
from baja_studio.cameras.drivers.agent_gateway import AgentGateway
from baja_studio.core.module import ModuleLogger
from baja_studio.imaging.config import ImageSetting
from baja_studio.imaging.controls import (
    ControlState,
    change_problem,
    copy_changes,
    default_changes,
    lock_changes,
    ordered,
    saved_after_write,
)
from baja_studio.imaging.sources import (
    AgentImageSource,
    ImageSource,
    ImageUnavailableError,
    UsbImageSource,
    WriteOutcome,
)
from baja_studio.imaging.usb_devices import Located, locate
from baja_studio.imaging.uvc_link import UvcChildLink

CameraEdit = Callable[[dict[str, Any]], dict[str, Any]]


class ImageRequestError(Exception):
    """The request itself is wrong (unknown camera or control); the message says how."""


@dataclass
class ImagingHost:
    """What the cameras module lends the imaging service."""

    publish: Callable[[str, Any], None]  # set a path in the cameras module's state
    read_state: Callable[[str], Any]  # read a path of the cameras module's state
    settings: Callable[[], CamerasSettings]
    # Saves an edited copy of one camera's settings entry (JSON form).
    edit_camera: Callable[[str, CameraEdit], None]
    logger: ModuleLogger


class ImagingService:
    """Image controls of every camera that has them."""

    def __init__(self, host: ImagingHost, link: UvcChildLink, agents: AgentGateway) -> None:
        """Nothing is read until the worker or a command asks."""
        self.host = host
        self.link = link
        self.agents = agents
        self._lock = threading.Lock()
        self._camera_locks: dict[str, threading.Lock] = {}
        self._controls: dict[str, dict[str, ControlState]] = {}
        self._errors: dict[str, dict[str, str]] = {}
        self._status: dict[str, tuple[str, str]] = {}
        self._published: dict[str, dict[str, Any]] = {}

    # --- which cameras ------------------------------------------------------------------

    def camera(self, camera_id: str) -> CameraConfig:
        """An enabled camera that has image settings; `ImageRequestError` otherwise."""
        cameras = {camera.id: camera for camera in self.host.settings().cameras if camera.enabled}
        camera = cameras.get(camera_id)
        if camera is None:
            raise ImageRequestError(f"no enabled camera called {camera_id!r}")
        if camera.usb_device is None and camera.driver != DriverKind.AGENT:
            raise ImageRequestError(
                f"camera {camera_id} has no image settings: pick its USB device on the "
                "Camera setup page"
            )
        return camera

    def locate_camera(self, camera_id: str) -> Located:
        """Where a camera's USB device is now (also used by the Pixy driver)."""
        cameras = [camera for camera in self.host.settings().cameras if camera.enabled]
        camera = next((c for c in cameras if c.id == camera_id), None)
        if camera is None or camera.usb_device is None:
            return Located(None, problem="no USB device picked (Camera setup)")
        status, message = self.link.status()
        if status != "ok":
            return Located(None, problem=message or f"USB camera control is {status}")
        return locate(camera.usb_device, self.link.devices())

    def source(self, camera: CameraConfig) -> ImageSource:
        """How to reach this camera's controls now; `ImageUnavailableError` if they cannot be."""
        if camera.usb_device is not None:
            located = self.locate_camera(camera.id)
            if located.device is None:
                raise ImageUnavailableError(located.problem)
            return UsbImageSource(self.link.request, located.device.path)
        agent_id = camera.address.agent_id
        if self.host.read_state(f"cameras.{camera.id}.route") == "fallback":
            raise ImageUnavailableError(
                f"agent {agent_id} is offline and the camera is driven directly, "
                "which has no image controls"
            )
        view = self.agents.link(agent_id)
        if view is None or view.status != "ok":
            status = view.status if view else "not configured"
            raise ImageUnavailableError(f"agent {agent_id} is {status}")
        return AgentImageSource(self.agents, agent_id, camera.address.remote_camera_id)

    # --- USB access for the Pixy driver ----------------------------------------------------

    def read_controls(self, path: str, names: list[str]) -> dict[str, Any]:
        """The UVC child's `read_controls` for one device."""
        answer: dict[str, Any] = self.link.request("read_controls", {"path": path, "names": names})
        return answer

    def write_controls(self, path: str, controls: dict[str, Any]) -> dict[str, str | None]:
        """The UVC child's `write_controls`: per control, None or the device's refusal."""
        answer: dict[str, str | None] = self.link.request(
            "write_controls", {"path": path, "controls": controls}
        )
        return answer

    # --- operator actions --------------------------------------------------------------

    def get(self, camera_id: str) -> dict[str, Any]:
        """Read the camera's controls afresh."""
        camera = self.camera(camera_id)
        with self._camera_lock(camera_id):
            source = self._reachable(camera)
            self._store(camera, source, self._read(camera, source))
        return self.entry(camera_id)

    def set(
        self, camera_id: str, changes: dict[str, ImageSetting], replace_saved: bool = False
    ) -> dict[str, Any]:
        """Write some controls now, save what the device took, show what it refused.

        `replace_saved`: the saved settings become exactly these (Reset).
        """
        camera = self.camera(camera_id)
        with self._camera_lock(camera_id):
            source = self._reachable(camera)
            current = self._controls.get(camera_id) or self._read(camera, source)
            unknown = sorted(name for name in changes if name not in current)
            if unknown:
                raise ImageRequestError(
                    f"camera {camera_id} has no image control {', '.join(unknown)}; "
                    f"it has {', '.join(ordered(list(current)))}"
                )
            problems = {n: p for n, c in changes.items() if (p := change_problem(current[n], c))}
            writable = {name: c for name, c in changes.items() if name not in problems}
            outcome = self._write(camera, source, writable, current)
            readback = outcome.readback or (self._read(camera, source) if writable else current)
            errors = self._errors.setdefault(camera_id, {})
            for name in changes:
                errors.pop(name, None)
            errors.update(problems)
            errors.update(outcome.errors)
            taken = {
                name: saved_after_write(readback[name])
                for name in writable
                if name not in outcome.errors and name in readback
            }
            self._store(camera, source, readback)
        if taken or replace_saved:
            self._save(camera_id, {} if replace_saved else taken, replace_saved)
        return self.entry(camera_id)

    def lock(self, camera_id: str) -> dict[str, Any]:
        """Lock: what the camera chose on auto, saved as manual, so it stops drifting."""
        camera = self.camera(camera_id)
        with self._camera_lock(camera_id):
            fresh = self._read(camera, self._reachable(camera))
        result = self.set(camera_id, lock_changes(fresh))
        return {**result, "locked": sorted(lock_changes(fresh))}

    def reset(self, camera_id: str) -> dict[str, Any]:
        """Every control to the device's defaults; nothing is saved any more (USB only)."""
        camera = self.camera(camera_id)
        if camera.usb_device is None:
            raise ImageRequestError(
                "an agent camera does not report its defaults; use All auto instead"
            )
        with self._camera_lock(camera_id):
            fresh = self._read(camera, self._reachable(camera))
        return self.set(camera_id, default_changes(fresh), replace_saved=True)

    def copy(self, from_id: str, to_id: str) -> dict[str, Any]:
        """Copy the controls both cameras have, clamped to the target's ranges."""
        if from_id == to_id:
            raise ImageRequestError("pick another camera to copy from")
        source_camera, target_camera = self.camera(from_id), self.camera(to_id)
        with self._camera_lock(from_id):
            theirs = self._read(source_camera, self._reachable(source_camera))
        with self._camera_lock(to_id):
            ours = self._read(target_camera, self._reachable(target_camera))
        changes, skipped = copy_changes(theirs, ours)
        result = self.set(to_id, changes)
        return {**result, "copied": ordered(list(changes)), "skipped": ordered(skipped)}

    # --- reapplying (the worker) -------------------------------------------------------------

    def reapply(self, camera: CameraConfig) -> None:
        """Send the saved settings again (device back, child restarted, agent back), then read."""
        with self._camera_lock(camera.id):
            source = self._reachable(camera)
            current = self._read(camera, source)
            saved = {name: s for name, s in camera.image.items() if name in current}
            problems = {n: p for n, s in saved.items() if (p := change_problem(current[n], s))}
            writable = {name: s for name, s in saved.items() if name not in problems}
            outcome = self._write(camera, source, writable, current)
            readback = outcome.readback or (self._read(camera, source) if writable else current)
            self._errors[camera.id] = {**problems, **outcome.errors}
            self._store(camera, source, readback)
        if writable:
            self.host.logger.info("camera %s: image settings reapplied", camera.id)

    def mark_unavailable(self, camera: CameraConfig, reason: str) -> None:
        """Show the camera's card offline with `reason`, keeping its last known controls."""
        with self._lock:
            changed = self._status.get(camera.id) != ("offline", reason)
            self._status[camera.id] = ("offline", reason)
        if changed or camera.id not in self._published:
            self._publish(camera)

    def forget(self, camera_id: str) -> None:
        """A camera no longer has image settings: drop its card."""
        with self._lock:
            for table in (self._controls, self._errors, self._status, self._published):
                table.pop(camera_id, None)
        entries = dict(self.host.read_state("image") or {})
        if entries.pop(camera_id, None) is not None:
            self.host.publish("image", entries)

    def published_ids(self) -> list[str]:
        """Cameras that have a card in the state."""
        with self._lock:
            return list(self._published)

    def entry(self, camera_id: str) -> dict[str, Any]:
        """The camera's card as last published (command results are this)."""
        with self._lock:
            return dict(self._published.get(camera_id) or {})

    # --- inside ----------------------------------------------------------------------------

    def _camera_lock(self, camera_id: str) -> threading.Lock:
        with self._lock:
            return self._camera_locks.setdefault(camera_id, threading.Lock())

    def _reachable(self, camera: CameraConfig) -> ImageSource:
        try:
            return self.source(camera)
        except ImageUnavailableError as error:
            self.mark_unavailable(camera, str(error))
            raise

    def _read(self, camera: CameraConfig, source: ImageSource) -> dict[str, ControlState]:
        try:
            return source.read()
        except ImageUnavailableError as error:
            self.mark_unavailable(camera, str(error))
            raise

    def _write(
        self,
        camera: CameraConfig,
        source: ImageSource,
        changes: dict[str, ImageSetting],
        current: dict[str, ControlState],
    ) -> WriteOutcome:
        if not changes:
            return WriteOutcome({})
        try:
            return source.write(changes, current)
        except ImageUnavailableError as error:
            self.mark_unavailable(camera, str(error))
            raise

    def _store(
        self, camera: CameraConfig, source: ImageSource, controls: dict[str, ControlState]
    ) -> None:
        """Keep what was just read (from `source`) and show it."""
        with self._lock:
            self._controls[camera.id] = controls
            self._status[camera.id] = ("ok", "")
        self._publish(camera)

    def _publish(self, camera: CameraConfig) -> None:
        located = self.locate_camera(camera.id) if camera.usb_device is not None else None
        with self._lock:
            controls = self._controls.get(camera.id, {})
            errors = self._errors.get(camera.id, {})
            status, reason = self._status.get(camera.id, ("offline", "not read yet"))
            entry = {
                "source": "usb" if camera.usb_device is not None else "agent",
                "status": status,
                "status_reason": reason,
                "device": _device_json(camera, located),
                "agent": camera.address.agent_id if camera.driver == DriverKind.AGENT else None,
                "controls": {
                    name: {**controls[name].as_json(), "error": errors.get(name, "")}
                    for name in ordered(list(controls))
                },
            }
            self._published[camera.id] = entry
        self.host.publish(f"image.{camera.id}", entry)

    def _save(self, camera_id: str, updates: dict[str, ImageSetting], replace: bool) -> None:
        def edit(entry: dict[str, Any]) -> dict[str, Any]:
            image = {} if replace else dict(entry.get("image") or {})
            image.update({name: setting.model_dump() for name, setting in updates.items()})
            return {**entry, "image": image}

        self.host.edit_camera(camera_id, edit)


def _device_json(camera: CameraConfig, located: Located | None) -> dict[str, Any] | None:
    if camera.usb_device is None:
        return None
    device = located.device if located is not None else None
    return {
        "name": camera.usb_device.name,
        "path": device.path if device else camera.usb_device.path,
        "present": device is not None,
        "matched_by": located.matched_by if located is not None else "",
    }
