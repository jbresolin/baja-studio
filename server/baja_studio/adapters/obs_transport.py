"""The socket under the OBS client, and the errors it can raise.

The client never opens a socket itself: it calls a `TransportFactory`. The
service uses `open_websocket_transport` (a real WebSocket); tests use the
fake OBS's in-process transport, so no port is ever opened in a test.
"""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

from websockets.exceptions import ConnectionClosed, InvalidHandshake, InvalidURI
from websockets.sync.client import ClientConnection, connect

# A screenshot (WP6) can be a few hundred kilobytes of base64; OBS sends no huge frames.
MAX_MESSAGE_BYTES = 16 * 1024 * 1024


class ObsError(Exception):
    """Base class of everything the OBS adapter raises."""


class ObsUnreachableError(ObsError):
    """OBS did not answer: not running, wrong address, or the WebSocket server is off."""


class ObsAuthenticationError(ObsError):
    """OBS refused the password (or wants one and none is configured)."""


class ObsNotConnectedError(ObsError):
    """A request was made while there is no identified connection."""


class ObsRequestTimeoutError(ObsError):
    """OBS did not answer a request in time; the connection is probably dead."""


class ObsRequestFailedError(ObsError):
    """OBS answered a request with a failure status (e.g. no such scene)."""

    def __init__(self, request_type: str, code: int, comment: str) -> None:
        """Keep the status code so callers can tell "not found" from other failures."""
        super().__init__(f"OBS refused {request_type}: {comment or 'error ' + str(code)}")
        self.request_type = request_type
        self.code = code
        self.comment = comment


class ObsConnectionClosedError(ObsError):
    """The socket closed; `code` is the WebSocket close code when there was one."""

    def __init__(self, code: int | None, reason: str) -> None:
        """Remember why, so authentication failures can be told apart."""
        super().__init__(f"connection closed ({code}): {reason}" if code else "connection closed")
        self.code = code
        self.reason = reason


@dataclass(frozen=True)
class ObsAddress:
    """Where OBS listens."""

    host: str
    port: int

    @property
    def uri(self) -> str:
        """The ws:// URI (IPv6 literals in brackets)."""
        host = f"[{self.host}]" if ":" in self.host else self.host
        return f"ws://{host}:{self.port}"


class ObsTransport(Protocol):
    """A connected message pipe to OBS."""

    def send(self, text: str) -> None:
        """Send one message; raises `ObsConnectionClosedError` if the socket is gone."""

    def receive(self) -> str:
        """Block until the next message; raises `ObsConnectionClosedError` once closed."""

    def close(self) -> None:
        """Close the pipe; a blocked `receive` then raises. Safe to call twice."""


TransportFactory = Callable[[ObsAddress, float], ObsTransport]


class WebsocketsTransport:
    """`ObsTransport` over a real WebSocket (the `websockets` sync client)."""

    def __init__(self, connection: ClientConnection) -> None:
        """Wrap an open connection; use `open_websocket_transport` to make one."""
        self._connection = connection

    def send(self, text: str) -> None:
        """Send a text frame."""
        try:
            self._connection.send(text)
        except ConnectionClosed as error:
            raise _closed_error(error) from error

    def receive(self) -> str:
        """Next text frame (binary frames are not used by obs-websocket's JSON mode)."""
        try:
            message = self._connection.recv()
        except ConnectionClosed as error:
            raise _closed_error(error) from error
        return message if isinstance(message, str) else message.decode()

    def close(self) -> None:
        """Close the socket (waits at most a second for OBS to agree)."""
        self._connection.close()


def open_websocket_transport(address: ObsAddress, timeout_seconds: float) -> ObsTransport:
    """Connect to OBS over the network; `ObsUnreachableError` if nothing answers in time."""
    try:
        connection = connect(
            address.uri,
            open_timeout=timeout_seconds,
            close_timeout=1.0,
            max_size=MAX_MESSAGE_BYTES,
            # obs-websocket does not ping; neither do we (requests have their own timeouts).
            ping_interval=None,
        )
    except (OSError, TimeoutError, InvalidHandshake, InvalidURI) as error:
        raise ObsUnreachableError(f"cannot reach OBS at {address.uri}: {error}") from error
    return WebsocketsTransport(connection)


def _closed_error(error: ConnectionClosed) -> ObsConnectionClosedError:
    close = error.rcvd
    if close is None:
        return ObsConnectionClosedError(None, "")
    return ObsConnectionClosedError(close.code, close.reason)
