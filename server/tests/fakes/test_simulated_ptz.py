"""Tests for the fake agent's simulated camera (fakes/simulated_ptz.py).

The camera is plain code with no clock of its own: each call says what time
it is (`at(seconds)`), so these tests can jump through time freely. They pin
down the contract's mode rules, which every real agent must follow too.
"""

import pytest

from baja_studio.agents.contract import (
    CameraKind,
    CropRect,
    ErrorCode,
    ImageControlChange,
    Mode,
    ModeRequest,
    MoveRequest,
    TrackerStatus,
)
from baja_studio.fakes.simulated_crop import CROP_LIMITS
from baja_studio.fakes.simulated_ptz import (
    PRESET_MAX,
    CameraCommandError,
    FakeTracker,
    Moment,
    SimulatedCamera,
)

STUDIO = "studio:test"


def at(seconds: float) -> Moment:
    """The moment `seconds` after the start of the test."""
    return Moment(seconds, f"t+{seconds}")


def ptz_in_auto(resume_after_ms: int = 3000) -> SimulatedCamera:
    """A PTZ already in auto mode, with its events cleared."""
    camera = SimulatedCamera("cam1", "Cam 1", CameraKind.PTZ)
    camera.set_mode(ModeRequest(mode=Mode.AUTO, profile={"resume_after_ms": resume_after_ms}))
    camera.pending_events.clear()
    return camera


def mode_of(camera: SimulatedCamera) -> Mode:
    """The camera's mode right now (a function, so each check reads it afresh)."""
    return camera.mode


def event_names(camera: SimulatedCamera) -> list[str]:
    return [name for name, _ in camera.pending_events]


def test_a_move_in_auto_pauses_auto_and_says_so() -> None:
    camera = ptz_in_auto()

    reply = camera.move(at(0), MoveRequest(pan=0.5), STUDIO)

    assert reply.mode == Mode.AUTO_PAUSED
    assert event_names(camera) == ["mode_changed", "manual_override_started"]


def test_auto_resumes_resume_after_ms_after_the_last_motion_ends() -> None:
    # resume_after_ms = 1000. The move lasts 300 ms (its TTL), so the
    # countdown starts at 0.3 s and auto comes back at 1.3 s, not 1.0 s.
    camera = ptz_in_auto(resume_after_ms=1000)
    camera.move(at(0), MoveRequest(pan=0.5, ttl_ms=300), STUDIO)

    camera.advance(at(1.2))
    assert mode_of(camera) == Mode.AUTO_PAUSED

    camera.advance(at(1.3))
    assert mode_of(camera) == Mode.AUTO
    assert event_names(camera)[-2:] == ["mode_changed", "manual_override_ended"]


def test_each_new_motion_command_restarts_the_resume_countdown() -> None:
    camera = ptz_in_auto(resume_after_ms=1000)
    camera.move(at(0), MoveRequest(pan=0.5, ttl_ms=300), STUDIO)

    # A joystick still held resends the move; then a preset recall at 2 s.
    camera.move(at(1.0), MoveRequest(pan=0.5, ttl_ms=300), STUDIO)
    camera.store_preset(at(1.5), 1)
    camera.recall_preset(at(2.0), 1, STUDIO)
    camera.advance(at(2.9))
    assert mode_of(camera) == Mode.AUTO_PAUSED

    camera.advance(at(3.0))
    assert mode_of(camera) == Mode.AUTO


def test_a_stop_also_counts_as_a_manual_override() -> None:
    camera = ptz_in_auto()

    camera.stop(at(0), STUDIO)

    assert mode_of(camera) == Mode.AUTO_PAUSED


def test_put_mode_auto_ends_the_pause_at_once() -> None:
    camera = ptz_in_auto()
    camera.move(at(0), MoveRequest(pan=0.5), STUDIO)

    camera.set_mode(ModeRequest(mode=Mode.AUTO))

    assert mode_of(camera) == Mode.AUTO
    assert event_names(camera)[-1] == "manual_override_ended"


def test_hold_never_resumes_auto_by_itself() -> None:
    camera = ptz_in_auto(resume_after_ms=0)
    camera.set_mode(ModeRequest(mode=Mode.HOLD))

    camera.move(at(0), MoveRequest(pan=0.5), STUDIO)
    camera.advance(at(3600))

    assert mode_of(camera) == Mode.HOLD


def test_manual_ends_auto_and_the_tracker_goes_idle() -> None:
    camera = ptz_in_auto()

    camera.set_mode(ModeRequest(mode=Mode.MANUAL))
    camera.advance(at(3600))

    assert mode_of(camera) == Mode.MANUAL
    assert camera.state(at(3600)).tracker.state == TrackerStatus.IDLE


def test_auto_is_refused_while_the_tracker_is_not_ready() -> None:
    camera = SimulatedCamera("cam1", "Cam 1", CameraKind.PTZ)
    camera.set_tracker(FakeTracker.NOT_READY)

    with pytest.raises(CameraCommandError) as refused:
        camera.set_mode(ModeRequest(mode=Mode.AUTO))

    assert refused.value.code == ErrorCode.TRACKER_NOT_READY
    assert mode_of(camera) == Mode.MANUAL


def test_a_profile_with_a_bad_resume_after_ms_is_refused() -> None:
    camera = SimulatedCamera("cam1", "Cam 1", CameraKind.PTZ)

    with pytest.raises(CameraCommandError) as refused:
        camera.set_mode(ModeRequest(mode=Mode.AUTO, profile={"resume_after_ms": "soon"}))

    assert refused.value.code == ErrorCode.BAD_REQUEST


def test_the_tracker_reports_acquiring_and_losing_a_target_in_auto() -> None:
    camera = ptz_in_auto()

    camera.set_tracker(FakeTracker.TRACKING)
    camera.set_tracker(FakeTracker.READY)

    assert event_names(camera) == ["tracker_acquired", "tracker_lost"]


def test_moving_is_true_only_until_the_ttl_runs_out() -> None:
    camera = SimulatedCamera("cam1", "Cam 1", CameraKind.PTZ)
    camera.move(at(0), MoveRequest(tilt=1.0, ttl_ms=500), STUDIO)

    assert camera.state(at(0.4)).moving is True
    assert camera.state(at(0.5)).moving is False


def test_position_stops_at_the_pan_limit() -> None:
    camera = SimulatedCamera("cam1", "Cam 1", CameraKind.PTZ)

    # 100 degrees per second for 2 s would be 200 degrees; the limit is 170.
    camera.move(at(0), MoveRequest(pan=1.0, ttl_ms=2000), STUDIO)

    position = camera.position(at(5))
    assert position is not None and position.pan == 170.0


def test_commands_to_an_offline_device_are_refused_even_stop() -> None:
    camera = SimulatedCamera("cam1", "Cam 1", CameraKind.PTZ)
    camera.set_online(at(0), False)

    with pytest.raises(CameraCommandError) as refused:
        camera.stop(at(1), STUDIO)

    assert refused.value.code == ErrorCode.DEVICE_OFFLINE
    assert event_names(camera) == ["device_offline"]


def test_image_values_are_snapped_to_the_step_and_ignored_while_auto_is_on() -> None:
    camera = SimulatedCamera("cam1", "Cam 1", CameraKind.PTZ)

    # Focus moves in steps of 5, so 12 becomes 10, but only once auto is off.
    values = camera.set_image({"focus": ImageControlChange(value=12, auto=False)})

    assert values["focus"].value == 10
    assert values["focus"].auto is False


def test_an_unknown_image_control_is_refused_and_nothing_changes() -> None:
    camera = SimulatedCamera("cam1", "Cam 1", CameraKind.PTZ)

    with pytest.raises(CameraCommandError) as refused:
        camera.set_image(
            {"gain": ImageControlChange(value=50), "zoominess": ImageControlChange(value=1)}
        )

    assert refused.value.code == ErrorCode.UNKNOWN_CONTROL
    assert camera.image_values()["gain"].value == 0


def test_crop_is_only_for_crop_cameras_and_presets_only_for_ptz() -> None:
    ptz = SimulatedCamera("cam1", "Cam 1", CameraKind.PTZ)
    crop = SimulatedCamera("crop1", "Crop 1", CameraKind.CROP)

    with pytest.raises(CameraCommandError) as ptz_refused:
        ptz.set_crop(at(0), CropRect(x=0, y=0, w=0.5, h=0.5), STUDIO)
    with pytest.raises(CameraCommandError) as crop_refused:
        crop.store_preset(at(0), 1)

    assert ptz_refused.value.code == crop_refused.value.code == ErrorCode.NOT_SUPPORTED


def test_storing_and_renaming_presets_announces_the_change() -> None:
    camera = SimulatedCamera("cam1", "Cam 1", CameraKind.PTZ)

    camera.store_preset(at(0), 3)
    camera.label_preset(3, "Finish line")

    assert [preset.label for preset in camera.info().presets] == ["Finish line"]
    assert event_names(camera) == ["presets_changed", "presets_changed"]


def test_a_preset_number_above_preset_max_is_refused() -> None:
    camera = SimulatedCamera("cam1", "Cam 1", CameraKind.PTZ)
    preset_max = camera.info().limits.preset_max
    assert preset_max == PRESET_MAX

    with pytest.raises(CameraCommandError) as refused:
        camera.store_preset(at(0), PRESET_MAX + 1)

    assert refused.value.code == ErrorCode.BAD_REQUEST
    assert camera.store_preset(at(0), PRESET_MAX).n == PRESET_MAX


def test_cameras_without_presets_have_no_preset_max() -> None:
    crop = SimulatedCamera("crop1", "Crop 1", CameraKind.CROP)
    fixed = SimulatedCamera("fixed1", "Fixed 1", CameraKind.FIXED)

    assert crop.info().limits.preset_max is None
    assert fixed.info().limits.preset_max is None


# --- crop cameras move too ---------------------------------------------------------


def crop_camera() -> SimulatedCamera:
    """A crop camera with a 0.4 x 0.4 window in the middle of the frame."""
    camera = SimulatedCamera("crop1", "Crop 1", CameraKind.CROP)
    camera.set_crop(at(0), CropRect(x=0.3, y=0.3, w=0.4, h=0.4), STUDIO)
    return camera


def test_a_move_drives_a_crop_window_until_its_ttl_runs_out() -> None:
    camera = crop_camera()

    camera.move(at(0), MoveRequest(pan=1.0, ttl_ms=200), STUDIO)

    assert camera.state(at(0.1)).moving is True
    position = camera.state(at(5)).position
    assert position is not None
    # 0.2 s at the top speed, and no further after the TTL.
    assert position.pan == pytest.approx(0.5 + CROP_LIMITS.pan_speed_max * 0.2)
    assert camera.crop(at(5)).x == pytest.approx(0.3 + CROP_LIMITS.pan_speed_max * 0.2)


def test_a_crop_cameras_position_is_its_window_centre_and_zoom() -> None:
    camera = crop_camera()

    position = camera.position(at(0))

    assert position is not None
    assert (position.pan, position.tilt) == pytest.approx((0.5, 0.5))
    assert camera.info().has_position_feedback is True


def test_a_move_on_a_crop_camera_in_auto_pauses_auto() -> None:
    camera = crop_camera()
    camera.set_mode(ModeRequest(mode=Mode.AUTO))

    reply = camera.move(at(1), MoveRequest(zoom=0.5), STUDIO)

    assert reply.mode == Mode.AUTO_PAUSED


def test_put_crop_is_a_studio_motion_and_starts_the_resume_countdown_at_once() -> None:
    camera = crop_camera()
    camera.set_mode(ModeRequest(mode=Mode.AUTO, profile={"resume_after_ms": 1000}))

    camera.set_crop(at(2), CropRect(x=0, y=0, w=0.5, h=0.5), STUDIO)
    assert mode_of(camera) == Mode.AUTO_PAUSED
    camera.advance(at(2.9))
    assert mode_of(camera) == Mode.AUTO_PAUSED

    camera.advance(at(3.0))
    assert mode_of(camera) == Mode.AUTO


def test_a_fixed_camera_does_not_move() -> None:
    camera = SimulatedCamera("fixed1", "Fixed 1", CameraKind.FIXED)

    with pytest.raises(CameraCommandError) as refused:
        camera.move(at(0), MoveRequest(pan=0.5), STUDIO)

    assert refused.value.code == ErrorCode.NOT_SUPPORTED


# --- restarts ------------------------------------------------------------------------


def test_a_restart_restores_auto_and_a_pause_does_not_survive_it() -> None:
    camera = ptz_in_auto()
    camera.move(at(0), MoveRequest(pan=0.5), STUDIO)  # now auto_paused

    camera.restart(at(1))

    assert mode_of(camera) == Mode.AUTO
    assert camera.restore_notice is None
    assert camera.state(at(1)).moving is False


def test_a_restart_without_a_tracker_restores_manual_and_keeps_a_notice() -> None:
    camera = ptz_in_auto()
    camera.set_tracker(FakeTracker.NOT_READY)

    camera.restart(at(1))

    assert mode_of(camera) == Mode.MANUAL
    assert camera.restore_notice == (
        "t+1",
        {"from": Mode.AUTO, "to": Mode.MANUAL, "reason": "tracker_not_ready"},
    )


def test_the_restore_notice_is_dropped_once_the_mode_changes_again() -> None:
    camera = ptz_in_auto()
    camera.set_tracker(FakeTracker.NOT_READY)
    camera.restart(at(1))

    camera.set_mode(ModeRequest(mode=Mode.HOLD))

    assert camera.restore_notice is None


def test_the_restore_notice_is_dropped_when_a_studio_sets_the_same_mode() -> None:
    camera = ptz_in_auto()
    camera.set_tracker(FakeTracker.NOT_READY)
    camera.restart(at(1))

    camera.set_mode(ModeRequest(mode=Mode.MANUAL))

    assert camera.restore_notice is None


def test_a_restart_keeps_manual_and_hold() -> None:
    for mode in (Mode.MANUAL, Mode.HOLD):
        camera = SimulatedCamera("cam1", "Cam 1", CameraKind.PTZ)
        camera.set_mode(ModeRequest(mode=mode))

        camera.restart(at(1))

        assert mode_of(camera) == mode
