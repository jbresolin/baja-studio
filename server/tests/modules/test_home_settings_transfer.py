"""Tests for settings export and import as the home module serves them
(modules/home/settings_transfer.py).

The page exports with a command, uploads a file to `POST /api/home/settings-import`,
then sends the dry-run and apply commands naming the upload. These tests run
the real module list (with the usual fakes) and play the page's part.
"""

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from baja_studio.core.module import CommandError
from baja_studio.core.registry import ModuleRegistry
from baja_studio.core.services import StudioServices
from baja_studio.main import build_module_list, create_app
from baja_studio.modules.home.settings_transfer import MAX_UPLOAD_BYTES
from tests.conftest import AppFactory, FakeClock
from tests.core.fake_system_probes import FakeSystemProbes
from tests.modules.gateway_studio import GatewayFakes

TOKEN = "cf-token-0123456789abcdef"
# No OBS host: the real module list would try to reach it over the network.
OBS = {"scene_a": "Replay A", "password": "obs-password", "cut_transition": "Corte"}


class Page:
    """Plays the Settings page against one running studio."""

    def __init__(self, client: TestClient) -> None:
        self.client = client
        app: FastAPI = client.app  # type: ignore[assignment]
        self.services: StudioServices = app.state.services

    def command(self, command: str, **args: Any) -> Any:
        return self.services.registry.dispatch_command("home", command, args, "web:alice")

    def upload(self, content: bytes) -> str:
        response = self.client.post("/api/home/settings-import", content=content)
        assert response.status_code == 200, response.text
        upload_id: str = response.json()["upload_id"]
        return upload_id

    def export(self) -> bytes:
        return json.dumps(self.command("export_settings")["document"]).encode("utf-8")


@pytest.fixture
def page(make_app: AppFactory) -> Iterator[Page]:
    """The streaming PC, set up: gateway with a token, playout with OBS and a password."""
    with TestClient(make_app(None)) as client:
        studio = Page(client)
        studio.services.settings.update(
            "gateway",
            {
                "hostname": "live.example.net",
                "tls": {"mode": "dns01", "cloudflare_api_token": TOKEN},
            },
        )
        studio.services.settings.update("playout", {"obs": OBS})
        yield studio


@pytest.fixture
def fresh_pc(
    tmp_path: Path,
    fake_clock: FakeClock,
    gateway_fakes: GatewayFakes,
    system_probes: FakeSystemProbes,
) -> Iterator[Page]:
    """A second studio with its own (empty) data folder: the PC after a reinstall."""

    def modules(registry: ModuleRegistry) -> list[Any]:
        return build_module_list(
            registry,
            gateway_fakes.services,
            gateway_fakes.system(),
            system_probes=system_probes.probes(),
        )

    app = create_app(
        data_directory=tmp_path / "reinstalled",
        clock=fake_clock,
        frontend_directory=tmp_path,
        coalesce_seconds=0.0,
        log_to_file=False,
        module_list_builder=modules,
    )
    with TestClient(app) as client:
        yield Page(client)


def test_export_gives_a_named_file_with_every_module_and_no_secret(page: Page) -> None:
    result = page.command("export_settings")

    assert result["file_name"].startswith("BajaStudio-settings-")
    assert result["file_name"].endswith("-20261001-120000.json")
    document = result["document"]
    assert {"home", "readiness", "cameras", "playout", "audio", "gateway", "agents"} <= set(
        document["modules"]
    )
    text = json.dumps(document)
    assert TOKEN not in text and "obs-password" not in text
    assert document["secrets_required"] == [
        "playout.obs.password",
        "gateway.tls.cloudflare_api_token",
    ]


def test_a_reinstalled_pc_takes_the_file_after_a_dry_run(page: Page, fresh_pc: Page) -> None:
    # Bench §7 step 3: export, reinstall, upload, look at the diff, apply.
    upload_id = fresh_pc.upload(page.export())

    dry_run = fresh_pc.command("import_settings_dryrun", upload_id=upload_id)
    playout = next(section for section in dry_run["sections"] if section["section"] == "playout")
    added = {change["key"]: change["new"] for change in playout["added"]}
    assert added["obs.scene_a"] == "Replay A"
    assert playout["changed"] == [] and playout["removed"] == []
    assert playout["secrets_to_enter"] == ["playout.obs.password"]

    result = fresh_pc.command(
        "import_settings_apply", upload_id=upload_id, token=dry_run["token"], apply_partial=False
    )

    assert {"section": "playout", "result": "applied"} in result["results"]
    assert result["secrets_to_enter"] == [
        "playout.obs.password",
        "gateway.tls.cloudflare_api_token",
    ]
    obs = fresh_pc.services.settings.get("playout").model_dump()["obs"]
    assert (obs["scene_a"], obs["cut_transition"], obs["password"]) == ("Replay A", "Corte", "")


def test_the_command_log_names_the_upload_never_the_file(page: Page, fresh_pc: Page) -> None:
    upload_id = fresh_pc.upload(page.export())
    token = fresh_pc.command("import_settings_dryrun", upload_id=upload_id)["token"]
    fresh_pc.command("import_settings_apply", upload_id=upload_id, token=token)

    entries = fresh_pc.services.command_log.tail(module="home")

    assert [(e["source"], e["command"], e["result"]) for e in entries[:2]] == [
        ("web:alice", "import_settings_apply", "ok"),
        ("web:alice", "import_settings_dryrun", "ok"),
    ]
    assert entries[0]["args"] == {"upload_id": upload_id, "token": token}


def test_apply_with_a_stale_token_is_refused(page: Page) -> None:
    # Someone saved a setting between the dry run and the apply: the operator
    # must look at the diff again.
    upload_id = page.upload(page.export())
    token = page.command("import_settings_dryrun", upload_id=upload_id)["token"]
    page.services.settings.update("readiness", {"interval_minutes": 9})

    with pytest.raises(CommandError, match="run the dry run again"):
        page.command("import_settings_apply", upload_id=upload_id, token=token)


def test_an_unknown_upload_is_refused(page: Page) -> None:
    with pytest.raises(CommandError, match="choose the file again"):
        page.command("import_settings_dryrun", upload_id="nope")


def test_a_file_that_is_not_an_export_is_refused_readably(page: Page) -> None:
    upload_id = page.upload(b'{"hello": "world"}')

    with pytest.raises(CommandError, match="not a Baja Studio settings file"):
        page.command("import_settings_dryrun", upload_id=upload_id)


def test_empty_and_huge_uploads_are_refused(page: Page) -> None:
    empty = page.client.post("/api/home/settings-import", content=b"")
    huge = page.client.post("/api/home/settings-import", content=b"x" * (MAX_UPLOAD_BYTES + 1))

    assert empty.status_code == 400
    assert huge.status_code == 413


def test_operational_state_survives_an_import(page: Page) -> None:
    # The command log (like the queue and the audio links) is not settings:
    # importing settings leaves it alone.
    page.services.command_log.append("audio", "device:mvave", "set_mute", {"channel": "1"}, "ok")
    before = page.services.command_log.tail(limit=1000)
    upload_id = page.upload(page.export())
    token = page.command("import_settings_dryrun", upload_id=upload_id)["token"]
    page.command("import_settings_apply", upload_id=upload_id, token=token)

    after = page.services.command_log.tail(limit=1000)

    assert after[-len(before) :] == before
