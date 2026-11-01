"""Tests for the Module base class and its context (core/module.py)."""

import logging
from pathlib import Path

import pytest

from baja_studio.core.commandlog import CommandLog
from baja_studio.core.database import Database
from baja_studio.core.module import CommandError, Module, ModuleContext, ModuleLogger
from baja_studio.core.settings import ModuleSettings, SettingsStore
from baja_studio.core.state import StateStore
from baja_studio.core.supervisor import Supervisor
from tests.conftest import FakeClock


class BareModule(Module):
    """A module that overrides nothing, to check the defaults."""

    name = "bare"
    title = "Bare"


@pytest.fixture
def context(tmp_path: Path, fake_clock: FakeClock) -> ModuleContext:
    """A context built by hand, the way the registry would build it."""
    database = Database(tmp_path / "module-test.sqlite3")
    settings = SettingsStore(database, fake_clock)
    settings.register("bare", ModuleSettings)
    return ModuleContext(
        name="bare",
        settings=settings,
        state=StateStore(),
        command_log=CommandLog(database, fake_clock),
        supervisor=Supervisor(fake_clock),
        clock=fake_clock,
        logger=ModuleLogger(logging.getLogger("test.bare"), {"studio_module": "bare"}),
    )


def test_the_defaults_have_no_state_no_workers_and_no_routes() -> None:
    module = BareModule()

    assert module.initial_state() == {}
    assert module.workers() == []
    assert module.routes() is None


def test_an_unknown_command_is_refused_with_a_command_error() -> None:
    with pytest.raises(CommandError, match="has no command 'jump'"):
        BareModule().handle_command("jump", {}, "web:test")


def test_using_the_context_before_start_is_a_clear_error() -> None:
    with pytest.raises(RuntimeError, match="has not been started"):
        _ = BareModule().context


def test_start_stores_the_context(context: ModuleContext) -> None:
    module = BareModule()

    module.start(context)

    assert module.context is context


def test_update_state_writes_into_the_modules_own_subtree(context: ModuleContext) -> None:
    context.update_state("lamp", "on")

    assert context.state.get("bare", "lamp") == "on"


def test_current_settings_returns_this_modules_settings(context: ModuleContext) -> None:
    assert context.current_settings() == ModuleSettings()


def test_new_child_process_creates_an_unstarted_host_owned_by_the_module(
    context: ModuleContext,
) -> None:
    host = context.new_child_process("baja_studio.childprocs.echo_child")

    assert host.python_module == "baja_studio.childprocs.echo_child"
    assert not host.is_running


def test_the_module_logger_tags_records_and_keeps_other_extras(
    caplog: pytest.LogCaptureFixture,
) -> None:
    # caplog is a pytest fixture that captures log records for inspection.
    module_logger = ModuleLogger(logging.getLogger("test.tagging"), {"studio_module": "demo"})

    with caplog.at_level(logging.INFO, logger="test.tagging"):
        module_logger.info("hello", extra={"worker": "ticker"})

    record = caplog.records[-1]
    assert record.studio_module == "demo"  # type: ignore[attr-defined]
    assert record.worker == "ticker"  # type: ignore[attr-defined]
