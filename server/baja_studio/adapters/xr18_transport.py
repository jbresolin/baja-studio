"""Datagrams to and from the XR18, behind a factory so tests open no socket.

The real transport is a UDP socket "connected" to the mixer's address, so
it only hears the mixer. The fake XR18's in-process network
(`fakes/fake_xr18_in_process.py`) provides the same interface.
"""

import contextlib
import socket
import threading
from collections.abc import Callable
from typing import Protocol

RECEIVE_POLL_SECONDS = 0.5
MAX_DATAGRAM_BYTES = 65535


class Xr18TransportClosedError(Exception):
    """`receive` was called on (or was waiting when) the transport closed."""


class Xr18UnreachableError(Exception):
    """The mixer's address cannot be used (unknown host name, no route)."""


class Xr18Transport(Protocol):
    """One conversation with one mixer."""

    def send(self, datagram: bytes) -> None:
        """Send one datagram; never blocks for long, silently lost if nobody listens."""

    def receive(self) -> bytes:
        """Wait for the next datagram; raises `Xr18TransportClosedError` once closed."""

    def close(self) -> None:
        """Stop; a `receive` waiting on another thread raises soon after."""


TransportFactory = Callable[[str, int], Xr18Transport]


class UdpTransport:
    """The real thing: a UDP socket bound to any free local port."""

    def __init__(self, host: str, port: int) -> None:
        """Resolve the mixer's address and open the socket.

        Raises `Xr18UnreachableError` if the host name does not resolve.
        """
        try:
            address = socket.getaddrinfo(host, port, socket.AF_INET, socket.SOCK_DGRAM)[0][4]
        except OSError as error:
            raise Xr18UnreachableError(f"cannot resolve mixer address {host!r}: {error}") from None
        self._socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._socket.bind(("0.0.0.0", 0))
        self._socket.connect(address)
        # recv wakes up this often to notice `close` (closing does not wake it everywhere).
        self._socket.settimeout(RECEIVE_POLL_SECONDS)
        self._closed = threading.Event()

    def send(self, datagram: bytes) -> None:
        """Send; network errors are ignored (UDP: the mixer's silence tells us instead)."""
        if self._closed.is_set():
            return
        with contextlib.suppress(OSError):
            self._socket.send(datagram)

    def receive(self) -> bytes:
        """The next datagram from the mixer."""
        while not self._closed.is_set():
            try:
                return self._socket.recv(MAX_DATAGRAM_BYTES)
            except TimeoutError:
                continue
            except ConnectionResetError:
                # Windows reports an earlier "port unreachable" (mixer off) this way.
                continue
            except OSError as error:
                if self._closed.is_set():
                    break
                raise Xr18TransportClosedError(str(error)) from error
        raise Xr18TransportClosedError("transport closed")

    def close(self) -> None:
        """Close the socket; the reader notices within half a second."""
        self._closed.set()
        self._socket.close()


def open_udp_transport(host: str, port: int) -> Xr18Transport:
    """The default `TransportFactory`."""
    return UdpTransport(host, port)
