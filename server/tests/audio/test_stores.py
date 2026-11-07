"""Tests for stored levels and link states in SQLite (audio/stores.py).

Each test opens a real SQLite file in a temporary folder, writes, then
opens it again the way a restarted service would.
"""

from collections.abc import Iterator
from pathlib import Path

import pytest

from baja_studio.audio.stores import LinkStore, StoredLevels, create_audio_tables
from baja_studio.core.database import Database
from tests.conftest import FakeClock

SEED = {"1": {"bus1": "linked", "bus2": "paused"}}


@pytest.fixture
def database(tmp_path: Path) -> Iterator[Database]:
    database = Database(tmp_path / "studio.sqlite3")
    create_audio_tables(database)
    yield database
    database.close()


def test_stored_levels_survive_a_restart(database: Database, fake_clock: FakeClock) -> None:
    StoredLevels(database, fake_clock).store("5:bus1", 0.6)

    reopened = StoredLevels(database, fake_clock)

    assert reopened.get("5:bus1") == 0.6
    assert reopened.all() == {"5:bus1": 0.6}


def test_forgetting_a_stored_level_returns_it_and_removes_it(
    database: Database, fake_clock: FakeClock
) -> None:
    levels = StoredLevels(database, fake_clock)
    levels.store("1:main", 0.7)

    assert levels.forget("1:main") == 0.7
    assert levels.forget("1:main") is None
    assert StoredLevels(database, fake_clock).all() == {}


def test_links_are_seeded_once_from_the_2026_matrix(database: Database) -> None:
    links = LinkStore(database, SEED)

    assert links.state("1", "bus1") == "linked"
    assert links.state("1", "bus2") == "paused"
    assert links.state("1", "bus3") == "unlinked"
    assert links.linked_buses("1") == ["bus1"]


def test_unlinking_everything_is_remembered_and_not_reseeded(database: Database) -> None:
    # If an empty table meant "seed again", an operator could never unlink all.
    links = LinkStore(database, SEED)
    links.set("1", "bus1", "unlinked")
    links.set("1", "bus2", "unlinked")

    assert LinkStore(database, SEED).matrix() == {}


def test_link_changes_survive_a_restart_and_report_the_previous_state(database: Database) -> None:
    links = LinkStore(database, SEED)

    assert links.set("1", "bus1", "paused") == "linked"
    assert links.set("4", "bus6", "linked") == "unlinked"
    assert LinkStore(database, SEED).matrix() == {
        "1": {"bus1": "paused", "bus2": "paused"},
        "4": {"bus6": "linked"},
    }


def test_an_unknown_link_state_is_refused(database: Database) -> None:
    with pytest.raises(ValueError, match="linked, paused or unlinked"):
        LinkStore(database, SEED).set("1", "bus1", "maybe")
