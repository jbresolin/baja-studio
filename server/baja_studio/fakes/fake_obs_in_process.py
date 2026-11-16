"""Reach a fake OBS without a network: a `TransportFactory` for tests.

`InProcessObsNetwork` maps addresses to fake OBS instances. Its `connect`
method is handed to `ObsWebsocketClient` as the transport factory. Tests
"quit OBS" with `unplug` (connections close, new ones are refused) and
"start it again" with `plug`. A simulation that moves time itself waits
for `wait_until_idle` before each step, so the client has handled every
message the fake sent (`tests/playout/obs_simulation.py`).

`hold_answers` / `release_answers` model a link that stops carrying what
OBS sends while requests still go out (an Airmax radio fading, a laptop
frozen): nothing comes back, so requests time out on the client's clock, and
when the link recovers everything held arrives in order (WP13, ADR 0021). It
is the network that is slow, not OBS, so this lives here and not in the fake.
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

    def __init__(self, fake: FakeObs, holding: bool = False) -> None:
        """Connect: the fake greets us with Hello straight away (held too, if `holding`)."""
        self._fake = fake
        self._incoming: queue.Queue[str | _Closed] = queue.Queue()
        self._closed: _Closed | None = None
        self._lock = threading.Lock()
        # While holding, what the fake sends waits here instead of reaching the client.
        self._holding = holding
        self._held: list[str] = []
        # Messages handed to the client, and calls to `receive` (each one
        # after the first means the message before was fully handled).
        self._progress = threading.Condition()
        self._delivered = 0
        self._receive_calls = 0
        self._session = fake.open_session(self._deliver, self._closed_by_obs)

    def send(self, text: str) -> None:
        """Hand a message to the fake, which answers on the same thread."""
        with self._lock:
            closed = self._closed
        if closed is not None:
            raise ObsConnectionClosedError(closed.code, closed.reason)
        self._fake.receive(self._session, text)

    def receive(self) -> str:
        """Next message from the fake; raises once the connection closed."""
        with self._progress:
            self._receive_calls += 1
            self._progress.notify_all()
        item = self._incoming.get()
        if isinstance(item, _Closed):
            self._incoming.put(item)  # every later receive raises too
            raise ObsConnectionClosedError(item.code, item.reason)
        return item

    def wait_until_idle(self, timeout_s: float = 10.0) -> bool:
        """Wait (real time) until the reader handled every message delivered so far.

        True once it is back in `receive` with nothing left, or the
        connection closed; False on timeout.
        """
        with self._progress:
            return self._progress.wait_for(
                lambda: self._receive_calls > self._delivered or self._closed is not None,
                timeout_s,
            )

    def hold(self) -> None:
        """From now on keep what the fake sends (the link stopped carrying it back)."""
        with self._lock:
            self._holding = True

    def release(self) -> None:
        """Hand over everything held, in order, and stop holding."""
        with self._lock:
            self._holding = False
            held, self._held = self._held, []
        for text in held:
            self._hand_over(text)

    def _deliver(self, text: str) -> None:
        with self._lock:
            if self._holding:
                self._held.append(text)
                return
        self._hand_over(text)

    def _hand_over(self, text: str) -> None:
        with self._progress:
            self._delivered += 1
        self._incoming.put(text)

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
        self._connections: dict[ObsAddress, list[InProcessObsTransport]] = {}
        self._holding: set[ObsAddress] = set()

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

    def hold_answers(self, host: str, port: int) -> None:
        """The link to host:port stops carrying OBS's answers and events (new connections too)."""
        address = ObsAddress(host, port)
        with self._lock:
            self._holding.add(address)
            connections = list(self._connections.get(address, []))
        for connection in connections:
            connection.hold()

    def release_answers(self, host: str, port: int) -> None:
        """The link recovers: everything held arrives, in order."""
        address = ObsAddress(host, port)
        with self._lock:
            self._holding.discard(address)
            connections = list(self._connections.get(address, []))
        for connection in connections:
            connection.release()

    def connect(self, address: ObsAddress, timeout_seconds: float) -> ObsTransport:
        """The transport factory: a connection to the fake at `address`, or unreachable."""
        with self._lock:
            fake = self._fakes.get(address)
            holding = address in self._holding
        if fake is None:
            raise ObsUnreachableError(f"cannot reach OBS at {address.uri}: connection refused")
        connection = InProcessObsTransport(fake, holding)
        with self._lock:
            # Only the latest few per address are kept: tests reconnect, never by thousands.
            kept = self._connections.setdefault(address, [])
            kept[:] = [*kept[-7:], connection]
        return connection
