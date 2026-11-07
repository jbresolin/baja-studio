"""Tests for the shared HTTP and WebSocket endpoints (core/api.py).

`client` (from conftest.py) runs the whole app in-process, so these tests
exercise the real routes without a network. Most use the heartbeat module
because it needs no child process.
"""

import re
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import baja_studio
from baja_studio.core.registry import ModuleRegistry
from baja_studio.core.settings import SECRET_MASK
from baja_studio.modules.heartbeat import HeartbeatModule
from baja_studio.modules.home import HomeModule
from tests.conftest import AppFactory


@pytest.fixture
def light_app(make_app: AppFactory) -> FastAPI:
    """The app with only home and heartbeat: quick to start, no child process."""
    return make_app(lambda registry: [HomeModule(registry), HeartbeatModule()])


@pytest.fixture
def light_client(light_app: FastAPI) -> Any:
    with TestClient(light_app) as test_client:
        yield test_client


def receive_until(socket: Any, wanted: Any) -> dict[str, Any]:
    """Read WebSocket messages until one matches `wanted(message)`; return it."""
    for _ in range(200):
        message: dict[str, Any] = socket.receive_json()
        if wanted(message):
            return message
    raise AssertionError("expected message never arrived")


# --- health ---------------------------------------------------------------------


def test_health_lists_every_module_with_its_state_and_the_version(
    light_client: TestClient,
) -> None:
    response = light_client.get("/api/health")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    # The version baked in at build time, so the installer's smoke test can check it.
    assert body["version"] == baja_studio.__version__
    assert [(m["name"], m["state"]) for m in body["modules"]] == [
        ("home", "ok"),
        ("heartbeat", "ok"),
    ]


# --- settings ---------------------------------------------------------------------


def test_the_settings_list_has_one_section_per_module_plus_system(
    light_client: TestClient,
) -> None:
    sections = light_client.get("/api/settings").json()

    assert {"section": "system", "title": "System"} in sections
    assert {"section": "heartbeat", "title": "Heartbeat (demo)"} in sections


def test_get_settings_returns_values_and_version(light_client: TestClient) -> None:
    body = light_client.get("/api/settings/heartbeat").json()

    assert body["version"] == 1
    assert body["values"] == {"enabled": True, "interval_s": 1.0}


def test_put_settings_saves_and_returns_the_new_version(light_client: TestClient) -> None:
    response = light_client.put(
        "/api/settings/heartbeat", json={"version": 1, "values": {"interval_s": 2.5}}
    )

    assert response.status_code == 200
    assert response.json()["version"] == 2
    assert response.json()["values"]["interval_s"] == 2.5


def test_put_settings_with_a_stale_version_returns_409_conflict(light_client: TestClient) -> None:
    # The first save moves the section to version 2...
    light_client.put("/api/settings/heartbeat", json={"version": 1, "values": {"interval_s": 2}})

    # ...so a second save still based on version 1 is refused.
    response = light_client.put(
        "/api/settings/heartbeat", json={"version": 1, "values": {"interval_s": 3}}
    )

    assert response.status_code == 409
    assert response.json()["detail"]["current_version"] == 2


def test_put_settings_with_an_invalid_value_returns_422(light_client: TestClient) -> None:
    response = light_client.put(
        "/api/settings/heartbeat", json={"version": 1, "values": {"interval_s": -1}}
    )

    assert response.status_code == 422
    assert response.json()["detail"][0]["loc"] == ["interval_s"]


def test_settings_of_an_unknown_section_return_404(light_client: TestClient) -> None:
    assert light_client.get("/api/settings/nope").status_code == 404
    assert light_client.get("/api/settings/nope/schema").status_code == 404
    assert light_client.put("/api/settings/nope", json={"values": {}}).status_code == 404


def test_the_schema_describes_the_fields_for_the_settings_form(light_client: TestClient) -> None:
    schema = light_client.get("/api/settings/heartbeat/schema").json()

    assert schema["properties"]["interval_s"]["type"] == "number"
    assert schema["properties"]["interval_s"]["minimum"] == 0.1


def test_secret_values_are_masked_in_responses(make_app: AppFactory) -> None:
    # A one-off module whose settings include a secret.
    from pydantic import Field

    from baja_studio.core.module import Module
    from baja_studio.core.settings import ModuleSettings

    class VaultSettings(ModuleSettings):
        token: str = Field(default="", json_schema_extra={"secret": True})

    class VaultModule(Module):
        name = "vault"
        title = "Vault"
        SettingsModel = VaultSettings

    with TestClient(make_app(lambda registry: [VaultModule()])) as client:
        saved = client.put("/api/settings/vault", json={"values": {"token": "abc123"}}).json()

        assert saved["values"]["token"] == SECRET_MASK
        assert client.get("/api/settings/vault").json()["values"]["token"] == SECRET_MASK


# --- commands -----------------------------------------------------------------------


def test_post_command_runs_it_and_records_it_with_an_api_source(
    light_client: TestClient,
) -> None:
    response = light_client.post(
        "/api/commands", json={"module": "heartbeat", "command": "reset", "caller": "d200"}
    )

    assert response.json() == {"ok": True, "result": {"count": 0}}
    entry = light_client.get("/api/commands", params={"module": "heartbeat"}).json()[0]
    assert entry["source"] == "api:d200"
    assert entry["command"] == "reset"


def test_post_command_to_an_unknown_module_is_404(light_client: TestClient) -> None:
    response = light_client.post("/api/commands", json={"module": "ghost", "command": "x"})

    assert response.status_code == 404


def test_post_command_that_is_refused_is_400_with_the_reason(light_client: TestClient) -> None:
    response = light_client.post("/api/commands", json={"module": "heartbeat", "command": "fly"})

    assert response.status_code == 400
    assert "no command 'fly'" in response.json()["detail"]


# --- logs ---------------------------------------------------------------------------


def test_recent_logs_include_startup_messages(light_client: TestClient) -> None:
    messages = [entry["message"] for entry in light_client.get("/api/logs").json()]

    assert "module started" in messages


# --- WebSocket ----------------------------------------------------------------------


def test_a_new_websocket_client_receives_a_snapshot_first(light_client: TestClient) -> None:
    with light_client.websocket_connect("/ws") as socket:
        first = socket.receive_json()

    assert first["type"] == "snapshot"
    assert "heartbeat" in first["state"]
    assert first["state"]["home"]["modules"]["heartbeat"]["state"] == "ok"


def test_the_snapshot_tells_the_page_its_client_id(light_client: TestClient) -> None:
    # The page shows its id in the footer, and the command log names it web:<id>.
    with light_client.websocket_connect("/ws") as socket:
        first = socket.receive_json()

    assert re.fullmatch(r"[0-9a-f]{8}", first["client_id"])


def test_a_page_that_reconnects_gets_the_client_id_it_asks_for(light_client: TestClient) -> None:
    with light_client.websocket_connect("/ws?client=desk-1") as socket:
        first = socket.receive_json()

    assert first["client_id"] == "desk-1"


def test_a_second_page_asking_for_a_connected_id_gets_a_fresh_one(
    light_client: TestClient,
) -> None:
    # A duplicated browser tab carries the same id; two pages must never share a source.
    with (
        light_client.websocket_connect("/ws?client=desk-1") as first_page,
        light_client.websocket_connect("/ws?client=desk-1") as second_page,
    ):
        first = first_page.receive_json()["client_id"]
        second = second_page.receive_json()["client_id"]

    assert first == "desk-1"
    assert second != "desk-1"


def test_a_malformed_client_id_is_replaced(light_client: TestClient) -> None:
    with light_client.websocket_connect("/ws?client=web:evil.name") as socket:
        first = socket.receive_json()

    assert first["client_id"] != "web:evil.name"


def test_websocket_commands_are_logged_under_the_pages_client_id(
    light_client: TestClient,
) -> None:
    with light_client.websocket_connect("/ws?client=phone") as socket:
        socket.receive_json()
        socket.send_json({"module": "heartbeat", "command": "reset", "request_id": "r1"})
        receive_until(socket, lambda message: message["type"] == "reply")

    entry = light_client.get("/api/commands?module=heartbeat").json()[0]
    assert entry["source"] == "web:phone"


def test_a_command_over_the_websocket_gets_a_reply_with_its_request_id(
    light_client: TestClient,
) -> None:
    with light_client.websocket_connect("/ws") as socket:
        socket.receive_json()  # snapshot
        socket.send_json(
            {"module": "heartbeat", "command": "reset", "args": {}, "request_id": "req-1"}
        )

        reply = receive_until(socket, lambda message: message["type"] == "reply")

    assert reply == {"type": "reply", "request_id": "req-1", "ok": True, "result": {"count": 0}}


def test_a_failing_websocket_command_replies_with_ok_false_and_the_error(
    light_client: TestClient,
) -> None:
    with light_client.websocket_connect("/ws") as socket:
        socket.receive_json()
        socket.send_json({"module": "heartbeat", "command": "fly", "request_id": 7})

        reply = receive_until(socket, lambda message: message["type"] == "reply")

    assert reply["ok"] is False
    assert reply["request_id"] == 7
    assert "no command 'fly'" in reply["error"]


def test_websocket_commands_are_logged_with_a_web_source(light_client: TestClient) -> None:
    with light_client.websocket_connect("/ws") as socket:
        socket.receive_json()
        socket.send_json({"module": "heartbeat", "command": "reset", "request_id": 1})
        receive_until(socket, lambda message: message["type"] == "reply")

    assert light_client.get("/api/commands").json()[0]["source"].startswith("web:")


def test_two_browsers_both_receive_the_same_patch(light_client: TestClient) -> None:
    with (
        light_client.websocket_connect("/ws") as first,
        light_client.websocket_connect("/ws") as second,
    ):
        first.receive_json()
        second.receive_json()

        # The first browser resets the counter; both must see count become 0.
        first.send_json({"module": "heartbeat", "command": "reset", "request_id": 1})

        def is_count_reset(message: dict[str, Any]) -> bool:
            return (
                message.get("type") == "patch"
                and message["path"] == "count"
                and (message["value"] == 0)
            )

        patch_seen_by_first = receive_until(first, is_count_reset)
        patch_seen_by_second = receive_until(second, is_count_reset)

    assert patch_seen_by_first["seq"] == patch_seen_by_second["seq"]


def test_a_snapshot_request_is_answered_with_a_snapshot(light_client: TestClient) -> None:
    with light_client.websocket_connect("/ws") as socket:
        socket.receive_json()
        socket.send_json({"type": "snapshot_request"})

        message = receive_until(socket, lambda message: message["type"] == "snapshot")

    assert "heartbeat" in message["state"]


def test_garbage_on_the_websocket_is_ignored(light_client: TestClient) -> None:
    with light_client.websocket_connect("/ws") as socket:
        socket.receive_json()
        socket.send_text("this is not json")
        socket.send_text("[1, 2, 3]")
        socket.send_json({"type": "snapshot_request"})

        # The connection is still alive and still answers.
        assert receive_until(socket, lambda message: message["type"] == "snapshot")


# --- static frontend ----------------------------------------------------------------


def test_without_a_built_frontend_the_home_page_explains_how_to_build_it(
    light_client: TestClient,
) -> None:
    response = light_client.get("/")

    assert response.status_code == 200
    assert "npm run build" in response.text


def test_with_a_built_frontend_files_are_served_and_other_paths_get_index_html(
    make_app: AppFactory, frontend_directory: Path
) -> None:
    (frontend_directory / "index.html").write_text("<html>shell</html>")
    (frontend_directory / "assets").mkdir()
    (frontend_directory / "assets" / "app.js").write_text("console.log('hi')")

    def only_home(registry: ModuleRegistry) -> list[Any]:
        return [HomeModule(registry)]

    with TestClient(make_app(only_home)) as client:
        assert client.get("/assets/app.js").text == "console.log('hi')"
        # /settings is a page the React router handles, so the server returns the shell.
        assert client.get("/settings").text == "<html>shell</html>"
        assert client.get("/api/unknown").status_code == 404


def test_paths_cannot_escape_the_frontend_folder(
    make_app: AppFactory, frontend_directory: Path, tmp_path: Path
) -> None:
    (frontend_directory / "index.html").write_text("<html>shell</html>")
    (tmp_path / "secret.txt").write_text("do not serve me")

    with TestClient(make_app(lambda registry: [HomeModule(registry)])) as client:
        response = client.get("/..%2Fsecret.txt")

    assert "do not serve me" not in response.text
