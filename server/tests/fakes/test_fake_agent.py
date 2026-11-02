"""Tests for the fake agent's own features (fakes/fake_agent.py, fake_agent_routes.py).

What every agent must do is checked by the conformance suite in
tests/agent_contract, which also runs against this fake. These tests cover
what only the fake has: its failure switches, its push loop on the fake
clock, and its command line.

`TestClient` runs the fake's app in-process; entering `with` starts its pushes.
"""

from collections.abc import Iterator
from typing import Any

import pytest
import uvicorn
from fastapi.testclient import TestClient

from baja_studio.agents.contract import TOKEN_HEADER
from baja_studio.fakes import fake_agent
from baja_studio.fakes.fake_agent import FakeAgent, create_fake_agent_app
from tests.conftest import FakeClock, eventually


@pytest.fixture
def agent(fake_clock: FakeClock) -> FakeAgent:
    """A fake with two PTZs and the token "secret", on the fake clock."""
    return FakeAgent(fake_clock, ptz_cameras=2, token="secret")


@pytest.fixture
def client(agent: FakeAgent) -> Iterator[TestClient]:
    with TestClient(create_fake_agent_app(agent), headers={TOKEN_HEADER: "secret"}) as test_client:
        yield test_client


def test_agent_info_needs_no_token_but_everything_else_does(agent: FakeAgent) -> None:
    # A subnet scan does not know any tokens, so /agent/info must stay open.
    with TestClient(create_fake_agent_app(agent)) as anonymous:
        assert anonymous.get("/agent/info").status_code == 200
        refused = anonymous.get("/agent/health")

    assert refused.status_code == 401
    assert refused.json()["error"] == "unauthorized"


def test_state_is_pushed_four_times_a_second_of_fake_time(
    client: TestClient, fake_clock: FakeClock
) -> None:
    with client.websocket_connect("/agent/ws", headers={TOKEN_HEADER: "secret"}) as socket:
        assert socket.receive_json()["type"] == "state"  # sent at once on connect
        # Wait until the push loop has had its first go and is sleeping.
        eventually(lambda: fake_clock.async_sleeping_count >= 1)

        for _ in range(4):
            fake_clock.advance(0.25)
            assert socket.receive_json()["type"] == "state"


def test_the_silence_switch_stops_pongs(client: TestClient) -> None:
    with client.websocket_connect("/agent/ws", headers={TOKEN_HEADER: "secret"}) as socket:
        socket.receive_json()  # the first state
        socket.send_json({"type": "ping"})
        assert socket.receive_json() == {"type": "pong"}

        client.post("/_fake/silence", json={"silent": True})
        socket.send_json({"type": "ping"})
        client.post("/_fake/silence", json={"silent": False})
        socket.send_json({"type": "ping"})

        # Only the second ping, sent after the agent spoke again, is answered.
        assert socket.receive_json() == {"type": "pong"}


def test_unplugging_a_device_makes_its_commands_fail_with_503(client: TestClient) -> None:
    client.post("/_fake/cameras/cam2/device", json={"online": False})

    refused = client.post("/cameras/cam2/stop")

    assert refused.status_code == 503
    assert refused.json()["error"] == "device_offline"
    assert client.get("/agent/health").json()["cameras"] == {"cam1": "ok", "cam2": "offline"}


def test_the_tracker_switch_decides_whether_auto_is_allowed(client: TestClient) -> None:
    client.post("/_fake/cameras/cam1/tracker", json={"tracker": "not_ready"})
    assert client.put("/cameras/cam1/mode", json={"mode": "auto"}).status_code == 409

    client.post("/_fake/cameras/cam1/tracker", json={"tracker": "tracking"})
    assert client.put("/cameras/cam1/mode", json={"mode": "auto"}).status_code == 200


def test_the_command_line_starts_uvicorn_with_the_requested_options(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Replace uvicorn.run so nothing is served; just look at what it was given.
    served: dict[str, Any] = {}

    def fake_run(app: Any, host: str, port: int) -> None:
        served.update(agent=app.state.agent, host=host, port=port)

    monkeypatch.setattr(uvicorn, "run", fake_run)

    fake_agent.main(["--port", "9000", "--cameras", "3", "--crop-cameras", "1", "--token", "t"])

    assert served["port"] == 9000
    assert served["host"] == "0.0.0.0"
    assert list(served["agent"].cameras) == ["cam1", "cam2", "cam3", "crop1"]
    assert served["agent"].token == "t"
