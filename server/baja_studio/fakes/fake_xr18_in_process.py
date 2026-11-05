"""Reach a simulated XR18 without a network: a `TransportFactory` for tests.

`InProcessXr18Network.open` is handed to the audio module as its transport
factory. A datagram sent is handled by the simulated mixer at once, on the
sender's thread; what the mixer sends back (replies and pushes to every
subscriber) lands in the receiving transports' queues. Time comes from the
studio's clock, so subscriptions expire on the fake clock.
"""

import itertools
import queue
import threading
from collections.abc import Callable

from baja_studio.adapters.osc import OscArgument
from baja_studio.adapters.xr18_transport import Xr18TransportClosedError
from baja_studio.fakes.simulated_xr18 import Outgoing, SimulatedXr18

_CLOSED = b""


class InProcessXr18Transport:
    """One studio-side socket talking to whatever mixer is at an address."""

    def __init__(self, network: "InProcessXr18Network", client: str, mixer: SimulatedXr18 | None):
        """Created by `InProcessXr18Network.open`."""
        self._network = network
        self.client = client
        self._mixer = mixer
        self._incoming: queue.Queue[bytes] = queue.Queue()
        self.closed = False

    def send(self, datagram: bytes) -> None:
        """The mixer handles it now; a mixer that is not there just never answers."""
        if self.closed or self._mixer is None:
            return
        self._network.sent_count += 1
        with self._network.lock:
            outgoing = self._mixer.handle(self.client, datagram, self._network.now())
        self._network.deliver(outgoing)

    def receive(self) -> bytes:
        """The next datagram from the mixer."""
        datagram = self._incoming.get()
        if datagram == _CLOSED:
            self._incoming.put(_CLOSED)
            raise Xr18TransportClosedError("transport closed")
        return datagram

    def close(self) -> None:
        """Stop; a waiting `receive` raises."""
        self.closed = True
        self._incoming.put(_CLOSED)

    def put(self, datagram: bytes) -> None:
        """Called by the network to deliver a datagram from the mixer."""
        if not self.closed:
            self._incoming.put(datagram)


class InProcessXr18Network:
    """Simulated mixers by address, and the transports talking to them."""

    def __init__(self, now: Callable[[], float]) -> None:
        """`now` is the studio clock's `monotonic`."""
        self.now = now
        self.lock = threading.Lock()
        self._mixers: dict[tuple[str, int], SimulatedXr18] = {}
        self._transports: dict[str, InProcessXr18Transport] = {}
        self._ids = itertools.count(1)
        # Datagrams studio transports have sent, for "no flood" assertions.
        self.sent_count = 0

    def add_mixer(self, host: str, port: int, mixer: SimulatedXr18) -> SimulatedXr18:
        """Put `mixer` at host:port."""
        self._mixers[(host, port)] = mixer
        return mixer

    def open(self, host: str, port: int) -> InProcessXr18Transport:
        """The `TransportFactory`: a transport to whatever is at host:port (maybe nothing)."""
        client = f"studio-{next(self._ids)}"
        transport = InProcessXr18Transport(self, client, self._mixers.get((host, port)))
        self._transports[client] = transport
        return transport

    def open_transports(self) -> list[InProcessXr18Transport]:
        """Transports not closed yet (tests check the module closes its own)."""
        return [transport for transport in self._transports.values() if not transport.closed]

    def external_change(self, mixer: SimulatedXr18, address: str, value: OscArgument) -> None:
        """Someone at Mixing Station changes `address`; subscribers hear about it."""
        with self.lock:
            outgoing = mixer.external_change(address, value, self.now())
        self.deliver(outgoing)

    def deliver(self, outgoing: Outgoing) -> None:
        """Hand each datagram to the transport of the client it is addressed to."""
        for client, datagram in outgoing:
            transport = self._transports.get(client)
            if transport is not None:
                transport.put(datagram)
