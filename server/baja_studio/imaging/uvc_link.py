"""The `uvc` worker: runs the UVC child process and keeps the list of USB cameras.

The child (`childprocs/uvc_child.py`) owns duvc-ctl, so a crash in native
DirectShow code takes only it down. This worker:

- starts the child, asks whether UVC is available here and lists the devices,
  then tells the imaging service (`on_started`) so it reapplies every USB
  camera's saved settings;
- takes the child's `devices` events (it lists the devices every 2 s) and
  tells the imaging service which devices appeared or left (`on_devices`),
  so a replugged camera gets its settings back;
- pings the child every 2 s; a dead or hung child raises `DeviceOfflineError`,
  and the supervisor starts a new one (which reapplies again).

Requests from other threads (the imaging service, the Pixy driver) go
through `request`, which the child answers one at a time, in order.
"""

import collections
import logging
import threading
from collections.abc import Callable
from typing import Any, Protocol

from baja_studio.core.childproc import EventCallback
from baja_studio.core.worker import DeviceOfflineError, HealthState, WorkerContext, WorkerSpec
from baja_studio.imaging.usb_devices import UsbDevice

UVC_WORKER = "uvc"
PING_INTERVAL_SECONDS = 2.0


class ChildHost(Protocol):
    """What the worker uses of `ChildProcessHost` (tests pass an in-process one)."""

    @property
    def is_running(self) -> bool:
        """True while the child runs."""

    def add_exit_listener(self, listener: Callable[[], None]) -> None:
        """Call `listener` when the child exits."""

    def start(self) -> None:
        """Start the child."""

    def request(self, method: str, params: dict[str, Any] | None = None) -> Any:
        """Send a request and wait for the answer."""

    def ping(self) -> None:
        """Raise `DeviceOfflineError` unless the child answers."""

    def stop(self) -> None:
        """Stop the child."""


ChildFactory = Callable[[EventCallback], ChildHost]
DevicesCallback = Callable[[list[UsbDevice], set[str], set[str]], None]


class UvcChildLink:
    """One UVC child at a time, for the cameras module."""

    def __init__(
        self,
        new_child: ChildFactory,
        logger: logging.Logger | logging.LoggerAdapter[logging.Logger],
        on_started: Callable[[], None],
        on_devices: DevicesCallback,
    ) -> None:
        """`new_child(on_event)` creates (not starts) a host.

        `on_started()` is called once a new child has listed its devices;
        `on_devices(devices, appeared_paths, gone_paths)` whenever the list
        changes (paths lower-case). Both run on this worker's thread.
        """
        self._new_child = new_child
        self._logger = logger
        self._on_started = on_started
        self._on_devices = on_devices
        self._lock = threading.Lock()
        self._inbox: collections.deque[tuple[str, Any]] = collections.deque()
        self._host: ChildHost | None = None
        self._worker: WorkerContext | None = None
        self._devices: list[UsbDevice] = []
        self._status = ("starting", "")

    def worker_spec(self) -> WorkerSpec:
        """The `uvc` worker; offline until the child answers."""
        return WorkerSpec(UVC_WORKER, self.run, starts_offline=True)

    def devices(self) -> list[UsbDevice]:
        """The USB cameras plugged in, as last listed (none while the child is down)."""
        with self._lock:
            return list(self._devices)

    def status(self) -> tuple[str, str]:
        """(`starting` | `ok` | `unavailable` | `offline`, message)."""
        with self._lock:
            return self._status

    def request(self, method: str, params: dict[str, Any] | None = None) -> Any:
        """Ask the running child something; raises `DeviceOfflineError` when there is none."""
        host = self._host
        if host is None or not host.is_running:
            raise DeviceOfflineError("USB camera control is not running (the UVC child is down)")
        return host.request(method, params)

    def refresh_devices(self) -> None:
        """Have the child list the devices now (the device picker's Refresh)."""
        self._take_devices(self.request("check"), announce=True)

    # --- the worker -------------------------------------------------------------------

    def run(self, worker: WorkerContext) -> None:
        """Worker body: one child until it fails or we stop."""
        self._worker = worker
        with self._lock:
            self._inbox.clear()
        host = self._new_child(self._on_child_event)
        host.add_exit_listener(worker.wake)
        host.start()
        self._host = host
        try:
            self._serve(worker, host)
        finally:
            self._host = None
            gone = {device.path.lower() for device in self.devices()}
            with self._lock:
                self._devices = []
                self._status = ("offline", "the UVC child stopped")
            host.stop()
            if gone and not worker.should_stop:
                self._on_devices([], set(), gone)

    def _serve(self, worker: WorkerContext, host: ChildHost) -> None:
        status = host.request("status")
        available, message = bool(status.get("available")), str(status.get("message") or "")
        self._take_devices(host.request("list_devices"), announce=False)
        worker.mark_online()
        with self._lock:
            self._status = ("ok", "") if available else ("unavailable", message)
        self._report(worker)
        self._on_started()
        clock = worker.clock
        next_ping = clock.monotonic() + PING_INTERVAL_SECONDS
        while not worker.should_stop:
            if not host.is_running:
                raise DeviceOfflineError("the UVC child process exited")
            self._handle_inbox()
            self._report(worker)
            if clock.monotonic() >= next_ping:
                next_ping = clock.monotonic() + PING_INTERVAL_SECONDS
                host.ping()
            worker.sleep(max(0.0, next_ping - clock.monotonic()))

    def _report(self, worker: WorkerContext) -> None:
        state, message = self.status()
        if state == "unavailable":
            worker.report_health(HealthState.OFFLINE, message)
            return
        count = len(self.devices())
        worker.report_health(HealthState.OK, f"{count} USB camera{'s' if count != 1 else ''}")

    def _handle_inbox(self) -> None:
        with self._lock:
            items = list(self._inbox)
            self._inbox.clear()
        for kind, data in items:
            if kind == "devices":
                self._take_devices(data.get("devices", []), announce=True)

    def _take_devices(self, listed: list[dict[str, Any]], announce: bool) -> None:
        devices = [UsbDevice.from_json(item) for item in listed]
        with self._lock:
            before = {device.path.lower() for device in self._devices}
            self._devices = devices
        after = {device.path.lower() for device in devices}
        appeared, gone = after - before, before - after
        for path in sorted(appeared):
            self._logger.info("USB camera plugged in: %s", path)
        for path in sorted(gone):
            self._logger.info("USB camera unplugged: %s", path)
        if announce and (appeared or gone):
            self._on_devices(devices, appeared, gone)

    def _on_child_event(self, event: str, data: Any) -> None:
        """Child reader thread: queue the event and wake the worker."""
        with self._lock:
            self._inbox.append((event, data))
        worker = self._worker
        if worker is not None:
            worker.wake()
