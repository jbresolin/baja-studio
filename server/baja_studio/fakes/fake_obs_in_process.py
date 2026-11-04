"""Reach a fake OBS without a network: a `TransportFactory` for tests.

`InProcessObsNetwork` maps addresses to fake OBS instances. Its `connect`
method is handed to `ObsWebsocketClient` as the transport factory. Tests
"quit OBS" with `unplug` (connections close, new ones are refused) and
"start it again" with `plug`.
"""

import queue
import threading
from dataclasses import dataclass

from baja_studio.adapters.obs_transport import (
    ObsAddress,
    ObsConnectionClosedError,
    ObsTransport,
    ObsUnreachableError,
)
from baja_studio.fakes.fake_obs import FakeObs


@dataclass(frozen=True)
class _Closed:
    code: int
    reason: str


class InProcessObsTransport:
    """One client connection to a fake, carried over a queue."""

    def __init__(self, fake: FakeObs) -> None:
        """Connect: the fake greets us with Hello straight away."""
        self._fake = fake
        self._incoming: queue.Queue[str | _Closed] = queue.Queue()
        self._closed: _Closed | None = None
        self._lock = threading.Lock()
        self._session = fake.open_session(self._incoming.put, self._closed_by_obs)

    def send(self, text: str) -> None:
        """Hand a message to the fake, which answers on the same thread."""
        with self._lock:
            closed = self._closed
        if closed is not None:
            raise ObsConnectionClosedError(closed.code, closed.reason)
        self._fake.receive(self._session, text)

    def receive(self) -> str:
        """Next message from the fake; raises once the connection closed."""
        item = self._incoming.get()
        if isinstance(item, _Closed):
            self._incoming.put(item)  # every later receive raises too
            raise ObsConnectionClosedError(item.code, item.reason)
        return item

    def close(self) -> None:
        """Hang up (like closing the WebSocket)."""
        self._finish(_Closed(1000, "closed by client"))
        self._fake.close_session(self._session)

    def _closed_by_obs(self, code: int, reason: str) -> None:
        self._finish(_Closed(code, reason))

    def _finish(self, closed: _Closed) -> None:
        with self._lock:
            if self._closed is not None:
                return
            self._closed = closed
        self._incoming.put(closed)


class InProcessObsNetwork:
    """Which fake OBS answers at which address."""

    def __init__(self) -> None:
        """No OBS anywhere yet."""
        self._lock = threading.Lock()
        self._fakes: dict[ObsAddress, FakeObs] = {}

    def plug(self, host: str, port: int, fake: FakeObs) -> None:
        """Make `fake` answer at host:port (OBS started)."""
        with self._lock:
            self._fakes[ObsAddress(host, port)] = fake

    def unplug(self, host: str, port: int) -> None:
        """OBS quit: its connections close and new ones are refused."""
        with self._lock:
            fake = self._fakes.pop(ObsAddress(host, port), None)
        if fake is not None:
            fake.disconnect_all()

    def connect(self, address: ObsAddress, timeout_seconds: float) -> ObsTransport:
        """The transport factory: a connection to the fake at `address`, or unreachable."""
        with self._lock:
            fake = self._fakes.get(address)
        if fake is None:
            raise ObsUnreachableError(f"cannot reach OBS at {address.uri}: connection refused")
        return InProcessObsTransport(fake)
