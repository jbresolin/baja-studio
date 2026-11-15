"""Child process that owns the USB cameras' UVC controls (duvc-ctl), run by the cameras module.

    python -m baja_studio.childprocs.uvc_child

duvc-ctl is native DirectShow code that could crash the interpreter, so it
runs apart from the service (goals §7.1), like the MIDI child. Requests (see
`core/childproc.py`):

- `status`: `{available, message}`; not available off Windows or without duvc-ctl.
- `list_devices`: `[{name, path, index}]`, `index` counting devices with the same name.
- `read_controls {path, names?}`: `{controls: {name: {value, auto, min, max,
  step, default, default_auto}}, errors: {name: message}}` for the controls
  the device has among `names` (all when left out).
- `write_controls {path, controls: {name: {value, auto}}}`: `{name: null |
  "error"}`; each control is written and judged on its own, in order.
- `check`: list the devices and touch every open handle with one read (the
  service sends it every 2 s; it is also the child's keep-alive).

Events: `devices {devices: [...]}` when a look finds the list changed;
`reopened {path}` when a device's handle had died (a quick unplug and
replug) and was opened again, so the service reapplies that camera.

Everything runs on the one thread that answers requests: DirectShow handles
are COM objects and must not be used or released from another thread, so
there is no watcher thread; the service's `check` drives the 2 s look.

The backend is duvc-ctl, or the fake library (`fakes/fake_duvc.py`) when
`BAJA_STUDIO_UVC_BACKEND=fake`; the fake also answers `fake_*` requests.
"""

import os
import threading
from typing import Any, Protocol

from baja_studio.core.childproc_child import ChildChannel, run_child

BACKEND_ENVIRONMENT_VARIABLE = "BAJA_STUDIO_UVC_BACKEND"


class UvcBackend(Protocol):
    """What the child needs from the library (`uvc_duvc.DuvcBackend`)."""

    def list_devices(self) -> list[Any]:
        """The cameras plugged in now (`ListedDevice`s)."""

    def supported(self, path: str) -> list[str]:
        """The studio names of the controls this device has."""

    def read(self, path: str, name: str) -> dict[str, Any]:
        """One control's value, auto, range and defaults."""

    def write(self, path: str, name: str, value: int, auto: bool) -> None:
        """Set one control; raises if refused."""

    def probe(self) -> None:
        """Touch every open handle; reopen the dead ones."""

    def take_reopened(self) -> list[str]:
        """Paths reopened since the last call."""


class UvcChild:
    """The request handler and device watcher, independent of the real stdin/stdout."""

    def __init__(self, backend: UvcBackend, channel: ChildChannel, unavailable: str = "") -> None:
        """`unavailable` is why UVC cannot be used here (then the backend lists nothing)."""
        self._backend = backend
        self._channel = channel
        self._unavailable = unavailable
        self._lock = threading.Lock()
        self._last_listed: list[dict[str, Any]] | None = None

    def handle(self, method: str, params: dict[str, Any]) -> Any:
        """Called by `run_child` for each request; then tells about reopened devices."""
        try:
            return self._handle(method, params)
        finally:
            for path in self._backend.take_reopened():
                self._channel.send_event("reopened", {"path": path})

    def _handle(self, method: str, params: dict[str, Any]) -> Any:
        if method == "status":
            return {"available": not self._unavailable, "message": self._unavailable}
        if method == "list_devices":
            return self.check_devices()
        if method == "check":
            listed = self.check_devices()
            with self._lock:
                self._backend.probe()
            return listed
        if method == "read_controls":
            return self._read(str(params["path"]), params.get("names"))
        if method == "write_controls":
            return self._write(str(params["path"]), params.get("controls") or {})
        if method.startswith("fake_"):
            fake_handler = getattr(self._backend, "handle_fake", None)
            if fake_handler is None:
                raise ValueError("fake_* requests need the fake UVC backend")
            with self._lock:
                return fake_handler(method, params)
        raise ValueError(f"unknown method {method!r}")

    def check_devices(self) -> list[dict[str, Any]]:
        """List the devices; tell the service with a `devices` event if the list changed."""
        with self._lock:
            listed = [device.as_json() for device in self._backend.list_devices()]
            changed = self._last_listed is not None and listed != self._last_listed
            self._last_listed = listed
        if changed:
            self._channel.send_event("devices", {"devices": listed})
        return listed

    def _read(self, path: str, names: list[str] | None) -> dict[str, Any]:
        with self._lock:
            supported = self._backend.supported(path)
            wanted = [name for name in supported if names is None or name in names]
            controls: dict[str, Any] = {}
            errors: dict[str, str] = {}
            for name in wanted:
                try:
                    controls[name] = self._backend.read(path, name)
                except Exception as error:
                    errors[name] = str(error)
            return {"controls": controls, "errors": errors}

    def _write(self, path: str, controls: dict[str, Any]) -> dict[str, str | None]:
        results: dict[str, str | None] = {}
        with self._lock:
            for name, wanted in controls.items():
                try:
                    value = int(wanted.get("value") or 0)
                    self._backend.write(path, name, value, bool(wanted.get("auto")))
                    results[name] = None
                except Exception as error:  # one refused control never stops the next
                    results[name] = str(error) or type(error).__name__
        return results


class UnavailableBackend:
    """Stands in when UVC cannot be used (not Windows, duvc-ctl missing): lists no devices."""

    def list_devices(self) -> list[Any]:
        """No devices."""
        return []

    def supported(self, path: str) -> list[str]:
        """Nothing is supported."""
        raise OSError("USB camera control is not available on this computer")

    def read(self, path: str, name: str) -> dict[str, Any]:
        """Never succeeds."""
        raise OSError("USB camera control is not available on this computer")

    def write(self, path: str, name: str, value: int, auto: bool) -> None:
        """Never succeeds."""
        raise OSError("USB camera control is not available on this computer")

    def probe(self) -> None:
        """No handles."""

    def take_reopened(self) -> list[str]:
        """Nothing is ever reopened."""
        return []


def make_backend() -> tuple[UvcBackend, str]:
    """duvc-ctl, or the fake library (`BAJA_STUDIO_UVC_BACKEND=fake`); and why not, if neither."""
    from baja_studio.childprocs.uvc_duvc import DuvcBackend

    if os.environ.get(BACKEND_ENVIRONMENT_VARIABLE) == "fake":
        from baja_studio.fakes.fake_duvc import FakeDuvcLibrary, studio_devices

        return DuvcBackend(FakeDuvcLibrary(studio_devices())), ""
    try:
        import importlib

        # Imported by name and kept as Any: duvc-ctl is Windows-only and ships no stubs.
        return DuvcBackend(importlib.import_module("duvc_ctl")), ""
    except Exception as error:  # ImportError off Windows; a broken install raises others
        return UnavailableBackend(), f"USB camera control is not available here: {error}"


def main() -> None:
    """Entry point when run as `python -m baja_studio.childprocs.uvc_child`."""
    channel = ChildChannel()
    backend, unavailable = make_backend()
    child = UvcChild(backend, channel, unavailable)
    run_child(child.handle, channel=channel)


if __name__ == "__main__":
    main()
