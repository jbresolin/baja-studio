"""Tests for the cameras module's readiness checks (cameras/readiness.py).

The cameras module runs with its fakes (tests/modules/camera_studio.py): VISCA
cameras on a pretend network, a joystick on a pretend serial port, a fake
camera-laptop agent. A check only reads what the drivers already publish, so
each test brings a device up (or not) and runs the module's checks.
"""

from baja_studio.core.readiness import CheckStatus
from baja_studio.fakes.fake_serial import SNIFFED_FRAMES
from tests.conftest import eventually, step_fake_time
from tests.fake_agents import StartAgent
from tests.modules.camera_studio import (
    CAMERA_CLOCK_WORKERS,
    SMTAV_HOST,
    Studio,
    add_agent,
    agent_camera,
    smtav,
)
from tests.modules.readiness_runner import assert_fix_given, run_module_checks, statuses


def test_no_cameras_and_no_controllers_means_no_checks(studio: Studio) -> None:
    assert run_module_checks(studio.services, "cameras", studio.clock) == {}


def test_a_direct_camera_that_answers_passes(studio: Studio) -> None:
    studio.network.add(SMTAV_HOST)
    studio.configure(cameras=[smtav(label="Finish PTZ")])
    eventually(lambda: studio.status("smtav") == "ok")

    result = run_module_checks(studio.services, "cameras", studio.clock)["camera:smtav"]

    assert result.label == "Camera Finish PTZ"
    assert result.status == CheckStatus.OK
    assert result.link == "/cameras/config"


def test_a_direct_camera_that_does_not_answer_fails_with_its_address(studio: Studio) -> None:
    studio.configure(cameras=[smtav()])  # nothing answers at that address
    eventually(lambda: studio.health().state.value == "offline")

    result = run_module_checks(studio.services, "cameras", studio.clock)["camera:smtav"]

    assert result.status == CheckStatus.FAIL
    assert result.detail.startswith("offline")
    assert SMTAV_HOST in result.fix
    assert_fix_given(result)


def test_a_disabled_camera_is_not_checked(studio: Studio) -> None:
    studio.configure(cameras=[smtav(enabled=False)])

    assert run_module_checks(studio.services, "cameras", studio.clock) == {}


def test_an_agent_camera_passes_while_its_agent_lists_it(
    studio: Studio, start_agent: StartAgent
) -> None:
    start_agent(ptz_cameras=1)
    add_agent(studio)
    studio.configure(cameras=[agent_camera()])
    studio.soon(lambda: studio.status("trackside") == "ok")

    result = run_module_checks(studio.services, "cameras", studio.clock)["camera:trackside"]

    assert result.status == CheckStatus.OK
    assert result.detail == "answering (agent)"
    assert result.link == "/modules/agents"


def test_an_agent_camera_without_its_agent_fails_and_names_the_laptop(studio: Studio) -> None:
    studio.configure(cameras=[agent_camera()])  # agent laptop-a was never added
    studio.soon(lambda: studio.status("trackside") == "offline")

    result = run_module_checks(studio.services, "cameras", studio.clock)["camera:trackside"]

    assert result.status == CheckStatus.FAIL
    assert result.detail == "offline: agent laptop-a is not configured"
    assert "agent laptop-a" in result.fix


def test_a_camera_that_needs_presets_warns_until_it_has_some(studio: Studio) -> None:
    studio.network.add(SMTAV_HOST)
    studio.configure(cameras=[smtav(needs_presets=True)])
    eventually(lambda: studio.status("smtav") == "ok")

    without = run_module_checks(studio.services, "cameras", studio.clock)["presets:smtav"]
    studio.configure(cameras=[smtav(needs_presets=True, presets=[{"n": 0, "label": "Finish"}])])
    with_one = run_module_checks(studio.services, "cameras", studio.clock)["presets:smtav"]

    assert without.status == CheckStatus.WARN
    assert_fix_given(without)
    assert (with_one.status, with_one.detail) == (CheckStatus.OK, "1 preset")


def test_a_camera_that_does_not_need_presets_has_no_preset_check(studio: Studio) -> None:
    studio.network.add(SMTAV_HOST)
    studio.configure(cameras=[smtav()])

    assert "presets:smtav" not in run_module_checks(studio.services, "cameras", studio.clock)


def test_a_controller_with_recent_frames_passes(studio: Studio) -> None:
    port = studio.ports.add("COM8")
    studio.configure(controllers=[{"id": "desk", "port": "COM8"}])
    eventually(lambda: studio.services.state.get("cameras", "controllers.desk.status") == "ok")
    port.feed(SNIFFED_FRAMES["stop"])
    eventually(lambda: studio.services.state.get("cameras", "controllers.desk.recent_frames"))

    result = run_module_checks(studio.services, "cameras", studio.clock)["controller:desk"]

    assert result.status == CheckStatus.OK
    assert result.detail == "port COM8 open, frames arriving"


def test_a_controller_silent_for_a_minute_only_warns(studio: Studio) -> None:
    # The port is open, but nobody touched the joystick: maybe a wiring
    # problem, maybe just a joystick at rest. Worth a look, not a red line.
    port = studio.ports.add("COM8")
    studio.configure(controllers=[{"id": "desk", "port": "COM8"}])
    eventually(lambda: studio.services.state.get("cameras", "controllers.desk.status") == "ok")
    port.feed(SNIFFED_FRAMES["stop"])
    eventually(lambda: studio.services.state.get("cameras", "controllers.desk.recent_frames"))
    step_fake_time(studio.clock, 61.0, CAMERA_CLOCK_WORKERS, step=1.0)

    result = run_module_checks(studio.services, "cameras", studio.clock)["controller:desk"]

    assert result.status == CheckStatus.WARN
    assert result.detail.startswith("port open, but no frames in the last minute")
    assert_fix_given(result)


def test_an_unplugged_controller_fails_and_says_which_port(studio: Studio) -> None:
    port = studio.ports.add("COM8")
    studio.configure(controllers=[{"id": "desk", "port": "COM8"}])
    eventually(lambda: studio.services.state.get("cameras", "controllers.desk.status") == "ok")

    port.unplug()
    eventually(lambda: studio.services.state.get("cameras", "controllers.desk.status") != "ok")
    results = run_module_checks(studio.services, "cameras", studio.clock)

    assert statuses(results) == {"controller:desk": CheckStatus.FAIL}
    assert results["controller:desk"].fix.startswith("Check the RS485 adapter is plugged in")
    assert "COM8" in results["controller:desk"].fix
