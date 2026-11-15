"""Tests for reapplying saved image settings by themselves (imaging/watch.py).

Saved settings come back without anyone touching the page when: a USB camera
is unplugged and plugged back in, the UVC child process crashes and is
restarted, the service starts, or an agent link comes back. The fake devices
"forget" their settings the way a webcam does when it loses power: the test
puts the fake control back on its default, as the camera would.
"""

from baja_studio.agents.contract import AGENT_PORT
from baja_studio.fakes.fake_duvc import S600_A_PATH, S600_B_PATH, CamMode
from baja_studio.fakes.in_process import InProcessTransport
from tests.fake_agents import (
    AGENT_HOST,
    DELIVERY_SLACK_SECONDS,
    StartAgent,
    advance_in_steps,
)
from tests.imaging.uvc_harness import image_card, s600_camera, saved_image
from tests.modules.camera_studio import Studio, add_agent, agent_camera


def power_loss(studio: Studio, path: str) -> None:
    """What a webcam does without power: exposure back on auto at its default."""
    exposure = studio.usb.control(path, "Exposure")
    exposure.mode, exposure.value = CamMode.Auto, exposure.range.default_val


def exposure_is(studio: Studio, path: str, value: int) -> bool:
    exposure = studio.usb.control(path, "Exposure")
    return bool(exposure.mode == CamMode.Manual and exposure.value == value)


def test_settings_come_back_when_a_camera_is_unplugged_and_plugged_in_again(
    studio: Studio,
) -> None:
    studio.configure(cameras=[s600_camera()])
    studio.soon(lambda: image_card(studio, "s600-a").get("status") == "ok")
    studio.command(
        "set_image", camera="s600-a", controls={"exposure": {"auto": False, "value": -9}}
    )

    # Unplugged: the card says so (and keeps its last controls).
    studio.usb.unplug(S600_A_PATH)
    studio.soon(lambda: image_card(studio, "s600-a")["status"] == "offline")
    power_loss(studio, S600_A_PATH)

    # Plugged in again: within a tick the saved exposure is back on the device.
    studio.usb.replug(S600_A_PATH)
    studio.soon(lambda: exposure_is(studio, S600_A_PATH, -9))
    assert image_card(studio, "s600-a")["status"] == "ok"


def test_settings_come_back_when_the_uvc_child_crashes_and_restarts(studio: Studio) -> None:
    studio.configure(cameras=[s600_camera()])
    studio.soon(lambda: image_card(studio, "s600-a").get("status") == "ok")
    studio.command(
        "set_image", camera="s600-a", controls={"exposure": {"auto": False, "value": -4}}
    )
    power_loss(studio, S600_A_PATH)  # say the crash took the camera's settings too

    studio.usb.host.crash()

    # The supervisor starts a new child after its back-off; the new child's
    # start reapplies every USB camera's saved settings.
    studio.soon(lambda: len(studio.usb.hosts) == 2, within_seconds=5)
    studio.soon(lambda: exposure_is(studio, S600_A_PATH, -4))


def test_settings_saved_before_a_start_are_applied_when_the_service_starts(
    studio: Studio,
) -> None:
    # As after a reboot of the studio PC: the settings are there first.
    studio.configure(
        cameras=[
            s600_camera(image={"exposure": {"value": -11, "auto": False}, "gain": {"value": 7}})
        ]
    )

    studio.soon(lambda: exposure_is(studio, S600_A_PATH, -11))
    assert studio.usb.control(S600_A_PATH, "Gain").value == 7


def test_a_saved_value_the_device_no_longer_takes_is_shown_not_dropped(studio: Studio) -> None:
    studio.usb.control(S600_A_PATH, "Gain").range.max = 10

    studio.configure(cameras=[s600_camera(image={"gain": {"value": 50}})])

    studio.soon(lambda: image_card(studio, "s600-a").get("status") == "ok")
    assert image_card(studio, "s600-a")["controls"]["gain"]["error"] == "50 is outside 0..10"
    assert saved_image(studio, "s600-a") == {"gain": {"value": 50, "auto": None}}


def test_two_cameras_with_one_name_each_get_their_own_settings_back(studio: Studio) -> None:
    studio.configure(
        cameras=[
            s600_camera("s600-a", S600_A_PATH, image={"exposure": {"value": -3, "auto": False}}),
            s600_camera(
                "s600-b", S600_B_PATH, 1, image={"exposure": {"value": -12, "auto": False}}
            ),
        ]
    )

    studio.soon(lambda: exposure_is(studio, S600_A_PATH, -3))
    studio.soon(lambda: exposure_is(studio, S600_B_PATH, -12))


def test_the_usb_list_names_the_camera_using_each_device(studio: Studio) -> None:
    studio.configure(cameras=[s600_camera("s600-b", S600_B_PATH, 1)])

    studio.soon(lambda: image_card(studio, "s600-b").get("status") == "ok")
    usb = studio.services.state.get("cameras", "usb")

    assert usb["status"] == "ok"
    assert [device["used_by"] for device in usb["devices"]] == [None, "s600-b", None]


def test_a_camera_removed_from_the_settings_loses_its_card(studio: Studio) -> None:
    studio.configure(cameras=[s600_camera()])
    studio.soon(lambda: image_card(studio, "s600-a").get("status") == "ok")

    studio.configure(cameras=[])

    studio.soon(lambda: image_card(studio, "s600-a") == {})


def test_an_agent_camera_gets_its_settings_back_when_its_agent_returns(
    studio: Studio, start_agent: StartAgent, transport: InProcessTransport
) -> None:
    runner = start_agent(ptz_cameras=1)
    add_agent(studio)
    studio.configure(cameras=[agent_camera()])
    studio.soon(lambda: image_card(studio, "trackside").get("status") == "ok")
    studio.command("set_image", camera="trackside", controls={"gain": {"value": 40}})

    # The agent's laptop restarts: its camera comes back with gain 0.
    transport.unplug(AGENT_HOST, AGENT_PORT)
    advance_in_steps(studio.clock, 5 + DELIVERY_SLACK_SECONDS)
    studio.soon(lambda: image_card(studio, "trackside")["status"] == "offline")
    runner = start_agent(ptz_cameras=1)
    advance_in_steps(studio.clock, 4)

    def agent_gain() -> float:
        return float(runner.run(lambda: runner.agent.cameras["cam1"].image["gain"].value))

    studio.soon(lambda: agent_gain() == 40, within_seconds=5)
    assert image_card(studio, "trackside")["status"] == "ok"
