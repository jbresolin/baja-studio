"""Tests for the home module (modules/home).

Home keeps `state.home.modules` up to date: one entry per module with its
title and health, which the home page shows as cards with badges.
"""

from fastapi.testclient import TestClient

from baja_studio.core.services import StudioServices
from tests.conftest import AppFactory, eventually


def test_home_lists_every_module_with_title_and_health(make_app: AppFactory) -> None:
    app = make_app(None)  # the real module list
    services: StudioServices = app.state.services

    with TestClient(app):
        # Echo needs a moment to start its child process before it is "ok".
        eventually(lambda: services.state.get("home", "modules.echo.state") == "ok")
        modules = services.state.get("home", "modules")

    assert set(modules) == {"home", "heartbeat", "echo"}
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
