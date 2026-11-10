"""Production → hosts messages in SQLite: kept for the session, exported with the aired log.

A message (WP9 brief §2):

    {id, ts, from: "production:<client id>", to: seat id | "all", text, kind
     ("info" | "question" | "urgent"), expires_s, expires_at,
     reactions: [{seat_id, reaction, ts}], reply_text, reply_seat_id,
     shown: [{seat_id, ts}], dismissed_at}

`shown` is delivery: a hosts screen showing the message for one of its
seats says so once per seat. A message is *answered* once it has a
reaction or a reply, and *dismissed* when production or a host waves it
away; the pages fade answered, dismissed and expired messages.

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


class MessageError(Exception):
    """A change to a message was refused; the message is for the operator."""


def create_hosts_tables(database: Database) -> None:
    """Create the messages table if it does not exist; called when the module starts."""
    with database.transaction() as connection:
        connection.execute(SCHEMA)


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

    def add_reaction(self, message_id: int, seat_id: int, reaction: str) -> dict[str, Any]:
        """Add a seat's reaction (an emoji or a quick reply) to a message."""
        message = self.get(message_id)
        reactions = [
            *message["reactions"],
            {"seat_id": seat_id, "reaction": reaction, "ts": self._now()},
        ]
        self._write(message_id, "reactions_json = ?", json.dumps(reactions))
        return self.get(message_id)

    def set_reply(self, message_id: int, seat_id: int, text: str) -> dict[str, Any]:
        """A seat's typed answer; a later one replaces it."""
        self.get(message_id)
        with self._database.transaction() as connection:
            connection.execute(
                "UPDATE hosts_messages SET reply_text = ?, reply_seat_id = ? WHERE id = ?",
                (text, seat_id, message_id),
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
        """Wave a message away (it fades on every page); dismissing twice changes nothing."""
        message = self.get(message_id)
        if message["dismissed_at"] is None:
            self._write(message_id, "dismissed_at = ?", self._now())
        return self.get(message_id)

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
    "reply_seat_id, shown_json, dismissed_at FROM hosts_messages"
)


def _message_view(row: sqlite3.Row) -> dict[str, Any]:
    target = row["target"]
    expires_at = None
    if row["expires_s"]:
        sent = datetime.fromisoformat(row["ts"])
        expires_at = (sent + timedelta(seconds=row["expires_s"])).isoformat()
    return {
        "id": row["id"],
        "ts": row["ts"],
        "from": row["sender"],
        "to": "all" if target == "all" else int(target),
        "text": row["text"],
        "kind": row["kind"],
        "expires_s": row["expires_s"],
        "expires_at": expires_at,
        "reactions": json.loads(row["reactions_json"]),
        "reply_text": row["reply_text"],
        "reply_seat_id": row["reply_seat_id"],
        "shown": json.loads(row["shown_json"]),
        "dismissed_at": row["dismissed_at"],
    }
