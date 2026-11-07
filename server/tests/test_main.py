"""Tests for the app factory and data directory choice (main.py)."""

import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import pytest
from fastapi import APIRouter
from fastapi.testclient import TestClient

from baja_studio.core.module import Module
from baja_studio.core.state import Message
from baja_studio.main import (
    DATA_DIRECTORY_ENVIRONMENT_VARIABLE,
    DATABASE_FILE_NAME,
    create_app,
    default_data_directory,
)
from baja_studio.modules.home import HomeModule
from tests.conftest import SAFETY_TIMEOUT_SECONDS, AppFactory, FakeClock, eventually


def test_the_data_directory_can_be_set_by_environment_variable(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv(DATA_DIRECTORY_ENVIRONMENT_VARIABLE, str(tmp_path / "custom"))

    assert default_data_directory() == tmp_path / "custom"


def test_the_default_data_directory_depends_on_the_platform(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(DATA_DIRECTORY_ENVIRONMENT_VARIABLE, raising=False)

    expected_ending = "BajaStudio" if sys.platform == "win32" else "baja-studio"
    assert default_data_directory().name == expected_ending


def test_create_app_registers_every_module_in_home_page_order(
    tmp_path: Path, fake_clock: FakeClock
) -> None:
    app = create_app(data_directory=tmp_path, clock=fake_clock, log_to_file=False)

    names = [module.name for module in app.state.services.registry.modules()]

    assert names == [
        "home",
        "cameras",
        "playout",
        "audio",
        "gateway",
        "agents",
        "heartbeat",
        "echo",
    ]
    assert (tmp_path / DATABASE_FILE_NAME).is_file()


def test_the_system_retention_setting_reaches_the_command_log(
    tmp_path: Path, fake_clock: FakeClock
) -> None:
    app = create_app(data_directory=tmp_path, clock=fake_clock, log_to_file=False)
    services = app.state.services
    services.command_log.append("echo", "web:1", "say", {}, "ok")
    fake_clock.advance(2 * 24 * 3600)

    # Keep only one day: the two-day-old entry goes at the next prune.
    services.settings.update("system", {"command_log_retention_days": 1})
    services.command_log.prune()

    assert services.command_log.tail() == []


class _DeviceContractModule(Module):
    """A module that, like audio's D200 contract, serves a URL outside `/api`."""

    name = "contract"
    title = "Contract"

    def __init__(self, path: str = "/action/ping") -> None:
        super().__init__()
        self._path = path

    def root_routes(self) -> APIRouter:
        router = APIRouter()

        @router.get(self._path)
        def answer() -> dict[str, bool]:
            return {"ok": True}

        return router


def test_root_routes_are_served_at_the_root_ahead_of_the_frontend_pages(
    make_app: AppFactory, frontend_directory: Path
) -> None:
    # A built frontend answers every unknown path with index.html (client-side
    # routes). The module's /action/ping must win over that catch-all.
    (frontend_directory / "index.html").write_text("<html>page</html>")
    app = make_app(lambda registry: [HomeModule(registry), _DeviceContractModule()])

    with TestClient(app) as client:
        assert client.get("/action/ping").json() == {"ok": True}
        assert "page" in client.get("/action/other").text


@pytest.mark.parametrize("path", ["/api", "/api/health", "/api/contract/x", "/ws", "/ws/extra"])
def test_a_root_route_may_not_claim_a_reserved_prefix(make_app: AppFactory, path: str) -> None:
    # /api and /ws belong to the core; a module claiming them is a programming error,
    # caught when the app is built rather than when a request happens to collide.
    with pytest.raises(ValueError, match=f"cannot mount '{path}' at the root"):
        make_app(lambda registry: [_DeviceContractModule(path)])


@pytest.mark.parametrize("path", ["/apiary", "/wsx", "/action/api"])
def test_a_root_route_that_only_shares_letters_with_a_reserved_prefix_is_served(
    make_app: AppFactory, path: str
) -> None:
    # Reserved by whole path segment, so /apiary is a different path from /api.
    app = make_app(lambda registry: [HomeModule(registry), _DeviceContractModule(path)])

    with TestClient(app) as client:
        assert client.get(path).json() == {"ok": True}


WRITERS = 4
WRITES_PER_REQUEST = 200


class _CountingModule(Module):
    """Each request to its root route counts one writer's state value up from 1."""

    name = "counting"
    title = "Counting"

    def __init__(self) -> None:
        super().__init__()
        # Every request waits here, so all of them write at the same time.
        self._start_together = threading.Barrier(WRITERS, timeout=SAFETY_TIMEOUT_SECONDS)

    def root_routes(self) -> APIRouter:
        router = APIRouter()

        # A plain `def`, so FastAPI runs it on the thread pool, as the D200 routes do.
        @router.get("/action/count/{writer}")
        def count(writer: str) -> dict[str, bool]:
            self._start_together.wait()
            for value in range(1, WRITES_PER_REQUEST + 1):
                self.context.update_state(f"counts.{writer}", value)
            return {"ok": True}

        return router


def _counts(tree: dict[str, Any]) -> dict[str, int]:
    counts: dict[str, int] = tree.get("counting", {}).get("counts", {})
    return counts


def _replay(snapshot: Message, messages: list[Message]) -> dict[str, Any]:
    """Apply what a browser received to its snapshot, checking order on the way."""
    tree: dict[str, Any] = snapshot["state"]
    seq = snapshot["seq"]
    for message in messages:
        if message["type"] == "snapshot":  # a slow client's backlog was replaced
            tree, seq = message["state"], message["seq"]
            continue
        assert message["seq"] == seq + 1, "a patch went missing or arrived twice"
        seq = message["seq"]
        if message["module"] != "counting":  # home's own updates share the sequence
            continue
        writer = message["path"].removeprefix("counts.")
        assert message["value"] > _counts(tree).get(writer, 0), f"{writer} went backwards"
        tree.setdefault("counting", {}).setdefault("counts", {})[writer] = message["value"]
    return tree


def test_root_routes_writing_state_concurrently_reach_the_websocket_in_order(
    make_app: AppFactory,
) -> None:
    # Several D200 buttons pressed at once means several root requests on
    # thread-pool threads, all writing state while browsers are connected.
    # A browser must see one gap-free sequence of patches in which no value
    # goes backwards, and replaying it must rebuild the server's tree.
    app = make_app(lambda registry: [HomeModule(registry), _CountingModule()])
    writers = [f"w{index}" for index in range(WRITERS)]
    finished = dict.fromkeys(writers, WRITES_PER_REQUEST)
    received: list[Message] = []
    all_received = threading.Event()

    # The socket is read on its own thread so that a store that stops sending
    # fails the test after the safety timeout instead of blocking it forever.
    def read_until_finished(socket: Any) -> None:
        seen: dict[str, int] = {}
        while seen != finished:
            message = socket.receive_json()
            received.append(message)
            if message["type"] == "snapshot":
                seen = dict(_counts(message["state"]))
            elif message["module"] == "counting":
                seen[message["path"].removeprefix("counts.")] = message["value"]
        all_received.set()

    # Switch threads far more often than the default 5 ms, so the writers interleave.
    switch_interval = sys.getswitchinterval()
    sys.setswitchinterval(1e-6)
    try:
        with TestClient(app) as client, client.websocket_connect("/ws") as socket:
            snapshot = socket.receive_json()
            reader = threading.Thread(target=read_until_finished, args=(socket,), daemon=True)
            reader.start()
            with ThreadPoolExecutor(max_workers=WRITERS) as pool:
                responses = list(pool.map(lambda w: client.get(f"/action/count/{w}"), writers))
            assert [response.json() for response in responses] == [{"ok": True}] * WRITERS
            eventually(all_received.is_set, "the browser never received every final value")
            server_counts = app.state.services.state.get("counting", "counts")
    finally:
        sys.setswitchinterval(switch_interval)

    assert server_counts == finished
    assert _counts(_replay(snapshot, received)) == finished
