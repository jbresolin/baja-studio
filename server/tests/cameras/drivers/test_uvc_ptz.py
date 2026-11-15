"""Tests for the Pixy driver (cameras/drivers/uvc_ptz.py) through the running cameras module.

The Pixy is a fake USB device behind an in-process UVC child
(tests/imaging/uvc_harness.py). Moves are sent as the web page sends them,
through the arbiter, and fake time passes in 0.05 s steps (the driver's
20 Hz). The checks read what reached the fake Pixy (`library.writes`), the
camera's state, and the saved settings.
"""

import pytest

from baja_studio.cameras.config import CamerasSettings
from baja_studio.core.module import CommandError
from baja_studio.fakes.fake_duvc import PIXY_PATH
from tests.conftest import eventually, step_fake_time
from tests.imaging.uvc_harness import pixy_camera
from tests.modules.camera_studio import CAMERA_CLOCK_WORKERS, Studio


def pixy_ready(studio: Studio, **overrides: object) -> None:
    """Configure the Pixy and wait until its driver has read its ranges."""
    studio.configure(cameras=[pixy_camera(**overrides)])
    studio.soon(lambda: studio.status("pixy") == "ok")


def pan_writes(studio: Studio) -> list[int]:
    """Every pan value written to the Pixy so far, in order."""
    return [value for path, prop, value, _ in studio.usb.library.writes if prop == "Pan"]


def pass_time(studio: Studio, seconds: float) -> None:
    step_fake_time(studio.clock, seconds, CAMERA_CLOCK_WORKERS, step=0.05)


def move(studio: Studio, source: str = "api:test", **args: object) -> None:
    """Send a move and wait until the driver's worker has taken it up.

    The command only wakes the worker (another thread); moving fake time on
    before it has looked would let the whole move pass while it still sleeps.
    """
    studio.command("move", source=source, camera="pixy", **args)
    eventually(lambda: studio.camera("pixy")["moving"] is True)


def test_the_pixy_is_a_ptz_whose_position_is_only_what_it_was_told(studio: Studio) -> None:
    pixy_ready(studio)

    camera = studio.camera("pixy")

    # It cannot read its real position back, so it says so twice over.
    assert camera["capabilities"]["has_position_feedback"] is False
    assert camera["capabilities"]["preset_max"] == 127
    assert camera["position_is_commanded"] is True
    assert camera["position"] == {"pan": 0, "tilt": 0, "zoom": 0}


def test_a_held_velocity_becomes_absolute_positions_about_twenty_times_a_second(
    studio: Studio,
) -> None:
    pixy_ready(studio)

    # Full pan right for 0.3 s: the driver integrates 60 degrees/s.
    move(studio, source="web:alice", pan=1.0, ttl_ms=300)
    pass_time(studio, 0.5)

    writes = pan_writes(studio)
    # Six 0.05 s steps of 3 degrees, then nothing once the ttl ran out.
    assert writes == [3, 6, 9, 12, 15, 18]
    assert studio.camera("pixy")["position"]["pan"] == 18
    assert studio.camera("pixy")["moving"] is False


def test_both_axes_of_a_diagonal_move_reach_the_camera(studio: Studio) -> None:
    pixy_ready(studio)

    move(studio, pan=1.0, tilt=1.0, ttl_ms=100)
    pass_time(studio, 0.3)

    tilts = [value for _, prop, value, _ in studio.usb.library.writes if prop == "Tilt"]
    assert pan_writes(studio)[-1] == 6
    assert tilts[-1] == 4  # 45 degrees/s for 0.1 s, rounded to a whole step


def test_the_position_stops_at_the_end_of_the_range(studio: Studio) -> None:
    pixy_ready(studio)

    # Two moves of 2 s (the longest ttl) would take pan to 240 degrees.
    for _ in range(2):
        move(studio, pan=1.0, ttl_ms=2000)
        pass_time(studio, 2.0)

    assert max(pan_writes(studio)) == 150  # the Pixy's range, read from the device


def test_stop_ends_the_motion_and_sends_nothing(studio: Studio) -> None:
    pixy_ready(studio)
    move(studio, pan=1.0, ttl_ms=1000)
    pass_time(studio, 0.1)
    writes_before_stop = len(studio.usb.library.writes)

    studio.command("stop", camera="pixy")
    eventually(lambda: studio.camera("pixy")["moving"] is False)
    pass_time(studio, 0.5)

    # No stop packet exists for UVC: the camera holds the last position written.
    assert len(studio.usb.library.writes) == writes_before_stop
    assert studio.camera("pixy")["moving"] is False


def test_presets_keep_pan_tilt_and_zoom_in_the_settings(studio: Studio) -> None:
    pixy_ready(studio)
    move(studio, pan=1.0, zoom=1.0, ttl_ms=200)
    pass_time(studio, 0.3)
    studio.command("store_preset", camera="pixy", n=3)

    settings = studio.services.settings.get("cameras")
    assert isinstance(settings, CamerasSettings)
    stored = settings.cameras[0].stored_positions
    assert [(p.n, p.pan, p.tilt, p.zoom) for p in stored] == [(3, 12, 0, 105)]

    # Somewhere else, then back to preset 3: all three axes jump back.
    move(studio, pan=-1.0, zoom=-1.0, ttl_ms=200)
    pass_time(studio, 0.3)
    studio.command("recall_preset", camera="pixy", n=3)

    eventually(lambda: studio.camera("pixy")["position"] == {"pan": 12, "tilt": 0, "zoom": 0.1})


def test_a_preset_that_was_never_stored_is_refused(studio: Studio) -> None:
    pixy_ready(studio)

    with pytest.raises(CommandError, match="preset 6 is not stored"):
        studio.command("recall_preset", camera="pixy", n=5)


def test_the_arbiter_rules_apply_one_owner_and_shaping(studio: Studio) -> None:
    # Pan is inverted in the camera's limits, and alice holds the camera.
    pixy_ready(studio, limits={"invert_pan": True})
    move(studio, source="web:alice", pan=1.0, ttl_ms=300)

    with pytest.raises(CommandError, match="being moved by web:alice"):
        studio.command("move", source="web:bob", camera="pixy", pan=1.0)
    pass_time(studio, 0.4)

    assert pan_writes(studio)[-1] == -18


def test_moving_the_pixy_by_hand_does_not_change_its_published_position(
    studio: Studio,
) -> None:
    pixy_ready(studio)
    move(studio, pan=1.0, ttl_ms=100)
    pass_time(studio, 0.2)

    studio.command("simulate_usb", action="hand_move", path=PIXY_PATH, pan=-120)
    pass_time(studio, 0.6)

    # UVC readback is the last value written; the studio cannot know better.
    assert studio.camera("pixy")["position"]["pan"] == 6


def test_an_unplugged_pixy_is_offline_and_comes_back_when_replugged(studio: Studio) -> None:
    pixy_ready(studio)

    studio.usb.unplug(PIXY_PATH)
    studio.soon(lambda: studio.status("pixy") == "offline")
    assert "not plugged in" in studio.camera("pixy")["status_reason"]

    studio.usb.replug(PIXY_PATH)
    studio.soon(lambda: studio.status("pixy") == "ok")
