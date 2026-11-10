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
        "shown": [],
        "dismissed_at": None,
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
    reacted = store.add_reaction(message_id, 2, "👍")
    assert reacted["reactions"] == [
        {"seat_id": 2, "reaction": "👍", "ts": "2026-10-01T12:00:02+00:00"}
    ]
    replied = store.set_reply(message_id, 2, "one more lap")
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

    store.add_reaction(message_id, 1, "👍")
    message = store.add_reaction(message_id, 2, "need 1 min")

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
        store.add_reaction(message_id, 1, "👍")
