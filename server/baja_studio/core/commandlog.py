"""Who sent what command, to which module, when, and what came of it.

Every command a module handles is appended to the `commands` table, so after
an event we can reconstruct what happened. Old entries are pruned after a
configurable number of days.
"""

import json
import logging
import threading
from datetime import timedelta
from typing import Any

from baja_studio.core.clock import Clock
from baja_studio.core.database import Database

logger = logging.getLogger(__name__)

DEFAULT_RETENTION_DAYS = 7
PRUNE_EVERY_SECONDS = 3600.0


def web_source(client_id: str) -> str:
    """Source label for a command from a browser."""
    return f"web:{client_id}"


def device_source(device_name: str) -> str:
    """Source label for a command from a physical control (joystick, MIDI, D200)."""
    return f"device:{device_name}"


def api_source(caller: str) -> str:
    """Source label for a command from another program over HTTP."""
    return f"api:{caller}"


class CommandLog:
    """Appends commands to SQLite and returns the most recent ones."""

    def __init__(
        self, database: Database, clock: Clock, retention_days: int = DEFAULT_RETENTION_DAYS
    ) -> None:
        """Create the log; `retention_days` can be changed later via `set_retention_days`."""
        self._database = database
        self._clock = clock
        self._retention_days = retention_days
        self._lock = threading.Lock()
        self._last_prune_monotonic: float | None = None

    def set_retention_days(self, days: int) -> None:
        """Change how long entries are kept; called when the system setting changes."""
        with self._lock:
            self._retention_days = days
            self._last_prune_monotonic = None

    def append(
        self, module: str, source: str, command: str, args: dict[str, Any], result: str
    ) -> None:
        """Record one handled command. `result` is "ok" or "error: <message>"."""
        self._prune_if_due()
        with self._database.transaction() as connection:
            connection.execute(
                "INSERT INTO commands (ts, module, source, command, args_json, result) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (
                    self._clock.now_utc().isoformat(),
                    module,
                    source,
                    command,
                    json.dumps(args, default=repr),
                    result,
                ),
            )

    def tail(self, module: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
        """The newest `limit` entries (optionally for one module), newest first."""
        query = "SELECT ts, module, source, command, args_json, result FROM commands"
        parameters: list[Any] = []
        if module:
            query += " WHERE module = ?"
            parameters.append(module)
        query += " ORDER BY ts DESC, rowid DESC LIMIT ?"
        parameters.append(limit)
        with self._database.transaction() as connection:
            rows = connection.execute(query, parameters).fetchall()
        return [
            {
                "ts": row["ts"],
                "module": row["module"],
                "source": row["source"],
                "command": row["command"],
                "args": json.loads(row["args_json"]),
                "result": row["result"],
            }
            for row in rows
        ]

    def prune(self) -> int:
        """Delete entries older than the retention period; returns how many were deleted."""
        with self._lock:
            retention_days = self._retention_days
            self._last_prune_monotonic = self._clock.monotonic()
        cutoff = (self._clock.now_utc() - timedelta(days=retention_days)).isoformat()
        with self._database.transaction() as connection:
            deleted = connection.execute("DELETE FROM commands WHERE ts < ?", (cutoff,)).rowcount
        if deleted:
            logger.info("pruned %d old command log entries", deleted)
        return deleted

    def _prune_if_due(self) -> None:
        # Pruning on append (at most hourly) avoids a dedicated cleanup thread.
        with self._lock:
            last = self._last_prune_monotonic
        if last is None or self._clock.monotonic() - last >= PRUNE_EVERY_SECONDS:
            self.prune()
