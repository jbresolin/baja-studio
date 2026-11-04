"""Tests for playout's state publisher (modules/playout/publisher.py).

The publisher turns the library, queue and switcher into the `playout` state
tree. Here it writes into a plain dict instead of the live state store, so
each test can look at exactly what a browser would receive.
"""

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from baja_studio.adapters.obs_requests import MediaStatus
from baja_studio.core.database import Database
from baja_studio.modules.playout.publisher import PlayoutPublisher, initial_playout_state
from baja_studio.playout.library import LibraryStore, ScannedFile
from baja_studio.playout.queue_store import QueueStore
from baja_studio.playout.schema import create_playout_tables
from tests.conftest import FakeClock


class RecordedState:
    """Collects `update_state(path, value)` calls, the last value per path winning."""

    def __init__(self) -> None:
        self.values: dict[str, Any] = {}

    def __call__(self, path: str, value: Any) -> None:
        self.values[path] = value


@pytest.fixture
def database(tmp_path: Path) -> Iterator[Database]:
    """A temporary database with playout's tables, closed after the test."""
    opened = Database(tmp_path / "publisher.sqlite3")
    create_playout_tables(opened)
    yield opened
    opened.close()


def make_publisher(
    database: Database, clock: FakeClock
) -> tuple[PlayoutPublisher, RecordedState, LibraryStore, QueueStore]:
    library, queue = LibraryStore(database, clock), QueueStore(database, clock)
    recorded = RecordedState()
    return PlayoutPublisher(recorded, library, queue), recorded, library, queue


def scanned(name: str) -> ScannedFile:
    return ScannedFile(f"/clips/{name}", name, "Delivery", 100, 1.0)


def test_the_initial_state_has_every_key_the_page_reads() -> None:
    state = initial_playout_state()

    assert state["obs"]["connection"] == "not_configured"
    assert state["program"] is None and state["preview"] is None
    assert state["queue"] == [] and state["library"] == {}
    assert state["take_confirmation"] is None
    assert state["auto_play_next"] is False


def test_queue_entries_carry_their_clip_title_and_duration_and_a_total(
    database: Database, fake_clock: FakeClock
) -> None:
    # Two clips in the library, one with a known duration, both queued.
    publisher, recorded, library, queue = make_publisher(database, fake_clock)
    first, second = library.apply_scan([scanned("a.mp4"), scanned("b.mp4")])
    library.set_probe_result(first, 12.5, None, None)
    queue.add([first, second], "web:alice")

    publisher.publish_queue()

    titles = [(entry["title"], entry["duration_s"]) for entry in recorded.values["queue"]]
    assert titles == [("a", 12.5), ("b", None)]
    # A clip with no duration yet counts as zero in the estimate.
    assert recorded.values["queue_total_s"] == 12.5


def test_one_changed_clip_is_one_small_patch_keyed_by_its_id(
    database: Database, fake_clock: FakeClock
) -> None:
    publisher, recorded, library, _ = make_publisher(database, fake_clock)
    (clip_id,) = library.apply_scan([scanned("a.mp4")])

    publisher.publish_clips([clip_id])

    assert recorded.values[f"library.{clip_id}"]["title"] == "a"
    assert recorded.values["library_summary"]["total"] == 1


def test_the_position_poll_publishes_position_duration_and_remaining(
    database: Database, fake_clock: FakeClock
) -> None:
    publisher, recorded, _, _ = make_publisher(database, fake_clock)

    publisher.publish_position(MediaStatus("OBS_MEDIA_STATE_PLAYING", 4.0, 10.0))

    assert recorded.values["program.position_s"] == 4.0
    assert recorded.values["program.remaining_s"] == 6.0
    # A source that does not know its length yet has no remaining time.
    publisher.publish_position(MediaStatus("OBS_MEDIA_STATE_OPENING", None, None))
    assert recorded.values["program.remaining_s"] is None
