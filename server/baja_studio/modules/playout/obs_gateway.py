"""Playout's OBS connection, lent to the cameras module (`cameras/drivers/obs_gateway.py`).

The playout module creates a new `ObsWebsocketClient` each time it starts
and `attach`es it here; the gateway forwards that client's events (and a
`CONNECTION_LOST` pseudo-event when it drops) to every listener the cameras
module added, so listeners survive a playout restart. Events of a client
that has since been replaced are dropped.

Why OBS is away is read from playout's own state (`state.playout.obs`), so
cameras show the same words as the playout page.
"""

import logging
import threading
from collections.abc import Callable
from typing import Any

from baja_studio.adapters.obs_requests import ObsRequestsMixin
from baja_studio.adapters.obs_websocket import ObsWebsocketClient
from baja_studio.cameras.drivers.obs_gateway import CONNECTION_LOST, ObsLinkView, ObsListener

logger = logging.getLogger(__name__)

PLAYOUT_OFF = "OBS is reached through the playout module, which is not running"
CONNECTING = "connecting to OBS"


class PlayoutObsGateway:
    """The `ObsGateway` the playout module provides."""

    def __init__(self, obs_state: Callable[[], dict[str, Any]]) -> None:
        """`obs_state()` reads playout's published `obs` state (connection, message)."""
        self._obs_state = obs_state
        self._lock = threading.Lock()
        self._client: ObsWebsocketClient | None = None
        self._listeners: list[ObsListener] = []

    def attach(self, client: ObsWebsocketClient) -> None:
        """Playout started with this client: forward its events from now on."""
        client.add_event_listener(lambda kind, data: self._forward(client, kind, data))
        client.add_disconnect_listener(
            lambda reason: self._forward(client, CONNECTION_LOST, {"reason": reason})
        )
        with self._lock:
            self._client = client

    def detach(self) -> None:
        """Playout stopped: there is no client until the next `attach`."""
        with self._lock:
            self._client = None

    def link(self) -> ObsLinkView:
        """Connected, or why not (playout off, not configured, unreachable, ...)."""
        with self._lock:
            client = self._client
        if client is None:
            return ObsLinkView(False, PLAYOUT_OFF)
        if client.is_connected:
            return ObsLinkView(True)
        state = self._obs_state()
        message = str(state.get("message") or "")
        if state.get("connection") == "connecting" or not message:
            return ObsLinkView(False, CONNECTING)
        return ObsLinkView(False, message)

    def client(self) -> ObsRequestsMixin | None:
        """Playout's client while playout runs."""
        with self._lock:
            return self._client

    def add_listener(self, listener: ObsListener) -> Callable[[], None]:
        """Forward every event of the current (and any later) client to `listener`."""
        with self._lock:
            self._listeners.append(listener)

        def remove() -> None:
            with self._lock:
                if listener in self._listeners:
                    self._listeners.remove(listener)

        return remove

    def _forward(self, client: ObsWebsocketClient, kind: str, data: dict[str, Any]) -> None:
        """Reader thread: hand one event to every listener (a failing one is logged)."""
        with self._lock:
            if client is not self._client:
                return
            listeners = list(self._listeners)
        for listener in listeners:
            try:
                listener(kind, data)
            except Exception:
                logger.exception("an OBS listener of the cameras module failed on %s", kind)
