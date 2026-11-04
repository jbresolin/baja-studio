"""Tests for the cameras module (modules/cameras and cameras/registry.py), end to end.

Each test runs the service with the agents and cameras modules on the fake
clock (the `studio` fixture, tests/modules/camera_studio.py). Settings are
changed the way the configuration page changes them, and the checks read the
module's state (what the browser sees), the fakes, and the command log. The
commands added for the camera pages are tested in test_cameras_commands.py.
"""

import pytest

from baja_studio.agents.contract import Mode
from baja_studio.core.module import CommandError
from baja_studio.core.supervisor import HealthState
from baja_studio.fakes.fake_serial import SNIFFED_FRAMES
from baja_studio.fakes.in_process import InProcessTransport
from tests.conftest import eventually
from tests.fake_agents import AGENT_HOST, DELIVERY_SLACK_SECONDS, StartAgent, advance_in_steps
from tests.modules.camera_studio import SMTAV_HOST, Studio, add_agent, agent_camera, smtav

# --- direct cameras -------------------------------------------------------------------------


def test_a_configured_camera_comes_up_ok_and_the_home_card_names_it(studio: Studio) -> None:
    studio.network.add(SMTAV_HOST)

    studio.configure(cameras=[smtav(label="Finish PTZ")])

    eventually(lambda: studio.status("smtav") == "ok")
    camera = studio.camera("smtav")
    assert camera["label"] == "Finish PTZ"
    assert camera["capabilities"]["kind"] == "ptz"
    assert camera["mode"] == "manual"
    assert studio.health().state == HealthState.OK
    assert "smtav ok" in studio.health().message


def test_a_camera_nobody_answers_for_is_offline_and_the_module_too(studio: Studio) -> None:
    studio.configure(cameras=[smtav()])  # no fake camera at that address

    eventually(lambda: studio.health().state == HealthState.OFFLINE)
    assert studio.status("smtav") == "offline"


def test_an_api_move_with_a_ttl_and_no_resend_stops_by_itself(studio: Studio) -> None:
    # Bench checklist item 6, without the bench.
    camera = studio.network.add(SMTAV_HOST)
    studio.configure(cameras=[smtav()])
    eventually(lambda: studio.status("smtav") == "ok")

    studio.command("move", camera="smtav", pan=0.5, ttl_ms=300)
    eventually(lambda: camera.moving)
    advance_in_steps(studio.clock, 0.5)

    eventually(lambda: not camera.moving)
    assert ("move", "ok", {"camera": "smtav", "pan": 0.5, "ttl_ms": 300}) in studio.log("api:")


def test_commands_name_an_enabled_camera_and_check_their_numbers(studio: Studio) -> None:
    studio.network.add(SMTAV_HOST)
    studio.configure(cameras=[smtav()])

    with pytest.raises(CommandError, match="no enabled camera called 'nope'; cameras: smtav"):
        studio.command("stop", camera="nope")
    with pytest.raises(CommandError, match="pan must be from -1 to 1"):
        studio.command("move", camera="smtav", pan=2)
    with pytest.raises(CommandError, match="whole preset number"):
        studio.command("recall_preset", camera="smtav", n="one")
    with pytest.raises(CommandError, match="only agent-owned"):
        studio.command("set_mode", camera="smtav", mode="auto")


def test_presets_are_stored_and_recalled_on_the_camera(studio: Studio) -> None:
    camera = studio.network.add(SMTAV_HOST)
    studio.configure(cameras=[smtav()])
    eventually(lambda: studio.status("smtav") == "ok")
    camera.pan.position = 1000

    studio.command("store_preset", camera="smtav", n=2)
    eventually(lambda: 2 in camera.presets)
    camera.pan.position = 0
    studio.command("recall_preset", camera="smtav", n=2)

    eventually(lambda: camera.pan.position == 1000)


# --- the joystick ----------------------------------------------------------------------------


def test_the_joystick_drives_the_camera_its_address_selects_and_the_log_says_so(
    studio: Studio,
) -> None:
    camera = studio.network.add(SMTAV_HOST)
    port = studio.ports.add("COM8")
    studio.configure(cameras=[smtav()], controllers=[{"id": "desk", "port": "COM8"}])
    eventually(lambda: studio.status("smtav") == "ok")
    eventually(lambda: studio.health().state == HealthState.OK)

    port.feed(SNIFFED_FRAMES["up_right_full"])
    eventually(lambda: camera.pan.rate > 0 and camera.tilt.rate > 0)
    port.feed(SNIFFED_FRAMES["stop"])
    eventually(lambda: not camera.moving)

    assert [entry[0] for entry in studio.log("joystick:desk")] == ["stop", "move"]  # newest first


def test_the_call_button_shows_in_the_controllers_state(studio: Studio) -> None:
    port = studio.ports.add("COM8")
    studio.configure(controllers=[{"id": "desk", "port": "COM8"}])
    eventually(lambda: studio.health().state == HealthState.OK)

    port.feed(SNIFFED_FRAMES["call_2"])

    eventually(
        lambda: (
            (studio.services.state.get("cameras", "controllers.desk.last_call") or {}).get("n") == 2
        )
    )


# --- settings applied live --------------------------------------------------------------------


def test_adding_changing_and_removing_cameras_takes_effect_without_a_restart(
    studio: Studio,
) -> None:
    studio.network.add(SMTAV_HOST)
    studio.network.add("10.0.0.10")
    studio.configure(cameras=[smtav()])
    eventually(lambda: studio.status("smtav") == "ok")

    # A label change keeps the same driver (same socket)...
    studio.configure(cameras=[smtav(label="Renamed")])
    eventually(lambda: studio.camera("smtav")["label"] == "Renamed")
    assert len(studio.network.opened) == 1

    # ...a new address gets a new one, and the old socket is closed.
    studio.configure(cameras=[smtav(address={"host": "10.0.0.10"})])
    eventually(lambda: studio.network.open_transports("10.0.0.10") != [])
    eventually(lambda: studio.network.open_transports(SMTAV_HOST) == [])

    # Removing the camera removes it from the state the page shows.
    studio.configure(cameras=[])
    eventually(lambda: studio.camera("smtav") is None)
    assert studio.services.supervisor.worker_names("cameras") == ["motion"]


def test_changing_the_controllers_port_moves_its_worker_to_the_new_port(studio: Studio) -> None:
    first, second = studio.ports.add("COM8"), studio.ports.add("COM9")
    studio.configure(controllers=[{"id": "desk", "port": "COM8"}])
    eventually(lambda: first.is_open)

    studio.configure(controllers=[{"id": "desk", "port": "COM9"}])

    eventually(lambda: second.is_open and not first.is_open)


def test_editing_only_the_call_bindings_keeps_the_controllers_port_open(
    studio: Studio,
) -> None:
    # Reopening the port would release the joystick's latch and stop its camera.
    port = studio.ports.add("COM8")
    studio.configure(controllers=[{"id": "desk", "port": "COM8"}])
    eventually(lambda: port.is_open)

    studio.configure(
        controllers=[
            {
                "id": "desk",
                "port": "COM8",
                "call_bindings": [{"n": 2, "action": "recall_preset", "preset": 4}],
            }
        ]
    )

    assert port.open_count == 1


def test_the_cameras_page_order_follows_the_settings_list_and_skips_disabled_cameras(
    studio: Studio,
) -> None:
    studio.configure(
        cameras=[
            smtav(id="wide", controller_address=None),
            smtav(id="spare", enabled=False, controller_address=None),
            smtav(id="finish", controller_address=None),
        ]
    )
    eventually(lambda: studio.services.state.get("cameras", "order") == ["wide", "finish"])

    # Dragging "finish" to the top on the configuration page saves the new order.
    studio.configure(
        cameras=[
            smtav(id="finish", controller_address=None),
            smtav(id="wide", controller_address=None),
        ]
    )

    eventually(lambda: studio.services.state.get("cameras", "order") == ["finish", "wide"])


def test_the_motion_settings_the_page_needs_are_in_the_state(studio: Studio) -> None:
    # The on-screen joystick uses the same deadband as the real one.
    studio.configure(motion={"joystick_deadband": 0.1})

    eventually(
        lambda: (
            (studio.services.state.get("cameras", "motion") or {}).get("joystick_deadband") == 0.1
        )
    )


def test_a_call_button_bound_to_a_camera_sets_the_controllers_selection(studio: Studio) -> None:
    port = studio.ports.add("COM8")
    studio.configure(
        cameras=[smtav()],
        controllers=[
            {
                "id": "desk",
                "port": "COM8",
                "call_bindings": [{"n": 2, "action": "select_camera", "camera": "smtav"}],
            }
        ],
    )
    eventually(lambda: port.is_open)

    port.feed(SNIFFED_FRAMES["call_2"])

    def selection() -> object:
        selected = studio.services.state.get("cameras", "controllers.desk.selected_camera")
        return selected and selected["camera"]

    eventually(lambda: selection() == "smtav")
    assert ("call", "ok", {"n": 2, "select_camera": "smtav"}) in studio.log("joystick:desk")


def test_a_call_button_bound_to_a_preset_recalls_it_on_the_controllers_camera(
    studio: Studio,
) -> None:
    camera = studio.network.add(SMTAV_HOST)
    port = studio.ports.add("COM8")
    studio.configure(
        cameras=[smtav()],
        controllers=[
            {
                "id": "desk",
                "port": "COM8",
                "call_bindings": [{"n": 2, "action": "recall_preset", "preset": 4}],
            }
        ],
    )
    eventually(lambda: studio.status("smtav") == "ok" and port.is_open)
    camera.pan.position = 500
    studio.command("store_preset", camera="smtav", n=4)
    eventually(lambda: 4 in camera.presets)
    camera.pan.position = 0

    # A frame addresses the SMTAV (the operator selected it on the controller), then Call 2.
    port.feed(SNIFFED_FRAMES["stop"] + SNIFFED_FRAMES["call_2"])

    eventually(lambda: camera.pan.position == 500)


def test_the_controllers_recent_frames_reach_the_state(studio: Studio) -> None:
    port = studio.ports.add("COM8")
    studio.configure(cameras=[smtav()], controllers=[{"id": "desk", "port": "COM8"}])
    eventually(lambda: port.is_open)

    port.feed(SNIFFED_FRAMES["pan_right_full"])

    def frames() -> list[dict[str, object]]:
        return studio.services.state.get("cameras", "controllers.desk.recent_frames") or []

    eventually(lambda: len(frames()) == 1)
    assert frames()[0]["action"] == "move"
    assert frames()[0]["pan"] == 1.0


def test_motion_settings_are_read_live(studio: Studio) -> None:
    camera = studio.network.add(SMTAV_HOST)
    studio.configure(cameras=[smtav()])
    eventually(lambda: studio.status("smtav") == "ok")

    # A one-second ttl instead of 300 ms: a move without a ttl now lasts longer.
    studio.configure(motion={"ttl_ms": 1000})
    studio.command("move", camera="smtav", tilt=0.5)
    advance_in_steps(studio.clock, 0.5)

    assert camera.moving


def test_one_camera_away_out_of_two_makes_the_module_degraded_and_names_it(
    studio: Studio,
) -> None:
    studio.network.add(SMTAV_HOST)
    studio.configure(
        cameras=[smtav(), smtav(id="spare", address={"host": "10.0.0.99"}, controller_address=2)]
    )

    eventually(lambda: studio.health().state == HealthState.DEGRADED)
    assert "spare offline" in studio.health().message
    assert "smtav ok" in studio.health().message


# --- agent-owned cameras ----------------------------------------------------------------------


def test_an_agent_camera_mirrors_the_agents_state_and_forwards_commands(
    studio: Studio, start_agent: StartAgent
) -> None:
    runner = start_agent(ptz_cameras=1)
    add_agent(studio)
    studio.configure(cameras=[agent_camera()])

    studio.soon(lambda: studio.status("trackside") == "ok")
    assert studio.camera("trackside")["mode"] == "manual"
    assert studio.camera("trackside")["route"] == "agent"

    studio.command("move", camera="trackside", pan=0.5, ttl_ms=300)

    def agent_saw_a_move() -> bool:
        log = runner.run(lambda: runner.agent.log_tail(50))
        return any(entry.command == "move" for entry in log)

    studio.soon(agent_saw_a_move)


def test_set_mode_on_an_agent_camera_returns_the_agents_answer(
    studio: Studio, start_agent: StartAgent
) -> None:
    start_agent(ptz_cameras=1)
    add_agent(studio)
    studio.configure(cameras=[agent_camera()])
    studio.soon(lambda: studio.status("trackside") == "ok")

    reply = studio.command("set_mode", camera="trackside", mode="hold")

    assert reply["mode"] == Mode.HOLD.value
    studio.soon(lambda: studio.camera("trackside")["mode"] == "hold")


def test_connecting_to_an_agent_camera_sends_it_no_stop(
    studio: Studio, start_agent: StartAgent
) -> None:
    # A stop would pause auto tracking on every agent camera (contract §7).
    runner = start_agent(ptz_cameras=1)
    add_agent(studio)
    studio.configure(cameras=[agent_camera()])
    studio.soon(lambda: studio.status("trackside") == "ok")
    advance_in_steps(studio.clock, 2)

    log = runner.run(lambda: runner.agent.log_tail(50))
    assert [entry.command for entry in log] == []


def test_with_its_agent_offline_the_camera_is_driven_directly_and_handed_back_with_resync(
    studio: Studio, start_agent: StartAgent, transport: InProcessTransport
) -> None:
    # Bench checklist item 7, without the bench.
    direct = studio.network.add(SMTAV_HOST)
    port = studio.ports.add("COM8")
    start_agent(ptz_cameras=1)
    add_agent(studio)
    studio.configure(
        cameras=[
            agent_camera(
                controller_address=1,
                direct_fallback={"driver": "visca_ip", "address": {"host": SMTAV_HOST}},
            )
        ],
        controllers=[{"id": "desk", "port": "COM8"}],
    )
    studio.soon(lambda: studio.status("trackside") == "ok")
    assert studio.network.opened == []  # the agent owns it: no direct connection yet

    # The agent goes away. It is offline after 5 s, and 10 s later the studio
    # takes the camera over directly.
    transport.unplug(AGENT_HOST, 8890)
    advance_in_steps(studio.clock, 5 + DELIVERY_SLACK_SECONDS + 10.5)
    studio.soon(lambda: studio.camera("trackside")["route"] == "fallback")
    studio.soon(lambda: studio.status("trackside") == "ok")  # the SMTAV answers directly

    port.feed(SNIFFED_FRAMES["pan_left_full"])
    eventually(lambda: direct.pan.rate < 0)
    port.feed(SNIFFED_FRAMES["stop"])
    eventually(lambda: not direct.moving)

    # The agent comes back: control returns to it, and it is asked to resync.
    runner = start_agent(ptz_cameras=1)
    advance_in_steps(studio.clock, 4)
    studio.soon(lambda: studio.camera("trackside")["route"] == "agent")

    def agent_was_resynced() -> bool:
        log = runner.run(lambda: runner.agent.log_tail(50))
        return any(entry.command == "resync" for entry in log)

    studio.soon(agent_was_resynced)
    studio.soon(lambda: studio.network.open_transports(SMTAV_HOST) == [])


def test_an_agent_camera_without_a_fallback_just_goes_offline_with_its_agent(
    studio: Studio, start_agent: StartAgent, transport: InProcessTransport
) -> None:
    start_agent(ptz_cameras=1)
    add_agent(studio)
    studio.configure(cameras=[agent_camera()])
    studio.soon(lambda: studio.status("trackside") == "ok")

    transport.unplug(AGENT_HOST, 8890)
    advance_in_steps(studio.clock, 5 + DELIVERY_SLACK_SECONDS + 12)

    studio.soon(lambda: studio.status("trackside") == "offline")
    assert studio.camera("trackside")["route"] == "agent"
    assert studio.network.opened == []
