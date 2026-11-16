"""Tests of the camera node's tray status: `GET /api/node/tray-status` and `port.txt`.

The tray icon on the laptop runs as whoever is logged on. It cannot know
the node's token (a secret setting the service keeps), so the node gives it
one small answer that needs no token. To keep that safe, the answer:

- is only given to the laptop itself (address 127.0.0.1 or ::1), never to
  another computer on the network, even one that sends the right token;
- holds nothing secret: no output URL, no OBS password, no token.

The tray also has to know the node's port, which can be changed on the
node's page; the node writes it to `port.txt` in its data folder at start.

In these tests the node runs inside the test process (`node_harness.py`):
no real camera, ffmpeg or OBS. A "client" is who the node thinks is asking:
the test client lets us pretend to be the laptop itself or another computer.
"""

from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from baja_studio.agents.contract import TOKEN_HEADER
from baja_studio.node.settings import NodeSettings
from baja_studio.node.test_cameras import fake_output_url
from baja_studio.node.tray_status import (
    PORT_FILE_NAME,
    TRAY_STATUS_PATH,
    tray_status,
    write_port_file,
)
from tests.conftest import eventually
from tests.node.node_harness import (
    NODE_TOKEN,
    OBS_PASSWORD,
    RunningNode,
    node_mode_camera,
    obs_mode_camera,
    running_node,
)

# Pretend to be the laptop itself (where the tray runs) ...
THIS_LAPTOP = ("127.0.0.1", 50000)
# ... or another computer on the trackside network.
ANOTHER_COMPUTER = ("192.168.10.50", 50000)


def ask_as(node: RunningNode, client: tuple[str, int], headers: dict[str, str]) -> Any:
    """GET the tray status as if `client` asked, sending only `headers` (no token by default)."""
    # A second test client on the same, already running app: only the asker's address differs.
    http = TestClient(node.app, client=client)
    return http.get(TRAY_STATUS_PATH, headers=headers)


def test_the_laptop_itself_gets_the_status_without_the_token(tmp_path: Path) -> None:
    cameras = [node_mode_camera("cam1", label="Left S600"), obs_mode_camera("cam2")]
    with running_node(tmp_path, cameras) as node:
        node.wait_until_ready()

        # No token is sent: the tray does not have it.
        answer = ask_as(node, THIS_LAPTOP, headers={})

        assert answer.status_code == 200
        status = answer.json()
        # The node's own health and version, like the studio's tray shows them.
        assert status["status"] in ("ok", "degraded")
        assert status["version"]
        # One line per served camera, in the order of the settings: id, name, video mode.
        assert [(c["id"], c["label"], c["mode"]) for c in status["cameras"]] == [
            ("cam1", "Left S600", "node"),
            ("cam2", "OBS S600", "obs"),
        ]


def test_a_working_camera_reads_ok(tmp_path: Path) -> None:
    with running_node(tmp_path, [node_mode_camera("cam1")]) as node:

        def cam1_ok() -> bool:
            cameras = ask_as(node, THIS_LAPTOP, headers={}).json()["cameras"]
            return bool(cameras) and cameras[0]["status"] == "ok"

        eventually(cam1_ok, "cam1 never read ok in the tray status")


def test_a_disabled_camera_is_not_listed(tmp_path: Path) -> None:
    # The tray lists what the node serves to the studio, like the studio sees it.
    cameras = [node_mode_camera("cam1"), node_mode_camera("cam3", enabled=False)]
    with running_node(tmp_path, cameras) as node:
        listed = ask_as(node, THIS_LAPTOP, headers={}).json()["cameras"]
        assert [camera["id"] for camera in listed] == ["cam1"]


def test_another_computer_is_refused_even_with_the_token(tmp_path: Path) -> None:
    with running_node(tmp_path, [node_mode_camera()]) as node:
        for headers in ({}, {TOKEN_HEADER: NODE_TOKEN}):
            answer = ask_as(node, ANOTHER_COMPUTER, headers=headers)
            assert answer.status_code == 403
            # The refusal says nothing about the cameras.
            assert "cam1" not in answer.text


def test_the_rest_of_the_page_api_still_needs_the_token_from_the_laptop(tmp_path: Path) -> None:
    # Only the tray status is open to the laptop: the settings (with the secrets
    # masked, but still) stay behind the token, even from 127.0.0.1.
    with running_node(tmp_path, [node_mode_camera()]) as node:
        http = TestClient(node.app, client=THIS_LAPTOP)
        assert http.get("/api/settings/node").status_code == 401
        assert http.get("/api/health").status_code == 401


def test_the_answer_holds_no_secret(tmp_path: Path) -> None:
    with running_node(tmp_path, [node_mode_camera("cam1"), obs_mode_camera("cam2")]) as node:
        node.wait_until_ready()
        text = ask_as(node, THIS_LAPTOP, headers={}).text

    # The camera's output URL (and its feed password), the OBS password and the token.
    url = fake_output_url("cam1")
    feed_password = url.split(":feed:")[1].split("&")[0]
    for secret in (url, feed_password, OBS_PASSWORD, NODE_TOKEN):
        assert secret not in text


class _CameraSaying:
    """A stand-in camera whose status reason quotes a secret (a bug elsewhere, say)."""

    def __init__(self, reason: str) -> None:
        self._reason = reason

    def status(self) -> tuple[Any, str]:
        class _Degraded:
            value = "degraded"

        return _Degraded(), self._reason


class _CamerasOf:
    def __init__(self, camera: _CameraSaying) -> None:
        self._camera = camera

    def camera(self, camera_id: str) -> _CameraSaying:
        return self._camera


class _ModuleWith:
    def __init__(self, camera: _CameraSaying) -> None:
        self.cameras = _CamerasOf(camera)


def test_a_reason_that_quotes_a_secret_is_scrubbed_again() -> None:
    # Reasons are already scrubbed where they are made; the tray status scrubs
    # them once more, so even a reason that slipped through shows *** instead.
    url = fake_output_url("cam1")
    settings = NodeSettings.model_validate(
        {"token": NODE_TOKEN, "cameras": [node_mode_camera("cam1")]}
    )
    camera = _CameraSaying(f"cannot reach {url} (token {NODE_TOKEN})")

    status = tray_status(_ModuleWith(camera), {"status": "ok"}, settings)  # type: ignore[arg-type]

    reason = status["cameras"][0]["reason"]
    assert url not in reason and NODE_TOKEN not in reason
    assert reason.startswith("cannot reach ")
    assert status["cameras"][0]["status"] == "degraded"


def test_the_node_writes_its_port_for_the_tray(tmp_path: Path) -> None:
    write_port_file(tmp_path, 8899)
    assert (tmp_path / PORT_FILE_NAME).read_text(encoding="ascii").strip() == "8899"


def test_a_port_file_that_cannot_be_written_does_not_stop_the_node(tmp_path: Path) -> None:
    # A file where the data folder should be: the folder cannot be made.
    blocked = tmp_path / "not-a-folder"
    blocked.write_text("in the way", encoding="ascii")
    write_port_file(blocked, 8891)  # logs a warning, raises nothing
