"""The play queue, the session values that survive a restart, and the aired log.

Queue entries have a stable `entry_id`, so two browsers moving and removing
entries at once act on the entry they see, not on whatever now sits at a
position. Positions are kept dense (0, 1, 2 ...) after every change.
"""

import json
from typing import Any

from baja_studio.core.clock import Clock
from baja_studio.core.database import Database


class QueueError(Exception):
    """A queue change was refused; the message is for the operator."""


class QueueStore:
    """Reads and writes `playout_queue`, `playout_session` and `playout_aired`."""

    def __init__(self, database: Database, clock: Clock) -> None:
        """Use the shared database; the tables must exist."""
        self._database = database
        self._clock = clock

    def entries(self) -> list[dict[str, Any]]:
        """The queue in play order: entry_id, clip_id, added_by, added_at."""
        with self._database.transaction() as connection:
            rows = connection.execute(
                "SELECT entry_id, clip_id, added_by, added_at FROM playout_queue "
                "ORDER BY position, entry_id"
            ).fetchall()
        return [dict(row) for row in rows]

    def add(self, clip_ids: list[int], added_by: str, at_front: bool = False) -> list[int]:
        """Append clips (or put them first, in the given order); returns the new entry ids."""
        now = self._clock.now_utc().isoformat()
        ids = []
        with self._database.transaction() as connection:
            existing = [row[0] for row in connection.execute(_ORDERED_IDS)]
            for clip_id in clip_ids:
                cursor = connection.execute(
                    "INSERT INTO playout_queue (position, clip_id, added_by, added_at) "
                    "VALUES (0, ?, ?, ?)",
                    (clip_id, added_by, now),
                )
                ids.append(int(cursor.lastrowid or 0))
            order = ids + existing if at_front else existing + ids
            _renumber(connection, order)
        return ids

    def remove(self, entry_ids: list[int]) -> None:
        """Take entries out of the queue; unknown ids are refused."""
        with self._database.transaction() as connection:
            order = [row[0] for row in connection.execute(_ORDERED_IDS)]
            unknown = set(entry_ids) - set(order)
            if unknown:
                raise QueueError("that entry is no longer in the queue; refresh and try again")
            connection.executemany(
                "DELETE FROM playout_queue WHERE entry_id = ?", [(i,) for i in entry_ids]
            )
            _renumber(connection, [entry for entry in order if entry not in entry_ids])

    def move(self, entry_id: int, to_index: int) -> None:
        """Move one entry so it ends up at `to_index` (clamped to the queue)."""
        with self._database.transaction() as connection:
            order = [row[0] for row in connection.execute(_ORDERED_IDS)]
            if entry_id not in order:
                raise QueueError("that entry is no longer in the queue; refresh and try again")
            order.remove(entry_id)
            order.insert(max(0, min(to_index, len(order))), entry_id)
            _renumber(connection, order)

    def clear(self) -> None:
        """Empty the queue."""
        with self._database.transaction() as connection:
            connection.execute("DELETE FROM playout_queue")

    def pop_first(self) -> dict[str, Any] | None:
        """Take the head of the queue off and return it (None if empty)."""
        entries = self.entries()
        if not entries:
            return None
        self.remove([entries[0]["entry_id"]])
        return entries[0]

    def session_value(self, key: str, default: Any = None) -> Any:
        """A value saved with `set_session_value`, or `default`."""
        with self._database.transaction() as connection:
            row = connection.execute(
                "SELECT value FROM playout_session WHERE key = ?", (key,)
            ).fetchone()
        return json.loads(row["value"]) if row is not None else default

    def set_session_value(self, key: str, value: Any) -> None:
        """Save a JSON-serialisable value that survives a restart."""
        with self._database.transaction() as connection:
            connection.execute(
                "INSERT INTO playout_session (key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, json.dumps(value)),
            )

    def start_airing(self, clip_id: int, title: str, started_at: str) -> int:
        """Log that a clip went to air; returns the log entry id."""
        with self._database.transaction() as connection:
            cursor = connection.execute(
                "INSERT INTO playout_aired (clip_id, title, started_at) VALUES (?, ?, ?)",
                (clip_id, title, started_at),
            )
        return int(cursor.lastrowid or 0)

    def end_airing(self, aired_id: int, ended_at: str) -> None:
        """Log when that airing left the air (only the first end counts)."""
        with self._database.transaction() as connection:
            connection.execute(
                "UPDATE playout_aired SET ended_at = ? WHERE id = ? AND ended_at IS NULL",
                (ended_at, aired_id),
            )

    def aired(self) -> list[dict[str, Any]]:
        """Every airing, oldest first."""
        with self._database.transaction() as connection:
            rows = connection.execute(
                "SELECT id, clip_id, title, started_at, ended_at FROM playout_aired ORDER BY id"
            ).fetchall()
        return [dict(row) for row in rows]


_ORDERED_IDS = "SELECT entry_id FROM playout_queue ORDER BY position, entry_id"


def _renumber(connection: Any, order: list[int]) -> None:
    connection.executemany(
        "UPDATE playout_queue SET position = ? WHERE entry_id = ?",
        [(position, entry_id) for position, entry_id in enumerate(order)],
    )
