"""What the gateway reaches outside the process, gathered so tests can swap all of it.

`main.py` builds the real one (the installed `caddy.exe`, the network);
tests/modules/gateway_studio.py builds one from fakes.
"""

import subprocess
from collections.abc import Callable
from dataclasses import dataclass, field

import httpx2

from baja_studio.core.system_services import InstallLayout, run_hidden
from baja_studio.modules.gateway.probes import (
    AddressLister,
    Resolver,
    TlsProbe,
    fetch_certificate,
    local_ipv4_addresses,
    resolve_ipv4,
)

CADDY_VERSION_TIMEOUT_SECONDS = 10.0


@dataclass(frozen=True)
class GatewaySystem:
    """Everything the gateway reaches outside the process; tests pass fakes."""

    admin_transport: httpx2.BaseTransport | None = None
    upstream_transport: httpx2.BaseTransport | None = None
    tls_probe: TlsProbe = fetch_certificate
    resolve: Resolver = resolve_ipv4
    list_addresses: AddressLister = local_ipv4_addresses
    caddy_version: Callable[[], str | None] = field(default=lambda: None)


def installed_caddy_version(layout: InstallLayout | None) -> Callable[[], str | None]:
    """A function asking the installed `caddy.exe version` (None from a checkout)."""

    def read_version() -> str | None:
        if layout is None or not layout.caddy_executable.is_file():
            return None
        try:
            result = run_hidden(
                [str(layout.caddy_executable), "version"], CADDY_VERSION_TIMEOUT_SECONDS
            )
        except (OSError, subprocess.TimeoutExpired):
            return None
        words = result.stdout.decode("utf-8", errors="replace").split()
        return words[0] if result.returncode == 0 and words else None

    return read_version
