"""Conformance: per-camera commands (docs/agent-api.md §6).

Run against a real agent, the PTZ under test moves briefly and slowly.
"""

from typing import Any

import pytest

from baja_studio.agents.contract import (
    CommandReply,
    CropRect,
    ImageControlValue,
    Mode,
    ModeReply,
    Position,
    Preset,
)
from tests.agent_contract.agent_under_test import (
    MARGIN_SECONDS,
    PUSH_INTERVAL_MAX_SECONDS,
    AgentUnderTest,
)


def camera_path(camera: dict[str, Any]) -> str:
    return f"/cameras/{camera['id']}"


def test_move_is_accepted_and_reports_the_mode(agent: AgentUnderTest, ptz: dict[str, Any]) -> None:
    answer = agent.http.post(f"{camera_path(ptz)}/move", json={"pan": 0.1, "ttl_ms": 200})

    assert answer.status_code == 200
    assert CommandReply.model_validate(answer.json()) == CommandReply(
        accepted=True, mode=Mode.MANUAL
    )


@pytest.mark.parametrize(
    "body",
    [{"pan": 1.5}, {"tilt": -2}, {"pan": 0.1, "ttl_ms": 2001}, {"pan": 0.1, "ttl_ms": 0}],
)
def test_velocities_outside_minus_one_to_one_and_bad_ttls_are_refused(
    agent: AgentUnderTest, ptz: dict[str, Any], body: dict[str, Any]
) -> None:
    answer = agent.http.post(f"{camera_path(ptz)}/move", json=body)

    assert answer.status_code == 400
    assert answer.json()["error"] == "bad_request"


def test_motion_stops_by_itself_when_the_ttl_runs_out(
    agent: AgentUnderTest, ptz: dict[str, Any]
) -> None:
    # The dead-man switch: no further /move, so the camera must stop after
    # ttl_ms, and the state must say so within one push cycle.
    with agent.socket() as socket:
        agent.http.post(f"{camera_path(ptz)}/move", json={"pan": 0.1, "ttl_ms": 300})
        socket.wait_for_camera(ptz["id"], lambda state: state["moving"], 1.0, "moving=true")

        socket.wait_for_camera(
            ptz["id"],
            lambda state: not state["moving"],
            0.3 + PUSH_INTERVAL_MAX_SECONDS + MARGIN_SECONDS,
            "moving=false after the TTL",
        )


def test_a_move_of_all_zeros_is_a_stop(agent: AgentUnderTest, ptz: dict[str, Any]) -> None:
    with agent.socket() as socket:
        agent.http.post(f"{camera_path(ptz)}/move", json={"pan": 0.1, "ttl_ms": 2000})
        socket.wait_for_camera(ptz["id"], lambda state: state["moving"], 1.0, "moving=true")

        agent.http.post(f"{camera_path(ptz)}/move", json={"pan": 0, "tilt": 0, "zoom": 0})

        socket.wait_for_camera(
            ptz["id"],
            lambda state: not state["moving"],
            PUSH_INTERVAL_MAX_SECONDS + MARGIN_SECONDS,
            "moving=false after a zero move",
        )


def test_stop_is_accepted(agent: AgentUnderTest, ptz: dict[str, Any]) -> None:
    answer = agent.http.post(f"{camera_path(ptz)}/stop")

    assert answer.status_code == 200
    assert answer.json()["accepted"] is True


def test_presets_can_be_stored_named_and_recalled(
    agent: AgentUnderTest, ptz: dict[str, Any]
) -> None:
    number = agent.free_preset_number(ptz)
    path = f"{camera_path(ptz)}/presets/{number}"

    assert agent.http.post(f"{path}/recall").json()["error"] == "unknown_preset"
    stored = Preset.model_validate(agent.http.post(f"{path}/store").json())
    named = Preset.model_validate(agent.http.put(path, json={"label": "Conformance"}).json())
    recalled = agent.http.post(f"{path}/recall")

    assert stored.n == number
    assert named == Preset(n=number, label="Conformance")
    assert recalled.status_code == 200
    assert any(p["n"] == number for p in agent.ptz()["presets"]), "info lists the new preset"


def test_a_preset_number_above_preset_max_is_refused(
    agent: AgentUnderTest, ptz: dict[str, Any]
) -> None:
    preset_max = ptz["limits"]["preset_max"]
    if preset_max is None or preset_max >= 255:
        pytest.skip("this PTZ can store every preset number the contract allows")

    answer = agent.http.post(f"{camera_path(ptz)}/presets/{preset_max + 1}/store")

    assert answer.status_code == 400
    assert answer.json()["error"] == "bad_request"


def test_position_matches_the_feedback_flag(agent: AgentUnderTest, ptz: dict[str, Any]) -> None:
    answer = agent.http.get(f"{camera_path(ptz)}/position")

    assert answer.status_code == 200
    if ptz["has_position_feedback"]:
        Position.model_validate(answer.json())
    else:
        assert answer.json() is None


def test_mode_can_be_read_and_auto_paused_cannot_be_requested(
    agent: AgentUnderTest, ptz: dict[str, Any]
) -> None:
    ModeReply.model_validate(agent.http.get(f"{camera_path(ptz)}/mode").json())

    answer = agent.http.put(f"{camera_path(ptz)}/mode", json={"mode": "auto_paused"})

    assert answer.status_code == 400
    assert answer.json()["error"] == "bad_request"


def test_image_controls_are_the_ones_info_lists_and_changes_are_read_back(
    agent: AgentUnderTest, ptz: dict[str, Any]
) -> None:
    controls = agent.http.get(f"{camera_path(ptz)}/image").json()
    assert set(controls) == set(ptz["image_controls"])
    if not controls:
        return
    name, current = next(iter(controls.items()))

    # Write back the value it already has: harmless on a real camera.
    answer = agent.http.put(f"{camera_path(ptz)}/image", json={name: {"value": current["value"]}})

    assert answer.status_code == 200
    ImageControlValue.model_validate(answer.json()[name])


def test_an_unknown_image_control_is_refused(agent: AgentUnderTest, ptz: dict[str, Any]) -> None:
    answer = agent.http.put(f"{camera_path(ptz)}/image", json={"no_such_control": {"value": 1}})

    assert answer.status_code == 400
    assert answer.json()["error"] == "unknown_control"


def test_crop_is_only_for_crop_cameras(agent: AgentUnderTest, ptz: dict[str, Any]) -> None:
    answer = agent.http.get(f"{camera_path(ptz)}/crop")

    assert answer.status_code == 400
    assert answer.json()["error"] == "not_supported"


def test_presets_are_only_for_ptz_cameras(agent: AgentUnderTest, crop: dict[str, Any]) -> None:
    # A crop camera's preset_max is null: storing, recalling or naming one is refused.
    path = f"{camera_path(crop)}/presets/0"

    answers = [
        agent.http.post(f"{path}/store"),
        agent.http.post(f"{path}/recall"),
        agent.http.put(path, json={"label": "Nope"}),
    ]

    assert [answer.status_code for answer in answers] == [400, 400, 400]
    assert {answer.json()["error"] for answer in answers} == {"not_supported"}


def test_a_crop_camera_takes_a_crop_inside_the_frame(agent: AgentUnderTest) -> None:
    crop = agent.camera_of_kind("crop")
    path = f"{camera_path(crop)}/crop"
    original = agent.http.get(path).json()

    applied = agent.http.put(path, json={"x": 0.1, "y": 0.1, "w": 0.5, "h": 0.5})
    outside = agent.http.put(path, json={"x": 0.6, "y": 0, "w": 0.5, "h": 0.5})
    agent.http.put(path, json=original)

    assert applied.status_code == 200
    CropRect.model_validate(applied.json())
    assert outside.status_code == 400
    assert outside.json()["error"] == "bad_request"


def test_a_move_drives_a_crop_cameras_window_and_its_ttl_applies(
    agent: AgentUnderTest, crop: dict[str, Any]
) -> None:
    path = camera_path(crop)
    # Start from a small window in the middle, so it has room to pan right.
    agent.http.put(f"{path}/crop", json={"x": 0.3, "y": 0.3, "w": 0.4, "h": 0.4})
    before = Position.model_validate(agent.http.get(f"{path}/position").json())

    with agent.socket() as socket:
        reply = agent.http.post(f"{path}/move", json={"pan": 0.5, "ttl_ms": 300})
        assert reply.status_code == 200
        assert reply.json()["mode"] == "manual"
        socket.wait_for_camera(
            crop["id"],
            lambda state: not state["moving"] and state["position"]["pan"] > before.pan,
            0.3 + PUSH_INTERVAL_MAX_SECONDS + MARGIN_SECONDS,
            "the window centre further right, and stopped after the TTL",
        )

    after = Position.model_validate(agent.http.get(f"{path}/position").json())
    window = CropRect.model_validate(agent.http.get(f"{path}/crop").json())
    # The position is the window's centre (and the window did not tilt).
    assert after.pan == pytest.approx(window.x + window.w / 2, abs=1e-3)
    assert after.tilt == pytest.approx(before.tilt, abs=1e-3)


def test_a_crop_cameras_position_is_its_window_centre(
    agent: AgentUnderTest, crop: dict[str, Any]
) -> None:
    path = camera_path(crop)
    agent.http.put(f"{path}/crop", json={"x": 0.1, "y": 0.2, "w": 0.4, "h": 0.4})

    position = agent.http.get(f"{path}/position").json()

    assert crop["has_position_feedback"] is True
    assert (position["pan"], position["tilt"]) == pytest.approx((0.3, 0.4))
    assert 0.0 <= position["zoom"] <= 1.0


def test_resync_is_accepted(agent: AgentUnderTest, ptz: dict[str, Any]) -> None:
    answer = agent.http.post(f"{camera_path(ptz)}/resync")

    assert answer.status_code == 200
    assert answer.json()["accepted"] is True


def test_commands_to_an_offline_device_fail_with_503(
    agent: AgentUnderTest, ptz: dict[str, Any]
) -> None:
    agent.fake_switch(f"/cameras/{ptz['id']}/device", {"online": False})

    answer = agent.http.post(f"{camera_path(ptz)}/move", json={"pan": 0.1})

    assert answer.status_code == 503
    assert answer.json()["error"] == "device_offline"
    agent.fake_switch(f"/cameras/{ptz['id']}/device", {"online": True})
