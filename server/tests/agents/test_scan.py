"""Tests for the subnet scan (agents/scan.py).

The scan is given a `probe` function to call for each address. Here the
probe is a small fake that "finds" agents at chosen addresses, so no network
is touched.
"""

import asyncio

import pytest

from baja_studio.agents.contract import AgentInfo
from baja_studio.agents.scan import SCAN_CONCURRENCY, ScanRangeError, addresses_to_scan, scan_subnet

pytestmark = pytest.mark.anyio


def agent_info(agent_id: str) -> AgentInfo:
    return AgentInfo(
        agent_id=agent_id, name=agent_id, contract_version="1.0", software_version="x", cameras=[]
    )


def test_a_slash_24_gives_its_254_host_addresses() -> None:
    addresses = addresses_to_scan("192.168.1.0/24")

    assert len(addresses) == 254
    assert addresses[0] == "192.168.1.1"
    assert addresses[-1] == "192.168.1.254"


def test_a_single_address_can_be_scanned() -> None:
    assert addresses_to_scan("10.0.0.7/32") == ["10.0.0.7"]


@pytest.mark.parametrize(
    ("cidr", "complaint"),
    [
        ("10.0.0.0/21", "at most 1024"),
        ("not a network", "not a network"),
        ("fd00::/120", "IPv4"),
    ],
)
def test_networks_we_will_not_scan_are_refused_with_a_reason(cidr: str, complaint: str) -> None:
    with pytest.raises(ScanRangeError, match=complaint):
        addresses_to_scan(cidr)


def test_a_slash_22_is_the_largest_network_allowed() -> None:
    assert len(addresses_to_scan("10.0.0.0/22")) == 1022


async def test_the_scan_returns_only_addresses_that_answered_in_address_order() -> None:
    # .20 and .5 are agents; .9 answers but says "no agent here" (None); every
    # other address fails, as a refused connection would.
    async def probe(host: str) -> AgentInfo | None:
        if host == "192.168.1.20":
            return agent_info("laptop-b")
        if host == "192.168.1.5":
            return agent_info("laptop-a")
        if host == "192.168.1.9":
            return None
        raise ConnectionRefusedError(host)

    found = await scan_subnet("192.168.1.0/24", probe)

    assert [(agent.host, agent.info.agent_id) for agent in found] == [
        ("192.168.1.5", "laptop-a"),
        ("192.168.1.20", "laptop-b"),
    ]


async def test_no_more_than_32_probes_run_at_once() -> None:
    # Each probe notes how many probes are running while it runs, and yields
    # to the event loop a few times so others can start meanwhile.
    running = 0
    most_at_once = 0

    async def probe(host: str) -> AgentInfo | None:
        nonlocal running, most_at_once
        running += 1
        most_at_once = max(most_at_once, running)
        for _ in range(3):
            await asyncio.sleep(0)
        running -= 1
        return None

    await scan_subnet("10.1.0.0/24", probe)

    assert most_at_once == SCAN_CONCURRENCY
