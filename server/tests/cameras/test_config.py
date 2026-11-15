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


def test_position_is_polled_once_a_second_by_default_and_zero_turns_it_off() -> None:
    # The bench PTZ's video froze under the old four inquiries a second.
    assert CameraConfig.model_validate(direct()).position_poll_seconds == 1.0
    assert CameraConfig.model_validate(direct(position_poll_seconds=0)).position_poll_seconds == 0


def test_a_position_poll_faster_than_a_quarter_second_is_refused() -> None:
    # One inquiry waits up to half a second anyway; faster only adds traffic.
    with pytest.raises(ValidationError, match=r"0 \(off\) or at least 0\.25"):
        CameraConfig.model_validate(direct(position_poll_seconds=0.1))
    with pytest.raises(ValidationError):
        CameraConfig.model_validate(direct(position_poll_seconds=11))


def test_the_cameras_settings_layout_is_version_2_since_the_position_poll_setting() -> None:
    # An older studio importing a newer export is told to upgrade, instead of
    # failing on the unknown key; a newer one fills in the default.
    assert CamerasSettings.settings_version == 2
    old_file = {"cameras": [direct()]}
    assert CamerasSettings.model_validate(old_file).cameras[0].position_poll_seconds == 1.0


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


# --- USB cameras (WP6a) ---------------------------------------------------------------

PIXY_DEVICE = {"path": r"\\?\usb#pixy", "name": "EMEET PIXY"}


def test_a_usb_camera_needs_no_host_and_a_uvc_camera_needs_its_device() -> None:
    # The Pixy is found by its USB device, not by a network address.
    pixy = CameraConfig.model_validate({"id": "pixy", "driver": "uvc", "usb_device": PIXY_DEVICE})
    fixed = CameraConfig.model_validate({"id": "wide", "driver": "none"})

    assert pixy.usb_device is not None and pixy.usb_device.index == 0
    assert fixed.usb_device is None
    with pytest.raises(ValidationError, match="a uvc camera needs its USB device"):
        CameraConfig.model_validate({"id": "pixy", "driver": "uvc"})


def test_an_agent_camera_cannot_have_a_usb_device() -> None:
    with pytest.raises(ValidationError, match="come from its agent"):
        CameraConfig.model_validate(
            {
                "id": "trackside",
                "owner": "agent",
                "driver": "agent",
                "address": {"agent_id": "laptop-a", "remote_camera_id": "cam1"},
                "usb_device": PIXY_DEVICE,
            }
        )


def test_a_usb_device_needs_a_path_or_at_least_a_name() -> None:
    with pytest.raises(ValidationError, match="needs its path or, failing that, its name"):
        CameraConfig.model_validate({"id": "wide", "driver": "none", "usb_device": {}})


def test_two_enabled_cameras_cannot_use_the_same_usb_device() -> None:
    # Paths are compared without letter case, as Windows does.
    first = {"id": "a", "driver": "none", "usb_device": PIXY_DEVICE}
    second = {"id": "b", "driver": "none", "usb_device": {"path": PIXY_DEVICE["path"].upper()}}

    with pytest.raises(ValidationError, match="same USB device"):
        CamerasSettings.model_validate({"cameras": [first, second]})
    CamerasSettings.model_validate({"cameras": [first, {**second, "enabled": False}]})


def test_a_new_usb_device_means_a_new_driver_but_new_image_settings_do_not() -> None:
    pixy = CameraConfig.model_validate({"id": "pixy", "driver": "uvc", "usb_device": PIXY_DEVICE})
    moved = CameraConfig.model_validate(
        {"id": "pixy", "driver": "uvc", "usb_device": {**PIXY_DEVICE, "path": r"\\?\usb#other"}}
    )
    tuned = CameraConfig.model_validate(
        {"id": "pixy", "driver": "uvc", "usb_device": PIXY_DEVICE, "image": {"gain": {"value": 3}}}
    )

    assert moved.connection_key() != pixy.connection_key()
    assert tuned.connection_key() == pixy.connection_key()


def test_an_obs_crop_camera_needs_its_obs_source_and_no_host() -> None:
    # A digital PTZ lives in OBS: the source is its address (WP6b).
    camera = CameraConfig.model_validate(
        {"id": "crop", "driver": "obs_crop", "obs_source": "Cam 4K"}
    )
    assert camera.crop.filter_name == "Baja crop"
    assert camera.crop.min_window == 0.5
    assert camera.crop.aspect_ratio == pytest.approx(16 / 9)

    with pytest.raises(ValidationError, match="needs its OBS source"):
        CameraConfig.model_validate({"id": "crop", "driver": "obs_crop"})


def test_crop_settings_refuse_impossible_values() -> None:
    def crop(**values: Any) -> dict[str, Any]:
        return {"id": "crop", "driver": "obs_crop", "obs_source": "Cam", "crop": values}

    with pytest.raises(ValidationError, match="aspect"):
        CameraConfig.model_validate(crop(aspect="wide"))
    with pytest.raises(ValidationError, match="min_window"):
        CameraConfig.model_validate(crop(min_window=0.0))
    with pytest.raises(ValidationError, match="update_hz"):
        CameraConfig.model_validate(crop(update_hz=500))
