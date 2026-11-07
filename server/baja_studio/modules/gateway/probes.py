"""Checks the gateway runs: is each app answering, does the name resolve here, is TLS valid.

Each check that touches the network has a real implementation here and is
passed into the module as a plain function, so tests swap in fakes.
"""

import socket
import ssl
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from typing import Any

import httpx2

UPSTREAM_TIMEOUT = httpx2.Timeout(connect=2.0, read=2.0, write=2.0, pool=2.0)
TLS_TIMEOUT_SECONDS = 3.0
HTTPS_PORT = 443


@dataclass(frozen=True)
class UpstreamCheck:
    """Whether an app behind the gateway answered; any HTTP answer counts."""

    reachable: bool
    status_code: int | None = None
    error: str = ""


def check_upstream(upstream: str, transport: httpx2.BaseTransport | None = None) -> UpstreamCheck:
    """GET `http://<upstream>/` directly (not through Caddy), with a 2 s limit."""
    try:
        with httpx2.Client(
            timeout=UPSTREAM_TIMEOUT, transport=transport, trust_env=False
        ) as client:
            response = client.get(f"http://{upstream}/")
    except httpx2.TransportError as error:
        return UpstreamCheck(False, error=str(error) or type(error).__name__)
    return UpstreamCheck(True, status_code=response.status_code)


@dataclass(frozen=True)
class CertificateInfo:
    """The certificate Caddy serves for the hostname, as a browser would check it."""

    issuer: str
    subject: str
    expires_at: str

    def as_state(self) -> dict[str, Any]:
        """JSON-ready, for the state tree."""
        return asdict(self)


class TlsProbeError(Exception):
    """No valid certificate: not issued yet, expired, or the handshake failed."""


TlsProbe = Callable[[str], CertificateInfo]


def certificate_info_from_peer(peer: dict[str, Any]) -> CertificateInfo:
    """Turn `SSLSocket.getpeercert()` into the fields the home page shows."""
    expires = datetime.fromtimestamp(ssl.cert_time_to_seconds(peer["notAfter"]), UTC)
    return CertificateInfo(
        issuer=_name_field(peer.get("issuer", ()), "organizationName")
        or _name_field(peer.get("issuer", ()), "commonName"),
        subject=_name_field(peer.get("subject", ()), "commonName"),
        expires_at=expires.isoformat(),
    )


def _name_field(name: Any, field: str) -> str:
    """Find `field` in getpeercert's nested ((("commonName", "x"),), ...) form."""
    for relative_name in name:
        for key, value in relative_name:
            if key == field:
                return str(value)
    return ""


def fetch_certificate(hostname: str) -> CertificateInfo:
    """Handshake with Caddy on this PC as `hostname`, verifying like a phone would.

    Connecting to 127.0.0.1 (not the name) tests Caddy itself even where the
    name does not resolve yet; the system's trusted roots must accept it.
    """
    context = ssl.create_default_context()
    try:
        with (
            socket.create_connection(("127.0.0.1", HTTPS_PORT), TLS_TIMEOUT_SECONDS) as raw,
            context.wrap_socket(raw, server_hostname=hostname) as secure,
        ):
            peer = secure.getpeercert()
    except (OSError, ssl.SSLError) as error:
        raise TlsProbeError(str(error) or type(error).__name__) from error
    if not peer:
        raise TlsProbeError("Caddy presented no certificate")
    return certificate_info_from_peer(dict(peer))


Resolver = Callable[[str], list[str]]
AddressLister = Callable[[], list[str]]


def resolve_ipv4(hostname: str) -> list[str]:
    """The IPv4 addresses `hostname` resolves to on this PC (empty when it does not)."""
    try:
        results = socket.getaddrinfo(hostname, None, socket.AF_INET, socket.SOCK_STREAM)
    except OSError:
        return []
    return sorted({str(result[4][0]) for result in results})


def local_ipv4_addresses() -> list[str]:
    """This PC's own IPv4 addresses, loopback left out."""
    try:
        results = socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET)
    except OSError:
        return []
    return sorted({str(result[4][0]) for result in results} - {"127.0.0.1"})


@dataclass(frozen=True)
class DnsCheck:
    """Where the hostname points, compared with this PC's addresses."""

    hostname: str
    resolved: list[str]
    lan_addresses: list[str]
    matches: bool
    message: str

    def as_state(self) -> dict[str, Any]:
        """JSON-ready, for the command result and the state tree."""
        return asdict(self)


def check_dns(hostname: str, resolve: Resolver, list_addresses: AddressLister) -> DnsCheck:
    """Resolve `hostname` and say whether it points at this PC."""
    resolved = resolve(hostname)
    mine = list_addresses()
    matches = bool(set(resolved) & set(mine))
    if not resolved:
        message = f"{hostname} does not resolve here; add the A record (and the hEX static entry)"
    elif matches:
        message = f"{hostname} points at this PC"
    else:
        message = (
            f"{hostname} points at {', '.join(resolved)}, "
            f"but this PC is {', '.join(mine) or 'unknown'}"
        )
    return DnsCheck(hostname, resolved, mine, matches, message)
