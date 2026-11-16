"""Fixtures of the agent conformance suite.

    pytest tests/agent_contract                                  # the in-process fake
    pytest tests/agent_contract --agent-impl node \\
        [--agent-camera cam1|cam2]                               # an in-process camera node
    pytest tests/agent_contract --agent-url http://laptop:8890 \\
        --agent-token <token> [--agent-camera cam1]              # a real agent

Against a real agent the PTZ under test really moves, a little: small, short
velocity commands, always followed by a stop and a return to manual.
"""

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx2
import pytest
from fastapi.testclient import TestClient

from baja_studio.agents.contract import STUDIO_ID_HEADER, TOKEN_HEADER
from baja_studio.core.clock import SystemClock
from baja_studio.fakes.fake_agent import FakeAgent, create_fake_agent_app
from tests.agent_contract.agent_under_test import (
    AgentUnderTest,
    in_process_socket,
    network_socket_opener,
)
from tests.node.node_harness import node_mode_camera, obs_mode_camera, running_node

FAKE_TOKEN = "conformance-token"
STUDIO_ID = "conformance-suite"


@pytest.fixture
def agent(request: pytest.FixtureRequest) -> Iterator[AgentUnderTest]:
    """The agent to check: `--agent-url` if given, else a fresh in-process fake."""
    url = request.config.getoption("--agent-url")
    camera = request.config.getoption("--agent-camera")
    if url:
        token = str(request.config.getoption("--agent-token"))
        headers = {STUDIO_ID_HEADER: STUDIO_ID, **({TOKEN_HEADER: token} if token else {})}
        with httpx2.Client(base_url=url, headers=headers, timeout=3.0, trust_env=False) as http:
            yield AgentUnderTest(http, token, network_socket_opener(url), camera)
        return
    if request.config.getoption("--agent-impl") == "node":
        yield from _in_process_node(request.getfixturevalue("tmp_path"), camera)
        return
    # Real time on purpose: the suite must hold for real agents too.
    fake = FakeAgent(SystemClock(), ptz_cameras=2, crop_cameras=1, token=FAKE_TOKEN)
    headers = {STUDIO_ID_HEADER: STUDIO_ID, TOKEN_HEADER: FAKE_TOKEN}
    with TestClient(create_fake_agent_app(fake), headers=headers) as client:
        yield AgentUnderTest(
            client,
            FAKE_TOKEN,
            lambda socket_headers: in_process_socket(client, socket_headers),
            camera,
        )


def _in_process_node(tmp_path: Path, camera: str | None) -> Iterator[AgentUnderTest]:
    """A camera node with fake video: cam1 in node mode, cam2 in OBS mode (ADR 0023)."""
    cameras = [node_mode_camera("cam1"), obs_mode_camera("cam2")]
    with running_node(tmp_path / "node", cameras, token=FAKE_TOKEN, studio_id=STUDIO_ID) as node:
        client = node.http
        # The node's cameras start at once, but OBS mode first reads its filter and
        # measures its source: wait until both cameras know their window.
        node.wait_until_ready()
        yield AgentUnderTest(
            client,
            FAKE_TOKEN,
            lambda socket_headers: in_process_socket(client, socket_headers),
            camera,
        )


@pytest.fixture
def movable(agent: AgentUnderTest) -> Iterator[dict[str, Any]]:
    """CameraInfo of a camera that moves (a PTZ, else a crop camera), in manual; stopped after."""
    camera = agent.movable()
    path = f"/cameras/{camera['id']}"
    agent.http.put(f"{path}/mode", json={"mode": "manual"})
    yield camera
    agent.http.post(f"{path}/stop")
    agent.http.put(f"{path}/mode", json={"mode": "manual"})


@pytest.fixture
def ptz(agent: AgentUnderTest) -> Iterator[dict[str, Any]]:
    """CameraInfo of the PTZ under test, in manual mode; stopped and manual again after."""
    camera = agent.ptz()
    path = f"/cameras/{camera['id']}"
    agent.http.put(f"{path}/mode", json={"mode": "manual"})
    yield camera
    agent.http.post(f"{path}/stop")
    agent.http.put(f"{path}/mode", json={"mode": "manual"})


@pytest.fixture
def crop(agent: AgentUnderTest) -> Iterator[dict[str, Any]]:
    """CameraInfo of the first crop camera, in manual; window, motion and mode put back after."""
    camera = agent.camera_of_kind("crop")
    path = f"/cameras/{camera['id']}"
    agent.http.put(f"{path}/mode", json={"mode": "manual"})
    original = agent.http.get(f"{path}/crop").json()
    yield camera
    agent.http.post(f"{path}/stop")
    agent.http.put(f"{path}/crop", json=original)
    agent.http.put(f"{path}/mode", json={"mode": "manual"})


def put_in_auto(agent: AgentUnderTest, camera_id: str, resume_after_ms: int) -> None:
    """Switch to auto, or skip the test if this camera has no auto or its tracker cannot run."""
    answer = agent.http.put(
        f"/cameras/{camera_id}/mode",
        json={"mode": "auto", "profile": {"resume_after_ms": resume_after_ms}},
    )
    if answer.status_code == 409 and answer.json().get("error") == "tracker_not_ready":
        pytest.skip("the tracker is not ready on this camera; start it and run again")
    if answer.status_code == 400 and answer.json().get("error") == "not_supported":
        pytest.skip(f"{camera_id} has no auto mode (supports_auto: false)")
    assert answer.status_code == 200, answer.text
    assert answer.json()["mode"] == "auto"
