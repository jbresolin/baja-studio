"""Tests for the cameras module's health summary (cameras/health.py)."""

from baja_studio.cameras.health import device_of, summarise
from baja_studio.core.supervisor import Health, HealthState

OK = Health(HealthState.OK)
AWAY = Health(HealthState.OFFLINE, "no answer")


def test_worker_names_are_mapped_to_their_device() -> None:
    assert device_of("camera:smtav:visca") == "smtav"
    assert device_of("camera:trackside:agent") == "trackside"
    assert device_of("controller:desk") == "desk"
    # The fallback is reported through its agent worker, and motion is no device.
    assert device_of("camera:trackside:fallback") is None
    assert device_of("motion") is None


def test_every_device_ok_is_ok_and_lists_them() -> None:
    health = summarise({"motion": OK, "camera:smtav:visca": OK, "controller:desk": OK})

    assert health == Health(HealthState.OK, "smtav ok · desk ok")


def test_some_devices_away_is_degraded_with_the_reason() -> None:
    health = summarise({"motion": OK, "camera:smtav:visca": OK, "controller:desk": AWAY})

    assert health == Health(HealthState.DEGRADED, "smtav ok · desk offline (no answer)")


def test_every_device_away_is_offline() -> None:
    health = summarise({"motion": OK, "camera:smtav:visca": AWAY})

    assert health.state == HealthState.OFFLINE


def test_a_failed_motion_loop_is_degraded_whatever_the_devices_say() -> None:
    health = summarise(
        {"motion": Health(HealthState.DEGRADED, "RuntimeError: boom"), "camera:a:visca": OK}
    )

    assert health.state == HealthState.DEGRADED
    assert health.message.startswith("motion loop: RuntimeError: boom")


def test_nothing_configured_is_ok_and_says_so() -> None:
    assert summarise({"motion": OK}) == Health(HealthState.OK, "no cameras or controllers enabled")
