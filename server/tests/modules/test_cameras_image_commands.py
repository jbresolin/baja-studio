"""Tests for the cameras module's image commands (modules/cameras/image_commands.py).

What the Image page and the Camera setup page send: the USB device list,
the argument checks, the fake's switches, and which writes the command log
keeps (a slider drag must not flood it).
"""

import pytest

from baja_studio.core.module import CommandError
from baja_studio.fakes.fake_duvc import PIXY_PATH, S600_A_PATH, S600_NAME
from tests.fake_agents import advance_in_steps
from tests.imaging.uvc_harness import image_card, s600_camera
from tests.modules.camera_studio import Studio


def s600_ready(studio: Studio) -> None:
    studio.configure(cameras=[s600_camera()])
    studio.soon(lambda: image_card(studio, "s600-a").get("status") == "ok")


def image_log(studio: Studio) -> list[tuple[str, str]]:
    """(command, result) of the image commands in the log, oldest first."""
    return [
        (command, result)
        for command, result, _ in reversed(studio.log("web:"))
        if command.endswith("_image")
    ]


def test_the_device_list_names_each_device_and_who_uses_it(studio: Studio) -> None:
    s600_ready(studio)

    answer = studio.command("list_usb_devices")

    assert answer["status"] == "ok"
    assert answer["devices"][0] == {
        "name": S600_NAME,
        "path": S600_A_PATH,
        "index": 0,
        "used_by": "s600-a",
    }
    assert answer["devices"][2]["path"] == PIXY_PATH
    assert answer["devices"][2]["used_by"] is None


def test_the_device_list_looks_again_when_asked(studio: Studio) -> None:
    # The page's Refresh button: the list is fresh without waiting for the 2 s look.
    s600_ready(studio)
    studio.usb.library.device(PIXY_PATH).plugged = False

    answer = studio.command("list_usb_devices")

    assert PIXY_PATH not in [device["path"] for device in answer["devices"]]


def test_a_slider_drag_logs_only_the_start_of_its_run_of_writes(studio: Studio) -> None:
    s600_ready(studio)

    # Ten steps of one drag, a tenth of a second apart: one log line.
    for value in range(40, 50):
        studio.command(
            "set_image", source="web:alice", camera="s600-a", controls={"gain": {"value": value}}
        )
        advance_in_steps(studio.clock, 0.1)
    # Another control is a new run, and so is the same one after a pause.
    studio.command(
        "set_image", source="web:alice", camera="s600-a", controls={"contrast": {"value": 60}}
    )
    advance_in_steps(studio.clock, 2.0)
    studio.command(
        "set_image", source="web:alice", camera="s600-a", controls={"contrast": {"value": 61}}
    )

    assert image_log(studio) == [("set_image", "ok"), ("set_image", "ok"), ("set_image", "ok")]


def test_actions_other_than_writes_are_always_logged_and_reads_never(studio: Studio) -> None:
    s600_ready(studio)

    studio.command("get_image", source="web:alice", camera="s600-a")
    studio.command("lock_image", source="web:alice", camera="s600-a")
    studio.command("reset_image", source="web:alice", camera="s600-a")

    assert image_log(studio) == [("lock_image", "ok"), ("reset_image", "ok")]


def test_badly_formed_arguments_are_refused_with_an_example(studio: Studio) -> None:
    s600_ready(studio)

    with pytest.raises(CommandError, match="controls must be an object"):
        studio.command("set_image", camera="s600-a", controls=[])
    with pytest.raises(CommandError, match="gain: give a number as value"):
        studio.command("set_image", camera="s600-a", controls={"gain": {"value": "loud"}})
    with pytest.raises(CommandError, match="camera must be a camera id"):
        studio.command("get_image")
    with pytest.raises(CommandError, match="from must be a camera id"):
        studio.command("copy_image", to="s600-a")


def test_the_fakes_switches_unplug_a_camera(studio: Studio) -> None:
    # What the rehearsal rig's scenarios use.
    s600_ready(studio)

    studio.command("simulate_usb", action="unplug", path=S600_A_PATH)
    studio.command("list_usb_devices")  # look now

    studio.soon(lambda: image_card(studio, "s600-a")["status"] == "offline")
    with pytest.raises(CommandError, match="action must be one of"):
        studio.command("simulate_usb", action="explode")
