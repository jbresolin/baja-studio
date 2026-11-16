"""One connection to a remote OBS (a trackside laptop's), shared by its crop cameras (ADR 0021).

At the race a 4K webcam sits on a laptop far from the streaming PC, and the
laptop's own OBS crops it (only the finished 1080p picture travels). The
studio drives that OBS's Crop/Pad filters exactly as it drives the local
one, through one obs-websocket connection per laptop: this class. It is an
`ObsGateway` (`cameras/drivers/obs_gateway.py`), so the crop driver, the
previews and Camera setup's commands use it as they use playout's.

Its worker (`remote-obs:<host>:<port>`, one of the cameras module's workers):

- connects with the same client playout uses (`adapters/obs_websocket.py`)
  and reconnects with the same `ReconnectBackoff` (0.5, 1, 2, then every
  4 s); a failure repeated identically is logged once (`FailureStreak`);
- asks OBS for its version every `KEEPALIVE_INTERVAL_SECONDS` while
  connected. A pulled network cable closes nothing (obs-websocket sends no
  pings, and TCP waits minutes), so without this the link would look up
  while every request timed out; with it, the link is dropped and dialled
  again, which also recovers at once when the cable is back;
- reconnects when the password it should use changes (Camera setup).

Requests time out after `REQUEST_TIMEOUT_SECONDS` (a remote link, maybe an
Airmax radio, is slower than the PC's own OBS). A slow or hung laptop only
holds up the threads that asked it: its cameras' workers and their previews,
never playout's connection, other laptops or the motion loop.

Why it is down is said in operator words naming the laptop's OBS, the same
for every camera on it: "OBS at 192.168.10.50:4455 unreachable: ..." makes
the cameras `offline`; "OBS at ... refused the password" `degraded` (that OBS
is there, the settings are wrong). The password is never logged or shown.
"""

import logging
import threading
from collections.abc import Callable
from typing import Any

from baja_studio.adapters.obs_protocol import EventSubscription
from baja_studio.adapters.obs_requests import ObsRequestsMixin
from baja_studio.adapters.obs_transport import (
    ObsAddress,
    ObsAuthenticationError,
    ObsError,
    ObsNotConnectedError,
    ObsRequestTimeoutError,
    TransportFactory,
)
from baja_studio.adapters.obs_websocket import ObsWebsocketClient, ReconnectBackoff
from baja_studio.agents.contract import Status
from baja_studio.cameras.drivers.obs_gateway import CONNECTION_LOST, ObsLinkView, ObsListener
from baja_studio.core.clock import Clock
from baja_studio.core.worker import FailureStreak, HealthState, WorkerContext, WorkerSpec

CONNECT_TIMEOUT_SECONDS = 3.0
REQUEST_TIMEOUT_SECONDS = 3.0
KEEPALIVE_INTERVAL_SECONDS = 2.0
# Only what crop cameras listen to: filter and input changes. Fewer events
# over a radio link, and none of playout's media or scene traffic.
REMOTE_EVENT_SUBSCRIPTIONS = EventSubscription.FILTERS | EventSubscription.INPUTS
WORKER_PREFIX = "remote-obs:"

Logger = logging.Logger | logging.LoggerAdapter[logging.Logger]


def where_of(host: str, port: int) -> str:
    """How a remote OBS is named to the operator: "192.168.10.50:4455" ([...] for IPv6)."""
    return f"[{host}]:{port}" if ":" in host else f"{host}:{port}"


def why_unreachable(error: ObsError, address: ObsAddress) -> str:
    """The useful part of a failed connect, for a reason that already names the OBS.

    The socket's own error when there is one ("timed out", "[Errno 111] ..."),
    else the client's words without the address it repeats.
    """
    if error.__cause__ is not None:
        return str(error.__cause__) or type(error.__cause__).__name__
    text = str(error)
    for prefix in (f"cannot reach OBS at {address.uri}: ", f"OBS at {address.uri} "):
        text = text.removeprefix(prefix)
    return text


class RemoteObsConnection:
    """An `ObsGateway` for one remote OBS, and the worker that keeps it connected."""

    def __init__(
        self,
        host: str,
        port: int,
        password: str,
        clock: Clock,
        transport_factory: TransportFactory,
        logger: Logger,
    ) -> None:
        """Nothing is opened until the worker (`worker_spec`) runs."""
        self.address = ObsAddress(host, port)
        self.where = where_of(host, port)
        self.worker_name = f"{WORKER_PREFIX}{self.where}"
        self._clock = clock
        self._logger = logger
        self._client = ObsWebsocketClient(
            clock,
            transport_factory,
            connect_timeout_s=CONNECT_TIMEOUT_SECONDS,
            request_timeout_s=REQUEST_TIMEOUT_SECONDS,
            event_subscriptions=REMOTE_EVENT_SUBSCRIPTIONS,
        )
        self._client.add_event_listener(self._forward)
        self._client.add_disconnect_listener(self._dropped_by_obs)
        self._lock = threading.Lock()
        self._password = password
        self._listeners: list[ObsListener] = []
        self._view = ObsLinkView(False, f"connecting to OBS at {self.where}", where=self.where)
        # Set by the client's reader thread when the socket closed under us.
        self._dropped = threading.Event()
        self._worker: WorkerContext | None = None

    # --- ObsGateway ------------------------------------------------------------------------

    def link(self) -> ObsLinkView:
        """Connected, or why not ("OBS at <where> unreachable: ...")."""
        with self._lock:
            view = self._view
        if view.connected and not self._client.is_connected:
            # The socket closed a moment ago; the worker has not caught up yet.
            return ObsLinkView(False, self._closed_reason(), where=self.where)
        return view

    def client(self) -> ObsRequestsMixin | None:
        """The client (requests fail with an `ObsError` while it is not connected)."""
        return self._client

    def add_listener(self, listener: ObsListener) -> Callable[[], None]:
        """Call `listener(event_type, data)` for this OBS's events and `CONNECTION_LOST`."""
        with self._lock:
            self._listeners.append(listener)

        def remove() -> None:
            with self._lock:
                if listener in self._listeners:
                    self._listeners.remove(listener)

        return remove

    # --- what the pool changes -----------------------------------------------------------

    @property
    def obs_client(self) -> ObsWebsocketClient:
        """The client itself (tests wait for its requests to be answered)."""
        return self._client

    def set_password(self, password: str) -> None:
        """Use this password from now on: reconnect with it if it is a new one."""
        with self._lock:
            changed, self._password = password != self._password, password
        if changed:
            self._wake()

    def worker_spec(self) -> WorkerSpec:
        """The worker that connects, keeps the link alive and reconnects."""
        return WorkerSpec(self.worker_name, self._run, starts_offline=True)

    # --- the worker ----------------------------------------------------------------------

    def _run(self, worker: WorkerContext) -> None:
        self._worker = worker
        backoff = ReconnectBackoff()
        failures = FailureStreak()
        try:
            while not worker.should_stop:
                password = self._current_password()
                if not self._connect(worker, password, failures):
                    worker.sleep(backoff.next_delay())
                    continue
                backoff.reset()
                self._serve(worker, password)
                self._client.close()
        finally:
            self._worker = None
            self._client.close()

    def _connect(self, worker: WorkerContext, password: str, failures: FailureStreak) -> bool:
        """One attempt; publishes the outcome as the link's view and the worker's health."""
        self._dropped.clear()
        try:
            self._client.connect(self.address, password)
        except ObsAuthenticationError as error:
            if password:
                reason = f"OBS at {self.where} refused the password (check it on Camera setup)"
            else:
                reason = f"OBS at {self.where} asks for a password: set it on Camera setup"
            self._down(worker, failures, error, reason, Status.DEGRADED)
            return False
        except ObsError as error:
            reason = f"OBS at {self.where} unreachable: {why_unreachable(error, self.address)}"
            self._down(worker, failures, error, reason, Status.OFFLINE)
            return False
        if worker.should_stop:
            return False
        with self._lock:
            self._view = ObsLinkView(True, "", Status.OK, self.where)
        recovered = failures.recover()
        after = f" after {recovered} failed attempts" if recovered else ""
        self._logger.info("connected to OBS at %s%s", self.where, after)
        worker.mark_online()
        worker.report_health(HealthState.OK)
        return True

    def _serve(self, worker: WorkerContext, password: str) -> None:
        """While connected: check OBS still answers, every few seconds, until something changes."""
        next_check = self._clock.monotonic() + KEEPALIVE_INTERVAL_SECONDS
        while not worker.should_stop:
            if self._dropped.is_set():
                # The reader already told the listeners (`_dropped_by_obs`).
                self._mark_down(worker, self._closed_reason())
                return
            if self._current_password() != password:
                self._logger.info("password for OBS at %s changed; reconnecting", self.where)
                self._mark_down(worker, f"reconnecting to OBS at {self.where}", tell=True)
                return
            if self._clock.monotonic() >= next_check - 1e-6:
                if not self._still_answers():
                    reason = f"OBS at {self.where} stopped answering; reconnecting"
                    self._logger.warning("%s", reason)
                    self._mark_down(worker, reason, tell=True)
                    return
                next_check = self._clock.monotonic() + KEEPALIVE_INTERVAL_SECONDS
            worker.sleep(max(0.0, next_check - self._clock.monotonic()))

    def _still_answers(self) -> bool:
        """One cheap request (`GetVersion`); False when it timed out."""
        try:
            self._client.get_version()
        except ObsRequestTimeoutError:
            return False
        except ObsNotConnectedError:
            return True  # closed meanwhile: `_dropped` says so on the next look
        except ObsError:
            return True  # OBS answered, even if with a refusal: it is there
        return True

    def _down(
        self,
        worker: WorkerContext,
        failures: FailureStreak,
        error: ObsError,
        reason: str,
        status: Status,
    ) -> None:
        """A failed attempt: the view, the health, and one log line per new kind of failure."""
        with self._lock:
            self._view = ObsLinkView(False, reason, status, self.where)
        if failures.record(error):
            self._logger.warning("%s", reason)
        else:
            self._logger.debug("%s (%d attempts in a row)", reason, failures.same_in_a_row)
        health = HealthState.DEGRADED if status == Status.DEGRADED else HealthState.OFFLINE
        worker.report_health(health, reason)

    def _mark_down(self, worker: WorkerContext, reason: str, tell: bool = False) -> None:
        """The connection ends; `tell`: we close it ourselves, so say so to the listeners
        (the client tells them only when OBS or the network closed it)."""
        with self._lock:
            self._view = ObsLinkView(False, reason, Status.OFFLINE, self.where)
        worker.report_health(HealthState.OFFLINE, reason)
        if tell:
            self._client.close()
            self._forward(CONNECTION_LOST, {"reason": reason})

    def _closed_reason(self) -> str:
        return f"OBS at {self.where} closed the connection; reconnecting"

    def _current_password(self) -> str:
        with self._lock:
            return self._password

    def _wake(self) -> None:
        worker = self._worker
        if worker is not None:
            worker.wake()

    # --- the client's reader thread ------------------------------------------------------

    def _forward(self, kind: str, data: dict[str, Any]) -> None:
        """Hand one event to every listener (a failing one is logged, the rest still get it)."""
        with self._lock:
            listeners = list(self._listeners)
        for listener in listeners:
            try:
                listener(kind, data)
            except Exception:
                self._logger.exception("a listener of OBS at %s failed on %s", self.where, kind)

    def _dropped_by_obs(self, reason: str) -> None:
        """The socket closed (OBS quit, the network dropped): tell listeners, wake the worker."""
        self._dropped.set()
        self._forward(CONNECTION_LOST, {"reason": reason})
        self._wake()
