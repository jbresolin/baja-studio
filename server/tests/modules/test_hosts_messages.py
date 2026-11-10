"""Tests for the hosts messages store (modules/hosts/messages.py).

Each test opens a real SQLite file in a temporary folder. Time is the fake
clock's, which starts at 2026-10-01 12:00:00 UTC, so timestamps are exact.
"""

from collections.abc import Iterator
from pathlib import Path

import pytest

from baja_studio.core.database import Database
from baja_studio.modules.hosts.messages import MessageError, MessageStore, create_hosts_tables
from tests.conftest import FakeClock

START = "2026-10-01T12:00:00+00:00"


@pytest.fixture
def database(tmp_path: Path) -> Iterator[Database]:
    database = Database(tmp_path / "studio.sqlite3")
    create_hosts_tables(database)
    yield database
    database.close()


@pytest.fixture
def store(database: Database, fake_clock: FakeClock) -> MessageStore:
    return MessageStore(database, fake_clock)


def test_a_new_message_has_the_briefs_shape(store: MessageStore) -> None:
    message = store.add("production:prod1", 2, "30 s", "info", expires_s=60)

    assert message == {
        "id": 1,
        "ts": START,
        "from": "production:prod1",
        "to": 2,
        "text": "30 s",
        "kind": "info",
        "expires_s": 60,
        "expires_at": "2026-10-01T12:01:00+00:00",
        "reactions": [],
        "reply_text": None,
        "reply_seat_id": None,
        "reply_by": None,
        "reply_at": None,
        "shown": [],
        "dismissed_at": None,
        "dismissed_by": [],
        "read_at": None,
    }


def test_a_message_to_everyone_keeps_all_as_its_target_and_may_never_expire(
    store: MessageStore,
) -> None:
    message = store.add("production:prod1", "all", "go to break", "urgent", expires_s=None)

    assert message["to"] == "all"
    assert (message["expires_s"], message["expires_at"]) == (None, None)


def test_the_lifecycle_send_shown_reaction_reply_dismiss(
    store: MessageStore, fake_clock: FakeClock
) -> None:
    # Production sends; one second later the hosts screen shows it to seat 2.
    message_id = store.add("production:prod1", 2, "wrap up", "question", 60)["id"]
    fake_clock.advance(1)
    shown = store.mark_shown(message_id, [2])
    assert shown is not None
    assert shown["shown"] == [{"seat_id": 2, "ts": "2026-10-01T12:00:01+00:00"}]

    # The host taps 👍, then types an answer.
    fake_clock.advance(1)
    reacted = store.add_reaction(message_id, 2, "👍", "seat:2")
    assert reacted["reactions"] == [
        {"seat_id": 2, "reaction": "👍", "ts": "2026-10-01T12:00:02+00:00", "by": "seat:2"}
    ]
    replied = store.set_reply(message_id, 2, "one more lap", "seat:2")
    assert (replied["reply_text"], replied["reply_seat_id"]) == ("one more lap", 2)

    # Production waves it away.
    dismissed = store.dismiss(message_id)
    assert dismissed["dismissed_at"] == "2026-10-01T12:00:02+00:00"


def test_showing_a_message_to_the_same_seat_again_changes_nothing(store: MessageStore) -> None:
    message_id = store.add("production:prod1", "all", "hi", "info", None)["id"]
    store.mark_shown(message_id, [1])

    # The screen re-renders (or reconnects) and says it again: no new delivery entry.
    assert store.mark_shown(message_id, [1]) is None
    # A second screen showing seat 2 is new.
    again = store.mark_shown(message_id, [1, 2])
    assert again is not None
    assert [entry["seat_id"] for entry in again["shown"]] == [1, 2]


def test_reactions_accumulate_in_the_order_they_arrive(store: MessageStore) -> None:
    message_id = store.add("production:prod1", "all", "ready?", "question", None)["id"]

    store.add_reaction(message_id, 1, "👍", "seat:1")
    message = store.add_reaction(message_id, 2, "need 1 min", "seat:2")

    assert [(r["seat_id"], r["reaction"]) for r in message["reactions"]] == [
        (1, "👍"),
        (2, "need 1 min"),
    ]


def test_recent_returns_the_newest_messages_oldest_first(store: MessageStore) -> None:
    for number in range(1, 6):
        store.add("production:prod1", 1, f"message {number}", "info", None)

    assert [m["text"] for m in store.recent(limit=3)] == ["message 3", "message 4", "message 5"]
    assert len(store.all()) == 5


def test_messages_survive_a_restart_until_production_clears_them(
    database: Database, fake_clock: FakeClock
) -> None:
    MessageStore(database, fake_clock).add("production:prod1", 1, "hello", "info", None)

    # A restarted service opens the same file.
    reopened = MessageStore(database, fake_clock)
    assert [m["text"] for m in reopened.all()] == ["hello"]

    assert reopened.clear() == 1
    assert reopened.all() == []


def test_changing_a_message_that_was_cleared_is_refused(store: MessageStore) -> None:
    message_id = store.add("production:prod1", 1, "hello", "info", None)["id"]
    store.clear()

    with pytest.raises(MessageError, match="history was cleared"):
        store.add_reaction(message_id, 1, "👍", "seat:1")


def test_a_screen_hiding_a_message_is_kept_per_seat_and_said_once(
    store: MessageStore, fake_clock: FakeClock
) -> None:
    # Production asks everyone; seat 1's screen hides it (its ✕).
    message_id = store.add("production:prod1", "all", "ready?", "question", None)["id"]
    fake_clock.advance(1)

    hidden = store.dismiss_for_seats(message_id, [1])

    # Kept for seat 1 only: seat 2's screen still shows it, and production's
    # own "dismissed" (which fades it everywhere) is untouched.
    assert hidden is not None
    assert hidden["dismissed_by"] == [{"seat_id": 1, "ts": "2026-10-01T12:00:01+00:00"}]
    assert hidden["dismissed_at"] is None
    # Pressing ✕ again (a slow screen) adds nothing.
    assert store.dismiss_for_seats(message_id, [1]) is None
    # A reload reads it back from the database.
    assert store.get(message_id)["dismissed_by"][0]["seat_id"] == 1


def test_a_hosts_message_goes_to_production_and_production_answers_it(
    store: MessageStore,
) -> None:
    # Seat 2 sends "pronto"; production reacts and replies as itself.
    message = store.add("seat:2", "production", "pronto", "info", None)
    store.add_reaction(message["id"], None, "👍", "production:prod1")
    answered = store.set_reply(message["id"], None, "ok, 10 s", "production:prod1")

    assert (answered["from"], answered["to"]) == ("seat:2", "production")
    assert answered["reactions"][0]["by"] == "production:prod1"
    assert answered["reactions"][0]["seat_id"] is None
    assert (answered["reply_text"], answered["reply_by"]) == ("ok, 10 s", "production:prod1")
    assert answered["reply_at"] == START


def test_read_marks_only_unread_messages_from_hosts(store: MessageStore) -> None:
    to_host = store.add("production:prod1", 1, "30 s", "info", None)["id"]
    from_host = store.add("seat:1", "production", "repete?", "info", None)["id"]

    # Production's page says it has seen both; only the host's message counts.
    assert store.mark_read([to_host, from_host]) == 1
    assert store.get(to_host)["read_at"] is None
    assert store.get(from_host)["read_at"] == START
    # Reading it again changes nothing.
    assert store.mark_read([from_host]) == 0


def test_a_messages_table_from_wp9_gets_the_new_columns_and_keeps_its_rows(
    tmp_path: Path, fake_clock: FakeClock
) -> None:
    # A studio upgraded from WP9 has the table without WP9b's columns, with
    # a conversation in it. Starting the module must add the columns and keep
    # the messages (a restart in the middle of a show keeps the conversation).
    database = Database(tmp_path / "wp9.sqlite3")
    with database.transaction() as connection:
        connection.execute(
            "CREATE TABLE hosts_messages (id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "ts TEXT NOT NULL, sender TEXT NOT NULL, target TEXT NOT NULL, text TEXT NOT NULL, "
            "kind TEXT NOT NULL, expires_s INTEGER, reactions_json TEXT NOT NULL DEFAULT '[]', "
            "reply_text TEXT, reply_seat_id INTEGER, shown_json TEXT NOT NULL DEFAULT '[]', "
            "dismissed_at TEXT)"
        )
        connection.execute(
            "INSERT INTO hosts_messages (ts, sender, target, text, kind) "
            "VALUES (?, 'production:prod1', '1', 'old one', 'info')",
            (START,),
        )

    create_hosts_tables(database)
    [message] = MessageStore(database, fake_clock).all()
    database.close()

    assert message["text"] == "old one"
    assert (message["dismissed_by"], message["read_at"], message["reply_by"]) == ([], None, None)
