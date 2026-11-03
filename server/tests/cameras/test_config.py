"""Tests for the camera registry's settings model (cameras/config.py).

These are the mistakes the Settings page must refuse with a message, rather
than save and fail later.
"""

from typing import Any

import pytest
from pydantic import ValidationError

from baja_studio.cameras.config import (
    CameraConfig,
    CamerasSettings,
    ControllerConfig,
    MotionSettings,
)


def direct(**overrides: Any) -> dict[str, Any]:
    return {"id": "cam1", "address": {"host": "10.0.0.9"}, **overrides}


def test_a_direct_camera_needs_only_an_id_and_a_host() -> None:
    camera = CameraConfig.model_validate(direct())

    assert camera.owner == "studio"
    assert camera.driver == "visca_ip"
    assert camera.address.port == 52381


def test_a_direct_camera_without_a_host_is_refused() -> None:
    with pytest.raises(ValidationError, match=r"needs address\.host"):
        CameraConfig.model_validate({"id": "cam1"})


def test_owner_and_driver_must_agree() -> None:
    with pytest.raises(ValidationError, match="agent-owned cameras use the 'agent' driver"):
        CameraConfig.model_validate(direct(owner="agent"))


def test_an_agent_camera_needs_the_agent_and_its_camera_id() -> None:
    with pytest.raises(ValidationError, match="agent_id and remote_camera_id"):
        CameraConfig.model_validate(
            {"id": "cam1", "owner": "agent", "driver": "agent", "address": {"agent_id": "a"}}
        )


def test_only_agent_cameras_may_have_a_direct_fallback() -> None:
    fallback = {"driver": "visca_ip", "address": {"host": "10.0.0.9"}}

    with pytest.raises(ValidationError, match="only agent-owned cameras"):
        CameraConfig.model_validate(direct(direct_fallback=fallback))


def test_a_direct_fallback_must_be_a_direct_driver_with_a_host() -> None:
    agent_camera = {
        "id": "cam1",
        "owner": "agent",
        "driver": "agent",
        "address": {"agent_id": "laptop-a", "remote_camera_id": "cam1"},
    }

    with pytest.raises(ValidationError, match="needs the camera's host"):
        CameraConfig.model_validate(
            {**agent_camera, "direct_fallback": {"address": {"agent_id": "x"}}}
        )


def test_ids_may_not_contain_dots_because_they_become_state_paths() -> None:
    with pytest.raises(ValidationError):
        CameraConfig.model_validate(direct(id="finish.ptz"))


def test_controller_addresses_go_from_1_to_255() -> None:
    with pytest.raises(ValidationError):
        CameraConfig.model_validate(direct(controller_address=0))


def test_two_enabled_cameras_cannot_answer_to_the_same_controller_address() -> None:
    with pytest.raises(ValidationError, match="same controller address"):
        CamerasSettings.model_validate(
            {"cameras": [direct(controller_address=1), direct(id="cam2", controller_address=1)]}
        )


def test_a_disabled_camera_may_share_an_address_with_an_enabled_one() -> None:
    # Handy at an event: keep the spare's entry, switched off, on the same button.
    CamerasSettings.model_validate(
        {
            "cameras": [
                direct(controller_address=1),
                direct(id="spare", controller_address=1, enabled=False),
            ]
        }
    )


def test_camera_ids_controller_ids_and_serial_ports_must_be_unique() -> None:
    with pytest.raises(ValidationError, match="same id"):
        CamerasSettings.model_validate({"cameras": [direct(), direct()]})
    with pytest.raises(ValidationError, match="same serial port"):
        CamerasSettings.model_validate(
            {"controllers": [{"id": "a", "port": "COM8"}, {"id": "b", "port": "COM8"}]}
        )


def test_motion_defaults_are_the_briefs() -> None:
    motion = MotionSettings()

    assert (motion.joystick_deadband, motion.ttl_ms, motion.resend_interval_ms) == (0.04, 300, 100)
    assert motion.joystick_zoom_velocity == 0.6
    # Off until the bench shows the controller repeats frames while a stick is held.
    assert motion.controller_silence_ms == 0


def test_top_speed_steps_default_to_0x18_and_0x14_and_stay_inside_the_visca_spec() -> None:
    camera = CameraConfig.model_validate(direct())

    assert (camera.limits.pan_speed_max_step, camera.limits.tilt_speed_max_step) == (0x18, 0x14)
    with pytest.raises(ValidationError):
        CameraConfig.model_validate(
            direct(limits={"tilt_speed_max_step": 0x18})
        )  # tilt tops at 0x17


def test_the_resend_interval_must_be_shorter_than_the_ttl() -> None:
    # Otherwise every held velocity would expire before it was renewed.
    with pytest.raises(ValidationError, match="shorter than ttl_ms"):
        MotionSettings(ttl_ms=100, resend_interval_ms=100)


def test_unknown_keys_are_refused_so_a_typo_is_not_silently_ignored() -> None:
    with pytest.raises(ValidationError):
        CameraConfig.model_validate(direct(controler_address=1))


def test_the_connection_key_changes_with_the_address_but_not_the_label() -> None:
    camera = CameraConfig.model_validate(direct())

    assert camera.connection_key() == camera.model_copy(update={"label": "x"}).connection_key()
    moved = CameraConfig.model_validate(direct(address={"host": "10.0.0.10"}))
    assert camera.connection_key() != moved.connection_key()


def test_a_call_binding_needs_the_target_its_action_names() -> None:
    with pytest.raises(ValidationError, match="select_camera needs a camera"):
        ControllerConfig.model_validate(
            {"id": "desk", "port": "COM8", "call_bindings": [{"n": 1, "action": "select_camera"}]}
        )
    with pytest.raises(ValidationError, match="recall_preset needs a preset number"):
        ControllerConfig.model_validate(
            {"id": "desk", "port": "COM8", "call_bindings": [{"n": 1, "action": "recall_preset"}]}
        )


def test_a_call_button_cannot_be_bound_twice() -> None:
    with pytest.raises(ValidationError, match="bound twice"):
        ControllerConfig.model_validate(
            {
                "id": "desk",
                "port": "COM8",
                "call_bindings": [{"n": 1, "action": "none"}, {"n": 1, "action": "none"}],
            }
        )


def test_a_call_binding_must_select_a_camera_in_the_list() -> None:
    with pytest.raises(ValidationError, match="selects camera 'gone', which is not in the list"):
        CamerasSettings.model_validate(
            {
                "cameras": [direct()],
                "controllers": [
                    {
                        "id": "desk",
                        "port": "COM8",
                        "call_bindings": [{"n": 2, "action": "select_camera", "camera": "gone"}],
                    }
                ],
            }
        )


def test_a_call_bound_to_none_counts_as_unbound() -> None:
    controller = ControllerConfig.model_validate(
        {"id": "desk", "port": "COM8", "call_bindings": [{"n": 3, "action": "none"}]}
    )

    assert controller.call_binding(3) is None
