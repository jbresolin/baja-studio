"""Tests of what the node's own page uses: its token, its settings, its commands, its state.

The node serves a small page (`frontend/node/`) from the same server as its
agent API. It reads and saves the settings through the studio's core API
(`/api/settings/node`), runs commands (`/api/commands`: the local OBS's
sources, Test connection, Create filter, the capture devices) and follows
the state over `/ws`. With a token set, all of that needs the token, like
the agent API: otherwise anyone on the LAN could read the settings, change
the token or point a camera elsewhere.
"""

from pathlib import Path

import pytest
from starlette.websockets import WebSocketDisconnect

from baja_studio.agents.contract import TOKEN_HEADER
from baja_studio.core.module import CommandError
from baja_studio.core.settings import SECRET_MASK
from tests.conftest import eventually
from tests.node.node_harness import (
    NODE_TOKEN,
    OBS_BARE_WEBCAM,
    OBS_PASSWORD,
    OBS_WEBCAMS,
    node_mode_camera,
    obs_mode_camera,
    running_node,
)

NO_TOKEN = {TOKEN_HEADER: ""}
# Not the laptop OBS's password (built at run time: nothing here is a credential).
WRONG_PASSWORD = "-".join(("not", "the", "one"))


def test_with_a_token_the_page_api_needs_it_but_agent_info_stays_open(tmp_path: Path) -> None:
    with running_node(tmp_path, [node_mode_camera()]) as node:
        # Without the token: the settings, the commands and the agent API are refused ...
        for method, path in [
            ("GET", "/api/settings/node"),
            ("POST", "/api/commands"),
            ("GET", "/agent/health"),
            ("POST", "/cameras/cam1/stop"),
        ]:
            answer = node.http.request(method, path, headers=NO_TOKEN)
            assert answer.status_code == 401, path
            assert answer.json()["error"] == "unauthorized"
        # ... but /agent/info is open, so the studio's subnet scan finds the node.
        assert node.http.get("/agent/info", headers=NO_TOKEN).status_code == 200


def test_the_pages_state_socket_needs_the_token_in_its_address(tmp_path: Path) -> None:
    # A browser cannot put a header on a WebSocket, so the page sends ?token=.
    with running_node(tmp_path, [node_mode_camera()]) as node:
        with pytest.raises(WebSocketDisconnect), node.http.websocket_connect("/ws") as socket:
            socket.receive_json()

        with node.http.websocket_connect(f"/ws?token={NODE_TOKEN}") as socket:
            first = socket.receive_json()
        assert "node" in str(first)


def test_without_a_token_everything_is_open(tmp_path: Path) -> None:
    # A fresh install has no token: the page works, and says so (the page's job).
    with running_node(tmp_path, [node_mode_camera()], token="") as node:
        assert node.http.get("/api/settings/node", headers=NO_TOKEN).status_code == 200
        with node.http.websocket_connect("/ws") as socket:
            assert socket.receive_json()


def test_the_settings_page_never_sees_a_secret(tmp_path: Path) -> None:
    # The token, the output URLs and the OBS password are masked in what the
    # page reads; sending the mask back keeps the saved value.
    with running_node(tmp_path, [node_mode_camera("cam1"), obs_mode_camera("cam2")]) as node:
        values = node.http.get("/api/settings/node").json()["values"]

        assert values["token"] == SECRET_MASK
        assert values["cameras"][0]["video"]["output_url"] == SECRET_MASK
        assert values["cameras"][1]["obs"]["password"] == SECRET_MASK

        # The page saves the whole form back, masks included, with a new label.
        values["cameras"][0]["label"] = "Finish line"
        saved = node.http.put("/api/settings/node", json={"values": values})

        assert saved.status_code == 200
        settings = node.settings()
        assert settings.token == NODE_TOKEN
        assert settings.cameras[1].obs.password == OBS_PASSWORD
        assert settings.cameras[0].label == "Finish line"


def test_the_page_lists_the_laptop_obs_sources_and_tests_the_connection(tmp_path: Path) -> None:
    with running_node(tmp_path, [obs_mode_camera("cam2")]) as node:
        node.wait_until_ready()

        listed = node.command("list_obs_sources", camera="cam2", obs={"port": 4455})
        right = node.command(
            "test_obs_connection", camera="cam2", obs={"port": 4455, "password": SECRET_MASK}
        )
        wrong = node.command(
            "test_obs_connection", camera="cam2", obs={"port": 4455, "password": WRONG_PASSWORD}
        )

        assert {source["name"] for source in listed["sources"]} >= set(OBS_WEBCAMS)
        # The masked password means "the saved one", which is right.
        assert right["result"] == "connected"
        assert wrong["result"] != "connected"


def test_create_filter_makes_the_crop_filter_on_the_cameras_source(tmp_path: Path) -> None:
    # A webcam in OBS with no crop filter yet: the camera is degraded, saying so.
    camera = obs_mode_camera("cam2")
    camera["obs"]["source"] = OBS_BARE_WEBCAM
    with running_node(tmp_path, [camera]) as node:
        eventually(lambda: node.state("cameras.cam2.status") == "degraded", "not degraded")
        assert "has no filter" in node.state("cameras.cam2.status_reason")

        # The page's Create filter button.
        created = node.command("create_crop_filter", camera="cam2")

        # The filter is made, and the camera picks it up by itself.
        assert created == {"created": "Baja crop", "source": OBS_BARE_WEBCAM}
        node.wait_until_ready()
        eventually(lambda: node.state("cameras.cam2.status") == "ok", "never ok")


def test_create_filter_needs_a_source_first(tmp_path: Path) -> None:
    camera = obs_mode_camera("cam2")
    camera["obs"]["source"] = ""
    with (
        running_node(tmp_path, [camera]) as node,
        pytest.raises(CommandError, match="choose the camera's OBS source"),
    ):
        node.command("create_crop_filter", camera="cam2")


def test_the_page_lists_capture_devices_and_their_modes(tmp_path: Path) -> None:
    with running_node(tmp_path, []) as node:
        devices = node.command("list_capture_devices")["devices"]
        modes = node.command("capture_modes", device=devices[0]["device"])["modes"]

        # Two S600s with the same name, told apart by their device string.
        assert [device["name"] for device in devices] == ["EMEET SmartCam S600"] * 2
        assert devices[0]["device"] != devices[1]["device"]
        assert {"width": 3840, "height": 2160, "fps": 30.0, "codec": "mjpeg"} in modes


def test_obs_going_away_is_a_device_offline_event_on_the_agent_socket(tmp_path: Path) -> None:
    with running_node(tmp_path, [obs_mode_camera("cam2")]) as node:
        node.wait_until_ready()
        with node.http.websocket_connect("/agent/ws", headers={TOKEN_HEADER: NODE_TOKEN}) as socket:
            # The first message is the greeting, sent as the socket opens. Then wait
            # for a *push* showing the camera ok: the node has seen it ok, so its
            # going away is a change the node reports.
            assert socket.receive_json()["type"] == "state"
            for _ in range(200):
                message = socket.receive_json()
                if message.get("type") == "state" and message["cameras"]["cam2"]["status"] == "ok":
                    break

            node.obs_network.unplug("127.0.0.1", 4455)

            # Messages until the event (state pushes come four times a second).
            for _ in range(200):
                message = socket.receive_json()
                if message.get("event") == "device_offline":
                    break
            else:
                pytest.fail("no device_offline event")
        assert message["camera_id"] == "cam2"
