"""Tests for the app factory and data directory choice (main.py)."""

import sys
from pathlib import Path

import pytest

from baja_studio.main import (
    DATA_DIRECTORY_ENVIRONMENT_VARIABLE,
    DATABASE_FILE_NAME,
    create_app,
    default_data_directory,
)
from tests.conftest import FakeClock


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


def test_create_app_registers_home_cameras_agents_heartbeat_and_echo_in_that_order(
    tmp_path: Path, fake_clock: FakeClock
) -> None:
    app = create_app(data_directory=tmp_path, clock=fake_clock, log_to_file=False)

    names = [module.name for module in app.state.services.registry.modules()]

    assert names == ["home", "cameras", "agents", "heartbeat", "echo"]
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
