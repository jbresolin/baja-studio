"""Tests for the `uvc` worker (imaging/uvc_link.py) through the running cameras module.

The worker runs the UVC child (here in-process, tests/imaging/uvc_harness.py),
keeps the list of USB cameras and restarts the child when it dies.
"""

from typing import Any

from baja_studio.core.supervisor import HealthState
from baja_studio.fakes.fake_duvc import S600_B_PATH
from tests.imaging.uvc_harness import image_card, s600_camera
from tests.modules.camera_studio import Studio


def usb_state(studio: Studio) -> Any:
    return studio.services.state.get("cameras", "usb")


def test_the_device_list_follows_plugging_and_unplugging(studio: Studio) -> None:
    studio.soon(lambda: usb_state(studio)["status"] == "ok")
    assert len(usb_state(studio)["devices"]) == 3

    studio.usb.unplug(S600_B_PATH)

    studio.soon(lambda: len(usb_state(studio)["devices"]) == 2)
    assert S600_B_PATH not in [device["path"] for device in usb_state(studio)["devices"]]


def test_a_crashed_child_is_restarted_and_the_list_comes_back(studio: Studio) -> None:
    studio.soon(lambda: usb_state(studio)["status"] == "ok")

    studio.usb.host.crash()

    # While it is down nothing is listed; the supervisor starts a new child.
    studio.soon(lambda: len(studio.usb.hosts) == 2, within_seconds=5)
    studio.soon(
        lambda: usb_state(studio)["status"] == "ok" and len(usb_state(studio)["devices"]) == 3
    )


def test_without_uvc_on_this_pc_the_list_says_why_and_only_usb_users_see_it(
    studio: Studio,
) -> None:
    # The next child is the one used off Windows (or without duvc-ctl).
    studio.soon(lambda: usb_state(studio)["status"] == "ok")
    studio.usb.unavailable_reason = "USB camera control is not available here: no duvc_ctl"
    studio.usb.host.crash()

    studio.soon(lambda: usb_state(studio)["status"] == "unavailable", within_seconds=5)
    assert usb_state(studio)["message"].endswith("no duvc_ctl")
    # With no camera using USB, the module stays ok...
    assert studio.health().state == HealthState.OK

    # ...and a USB camera's card and the home card then say why.
    studio.configure(cameras=[s600_camera()])
    studio.soon(lambda: image_card(studio, "s600-a").get("status") == "offline")
    assert image_card(studio, "s600-a")["status_reason"].endswith("no duvc_ctl")
    studio.soon(lambda: "usb offline" in studio.health().message)


def test_a_usb_camera_shows_the_child_on_the_home_card(studio: Studio) -> None:
    studio.configure(cameras=[s600_camera()])

    studio.soon(lambda: image_card(studio, "s600-a").get("status") == "ok")
    assert "usb ok (3 USB cameras)" in studio.health().message
