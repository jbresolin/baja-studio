"""Tests for the queue, session values and aired log (playout/queue_store.py)."""

import pytest

from baja_studio.core.database import Database
from baja_studio.playout.queue_store import QueueError, QueueStore
from tests.conftest import FakeClock


@pytest.fixture
def queue(database: Database, fake_clock: FakeClock) -> QueueStore:
    """An empty queue on the fake clock."""
    return QueueStore(database, fake_clock)


def clip_order(queue: QueueStore) -> list[int]:
    """The clip ids in play order."""
    return [entry["clip_id"] for entry in queue.entries()]


def test_clips_are_appended_in_order_with_who_added_them(
    queue: QueueStore, fake_clock: FakeClock
) -> None:
    entry_ids = queue.add([10, 11], "web:desk")
    queue.add([12], "web:tablet")

    # Play order follows the order of adding; each entry remembers its source.
    assert clip_order(queue) == [10, 11, 12]
    first = queue.entries()[0]
    assert first["entry_id"] == entry_ids[0]
    assert first["added_by"] == "web:desk"
    assert first["added_at"] == fake_clock.now_utc().isoformat()


def test_adding_at_the_front_keeps_the_given_order(queue: QueueStore) -> None:
    queue.add([1, 2], "web:a")

    queue.add([8, 9], "web:a", at_front=True)

    assert clip_order(queue) == [8, 9, 1, 2]


def test_the_same_clip_can_be_queued_twice_as_separate_entries(queue: QueueStore) -> None:
    # A replay: the clip appears twice, and each entry can be removed on its own.
    first, second = queue.add([5, 5], "web:a")

    queue.remove([first])

    assert [entry["entry_id"] for entry in queue.entries()] == [second]


def test_entries_are_moved_by_their_id_not_their_position(queue: QueueStore) -> None:
    a, b, c = queue.add([1, 2, 3], "web:a")

    # Move the last entry to the top, then the first one past the end (clamped).
    queue.move(c, 0)
    assert clip_order(queue) == [3, 1, 2]
    queue.move(a, 99)
    assert clip_order(queue) == [3, 2, 1]
    # Ids stay attached to their clips whatever moved.
    assert [entry["entry_id"] for entry in queue.entries()] == [c, b, a]


def test_an_entry_another_operator_already_removed_is_refused(queue: QueueStore) -> None:
    # Two browsers: one removed the entry, the other still shows it.
    (entry,) = queue.add([1], "web:a")
    queue.remove([entry])

    with pytest.raises(QueueError, match="no longer in the queue"):
        queue.remove([entry])
    with pytest.raises(QueueError, match="no longer in the queue"):
        queue.move(entry, 0)


def test_pop_first_takes_the_head_and_clear_empties_the_queue(queue: QueueStore) -> None:
    queue.add([1, 2, 3], "web:a")

    head = queue.pop_first()

    assert head is not None and head["clip_id"] == 1
    assert clip_order(queue) == [2, 3]
    queue.clear()
    assert queue.entries() == []
    assert queue.pop_first() is None


def test_session_values_round_trip_and_default_when_unset(queue: QueueStore) -> None:
    # What is on air and cued is saved this way, to survive a restart.
    assert queue.session_value("program", default="nothing") == "nothing"

    queue.set_session_value("program", {"clip_id": 4, "slot": "a"})
    queue.set_session_value("program", {"clip_id": 5, "slot": "b"})

    # The second save replaced the first.
    assert queue.session_value("program") == {"clip_id": 5, "slot": "b"}


def test_an_airing_is_logged_with_its_start_and_only_its_first_end(queue: QueueStore) -> None:
    aired_id = queue.start_airing(7, "Enduro · carro 42", "2026-11-07T14:05:00+00:00")

    queue.end_airing(aired_id, "2026-11-07T14:05:30+00:00")
    # A second end (a late event after the operator returned) is ignored.
    queue.end_airing(aired_id, "2026-11-07T14:09:00+00:00")

    assert queue.aired() == [
        {
            "id": aired_id,
            "clip_id": 7,
            "title": "Enduro · carro 42",
            "started_at": "2026-11-07T14:05:00+00:00",
            "ended_at": "2026-11-07T14:05:30+00:00",
            "playlist": None,
        }
    ]


def test_an_airing_from_a_playlist_is_logged_with_the_playlist_name(queue: QueueStore) -> None:
    queue.start_airing(7, "Logo", "2026-11-07T14:05:00+00:00", playlist="Patrocinadores")

    assert queue.aired()[0]["playlist"] == "Patrocinadores"


def test_queue_entries_remember_the_playlist_they_came_from(queue: QueueStore) -> None:
    queue.add([1, 2], "web:alice", playlist_id=5)
    queue.add([3], "web:alice")

    assert [entry["playlist_id"] for entry in queue.entries()] == [5, 5, None]
