"""Messages between production and the hosts in SQLite, kept for the session (WP9, WP9b).

A message (WP9 brief §2, WP9b §1):

    {id, ts, from: "production:<client id>" | "seat:<id>",
     to: seat id | "all" | "production", text, kind ("info" | "question" | "urgent"),
     expires_s, expires_at,
     reactions: [{seat_id, reaction, ts, by}], reply_text, reply_seat_id, reply_by, reply_at,
     shown: [{seat_id, ts}], dismissed_at, dismissed_by: [{seat_id, ts}], read_at}

Production writes to a seat or to all; a host writes to production only
(no host-to-host messages). On a host's message production reacts and
replies (`seat_id` null, `by` the producer); on production's, the hosts do.

`shown` is delivery: a hosts screen showing the message for one of its
seats says so once per seat. `read_at` is production having seen a host's
message (the unread badge). A message is *answered* once it has a reaction
or a reply. `dismissed_at` is production waving it away (it fades
everywhere); `dismissed_by` lists the seats whose screen hid it.

The session lasts until production clears it (`clear_messages`), so a
service restart in the middle of a show keeps the conversation.
"""

import json
import sqlite3
from datetime import datetime, timedelta
from typing import Any

from baja_studio.core.clock import Clock
from baja_studio.core.database import Database

MESSAGE_KINDS = ("info", "question", "urgent")
PRODUCTION = "production"
# How many messages the state tree carries; the database keeps the whole session.
MESSAGES_IN_STATE = 50

SCHEMA = """CREATE TABLE IF NOT EXISTS hosts_messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    sender TEXT NOT NULL,
    target TEXT NOT NULL,
    text TEXT NOT NULL,
    kind TEXT NOT NULL,
    expires_s INTEGER,
    reactions_json TEXT NOT NULL DEFAULT '[]',
    reply_text TEXT,
    reply_seat_id INTEGER,
    shown_json TEXT NOT NULL DEFAULT '[]',
    dismissed_at TEXT
)"""
# Columns added after WP9 shipped: added to an existing table when missing.
ADDED_COLUMNS = {
    "dismissed_by_json": "TEXT NOT NULL DEFAULT '[]'",
    "read_at": "TEXT",
    "reply_by": "TEXT",
    "reply_at": "TEXT",
}


class MessageError(Exception):
    """A change to a message was refused; the message is for the operator."""


def create_hosts_tables(database: Database) -> None:
    """Create the messages table, or add the columns WP9b added; called when the module starts."""
    with database.transaction() as connection:
        connection.execute(SCHEMA)
        existing = {row["name"] for row in connection.execute("PRAGMA table_info(hosts_messages)")}
        for column, definition in ADDED_COLUMNS.items():
            if column not in existing:
                connection.execute(f"ALTER TABLE hosts_messages ADD COLUMN {column} {definition}")


def seat_sender(seat_id: int) -> str:
    """How a host's message names its sender: "seat:2"."""
    return f"seat:{seat_id}"


class MessageStore:
    """Reads and writes `hosts_messages`; messages come back as the pages see them."""

    def __init__(self, database: Database, clock: Clock) -> None:
        """Use the shared database; the table must exist."""
        self._database = database
        self._clock = clock

    def add(
        self, sender: str, target: int | str, text: str, kind: str, expires_s: int | None
    ) -> dict[str, Any]:
        """Store a new message and return it."""
        now = self._clock.now_utc().isoformat()
        with self._database.transaction() as connection:
            cursor = connection.execute(
                "INSERT INTO hosts_messages (ts, sender, target, text, kind, expires_s) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (now, sender, str(target), text, kind, expires_s),
            )
            message_id = int(cursor.lastrowid or 0)
        return self.get(message_id)

    def get(self, message_id: int) -> dict[str, Any]:
        """One message, or `MessageError` if it is not (or no longer) there."""
        with self._database.transaction() as connection:
            row = connection.execute(f"{_SELECT} WHERE id = ?", (message_id,)).fetchone()
        if row is None:
            raise MessageError("that message is no longer there; the history was cleared")
        return _message_view(row)

    def recent(self, limit: int = MESSAGES_IN_STATE) -> list[dict[str, Any]]:
        """The newest `limit` messages, oldest first (the order they are read in)."""
        with self._database.transaction() as connection:
            rows = connection.execute(f"{_SELECT} ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return [_message_view(row) for row in reversed(rows)]

    def all(self) -> list[dict[str, Any]]:
        """Every message of the session, oldest first, for the export."""
        with self._database.transaction() as connection:
            rows = connection.execute(f"{_SELECT} ORDER BY id").fetchall()
        return [_message_view(row) for row in rows]

    def add_reaction(
        self, message_id: int, seat_id: int | None, reaction: str, by: str
    ) -> dict[str, Any]:
        """Add a reaction (emoji or quick reply) from a seat, or production (`seat_id` None)."""
        message = self.get(message_id)
        reactions = [
            *message["reactions"],
            {"seat_id": seat_id, "reaction": reaction, "ts": self._now(), "by": by},
        ]
        self._write(message_id, "reactions_json = ?", json.dumps(reactions))
        return self.get(message_id)

    def set_reply(self, message_id: int, seat_id: int | None, text: str, by: str) -> dict[str, Any]:
        """A typed answer from a seat, or production (`seat_id` None); a later one replaces it."""
        self.get(message_id)
        with self._database.transaction() as connection:
            connection.execute(
                "UPDATE hosts_messages SET reply_text = ?, reply_seat_id = ?, reply_by = ?, "
                "reply_at = ? WHERE id = ?",
                (text, seat_id, by, self._now(), message_id),
            )
        return self.get(message_id)

    def mark_shown(self, message_id: int, seat_ids: list[int]) -> dict[str, Any] | None:
        """Record that a hosts screen showed the message to these seats; None if nothing new."""
        message = self.get(message_id)
        already = {entry["seat_id"] for entry in message["shown"]}
        new = [seat_id for seat_id in seat_ids if seat_id not in already]
        if not new:
            return None
        now = self._now()
        shown = [*message["shown"], *({"seat_id": seat_id, "ts": now} for seat_id in new)]
        self._write(message_id, "shown_json = ?", json.dumps(shown))
        return self.get(message_id)

    def dismiss(self, message_id: int) -> dict[str, Any]:
        """Production waves a message away (it fades on every page); twice changes nothing."""
        message = self.get(message_id)
        if message["dismissed_at"] is None:
            self._write(message_id, "dismissed_at = ?", self._now())
        return self.get(message_id)

    def dismiss_for_seats(self, message_id: int, seat_ids: list[int]) -> dict[str, Any] | None:
        """These seats' screen hid the message (it stays gone for them); None if nothing new."""
        message = self.get(message_id)
        already = {entry["seat_id"] for entry in message["dismissed_by"]}
        new = [seat_id for seat_id in seat_ids if seat_id not in already]
        if not new:
            return None
        now = self._now()
        dismissed = [
            *message["dismissed_by"],
            *({"seat_id": seat_id, "ts": now} for seat_id in new),
        ]
        self._write(message_id, "dismissed_by_json = ?", json.dumps(dismissed))
        return self.get(message_id)

    def mark_read(self, message_ids: list[int]) -> int:
        """Production has seen these hosts' messages; returns how many were unread."""
        if not message_ids:
            return 0
        marks = ",".join("?" * len(message_ids))
        with self._database.transaction() as connection:
            cursor = connection.execute(
                f"UPDATE hosts_messages SET read_at = ? WHERE read_at IS NULL "
                f"AND target = ? AND id IN ({marks})",
                (self._now(), PRODUCTION, *message_ids),
            )
        return cursor.rowcount

    def clear(self) -> int:
        """Start a new session: forget every message; returns how many there were."""
        with self._database.transaction() as connection:
            cursor = connection.execute("DELETE FROM hosts_messages")
        return cursor.rowcount

    def _write(self, message_id: int, assignment: str, value: Any) -> None:
        with self._database.transaction() as connection:
            connection.execute(
                f"UPDATE hosts_messages SET {assignment} WHERE id = ?", (value, message_id)
            )

    def _now(self) -> str:
        return self._clock.now_utc().isoformat()


_SELECT = (
    "SELECT id, ts, sender, target, text, kind, expires_s, reactions_json, reply_text, "
    "reply_seat_id, reply_by, reply_at, shown_json, dismissed_at, dismissed_by_json, read_at "
    "FROM hosts_messages"
)


def _target(stored: str) -> int | str:
    return stored if stored in ("all", PRODUCTION) else int(stored)


def _message_view(row: sqlite3.Row) -> dict[str, Any]:
    expires_at = None
    if row["expires_s"]:
        sent = datetime.fromisoformat(row["ts"])
        expires_at = (sent + timedelta(seconds=row["expires_s"])).isoformat()
    return {
        "id": row["id"],
        "ts": row["ts"],
        "from": row["sender"],
        "to": _target(row["target"]),
        "text": row["text"],
        "kind": row["kind"],
        "expires_s": row["expires_s"],
        "expires_at": expires_at,
        "reactions": json.loads(row["reactions_json"]),
        "reply_text": row["reply_text"],
        "reply_seat_id": row["reply_seat_id"],
        "reply_by": row["reply_by"],
        "reply_at": row["reply_at"],
        "shown": json.loads(row["shown_json"]),
        "dismissed_at": row["dismissed_at"],
        "dismissed_by": json.loads(row["dismissed_by_json"]),
        "read_at": row["read_at"],
    }
