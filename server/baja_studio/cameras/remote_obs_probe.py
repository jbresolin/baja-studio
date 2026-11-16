"""Camera setup's short-lived connections to an OBS that is not (yet) in use (ADR 0021).

"Test connection" must test what the form says (an address and password not
saved yet), and the source picker must list a laptop's inputs before any
camera uses that laptop. Both open a connection of their own for the one
question, with the remote connection's timeouts and no events, and always
close it. They run on a command's thread, which may wait a few seconds; no
worker, lock or other OBS waits with them.

The password is used for the handshake only: it never appears in a result,
an error or a log line.
"""

import contextlib
from collections.abc import Iterator
from dataclasses import dataclass

from baja_studio.adapters.obs_transport import (
    ObsAddress,
    ObsAuthenticationError,
    ObsError,
    TransportFactory,
)
from baja_studio.adapters.obs_websocket import ObsWebsocketClient
from baja_studio.cameras.remote_obs_connection import (
    CONNECT_TIMEOUT_SECONDS,
    REQUEST_TIMEOUT_SECONDS,
    where_of,
    why_unreachable,
)
from baja_studio.core.clock import Clock

CONNECTED, UNREACHABLE, WRONG_PASSWORD = "connected", "unreachable", "wrong_password"
NO_EVENTS = 0


class ObsProbeError(Exception):
    """The OBS could not be reached or refused us; `outcome` and the message say how."""

    def __init__(self, outcome: str, message: str) -> None:
        """`outcome` is `UNREACHABLE` or `WRONG_PASSWORD`; the message is for the operator."""
        super().__init__(message)
        self.outcome = outcome


@dataclass(frozen=True)
class ProbeResult:
    """What Test connection reports: the outcome, in words, and OBS's versions when connected."""

    outcome: str
    message: str
    obs_version: str = ""
    websocket_version: str = ""

    def as_reply(self) -> dict[str, str]:
        """The command's answer to the page."""
        return {
            "result": self.outcome,
            "message": self.message,
            "obs_version": self.obs_version,
            "websocket_version": self.websocket_version,
        }


@contextlib.contextmanager
def temporary_client(
    host: str, port: int, password: str, clock: Clock, transport_factory: TransportFactory
) -> Iterator[ObsWebsocketClient]:
    """A connected client to host:port for one question; closed on the way out.

    Raises `ObsProbeError` when it cannot connect.
    """
    where = where_of(host, port)
    client = ObsWebsocketClient(
        clock,
        transport_factory,
        connect_timeout_s=CONNECT_TIMEOUT_SECONDS,
        request_timeout_s=REQUEST_TIMEOUT_SECONDS,
        event_subscriptions=NO_EVENTS,
    )
    try:
        try:
            client.connect(ObsAddress(host, port), password)
        except ObsAuthenticationError:
            if password:
                raise ObsProbeError(
                    WRONG_PASSWORD, f"OBS at {where} refused the password"
                ) from None
            raise ObsProbeError(
                WRONG_PASSWORD, f"OBS at {where} asks for a password: type it above"
            ) from None
        except ObsError as error:
            detail = why_unreachable(error, ObsAddress(host, port))
            raise ObsProbeError(UNREACHABLE, f"OBS at {where} unreachable: {detail}") from None
        yield client
    finally:
        client.close()


def probe_obs(
    host: str, port: int, password: str, clock: Clock, transport_factory: TransportFactory
) -> ProbeResult:
    """Connect, ask OBS its version, close: Camera setup's "Test connection"."""
    where = where_of(host, port)
    try:
        with temporary_client(host, port, password, clock, transport_factory) as client:
            version = client.get_version()
    except ObsProbeError as failed:
        return ProbeResult(failed.outcome, str(failed))
    except ObsError as error:
        return ProbeResult(UNREACHABLE, f"OBS at {where} stopped answering: {error}")
    obs_version = str(version.get("obsVersion", ""))
    websocket_version = str(version.get("obsWebSocketVersion", ""))
    return ProbeResult(
        CONNECTED,
        f"connected to OBS {obs_version} (obs-websocket {websocket_version}) at {where}",
        obs_version,
        websocket_version,
    )
