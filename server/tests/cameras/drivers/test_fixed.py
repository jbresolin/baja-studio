"""Tests for the `none` driver (cameras/drivers/fixed.py): a camera with no motion of its own.

A fixed S600 lives in the registry for its image settings and preview; it
must not pretend to be a PTZ, and its status follows its USB device.
"""

import pytest

from baja_studio.core.module import CommandError
from baja_studio.core.supervisor import HealthState
from baja_studio.fakes.fake_duvc import S600_A_PATH
from tests.imaging.uvc_harness import s600_camera
from tests.modules.camera_studio import Studio


def test_a_fixed_usb_camera_is_ok_while_plugged_in_and_offline_while_not(
    studio: Studio,
) -> None:
    studio.configure(cameras=[s600_camera()])
    studio.soon(lambda: studio.status("s600-a") == "ok")
    assert studio.camera("s600-a")["capabilities"]["kind"] == "fixed"

    studio.usb.unplug(S600_A_PATH)

    studio.soon(lambda: studio.status("s600-a") == "offline")
    # The home card names it, like any other device.
    assert "s600-a offline" in studio.health().message


def test_motion_and_presets_are_refused_on_a_camera_that_cannot_move(studio: Studio) -> None:
    studio.configure(cameras=[s600_camera()])
    studio.soon(lambda: studio.status("s600-a") == "ok")

    with pytest.raises(CommandError, match="has no motion of its own"):
        studio.command("move", camera="s600-a", pan=0.5)
    with pytest.raises(CommandError, match="has no motion of its own"):
        studio.command("nudge", camera="s600-a")
    with pytest.raises(CommandError, match="has no presets"):
        studio.command("recall_preset", camera="s600-a", n=0)


def test_a_fixed_camera_without_a_usb_device_is_simply_ok(studio: Studio) -> None:
    studio.configure(cameras=[{"id": "wide", "driver": "none"}])

    studio.soon(lambda: studio.status("wide") == "ok")
    assert studio.health().state == HealthState.OK
