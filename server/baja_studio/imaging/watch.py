"""The `imaging` worker: puts saved image settings back by itself, and publishes the USB list.

Twice a second it looks at every camera that has image settings:

- **USB cameras**: when the camera's device is present and has not had its
  settings since it appeared (or since the UVC child last started, or since
  its device changed, or since its handle was reopened after a quick
  replug), the saved settings are reapplied and the controls read.
  When it is absent, its card says why and keeps its last controls.
- **Agent cameras**: when the agent link turns `ok` and the agent lists image
  controls for the camera, the saved settings are reapplied and read. While
  the link is down, or the camera is driven directly in fallback, the card
  is offline.

It also publishes `state.cameras.usb`: the child's status and the devices
plugged in, each with the camera that uses it.
"""

import threading
from typing import Any

from baja_studio.cameras.config import CameraConfig, DriverKind
from baja_studio.core.worker import WorkerContext, WorkerSpec
from baja_studio.imaging.service import ImagingService
from baja_studio.imaging.sources import ImageUnavailableError
from baja_studio.imaging.usb_devices import UsbDevice

IMAGING_WORKER = "imaging"
TICK_SECONDS = 0.5


class ImagingWatch:
    """The worker body and what it remembers between ticks."""

    def __init__(self, service: ImagingService) -> None:
        """Nothing has been applied yet."""
        self._service = service
        self._lock = threading.Lock()
        self._generation = 0
        self._usb_applied: dict[str, tuple[int, str]] = {}
        self._agent_applied: set[str] = set()
        self._reopened: set[str] = set()
        self._worker: WorkerContext | None = None
        self._last_usb_state: dict[str, Any] | None = None

    def worker_spec(self) -> WorkerSpec:
        """The `imaging` worker."""
        return WorkerSpec(IMAGING_WORKER, self.run)

    def uvc_started(self) -> None:
        """A new UVC child is up: every USB camera present gets its settings again."""
        with self._lock:
            self._generation += 1
        self.wake()

    def usb_devices_changed(
        self, devices: list[UsbDevice], appeared: set[str], gone: set[str]
    ) -> None:
        """The device list changed (a camera plugged in or out): look now."""
        self.wake()

    def device_reopened(self, path: str) -> None:
        """The child had to open this device again (it was replugged): reapply its camera."""
        with self._lock:
            self._reopened.add(path.lower())
        self.wake()

    def wake(self) -> None:
        """Look now rather than at the next tick."""
        worker = self._worker
        if worker is not None:
            worker.wake()

    def run(self, worker: WorkerContext) -> None:
        """Worker body."""
        self._worker = worker
        try:
            while not worker.should_stop:
                self.tick()
                worker.sleep(TICK_SECONDS)
        finally:
            # Unsaved writes are saved by the module's `stop`, once every worker
            # has stopped: saving here would notify the module while the
            # supervisor is still stopping it.
            self._worker = None

    def tick(self) -> None:
        """One look at every camera with image settings."""
        cameras = [camera for camera in self._service.host.settings().cameras if camera.enabled]
        with_cards: set[str] = set()
        for camera in cameras:
            if camera.usb_device is not None:
                with_cards.add(camera.id)
                self._watch_usb(camera)
            elif camera.driver == DriverKind.AGENT and self._watch_agent(camera):
                with_cards.add(camera.id)
        for camera_id in self._service.published_ids():
            if camera_id not in with_cards:
                self._service.forget(camera_id)
                self._usb_applied.pop(camera_id, None)
                self._agent_applied.discard(camera_id)
        self._publish_usb(cameras)
        self._service.flush_due()

    def _watch_usb(self, camera: CameraConfig) -> None:
        located = self._service.locate_camera(camera.id)
        if located.device is None:
            self._usb_applied.pop(camera.id, None)
            self._service.mark_unavailable(camera, located.problem)
            return
        path = located.device.path.lower()
        with self._lock:
            key = (self._generation, path)
            if path in self._reopened:
                self._reopened.discard(path)
                self._usb_applied.pop(camera.id, None)
        if self._usb_applied.get(camera.id) == key:
            return
        try:
            self._service.reapply(camera)
        except ImageUnavailableError:
            return  # the card already says why; tried again next tick
        self._usb_applied[camera.id] = key

    def _watch_agent(self, camera: CameraConfig) -> bool:
        """Returns whether the camera has (or keeps) a card."""
        view = self._service.agents.link(camera.address.agent_id)
        listed = _image_controls_listed(view.info if view else None, camera)
        has_card = listed or camera.id in self._service.published_ids()
        if view is None or view.status != "ok":
            self._agent_applied.discard(camera.id)
            if has_card:
                status = view.status if view else "not configured"
                self._service.mark_unavailable(
                    camera, f"agent {camera.address.agent_id} is {status}"
                )
            return has_card
        if not listed:
            return False
        if camera.id in self._agent_applied:
            return True
        try:
            self._service.reapply(camera)
        except ImageUnavailableError:
            return True
        self._agent_applied.add(camera.id)
        return True

    def usb_state(self) -> dict[str, Any]:
        """The child's status and the USB cameras plugged in, each with its camera."""
        cameras = [camera for camera in self._service.host.settings().cameras if camera.enabled]
        return self._usb_state(cameras)

    def _publish_usb(self, cameras: list[CameraConfig]) -> None:
        state = self._usb_state(cameras)
        if state != self._last_usb_state:
            self._last_usb_state = state
            self._service.host.publish("usb", state)

    def _usb_state(self, cameras: list[CameraConfig]) -> dict[str, Any]:
        status, message = self._service.link.status()
        used_by: dict[str, str] = {}
        for camera in cameras:
            if camera.usb_device is not None:
                device = self._service.locate_camera(camera.id).device
                if device is not None:
                    used_by[device.path.lower()] = camera.id
        return {
            "status": status,
            "message": message,
            "devices": [
                {**device.as_json(), "used_by": used_by.get(device.path.lower())}
                for device in self._service.link.devices()
            ],
        }


def _image_controls_listed(info: dict[str, Any] | None, camera: CameraConfig) -> bool:
    """Whether the agent's `/agent/info` lists image controls for this camera."""
    for entry in (info or {}).get("cameras", []):
        if entry.get("id") == camera.address.remote_camera_id:
            return bool(entry.get("image_controls"))
    return False
