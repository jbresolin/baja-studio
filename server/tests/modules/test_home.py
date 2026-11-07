"""Tests for the home module (modules/home).

Home keeps `state.home.modules` up to date: one entry per module with its
title and health, which the home page shows as cards with badges. It also
builds the diagnostics bundle the home page's button downloads.
"""

import io
import json
import zipfile

import pytest
from fastapi.testclient import TestClient

import baja_studio
from baja_studio.core.services import StudioServices
from tests.conftest import AppFactory, eventually
from tests.modules.gateway_studio import FAKE_CADDY_VERSION


def test_home_lists_every_module_with_title_and_health(make_app: AppFactory) -> None:
    app = make_app(None)  # the real module list
    services: StudioServices = app.state.services

    with TestClient(app):
        # Echo needs a moment to start its child process before it is "ok".
        eventually(lambda: services.state.get("home", "modules.echo.state") == "ok")
        modules = services.state.get("home", "modules")

    assert set(modules) == {
        "home",
        "readiness",
        "cameras",
        "playout",
        "audio",
        "gateway",
        "agents",
        "heartbeat",
        "echo",
    }
    assert modules["heartbeat"] == {"title": "Heartbeat (demo)", "state": "ok", "message": ""}


def test_home_follows_health_changes(make_app: AppFactory) -> None:
    app = make_app(None)
    services: StudioServices = app.state.services

    with TestClient(app):
        # Turn heartbeat off; its card should switch to "stopped".
        services.settings.update("heartbeat", {"enabled": False})

        assert services.state.get("home", "modules.heartbeat.state") == "stopped"


def test_home_cannot_be_disabled(make_app: AppFactory) -> None:
    app = make_app(None)
    services: StudioServices = app.state.services

    with TestClient(app):
        services.settings.update("home", {"enabled": False})

        assert services.registry.is_running("home")


def test_home_publishes_the_service_version(make_app: AppFactory) -> None:
    app = make_app(None)
    services: StudioServices = app.state.services

    with TestClient(app):
        assert services.state.get("home", "version") == baja_studio.__version__


def test_a_diagnostics_bundle_is_built_and_downloaded(make_app: AppFactory) -> None:
    # The home page's button sends this command, then follows the returned URL.
    app = make_app(None)
    services: StudioServices = app.state.services

    with TestClient(app) as client:
        result = services.registry.dispatch_command("home", "diagnostics_bundle", {}, "web:t")
        response = client.get(result["url"])

    assert result["url"] == f"/api/home/diagnostics/{result['file_name']}"
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/zip"
    with zipfile.ZipFile(io.BytesIO(response.content)) as bundle:
        assert "settings.json" in bundle.namelist()
        # The gateway's state is in the bundle, with Caddy's version.
        assert json.loads(bundle.read("versions.json"))["caddy"] == FAKE_CADDY_VERSION


@pytest.mark.parametrize(
    "file_name", ["baja-studio.sqlite3", "..%2Fbaja-studio.sqlite3", "BajaStudio-diag-1.zip"]
)
def test_only_bundles_can_be_downloaded(make_app: AppFactory, file_name: str) -> None:
    # The route serves files from the data folder: anything but a bundle is refused.
    with TestClient(make_app(None)) as client:
        response = client.get(f"/api/home/diagnostics/{file_name}")

    assert response.status_code == 404
