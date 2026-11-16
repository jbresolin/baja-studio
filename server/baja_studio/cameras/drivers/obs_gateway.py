"""How the cameras module reaches an OBS: playout's connection, or a remote one (ADRs 0020, 0021).

Playout owns the obs-websocket client of the OBS on this PC and its settings
(address, password, reconnect). The cameras module never opens a second
connection to it: it is handed an `ObsGateway` (the way the agents module is
handed to it as an `AgentGateway`), and `main.py` gives it playout's. A crop
camera whose 4K webcam sits on a trackside laptop uses that laptop's OBS
instead, through a connection the cameras module opens itself and shares
between the cameras on that laptop (`cameras/remote_obs.py`, ADR 0021); it is
an `ObsGateway` too, so drivers, previews and commands treat both alike.
Requests may be made from any thread but an event listener's (the client's
own rule).

When OBS is away (not configured, not running, playout switched off, a
laptop unreachable) the gateway says so in operator words, and every OBS
crop camera and preview shows that reason; nothing blocks or crashes.
"""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol

from baja_studio.adapters.obs_requests import ObsRequestsMixin
from baja_studio.agents.contract import Status

# The pseudo-event a listener gets when the connection drops (then it reconnects).
CONNECTION_LOST = "StudioObsConnectionLost"

ObsListener = Callable[[str, dict[str, Any]], None]


@dataclass(frozen=True)
class ObsLinkView:
    """Whether OBS can be asked something right now, and why not.

    `where` is empty for playout's OBS (on this PC) and "host:port" for a
    remote one; a remote OBS's `reason` already names it ("OBS at
    192.168.10.50:4455 unreachable: ..."), so both laptops' cameras show the
    same words. `status` is what a camera on it is while not connected:
    `offline`, or `degraded` when that OBS answered but refused the password.
    """

    connected: bool
    reason: str = ""
    status: Status = Status.OFFLINE
    where: str = ""

    @property
    def problem(self) -> str:
        """Why this OBS cannot be asked, naming it: a camera's status reason."""
        return self.reason if self.where else f"OBS offline: {self.reason}"

    def lost(self, detail: object) -> str:
        """A request found the connection gone or timed out (`detail`: the error)."""
        return f"OBS at {self.where} offline: {detail}" if self.where else f"OBS offline: {detail}"

    def refused(self, detail: object) -> str:
        """OBS answered a request with a failure (`detail`: the error)."""
        return f"OBS at {self.where}: {detail}" if self.where else f"OBS: {detail}"


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
