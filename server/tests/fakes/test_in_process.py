"""Tests for the in-process "network" to fake agents (fakes/in_process.py).

The transport routes requests for an address to whichever fake agent is
plugged in there. Unplugging must look to the client like a laptop that
vanished: requests refused, open sockets closed.
"""

import httpx2
import pytest

from baja_studio.agents.transport import AgentHandshakeRejectedError, AgentSocketClosedError
from baja_studio.fakes.in_process import InProcessTransport
from tests.fake_agents import AGENT_HOST, StartAgent

pytestmark = pytest.mark.anyio

TIMEOUT = httpx2.Timeout(2.0)


async def test_requests_reach_the_agent_plugged_in_at_that_address(
    start_agent: StartAgent, transport: InProcessTransport
) -> None:
    start_agent(agent_id="laptop-a")

    async with transport.http_client(f"http://{AGENT_HOST}:8890", {}, TIMEOUT) as http:
        answer = await http.get("/agent/info")

    assert answer.json()["agent_id"] == "laptop-a"


async def test_an_address_with_nothing_plugged_in_refuses_the_connection(
    transport: InProcessTransport,
) -> None:
    async with transport.http_client("http://10.9.9.9:8890", {}, TIMEOUT) as http:
        with pytest.raises(httpx2.ConnectError):
            await http.get("/agent/info")


async def test_unplugging_closes_an_open_socket(
    start_agent: StartAgent, transport: InProcessTransport
) -> None:
    start_agent()

    async with transport.open_websocket(f"ws://{AGENT_HOST}:8890/agent/ws", {}) as socket:
        assert (await socket.receive_json())["type"] == "state"
        transport.unplug(AGENT_HOST, 8890)

        with pytest.raises(AgentSocketClosedError):
            await socket.receive_json()


async def test_a_refused_handshake_reports_the_http_status(
    start_agent: StartAgent, transport: InProcessTransport
) -> None:
    start_agent(token="needed")

    with pytest.raises(AgentHandshakeRejectedError) as refused:
        async with transport.open_websocket(f"ws://{AGENT_HOST}:8890/agent/ws", {}):
            pass

    assert refused.value.status_code == 401
