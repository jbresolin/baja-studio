"""The UVC child without the child process, for tests of the imaging service and the module.

`InProcessUvcHost` is a `UvcChild` (the real request handler) over the real
`DuvcBackend`, with the fake duvc library underneath, called directly instead
of over a pipe. tests/childprocs/test_uvc_child.py checks the real child
process separately. `FakeUsb` is the studio PC's USB side as a test drives
it: plug and unplug cameras, change the light, crash the child.
"""

import threading
from collections.abc import Callable
from typing import Any

from baja_studio.childprocs.uvc_child import UnavailableBackend, UvcChild
from baja_studio.childprocs.uvc_duvc import DuvcBackend
from baja_studio.core.childproc import ChildRequestError, EventCallback
from baja_studio.core.worker import DeviceOfflineError
from baja_studio.fakes.fake_duvc import (
    PIXY_NAME,
    PIXY_PATH,
    S600_A_PATH,
    S600_NAME,
    FakeDuvcLibrary,
    studio_devices,
)


class _EventsToHost:
    """Stands in for the child's stdout: events go straight to the host's callback."""

    def __init__(self, on_event: EventCallback) -> None:
        self._on_event = on_event

    def send_event(self, event: str, data: Any = None) -> None:
        self._on_event(event, data)

    def write(self, message: dict[str, Any]) -> None:
        pass  # replies are returned directly by `request`


class InProcessUvcHost:
    """A UVC child called in-process: same answers, same errors, no process."""

    def __init__(
        self, library: FakeDuvcLibrary, on_event: EventCallback, unavailable_reason: str = ""
    ) -> None:
        backend = UnavailableBackend() if unavailable_reason else DuvcBackend(library)
        events = _EventsToHost(on_event)
        self.child = UvcChild(backend, events, unavailable_reason)  # type: ignore[arg-type]
        self._running = False
        self._exit_listeners: list[Callable[[], None]] = []
        self.requests: list[str] = []

    @property
    def is_running(self) -> bool:
        return self._running

    def add_exit_listener(self, listener: Callable[[], None]) -> None:
        self._exit_listeners.append(listener)

    def start(self) -> None:
        self._running = True

    def request(self, method: str, params: dict[str, Any] | None = None) -> Any:
        if not self._running:
            raise DeviceOfflineError("child not running")
        self.requests.append(method)
        try:
            return self.child.handle(method, params or {})
        except Exception as error:
            # The real child answers an exception as an error reply.
            raise ChildRequestError(f"{type(error).__name__}: {error}") from error

    def ping(self) -> None:
        if not self._running:
            raise DeviceOfflineError("child not running")

    def stop(self) -> None:
        self._running = False

    def crash(self) -> None:
        """Like the child process dying: listeners hear about it."""
        self._running = False
        for listener in self._exit_listeners:
            listener()


class FakeUsb:
    """The studio PC's USB cameras (two S600s and a Pixy), and every UVC child started."""

    def __init__(self) -> None:
        self.library = FakeDuvcLibrary(studio_devices())
        self.hosts: list[InProcessUvcHost] = []
        # Set before a child starts to make it the one used off Windows (no UVC at all).
        self.unavailable_reason = ""
        self._lock = threading.Lock()

    def new_host(self, context: Any, on_event: EventCallback) -> InProcessUvcHost:
        """The module's UVC child factory."""
        host = InProcessUvcHost(self.library, on_event, self.unavailable_reason)
        with self._lock:
            self.hosts.append(host)
        return host

    @property
    def host(self) -> InProcessUvcHost:
        """The newest child."""
        with self._lock:
            return self.hosts[-1]

    def unplug(self, path: str) -> None:
        """Pull a camera's cable; the child notices at its next 2 s look (done now)."""
        self.library.device(path).plugged = False
        self._look()

    def replug(self, path: str) -> None:
        """Plug it back in."""
        self.library.device(path).plugged = True
        self._look()

    def _look(self) -> None:
        with self._lock:
            hosts = list(self.hosts)
        if hosts and hosts[-1].is_running:
            hosts[-1].child.check_devices()

    def control(self, path: str, name: str) -> Any:
        """A fake device's control, by the library's property name (e.g. "Exposure")."""
        device = self.library.device(path)
        return next(control for prop, control in device.controls.items() if prop.name == name)


def s600_camera(
    camera_id: str = "s600-a", path: str = S600_A_PATH, index: int = 0, **overrides: Any
) -> dict[str, Any]:
    """A fixed S600 (driver `none`) entry, as Camera setup saves it after picking the device."""
    return {
        "id": camera_id,
        "driver": "none",
        "usb_device": {"path": path, "name": S600_NAME, "index": index},
        **overrides,
    }


def pixy_camera(**overrides: Any) -> dict[str, Any]:
    """The Pixy as a PTZ (driver `uvc`), selected by joystick address 2."""
    return {
        "id": "pixy",
        "driver": "uvc",
        "usb_device": {"path": PIXY_PATH, "name": PIXY_NAME, "index": 0},
        "controller_address": 2,
        **overrides,
    }


def image_card(studio: Any, camera_id: str) -> dict[str, Any]:
    """The camera's image card as the page sees it (`state.cameras.image.<id>`), or {}."""
    card: dict[str, Any] = studio.services.state.get("cameras", f"image.{camera_id}") or {}
    return card


def saved_image(studio: Any, camera_id: str) -> dict[str, Any]:
    """The camera's saved image settings, as plain JSON."""
    for camera in studio.services.settings.get("cameras").cameras:
        if camera.id == camera_id:
            return {name: setting.model_dump() for name, setting in camera.image.items()}
    raise KeyError(camera_id)
