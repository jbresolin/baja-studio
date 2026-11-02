"""Find agents on a subnet by asking every address for `GET /agent/info`.

At most 32 probes run at once, each given 300 ms (the contract requires
`/agent/info` to answer within that), so a /24 takes about 2.5 s even when
nothing answers. Networks larger than a /22 (1024 addresses) are refused:
that is a typo, not a studio LAN.

All probes share one HTTP client (`http_info_probe`): creating a client
costs ~12 ms of blocked event loop, which, once per address, would eat
the 300 ms budget of every probe.
"""

import asyncio
import ipaddress
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

import httpx2

from baja_studio.agents.contract import AGENT_PORT, AgentInfo

SCAN_CONCURRENCY = 32
PROBE_TIMEOUT_SECONDS = 0.3
SMALLEST_PREFIX_ALLOWED = 22

# Asks one address for its /agent/info; returns None (or raises) if nothing answers.
InfoProbe = Callable[[str], Awaitable[AgentInfo | None]]


class ScanRangeError(ValueError):
    """The CIDR is not a network we are willing to scan."""


@dataclass(frozen=True)
class FoundAgent:
    """An address that answered like an agent."""

    host: str
    info: AgentInfo


def addresses_to_scan(cidr: str) -> list[str]:
    """The host addresses of `cidr`, or `ScanRangeError` with a message for the operator."""
    try:
        network = ipaddress.ip_network(cidr.strip(), strict=False)
    except ValueError:
        raise ScanRangeError(f"{cidr!r} is not a network like 192.168.1.0/24") from None
    if network.version != 4:
        raise ScanRangeError("only IPv4 networks can be scanned")
    if network.prefixlen < SMALLEST_PREFIX_ALLOWED:
        raise ScanRangeError(
            f"{cidr} has {network.num_addresses} addresses; scan a /{SMALLEST_PREFIX_ALLOWED} "
            "or smaller (at most 1024 addresses)"
        )
    hosts = list(network.hosts())
    # A /32 has no "hosts" in ipaddress terms, but the operator means that one address.
    return [str(address) for address in hosts or [network.network_address]]


def http_info_probe(http: httpx2.AsyncClient, port: int = AGENT_PORT) -> InfoProbe:
    """A probe asking `http://<host>:<port>/agent/info` through one shared client."""

    async def probe(host: str) -> AgentInfo | None:
        answer = await http.get(f"http://{host}:{port}/agent/info")
        if not answer.is_success:
            return None
        return AgentInfo.model_validate(answer.json())

    return probe


async def scan_subnet(cidr: str, probe: InfoProbe) -> list[FoundAgent]:
    """Probe every address in `cidr` and return the ones that answered, in address order.

    `probe` is usually `http_info_probe`; any exception it raises (refused,
    timed out, not an agent) just means "nothing there".
    """
    addresses = addresses_to_scan(cidr)
    limit = asyncio.Semaphore(SCAN_CONCURRENCY)

    async def probe_one(host: str) -> FoundAgent | None:
        async with limit:
            try:
                info = await asyncio.wait_for(probe(host), PROBE_TIMEOUT_SECONDS)
            except Exception:
                return None
        return None if info is None else FoundAgent(host, info)

    results = await asyncio.gather(*(probe_one(host) for host in addresses))
    return [found for found in results if found is not None]
