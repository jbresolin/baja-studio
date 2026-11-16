"""Tests of a node camera's image controls (exposure, white balance, focus...), in both video modes.

The studio's Image page changes them through `GET`/`PUT /cameras/{id}/image`
(contract §6). On the node they go to the camera's USB device through the
UVC child, whatever does the video (the node's pipeline or OBS). Here the
UVC child runs in the test process over the fake duvc library, which has
the studio PC's two EMEET S600s and a Pixy (`tests/imaging/uvc_harness.py`).
"""

from pathlib import Path
from typing import Any

import pytest

from baja_studio.fakes.fake_duvc import S600_A_PATH, CamMode
from tests.conftest import eventually
from tests.node.node_harness import (
    RunningNode,
    node_mode_camera,
    obs_mode_camera,
    running_node,
    s600_usb_device,
)

# One camera of each mode, both on the first S600's USB device.
MODES = {
    "node": lambda: node_mode_camera("cam1", usb_device=s600_usb_device()),
    "obs": lambda: obs_mode_camera("cam1", usb_device=s600_usb_device()),
}


def image(node: RunningNode) -> dict[str, Any]:
    answer: dict[str, Any] = node.http.get("/cameras/cam1/image").json()
    return answer


@pytest.mark.parametrize("mode", MODES)
def test_the_cameras_controls_are_read_from_its_usb_device(tmp_path: Path, mode: str) -> None:
    with running_node(tmp_path, [MODES[mode]()]) as node:
        controls = image(node)

        # An S600 has exposure, white balance, focus and more, each with a range.
        assert {"exposure", "white_balance", "focus"} <= set(controls)
        exposure = controls["exposure"]
        assert exposure["min"] < exposure["max"]
        assert exposure["locked"] is False  # the node never changes a control by itself
        # /agent/info lists the same controls (remembered from that read).
        info = node.http.get("/agent/info").json()["cameras"][0]
        assert set(info["image_controls"]) == set(controls)


@pytest.mark.parametrize("mode", MODES)
def test_a_change_is_written_to_the_device_and_read_back(tmp_path: Path, mode: str) -> None:
    with running_node(tmp_path, [MODES[mode]()]) as node:
        exposure = image(node)["exposure"]
        wanted = exposure["min"] + exposure["step"]

        # Exposure to manual, one step above its minimum.
        answer = node.http.put(
            "/cameras/cam1/image", json={"exposure": {"auto": False, "value": wanted}}
        )

        # The answer is read back from the device: what it took.
        assert answer.status_code == 200
        assert answer.json()["exposure"]["auto"] is False
        assert answer.json()["exposure"]["value"] == wanted
        assert node.usb.control(S600_A_PATH, "Exposure").value == wanted


def test_a_value_outside_the_range_is_clamped_like_a_webcam_does(tmp_path: Path) -> None:
    with running_node(tmp_path, [MODES["node"]()]) as node:
        exposure = image(node)["exposure"]

        answer = node.http.put(
            "/cameras/cam1/image", json={"exposure": {"auto": False, "value": exposure["max"] + 50}}
        )

        assert answer.json()["exposure"]["value"] == exposure["max"]


def test_an_unknown_control_changes_nothing(tmp_path: Path) -> None:
    with running_node(tmp_path, [MODES["node"]()]) as node:
        before = image(node)

        answer = node.http.put(
            "/cameras/cam1/image",
            json={"exposure": {"auto": False}, "no_such_control": {"value": 1}},
        )

        # The whole change is refused, so exposure is still as it was.
        assert answer.status_code == 400
        assert answer.json()["error"] == "unknown_control"
        assert image(node)["exposure"] == before["exposure"]


def test_a_camera_without_a_usb_device_has_no_controls(tmp_path: Path) -> None:
    with running_node(tmp_path, [node_mode_camera("cam1")]) as node:
        assert image(node) == {}
        assert node.http.get("/agent/info").json()["cameras"][0]["image_controls"] == []


def test_an_unplugged_device_is_a_503_and_its_settings_return_with_it(tmp_path: Path) -> None:
    with running_node(tmp_path, [MODES["obs"]()]) as node:
        exposure = image(node)["exposure"]
        wanted = exposure["min"] + exposure["step"]
        node.http.put("/cameras/cam1/image", json={"exposure": {"auto": False, "value": wanted}})

        # The S600's cable is pulled: its controls cannot be read.
        node.usb.unplug(S600_A_PATH)
        answer = node.http.get("/cameras/cam1/image")
        assert answer.status_code == 503
        assert answer.json()["error"] == "device_offline"

        # A real webcam forgets its settings when unplugged; the fake does not, so
        # the test makes it forget: exposure back to its default, on auto.
        control = node.usb.control(S600_A_PATH, "Exposure")
        control.value, control.mode = control.range.default_val, CamMode.Auto

        # Plugged back in: the node writes the last settings again by itself.
        node.usb.replug(S600_A_PATH)
        eventually(
            lambda: control.value == wanted and control.mode == CamMode.Manual,
            "the exposure was not written again after the replug",
        )
