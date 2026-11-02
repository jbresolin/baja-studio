"""Conformance: the state WebSocket (docs/agent-api.md §5).

Messages are checked against the contract models, the push rate is timed for
real, and a mode change must show up as an event and in the next state push.
"""

from itertools import pairwise
from typing import Any

from baja_studio.agents.contract import EventMessage, StateMessage
from tests.agent_contract.agent_under_test import (
    MARGIN_SECONDS,
    PUSH_INTERVAL_MAX_SECONDS,
    AgentUnderTest,
)


def test_the_first_message_is_the_state_of_every_camera_and_comes_at_once(
    agent: AgentUnderTest,
) -> None:
    camera_ids = {camera["id"] for camera in agent.info()["cameras"]}

    with agent.socket() as socket:
        first = socket.receive(timeout=MARGIN_SECONDS)

    state = StateMessage.model_validate(first)
    assert set(state.cameras) == camera_ids


def test_state_is_pushed_at_least_twice_a_second_even_when_nothing_changes(
    agent: AgentUnderTest,
) -> None:
    with agent.socket() as socket:
        socket.receive(timeout=MARGIN_SECONDS)
        arrivals = [
            arrived
            for arrived, message in socket.collect(seconds=2.5)
            if message["type"] == "state"
        ]

    gaps = [later - earlier for earlier, later in pairwise(arrivals)]
    assert len(arrivals) >= 4
    # 0.5 s apart at the most, plus a little for the test machine.
    assert max(gaps) <= PUSH_INTERVAL_MAX_SECONDS + 0.1


def test_a_ping_is_answered_with_a_pong(agent: AgentUnderTest) -> None:
    with agent.socket() as socket:
        socket.send_json({"type": "ping"})

        socket.receive_until(lambda message: message == {"type": "pong"}, 1.0, "pong")


def test_a_mode_change_is_an_event_and_in_the_next_state(
    agent: AgentUnderTest, ptz: dict[str, Any]
) -> None:
    camera_id = ptz["id"]
    with agent.socket() as socket:
        socket.receive(timeout=MARGIN_SECONDS)

        agent.http.put(f"/cameras/{camera_id}/mode", json={"mode": "hold"})

        event = socket.receive_until(
            lambda message: (
                message.get("type") == "event"
                and message.get("camera_id") == camera_id
                and message.get("event") == "mode_changed"
                and message.get("detail", {}).get("to") == "hold"
            ),
            1.0,
            "mode_changed event",
        )
        socket.wait_for_camera(
            camera_id,
            lambda state: state["mode"] == "hold",
            PUSH_INTERVAL_MAX_SECONDS + MARGIN_SECONDS,
            "state showing hold",
        )
    assert EventMessage.model_validate(event).detail["to"] == "hold"
