"""Tests for the cameras module's commands (modules/cameras/commands.py), end to end.

These are the commands the camera pages (WP3) use: the Cameras page sends
`move`/`stop` as a browser source (`web:<client id>`), the configuration page
lists serial ports, nudges a camera, renames presets and reads raw frames.
Each test runs the real service on the fake clock with fake cameras, a fake
joystick and fake agents around it (the `studio` fixture,
tests/modules/camera_studio.py).
"""

import pytest

from baja_studio.cameras.inputs.serial_ports import SerialPortInfo
from baja_studio.core.module import CommandError
from baja_studio.fakes.fake_serial import SNIFFED_FRAMES
from baja_studio.fakes.fake_visca_camera import FakeViscaCamera
from tests.conftest import eventually
from tests.fake_agents import StartAgent, advance_in_steps
from tests.modules.camera_studio import SMTAV_HOST, Studio, add_agent, agent_camera, smtav

WEB, OTHER_WEB = "web:desk-pc", "web:phone"


def start_smtav(studio: Studio, **overrides: object) -> FakeViscaCamera:
    """One direct camera, answering, as most tests need."""
    camera = studio.network.add(SMTAV_HOST)
    studio.configure(cameras=[smtav(**overrides)])
    eventually(lambda: studio.status("smtav") == "ok")
    return camera


# --- the browser as one more source --------------------------------------------------------


def test_a_browser_holding_a_key_moves_the_camera_and_its_stream_is_logged_once(
    studio: Studio,
) -> None:
    camera = studio.network.add(SMTAV_HOST)
    studio.configure(cameras=[smtav()])
    eventually(lambda: studio.status("smtav") == "ok")

    # The page sends the same move ten times a second while the key is held.
    for _ in range(10):
        studio.command("move", source=WEB, camera="smtav", pan=0.5, ttl_ms=300)
        advance_in_steps(studio.clock, 0.1)
    eventually(lambda: camera.moving)

    # The log keeps the start of the move, not its ten repeats.
    assert [entry[0] for entry in studio.log(WEB)] == ["move"]


def test_a_browser_refused_while_another_holds_is_logged_once_per_run_of_refusals(
    studio: Studio,
) -> None:
    start_smtav(studio)
    studio.command("move", source=WEB, camera="smtav", pan=0.5, ttl_ms=300)

    # The phone tries to move the same camera, three times in a row.
    for _ in range(3):
        with pytest.raises(CommandError, match="being moved by web:desk-pc"):
            studio.command("move", source=OTHER_WEB, camera="smtav", tilt=0.5, ttl_ms=300)

    assert [(command, result[:5]) for command, result, _ in studio.log(OTHER_WEB)] == [
        ("move", "error")
    ]


def test_letting_go_after_a_refused_move_does_not_stop_the_source_that_owns_the_camera(
    studio: Studio,
) -> None:
    # Bench step 6: one operator holds, the other tries and lets go.
    camera = start_smtav(studio)
    studio.command("move", source=WEB, camera="smtav", pan=0.5, ttl_ms=300)
    eventually(lambda: camera.moving)
    with pytest.raises(CommandError):
        studio.command("move", source=OTHER_WEB, camera="smtav", tilt=0.5, ttl_ms=300)

    # The phone's page sends a *release* stop: it applies only to its own move.
    reply = studio.command("stop", source=OTHER_WEB, camera="smtav", release=True)

    assert reply == {"stopped": False}
    assert studio.camera("smtav")["control"]["velocity_owner"] == WEB
    assert camera.moving
    assert studio.log(OTHER_WEB)[0][0] == "move"  # the release that did nothing is not logged


def test_the_stop_button_stops_a_camera_another_source_moves(studio: Studio) -> None:
    camera = start_smtav(studio)
    studio.command("move", source=WEB, camera="smtav", pan=0.5, ttl_ms=300)
    eventually(lambda: camera.moving)

    reply = studio.command("stop", source=OTHER_WEB, camera="smtav")

    assert reply == {"stopped": True}
    eventually(lambda: not camera.moving)


def test_a_joystick_move_is_refused_while_the_web_holds_and_wins_after_the_web_lets_go(
    studio: Studio,
) -> None:
    # Bench step 7, first half: web holds, joystick moves.
    camera = studio.network.add(SMTAV_HOST)
    port = studio.ports.add("COM8")
    studio.configure(cameras=[smtav()], controllers=[{"id": "desk", "port": "COM8"}])
    eventually(lambda: studio.status("smtav") == "ok" and port.is_open)
    studio.command("move", source=WEB, camera="smtav", tilt=0.5, ttl_ms=300)

    port.feed(SNIFFED_FRAMES["pan_left_full"])
    eventually(lambda: any(r.startswith("error") for _, r, _ in studio.log("joystick:desk")))
    assert studio.camera("smtav")["control"]["velocity_owner"] == WEB

    # The web lets go; the next stick movement takes over.
    studio.command("stop", source=WEB, camera="smtav", release=True)
    port.feed(SNIFFED_FRAMES["pan_right_full"])

    eventually(lambda: camera.pan.rate > 0)
    assert studio.camera("smtav")["control"]["velocity_owner"] == "joystick:desk"


def test_the_web_stop_button_stops_a_camera_the_joystick_latched(studio: Studio) -> None:
    # Bench step 7, second half: joystick holds, web Stop.
    camera = studio.network.add(SMTAV_HOST)
    port = studio.ports.add("COM8")
    studio.configure(cameras=[smtav()], controllers=[{"id": "desk", "port": "COM8"}])
    eventually(lambda: studio.status("smtav") == "ok" and port.is_open)
    port.feed(SNIFFED_FRAMES["pan_right_full"])
    eventually(lambda: camera.moving)

    # A web *move* is refused (the joystick latched the camera)...
    with pytest.raises(CommandError, match="joystick:desk"):
        studio.command("move", source=WEB, camera="smtav", pan=-0.5, ttl_ms=300)
    # ...but its Stop button is always obeyed.
    studio.command("stop", source=WEB, camera="smtav")

    eventually(lambda: not camera.moving)
    assert studio.camera("smtav")["control"]["velocity_owner"] is None


# --- configuration helpers ------------------------------------------------------------------


def test_serial_ports_are_listed_with_what_they_are_and_who_uses_them(studio: Studio) -> None:
    studio.serial_ports.extend(
        [
            SerialPortInfo("COM3", "Intel(R) Active Management", "PCI\\VEN_8086"),
            SerialPortInfo("COM8", "Silicon Labs CP210x", "USB VID:PID=10C4:EA60 SER=0001"),
        ]
    )
    studio.configure(controllers=[{"id": "desk", "port": "COM8"}])

    reply = studio.command("list_serial_ports")

    assert reply["ports"] == [
        {
            "device": "COM3",
            "description": "Intel(R) Active Management",
            "hwid": "PCI\\VEN_8086",
            "used_by": None,
        },
        {
            "device": "COM8",
            "description": "Silicon Labs CP210x",
            "hwid": "USB VID:PID=10C4:EA60 SER=0001",
            "used_by": "desk",
        },
    ]
    assert studio.log("api:") == []  # a read, not an action: not in the command log


def test_nudge_pans_right_slowly_and_stops_by_itself(studio: Studio) -> None:
    camera = start_smtav(studio)

    studio.command("nudge", camera="smtav")
    eventually(lambda: camera.pan.rate > 0)
    advance_in_steps(studio.clock, 0.5)

    eventually(lambda: not camera.moving)


def test_nudge_goes_through_the_cameras_shaping_so_it_shows_an_inverted_pan(
    studio: Studio,
) -> None:
    camera = start_smtav(studio, limits={"invert_pan": True})

    studio.command("nudge", camera="smtav")

    eventually(lambda: camera.pan.rate < 0)


def test_a_direct_cameras_preset_is_renamed_in_its_settings(studio: Studio) -> None:
    start_smtav(studio, presets=[{"n": 0, "label": "Start"}])

    studio.command("rename_preset", camera="smtav", n=2, label="Jump")
    studio.command("rename_preset", camera="smtav", n=0, label="")  # an empty name removes it

    saved = studio.services.settings.get("cameras").model_dump()["cameras"][0]
    assert saved["presets"] == [{"n": 2, "label": "Jump"}]
    eventually(lambda: studio.camera("smtav")["presets"] == [{"n": 2, "label": "Jump"}])


def test_a_preset_number_the_camera_does_not_have_cannot_be_renamed(studio: Studio) -> None:
    start_smtav(studio)

    with pytest.raises(CommandError, match="from 0 to 127"):
        studio.command("rename_preset", camera="smtav", n=200, label="Far")


def test_an_agent_cameras_preset_is_renamed_on_the_agent_and_shown(
    studio: Studio, start_agent: StartAgent
) -> None:
    start_agent(ptz_cameras=1)
    add_agent(studio)
    studio.configure(cameras=[agent_camera()])
    studio.soon(lambda: studio.status("trackside") == "ok")
    studio.command("store_preset", camera="trackside", n=3)
    # Wait for the stored preset to show (fake time passes meanwhile, so the
    # agent's next presets_changed event has a later timestamp and is not
    # taken for a replay of this one).
    studio.soon(lambda: [p["n"] for p in studio.camera("trackside")["presets"]] == [3])

    reply = studio.command("rename_preset", camera="trackside", n=3, label="Jump")

    assert reply == {"n": 3, "label": "Jump"}
    # The agent says its presets changed; the studio reads them again and shows them.
    studio.soon(lambda: {"n": 3, "label": "Jump"} in studio.camera("trackside")["presets"])


def test_raw_frames_are_the_controllers_last_frames_in_hex(studio: Studio) -> None:
    port = studio.ports.add("COM8")
    studio.configure(controllers=[{"id": "desk", "port": "COM8"}])
    eventually(lambda: port.is_open)

    port.feed(SNIFFED_FRAMES["stop"] + SNIFFED_FRAMES["zoom_in"])

    def frames() -> list[str]:
        reply = studio.command("raw_frames", controller="desk")
        return list(reply["frames"])

    eventually(lambda: len(frames()) == 2)
    assert frames() == [SNIFFED_FRAMES["stop"].hex(" ").upper(), "FF 01 00 40 00 00 41"]
    with pytest.raises(CommandError, match="no enabled controller called 'nope'"):
        studio.command("raw_frames", controller="nope")
