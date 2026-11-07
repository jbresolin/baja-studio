"""Fakes for everything the gateway module reaches outside the process.

- `FakeCaddy` (baja_studio/fakes/fake_caddy.py): the admin API;
- `FakeServiceControl`: NSSM, remembering every environment set and restart;
- `FakeUpstreams`: the apps behind the gateway, answering or not;
- `FakeCertificates`: what a TLS handshake with Caddy would find;
- a resolver and an address list for `test_dns`.

`gateway_fakes` is a fixture; `make_app(None)` (tests/conftest.py) uses it, so
the real module list never touches the network either.
"""

from collections.abc import Mapping
from dataclasses import dataclass, field

import httpx2
import pytest

from baja_studio.core.system_services import (
    GATEWAY_SERVICE,
    NOT_INSTALLED,
    STUDIO_SERVICE,
)
from baja_studio.fakes.fake_caddy import FakeCaddy
from baja_studio.modules.gateway import GatewaySystem
from baja_studio.modules.gateway.probes import CertificateInfo, TlsProbeError

STUDIO_UPSTREAM = "127.0.0.1:8888"
FAKE_CADDY_VERSION = "v2.11.8"
LAN_ADDRESS = "192.168.10.20"


class FakeServiceControl:
    """Stands in for NSSM: services that are installed and running, unless told otherwise."""

    def __init__(self, installed: bool = True) -> None:
        state = "SERVICE_RUNNING" if installed else NOT_INSTALLED
        self.statuses = {STUDIO_SERVICE: state, GATEWAY_SERVICE: state}
        self.environments: dict[str, dict[str, str]] = {GATEWAY_SERVICE: {}}
        self.restarts: list[str] = []

    def status(self, service: str) -> str:
        return self.statuses.get(service, NOT_INSTALLED)

    def environment(self, service: str) -> dict[str, str]:
        return dict(self.environments.get(service, {}))

    def set_environment(self, service: str, variables: Mapping[str, str]) -> None:
        self.environments[service] = dict(variables)

    def restart(self, service: str) -> None:
        self.restarts.append(service)


class FakeUpstreams:
    """The apps behind the gateway: an address answers with its status, or refuses."""

    def __init__(self) -> None:
        self.answering: dict[str, int] = {STUDIO_UPSTREAM: 200}

    def handle(self, request: httpx2.Request) -> httpx2.Response:
        address = f"{request.url.host}:{request.url.port}"
        if address not in self.answering:
            raise httpx2.ConnectError(f"connection refused by {address}", request=request)
        return httpx2.Response(self.answering[address])

    def transport(self) -> httpx2.MockTransport:
        return httpx2.MockTransport(self.handle)


class FakeCertificates:
    """What a handshake with Caddy finds: a certificate, or the error it fails with."""

    def __init__(self) -> None:
        self.certificate: CertificateInfo | None = None
        self.error = "certificate verify failed: unable to get local issuer certificate"
        self.probed: list[str] = []

    def probe(self, hostname: str) -> CertificateInfo:
        self.probed.append(hostname)
        if self.certificate is None:
            raise TlsProbeError(self.error)
        return self.certificate


@dataclass
class GatewayFakes:
    """One of each fake, and the `GatewaySystem` that uses them."""

    caddy: FakeCaddy = field(default_factory=FakeCaddy)
    services: FakeServiceControl = field(default_factory=FakeServiceControl)
    upstreams: FakeUpstreams = field(default_factory=FakeUpstreams)
    certificates: FakeCertificates = field(default_factory=FakeCertificates)
    dns_records: dict[str, list[str]] = field(default_factory=dict)
    lan_addresses: list[str] = field(default_factory=lambda: [LAN_ADDRESS])

    def system(self) -> GatewaySystem:
        return GatewaySystem(
            admin_transport=self.caddy.transport(),
            upstream_transport=self.upstreams.transport(),
            tls_probe=self.certificates.probe,
            resolve=lambda hostname: list(self.dns_records.get(hostname, [])),
            list_addresses=lambda: list(self.lan_addresses),
            caddy_version=lambda: FAKE_CADDY_VERSION,
        )


@pytest.fixture
def gateway_fakes() -> GatewayFakes:
    """Fresh fakes: Caddy up, both services running, only the studio answering."""
    return GatewayFakes()
