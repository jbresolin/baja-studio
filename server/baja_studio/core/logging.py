"""Structured logging: JSON lines to a rotating file, readable text to the console,
and an in-memory hub that feeds the live Logs page over Server-Sent Events.

Code logs with the standard library (`logging.getLogger(__name__)`) and passes
context through `extra={"studio_module": "echo"}` (not "module": LogRecord
already uses that name for the source file); this file decides where it goes.
"""

import asyncio
import contextlib
import json
import logging
import logging.handlers
import threading
from collections import deque
from collections.abc import AsyncIterator, Awaitable, Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT_LOGGER_NAME = "baja_studio"
LOG_FILE_NAME = "baja-studio.log"
LOG_FILE_MAX_BYTES = 5 * 1024 * 1024
LOG_FILE_BACKUP_COUNT = 5
RECENT_ENTRIES_KEPT = 500

# Attributes every LogRecord has; anything else on a record came from `extra=`.
_STANDARD_RECORD_ATTRIBUTES = set(vars(logging.makeLogRecord({}))) | {"message", "asctime"}


def record_to_entry(record: logging.LogRecord) -> dict[str, Any]:
    """Turn a log record into the JSON-serialisable dict written to file and SSE."""
    entry: dict[str, Any] = {
        "ts": datetime.fromtimestamp(record.created, UTC).isoformat(),
        "level": record.levelname,
        "logger": record.name,
        "message": record.getMessage(),
    }
    for key, value in vars(record).items():
        if key not in _STANDARD_RECORD_ATTRIBUTES and not key.startswith("_"):
            entry[key] = value if _is_json_safe(value) else repr(value)
    if record.exc_info:
        entry["traceback"] = logging.Formatter().formatException(record.exc_info)
    return entry


def _is_json_safe(value: object) -> bool:
    try:
        json.dumps(value)
    except (TypeError, ValueError):
        return False
    return True


class JsonLinesFormatter(logging.Formatter):
    """Formats each record as one JSON object per line, for the log file."""

    def format(self, record: logging.LogRecord) -> str:
        """Called by the file handler for every record."""
        return json.dumps(record_to_entry(record), ensure_ascii=False)


class ConsoleFormatter(logging.Formatter):
    """Readable one-line format for the console, with the studio module when known."""

    def __init__(self) -> None:
        """Timestamp, level, logger name, then the message."""
        super().__init__("%(asctime)s %(levelname)-7s %(name)s: %(message)s")

    def format(self, record: logging.LogRecord) -> str:
        """Called by the console handler; prefixes `[module]` when the record has one."""
        text = super().format(record)
        studio_module = getattr(record, "studio_module", None)
        return text if studio_module is None else text.replace(": ", f": [{studio_module}] ", 1)


class LogHub(logging.Handler):
    """Keeps the most recent entries and pushes new ones to live subscribers.

    `emit` runs on whatever thread logged; subscribers are asyncio queues, so
    entries are handed to their event loop thread-safely.
    """

    def __init__(self, keep: int = RECENT_ENTRIES_KEPT) -> None:
        """Create an empty hub that remembers the last `keep` entries."""
        super().__init__(level=logging.DEBUG)
        self._recent: deque[dict[str, Any]] = deque(maxlen=keep)
        self._subscribers: list[tuple[asyncio.AbstractEventLoop, asyncio.Queue[dict[str, Any]]]]
        self._subscribers = []
        self._subscribers_lock = threading.Lock()

    def emit(self, record: logging.LogRecord) -> None:
        """Called by `logging` for every record; stores it and fans it out."""
        entry = record_to_entry(record)
        with self._subscribers_lock:
            self._recent.append(entry)
            subscribers = list(self._subscribers)
        for loop, queue in subscribers:
            # RuntimeError: the subscriber's loop already closed; it will unsubscribe itself.
            with contextlib.suppress(RuntimeError):
                loop.call_soon_threadsafe(queue.put_nowait, entry)

    def recent(self, limit: int | None = None) -> list[dict[str, Any]]:
        """Return the newest entries, oldest first, for the initial page load."""
        with self._subscribers_lock:
            entries = list(self._recent)
        return entries if limit is None else entries[-limit:]

    def subscribe(self) -> asyncio.Queue[dict[str, Any]]:
        """Register the calling coroutine's loop for live entries; call from async code."""
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        with self._subscribers_lock:
            self._subscribers.append((asyncio.get_running_loop(), queue))
        return queue

    def unsubscribe(self, queue: asyncio.Queue[dict[str, Any]]) -> None:
        """Stop sending entries to `queue`, when its SSE client disconnects."""
        with self._subscribers_lock:
            self._subscribers = [pair for pair in self._subscribers if pair[1] is not queue]


async def stream_log_events(
    hub: LogHub,
    client_disconnected: Callable[[], Awaitable[bool]],
    backlog: int = 200,
    poll_seconds: float = 1.0,
) -> AsyncIterator[str]:
    """Yield SSE frames: the recent backlog first, then every new entry.

    `poll_seconds` bounds how long we wait before re-checking whether the
    browser has gone away, so abandoned streams are cleaned up.
    """
    queue = hub.subscribe()
    try:
        for entry in hub.recent(backlog):
            yield _sse_frame(entry)
        while not await client_disconnected():
            try:
                entry = await asyncio.wait_for(queue.get(), timeout=poll_seconds)
            except TimeoutError:
                # A comment line keeps proxies from closing an idle stream.
                yield ": keep-alive\n\n"
                continue
            yield _sse_frame(entry)
    finally:
        hub.unsubscribe(queue)


def _sse_frame(entry: dict[str, Any]) -> str:
    return f"data: {json.dumps(entry, ensure_ascii=False)}\n\n"


def configure_logging(log_directory: Path | None, hub: LogHub, level: int = logging.INFO) -> None:
    """Attach console, rotating-file and hub handlers to the `baja_studio` logger.

    Called once at startup. Passing `log_directory=None` skips the file (tests).
    Calling it again replaces the handlers instead of duplicating them.
    """
    logger = logging.getLogger(ROOT_LOGGER_NAME)
    logger.setLevel(level)
    for old_handler in list(logger.handlers):
        logger.removeHandler(old_handler)
        old_handler.close()

    console_handler = logging.StreamHandler()
    console_handler.setFormatter(ConsoleFormatter())
    logger.addHandler(console_handler)

    if log_directory is not None:
        log_directory.mkdir(parents=True, exist_ok=True)
        file_handler = logging.handlers.RotatingFileHandler(
            log_directory / LOG_FILE_NAME,
            maxBytes=LOG_FILE_MAX_BYTES,
            backupCount=LOG_FILE_BACKUP_COUNT,
            encoding="utf-8",
        )
        file_handler.setFormatter(JsonLinesFormatter())
        logger.addHandler(file_handler)

    logger.addHandler(hub)
