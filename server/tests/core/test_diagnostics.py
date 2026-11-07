"""Tests for the diagnostics bundle (core/diagnostics.py).

The bundle is built from a real app's services (only the heartbeat module,
plus a fake gateway section of settings with a token), then opened as a zip
to check what is inside, and, above all, what is not.
"""

import json
import zipfile
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import Field

from baja_studio.core.diagnostics import (
    CUT_MARKER,
    DiagnosticsSources,
    build_bundle,
    read_tail,
    scrub,
)
from baja_studio.core.module import Module
from baja_studio.core.services import StudioServices
from baja_studio.core.settings import ModuleSettings
from baja_studio.modules.heartbeat import HeartbeatModule
from tests.conftest import AppFactory, FakeClock
from tests.modules.gateway_studio import FakeServiceControl

TOKEN = "cf-token-0123456789abcdef"


class SecretSettings(ModuleSettings):
    """A module with a secret, standing in for the gateway's token."""

    token: str = Field(default="", json_schema_extra={"secret": True})


class SecretModule(Module):
    name = "secretive"
    title = "Secretive"
    SettingsModel = SecretSettings


@pytest.fixture
def services(make_app: AppFactory) -> Iterator[StudioServices]:
    app = make_app(lambda registry: [HeartbeatModule(), SecretModule()])
    services: StudioServices = app.state.services
    with TestClient(app):
        services.settings.update("secretive", {"token": TOKEN})
        yield services


def sources(services: StudioServices, data_directory: Path) -> DiagnosticsSources:
    return DiagnosticsSources(
        data_directory=data_directory,
        log_directory=data_directory / "logs",
        caddyfile=data_directory / "caddy" / "Caddyfile",
        clock=services.clock,
        settings=services.settings,
        state=services.state,
        command_log=services.command_log,
        module_names=lambda: [module.name for module in services.registry.modules()],
        health=services.registry.health_report,
        service_control=FakeServiceControl(),
    )


def open_bundle(path: Path) -> dict[str, str]:
    with zipfile.ZipFile(path) as bundle:
        return {name: bundle.read(name).decode("utf-8") for name in bundle.namelist()}


def write_log(data_directory: Path, name: str, text: str) -> None:
    folder = data_directory / "logs"
    folder.mkdir(parents=True, exist_ok=True)
    # Exact bytes: on Windows `write_text` would turn each "\n" into "\r\n".
    (folder / name).write_bytes(text.encode("utf-8"))


def test_the_bundle_holds_logs_settings_health_state_commands_versions_and_services(
    services: StudioServices, data_directory: Path
) -> None:
    write_log(data_directory, "baja-studio.log", '{"message": "hello"}\n')
    (data_directory / "caddy").mkdir(parents=True)
    (data_directory / "caddy" / "Caddyfile").write_text(":80 {\n}\n", encoding="utf-8")
    services.registry.dispatch_command("heartbeat", "reset", {}, "web:test")

    members = open_bundle(build_bundle(sources(services, data_directory)))

    assert sorted(members) == [
        "Caddyfile",
        "commands/heartbeat.json",
        "commands/secretive.json",
        "health.json",
        "logs/baja-studio.log",
        "services.json",
        "settings.json",
        "state.json",
        "versions.json",
    ]
    assert json.loads(members["commands/heartbeat.json"])[0]["command"] == "reset"
    assert json.loads(members["health.json"])["status"] == "ok"
    assert "heartbeat" in json.loads(members["state.json"])
    assert set(json.loads(members["versions.json"])) == {"app", "python", "os", "caddy"}
    assert json.loads(members["services.json"]) == {
        "BajaStudio": "SERVICE_RUNNING",
        "BajaStudioGateway": "SERVICE_RUNNING",
    }


def test_the_bundle_is_named_by_time_and_lives_in_the_data_folder(
    services: StudioServices, data_directory: Path
) -> None:
    path = build_bundle(sources(services, data_directory))

    # The fake clock starts at 2026-10-01 12:00:00 UTC.
    assert path == data_directory / "diagnostics" / "BajaStudio-diag-20261001-120000.zip"


def test_settings_are_masked_and_a_secret_is_scrubbed_from_every_file(
    services: StudioServices, data_directory: Path
) -> None:
    # Say a library logged the token by mistake: the bundle must still not carry it.
    write_log(data_directory, "service-stderr.log", f"connecting with token {TOKEN}\n")

    members = open_bundle(build_bundle(sources(services, data_directory)))

    assert json.loads(members["settings.json"])["secretive"]["token"] == "********"
    assert members["logs/service-stderr.log"] == "connecting with token ********\n"
    assert not any(TOKEN in text for text in members.values())


def test_the_database_is_never_included(services: StudioServices, data_directory: Path) -> None:
    members = open_bundle(build_bundle(sources(services, data_directory)))

    assert (data_directory / "baja-studio.sqlite3").is_file()
    assert not any("sqlite" in name for name in members)


def test_long_logs_keep_only_their_end_from_a_whole_line(
    services: StudioServices, data_directory: Path
) -> None:
    write_log(data_directory, "big.log", "".join(f"line {number:04}\n" for number in range(1000)))

    members = open_bundle(build_bundle(sources(services, data_directory), log_tail_bytes=100))

    tail = members["logs/big.log"]
    assert tail.startswith(CUT_MARKER)
    assert tail.endswith("line 0999\n")
    # Whole lines only: the first kept line is complete.
    assert tail.removeprefix(CUT_MARKER).split("\n")[0].startswith("line ")


def test_command_history_is_capped_per_module(
    services: StudioServices, data_directory: Path
) -> None:
    for _ in range(5):
        services.registry.dispatch_command("heartbeat", "reset", {}, "web:test")

    members = open_bundle(build_bundle(sources(services, data_directory), commands_per_module=3))

    assert len(json.loads(members["commands/heartbeat.json"])) == 3


def test_only_the_five_newest_bundles_are_kept(
    services: StudioServices, data_directory: Path, fake_clock: FakeClock
) -> None:
    paths = []
    for _ in range(7):
        paths.append(build_bundle(sources(services, data_directory)))
        fake_clock.advance(1)

    remaining = sorted((data_directory / "diagnostics").iterdir())
    assert remaining == paths[2:]


def test_scrubbing_replaces_a_longer_secret_before_one_it_contains() -> None:
    assert scrub("a=abcdef b=abc", ["abc", "abcdef"]) == "a=******** b=********"


def test_a_short_file_is_read_whole(tmp_path: Path) -> None:
    path = tmp_path / "small.log"
    path.write_bytes(b"one\ntwo\n")

    assert read_tail(path, 1000) == "one\ntwo\n"
