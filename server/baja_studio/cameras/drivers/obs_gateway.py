"""How the cameras module reaches OBS: through the playout module's one connection (ADR 0020).

Playout owns the obs-websocket client and its settings (address, password,
reconnect). The cameras module never opens a second connection: it is handed
an `ObsGateway` (the way the agents module is handed to it as an
`AgentGateway`), and `main.py` gives it playout's. Requests may be made from
any thread but an event listener's (the client's own rule).

When OBS is away (not configured, not running, playout switched off) the
gateway says so in operator words, and every OBS crop camera and preview
shows that reason; nothing blocks or crashes.
"""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol

from baja_studio.adapters.obs_requests import ObsRequestsMixin

# The pseudo-event a listener gets when the connection drops (then it reconnects).
CONNECTION_LOST = "StudioObsConnectionLost"

ObsListener = Callable[[str, dict[str, Any]], None]


@dataclass(frozen=True)
class ObsLinkView:
    """Whether OBS can be asked something right now, and why not."""

    connected: bool
    reason: str = ""


class ObsGateway(Protocol):
    """OBS as the cameras module sees it. Safe to call from any thread."""

    def link(self) -> ObsLinkView:
        """Connected or not, with the reason in operator words when not."""

    def client(self) -> ObsRequestsMixin | None:
        """The client to send requests through, or None while there is none.

        A request can still fail with an `ObsError` (OBS went away meanwhile).
        """

    def add_listener(self, listener: ObsListener) -> Callable[[], None]:
        """Call `listener(event_type, data)` for every OBS event, and `CONNECTION_LOST`.

        It runs on the client's reader thread: queue and wake, never request.
        Returns a function that removes the listener.
        """


class NoObsGateway:
    """A service without OBS (tests, a cameras module on its own): always offline."""

    def __init__(self, reason: str = "no OBS connection in this service") -> None:
        """`reason` is what OBS crop cameras and previews show."""
        self._reason = reason

    def link(self) -> ObsLinkView:
        """Never connected."""
        return ObsLinkView(False, self._reason)

    def client(self) -> ObsRequestsMixin | None:
        """No client."""
        return None

    def add_listener(self, listener: ObsListener) -> Callable[[], None]:
        """Nothing will ever be heard; the remover does nothing."""
        return lambda: None
