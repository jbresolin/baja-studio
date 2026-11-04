"""Tests for the controller's Call buttons (cameras/inputs/call_bindings.py).

`CallBindings` is told about each Call press and the binding it has. Two
cameras with real arbiters over recording drivers stand in for the cameras;
what is published and logged is collected in plain lists and dicts.
"""

import logging
from typing import Any

import pytest

from baja_studio.cameras.config import (
    CallAction,
    CallBinding,
    CameraAddress,
    CameraConfig,
    MotionSettings,
)
from baja_studio.cameras.driver import Velocity
from baja_studio.cameras.inputs.call_bindings import CallBindings
from baja_studio.cameras.motion.arbiter import MotionArbiter
from tests.cameras.recording_driver import RecordingDriver
from tests.conftest import FakeClock

JOYSTICK = "joystick:desk"


class Rig:
    """Call bindings for one controller, with cameras "wide" and "finish"."""

    def __init__(self, fake_clock: FakeClock) -> None:
        self.drivers = {name: RecordingDriver() for name in ("wide", "finish")}
        self.arbiters = {
            name: self._arbiter(fake_clock, name, driver) for name, driver in self.drivers.items()
        }
        self.published: dict[str, Any] = {}
        self.logged: list[tuple[str, dict[str, Any], str]] = []
        self.calls = CallBindings(
            JOYSTICK,
            self.arbiters.get,
            self.published.__setitem__,
            lambda command, args, result: self.logged.append((command, args, result)),
            fake_clock,
            logging.getLogger("test"),
        )

    @staticmethod
    def _arbiter(fake_clock: FakeClock, name: str, driver: RecordingDriver) -> MotionArbiter:
        camera = CameraConfig(id=name, address=CameraAddress(host="10.0.0.9"))
        return MotionArbiter(
            driver,
            config=lambda: camera,
            motion=MotionSettings,
            clock=fake_clock,
            publish=lambda key, value: None,
            logger=logging.getLogger("test"),
        )


@pytest.fixture
def rig(fake_clock: FakeClock) -> Rig:
    return Rig(fake_clock)


def select(camera: str, n: int = 1) -> CallBinding:
    return CallBinding(n=n, action=CallAction.SELECT_CAMERA, camera=camera)


def recall(preset: int, n: int = 2) -> CallBinding:
    return CallBinding(n=n, action=CallAction.RECALL_PRESET, preset=preset)


def test_an_unbound_call_is_only_published_and_logged(rig: Rig) -> None:
    rig.calls.press(4, None)

    assert rig.published["last_call"]["n"] == 4
    assert rig.logged == [("call", {"n": 4}, "ok")]
    assert all(driver.calls == [] for driver in rig.drivers.values())


def test_a_select_binding_publishes_the_controllers_selection_and_moves_nothing(rig: Rig) -> None:
    rig.calls.press(1, select("finish"))

    assert rig.published["selected_camera"]["camera"] == "finish"
    assert all(driver.calls == [] for driver in rig.drivers.values())


def test_a_preset_binding_recalls_on_the_selected_camera(rig: Rig) -> None:
    rig.calls.addressed("wide")  # the stick last went to "wide"...
    rig.calls.press(1, select("finish"))  # ...but Call 1 selected "finish"

    rig.calls.press(2, recall(5))

    assert rig.drivers["finish"].calls == [("recall_preset", 5)]
    assert rig.drivers["wide"].calls == []


def test_before_any_selection_a_preset_binding_uses_the_camera_last_addressed(rig: Rig) -> None:
    rig.calls.addressed("wide")

    rig.calls.press(2, recall(5))

    assert rig.drivers["wide"].calls == [("recall_preset", 5)]


def test_a_preset_binding_with_no_camera_to_go_to_is_logged_as_an_error(rig: Rig) -> None:
    rig.calls.press(2, recall(5))

    assert rig.logged[-1][2] == "error: no camera selected yet"


def test_a_preset_binding_obeys_the_holder_rule(rig: Rig) -> None:
    # The web page is moving "wide": the joystick's recall must not yank it away.
    rig.arbiters["wide"].move("web:phone", Velocity(pan=0.5))
    rig.calls.addressed("wide")

    rig.calls.press(2, recall(5))

    assert ("recall_preset", 5) not in rig.drivers["wide"].calls
    assert rig.logged[-1][2].startswith("error: camera wide is being moved by web:phone")
