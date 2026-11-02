"""Conformance: who drives the camera (docs/agent-api.md §7, "Mode semantics").

The rule under test: the studio always wins. Any studio motion in `auto`
pauses the tracker; auto comes back by itself `resume_after_ms` after the
motion ends; `manual` and `hold` never bring it back by themselves.

Against a real agent these need a running tracker; if the agent answers
`tracker_not_ready`, the tests are skipped with that reason.
"""

from typing import Any

from tests.agent_contract.agent_under_test import (
    MARGIN_SECONDS,
    PUSH_INTERVAL_MAX_SECONDS,
    AgentUnderTest,
)
from tests.agent_contract.conftest import put_in_auto

# Short, so the suite stays quick; long enough to tell "paused" from "resumed".
RESUME_AFTER_MS = 600
ONE_PUSH = PUSH_INTERVAL_MAX_SECONDS + MARGIN_SECONDS


def mode_is(mode: str) -> Any:
    return lambda state: state.get("mode") == mode


def test_a_move_in_auto_pauses_auto_and_auto_resumes_by_itself(
    agent: AgentUnderTest, ptz: dict[str, Any]
) -> None:
    camera_id = ptz["id"]
    put_in_auto(agent, camera_id, RESUME_AFTER_MS)
    with agent.socket() as socket:
        reply = agent.http.post(f"/cameras/{camera_id}/move", json={"pan": 0.1, "ttl_ms": 200})

        assert reply.json()["mode"] == "auto_paused"
        socket.receive_until(
            lambda message: message.get("event") == "manual_override_started",
            1.0,
            "manual_override_started event",
        )
        socket.wait_for_camera(camera_id, mode_is("auto_paused"), ONE_PUSH, "auto_paused")
        # 200 ms of motion, then 600 ms of quiet, then one push to report it.
        socket.receive_until(
            lambda message: message.get("event") == "manual_override_ended",
            0.2 + 0.6 + ONE_PUSH,
            "manual_override_ended event",
        )
        socket.wait_for_camera(camera_id, mode_is("auto"), ONE_PUSH, "auto again by itself")


def test_stop_and_preset_recall_also_pause_auto(agent: AgentUnderTest, ptz: dict[str, Any]) -> None:
    camera_id = ptz["id"]
    used = {preset["n"] for preset in ptz["presets"]}
    number = max(n for n in range(256) if n not in used)
    agent.http.post(f"/cameras/{camera_id}/presets/{number}/store")

    put_in_auto(agent, camera_id, RESUME_AFTER_MS)
    assert agent.http.post(f"/cameras/{camera_id}/stop").json()["mode"] == "auto_paused"

    put_in_auto(agent, camera_id, RESUME_AFTER_MS)
    recall = agent.http.post(f"/cameras/{camera_id}/presets/{number}/recall")
    assert recall.json()["mode"] == "auto_paused"


def test_put_mode_auto_ends_the_pause_at_once(agent: AgentUnderTest, ptz: dict[str, Any]) -> None:
    camera_id = ptz["id"]
    put_in_auto(agent, camera_id, 60_000)  # a pause that would last a minute
    agent.http.post(f"/cameras/{camera_id}/stop")

    put_in_auto(agent, camera_id, 60_000)

    assert agent.http.get(f"/cameras/{camera_id}/mode").json()["mode"] == "auto"


def test_manual_and_hold_never_resume_auto_by_themselves(
    agent: AgentUnderTest, ptz: dict[str, Any]
) -> None:
    camera_id = ptz["id"]
    with agent.socket() as socket:
        for mode in ("manual", "hold"):
            put_in_auto(agent, camera_id, 100)
            agent.http.put(f"/cameras/{camera_id}/mode", json={"mode": mode})
            reply = agent.http.post(f"/cameras/{camera_id}/move", json={"pan": 0.1, "ttl_ms": 100})
            assert reply.json()["mode"] == mode

            # Well past resume_after_ms: still the same mode in every push.
            for _, message in socket.collect(seconds=0.1 + 0.1 + ONE_PUSH):
                if message.get("type") == "state":
                    assert message["cameras"][camera_id]["mode"] == mode


def test_auto_is_refused_with_409_when_the_tracker_cannot_run(
    agent: AgentUnderTest, ptz: dict[str, Any]
) -> None:
    agent.fake_switch(f"/cameras/{ptz['id']}/tracker", {"tracker": "not_ready"})

    answer = agent.http.put(f"/cameras/{ptz['id']}/mode", json={"mode": "auto"})

    assert answer.status_code == 409
    assert answer.json()["error"] == "tracker_not_ready"
    agent.fake_switch(f"/cameras/{ptz['id']}/tracker", {"tracker": "ready"})
