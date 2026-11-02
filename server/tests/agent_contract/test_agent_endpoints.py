"""Conformance: agent-level endpoints and the token (docs/agent-api.md §3, §4).

Each test talks to the agent only through the contract, so a real agent
must pass exactly as the fake does.
"""

import time

import httpx2
import pytest

from baja_studio.agents.contract import (
    INFO_ANSWER_WITHIN_SECONDS,
    TOKEN_HEADER,
    AgentHealth,
    AgentInfo,
    LogEntry,
    contract_major,
)
from tests.agent_contract.agent_under_test import AgentUnderTest, SocketRefusedError


def test_info_has_every_field_and_a_major_minor_contract_version(agent: AgentUnderTest) -> None:
    answer = agent.http.get("/agent/info")

    assert answer.status_code == 200
    info = AgentInfo.model_validate(answer.json())  # fails if any field is missing or wrong
    assert contract_major(info.contract_version) == 1
    assert info.cameras, "an agent lists at least one camera"
    assert len({camera.id for camera in info.cameras}) == len(info.cameras)


def test_info_needs_no_token_and_answers_within_300_ms(agent: AgentUnderTest) -> None:
    # A subnet scan asks every address for /agent/info without knowing any
    # token, and gives each address only 300 ms.
    started = time.monotonic()
    answer = agent.http.get("/agent/info", headers={TOKEN_HEADER: ""})
    elapsed = time.monotonic() - started

    assert answer.status_code == 200
    assert elapsed < INFO_ANSWER_WITHIN_SECONDS


def test_health_reports_every_camera(agent: AgentUnderTest) -> None:
    health = AgentHealth.model_validate(agent.http.get("/agent/health").json())

    assert set(health.cameras) == {camera["id"] for camera in agent.info()["cameras"]}
    assert health.uptime_s >= 0


def test_the_log_lists_our_commands_newest_first_with_our_studio_id(
    agent: AgentUnderTest, ptz: dict[str, object]
) -> None:
    agent.http.post(f"/cameras/{ptz['id']}/stop")

    entries = agent.http.get("/agent/log", params={"limit": 1}).json()

    assert len(entries) == 1
    newest = LogEntry.model_validate(entries[0])
    assert newest.command == "stop"
    assert "conformance-suite" in newest.source


def test_a_wrong_token_is_refused_everywhere_but_info(agent: AgentUnderTest) -> None:
    if not agent.token:
        pytest.skip("run with --agent-token to check token handling")
    camera_id = agent.info()["cameras"][0]["id"]
    wrong = {TOKEN_HEADER: "not-the-token"}

    for method, path in [("GET", "/agent/health"), ("POST", f"/cameras/{camera_id}/stop")]:
        answer = agent.http.request(method, path, headers=wrong)
        assert answer.status_code == 401, path
        assert answer.json()["error"] == "unauthorized"

    with pytest.raises(SocketRefusedError), agent.socket(token="not-the-token"):
        pass


def test_errors_are_json_with_a_code_and_a_detail(agent: AgentUnderTest) -> None:
    answer = agent.http.post("/cameras/no-such-camera/stop")

    assert answer.status_code == 404
    body = answer.json()
    assert body["error"] == "unknown_camera"
    assert isinstance(body["detail"], str) and body["detail"]


def test_a_malformed_body_is_a_400_bad_request(
    agent: AgentUnderTest, ptz: dict[str, object]
) -> None:
    answer = agent.http.post(
        f"/cameras/{ptz['id']}/move",
        content=b"{not json",
        headers={"Content-Type": "application/json"},
    )

    assert answer.status_code == 400
    assert answer.json()["error"] == "bad_request"


def test_the_agent_is_plain_http_json(agent: AgentUnderTest) -> None:
    answer = agent.http.get("/agent/health")

    assert isinstance(answer, httpx2.Response)
    assert answer.headers["content-type"].startswith("application/json")
