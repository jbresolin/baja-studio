"""Tests for structured logging and the live log stream (core/logging.py)."""

import asyncio
import json
import logging
from collections.abc import Iterator
from pathlib import Path

import pytest

from baja_studio.core.logging import (
    LOG_FILE_NAME,
    ROOT_LOGGER_NAME,
    ConsoleFormatter,
    LogHub,
    configure_logging,
    prune_rotated_logs,
    record_to_entry,
    stream_log_events,
)


@pytest.fixture
def test_logger() -> Iterator[logging.Logger]:
    """A logger under the service's root logger, with handlers removed afterwards."""
    yield logging.getLogger(f"{ROOT_LOGGER_NAME}.tests")
    configure_logging(None, LogHub())


def make_record(message: str, **extra: object) -> logging.LogRecord:
    """Build a log record the way `logger.info(message, extra=...)` would."""
    record = logging.makeLogRecord({"name": "test", "levelname": "INFO", "msg": message})
    for key, value in extra.items():
        setattr(record, key, value)
    return record


def test_a_record_becomes_a_json_ready_entry_with_its_extra_fields() -> None:
    entry = record_to_entry(make_record("worker failed", studio_module="echo", worker="child"))

    assert entry["message"] == "worker failed"
    assert entry["level"] == "INFO"
    assert entry["studio_module"] == "echo"
    assert entry["worker"] == "child"
    json.dumps(entry)  # must not raise


def test_extra_values_that_are_not_json_are_stored_as_text() -> None:
    entry = record_to_entry(make_record("odd", thing=object()))

    assert entry["thing"].startswith("<object object")


def test_exceptions_are_included_as_a_traceback() -> None:
    try:
        raise ValueError("bad value")
    except ValueError:
        record = logging.makeLogRecord({"msg": "failed", "exc_info": __import__("sys").exc_info()})

    entry = record_to_entry(record)

    assert "ValueError: bad value" in entry["traceback"]


def test_the_console_format_shows_the_module_in_brackets() -> None:
    text = ConsoleFormatter().format(make_record("started", studio_module="echo"))

    assert "test: [echo] started" in text


def test_the_hub_remembers_recent_entries_up_to_its_limit() -> None:
    hub = LogHub(keep=2)
    for message in ["one", "two", "three"]:
        hub.emit(make_record(message))

    assert [entry["message"] for entry in hub.recent()] == ["two", "three"]
    assert [entry["message"] for entry in hub.recent(limit=1)] == ["three"]


def test_the_stream_sends_the_backlog_then_new_entries_and_ends_on_disconnect() -> None:
    hub = LogHub()
    hub.emit(make_record("before the browser connected"))

    async def scenario() -> list[str]:
        disconnected = asyncio.Event()

        async def client_disconnected() -> bool:
            return disconnected.is_set()

        frames = []
        stream = stream_log_events(hub, client_disconnected, poll_seconds=5)
        frames.append(await anext(stream))  # the backlog entry
        # Log from another thread, like a worker would.
        await asyncio.to_thread(hub.emit, make_record("while connected"))
        frames.append(await anext(stream))
        # The browser goes away; the stream should finish.
        disconnected.set()
        hub.emit(make_record("wake the stream so it notices"))
        frames.extend([frame async for frame in stream])
        return frames

    frames = asyncio.run(scenario())

    assert frames[0].startswith("data: ")
    assert "before the browser connected" in frames[0]
    assert "while connected" in frames[1]
    assert frames[0].endswith("\n\n")


def test_the_stream_stops_sending_to_the_hub_after_it_ends() -> None:
    hub = LogHub()

    async def scenario() -> None:
        async def always_disconnected() -> bool:
            return True

        async for _ in stream_log_events(hub, always_disconnected):
            pass

    asyncio.run(scenario())

    # The stream unsubscribed itself, so logging does not try to reach it.
    assert hub._subscribers == []


def test_configure_logging_writes_json_lines_to_the_log_file(
    tmp_path: Path, test_logger: logging.Logger
) -> None:
    hub = LogHub()
    configure_logging(tmp_path, hub)

    test_logger.info("hello file", extra={"studio_module": "demo"})

    line = (tmp_path / LOG_FILE_NAME).read_text(encoding="utf-8").strip().splitlines()[-1]
    entry = json.loads(line)
    assert entry["message"] == "hello file"
    assert entry["studio_module"] == "demo"
    assert hub.recent()[-1]["message"] == "hello file"


def test_configuring_twice_does_not_duplicate_handlers(
    tmp_path: Path, test_logger: logging.Logger
) -> None:
    configure_logging(tmp_path, LogHub())
    configure_logging(tmp_path, LogHub())

    # Console + file + hub: three handlers, not six.
    assert len(logging.getLogger(ROOT_LOGGER_NAME).handlers) == 3


def test_only_the_newest_files_nssm_rotated_out_are_kept(tmp_path: Path) -> None:
    # NSSM renames a full log with a timestamp and never deletes old ones.
    for minute in range(7):
        (tmp_path / f"service-stdout-20261106T22{minute:02}00.000.log").write_text("x")
    (tmp_path / "caddy-stderr-20261106T220000.000.log").write_text("x")
    (tmp_path / "service-stdout.log").write_text("current")

    deleted = prune_rotated_logs(tmp_path, keep=5)

    assert sorted(path.name for path in deleted) == [
        "service-stdout-20261106T220000.000.log",
        "service-stdout-20261106T220100.000.log",
    ]
    # Each log keeps its own five; the file being written is never touched.
    remaining = sorted(path.name for path in tmp_path.iterdir())
    assert "caddy-stderr-20261106T220000.000.log" in remaining
    assert "service-stdout.log" in remaining
    assert len(remaining) == 7
