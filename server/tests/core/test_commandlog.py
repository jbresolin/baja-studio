"""Tests for the command log (core/commandlog.py)."""

from pathlib import Path

import pytest

from baja_studio.core.commandlog import CommandLog, api_source, device_source, web_source
from baja_studio.core.database import Database
from tests.conftest import FakeClock

ONE_DAY_SECONDS = 24 * 3600


@pytest.fixture
def command_log(tmp_path: Path, fake_clock: FakeClock) -> CommandLog:
    return CommandLog(Database(tmp_path / "commands.sqlite3"), fake_clock, retention_days=7)


def test_source_labels_say_where_a_command_came_from() -> None:
    assert web_source("a1b2") == "web:a1b2"
    assert device_source("joystick") == "device:joystick"
    assert api_source("d200") == "api:d200"


def test_appended_commands_come_back_newest_first(command_log: CommandLog) -> None:
    command_log.append("echo", "web:1", "say", {"text": "a"}, "ok")
    command_log.append("echo", "web:1", "say", {"text": "b"}, "ok")

    entries = command_log.tail()

    assert [entry["args"]["text"] for entry in entries] == ["b", "a"]
    assert entries[0]["module"] == "echo"
    assert entries[0]["source"] == "web:1"
    assert entries[0]["result"] == "ok"


def test_tail_can_filter_by_module_and_limit_the_count(command_log: CommandLog) -> None:
    for number in range(5):
        command_log.append("heartbeat", "api:test", "reset", {"n": number}, "ok")
    command_log.append("echo", "api:test", "say", {}, "error: not running")

    heartbeat_entries = command_log.tail(module="heartbeat", limit=2)

    assert [entry["args"]["n"] for entry in heartbeat_entries] == [4, 3]
    assert command_log.tail(module="echo")[0]["result"] == "error: not running"


def test_timestamps_come_from_the_clock(command_log: CommandLog, fake_clock: FakeClock) -> None:
    # The fake clock starts on 2026-10-01 at 12:00 UTC (see conftest.py).
    command_log.append("echo", "web:1", "say", {}, "ok")

    assert command_log.tail()[0]["ts"].startswith("2026-10-01T12:00:00")


def test_prune_deletes_entries_older_than_the_retention_period(
    command_log: CommandLog, fake_clock: FakeClock
) -> None:
    command_log.append("echo", "web:1", "old", {}, "ok")
    # Eight days later (one more than the seven kept), log another command.
    fake_clock.advance(8 * ONE_DAY_SECONDS)
    command_log.append("echo", "web:1", "new", {}, "ok")

    # Appending prunes at most hourly, and more than an hour passed, so the
    # old entry is already gone.
    assert [entry["command"] for entry in command_log.tail()] == ["new"]


def test_changing_the_retention_applies_at_the_next_prune(
    command_log: CommandLog, fake_clock: FakeClock
) -> None:
    command_log.append("echo", "web:1", "two-days-old", {}, "ok")
    fake_clock.advance(2 * ONE_DAY_SECONDS)

    command_log.set_retention_days(1)
    deleted = command_log.prune()

    assert deleted == 1
    assert command_log.tail() == []
