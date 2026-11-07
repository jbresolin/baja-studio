"""Tests for which mixer addresses audio asks for (modules/audio/queries.py).

The rule from 2026: never poll every aux send. These tests count.
"""

from pathlib import Path

from baja_studio.audio.config import AudioSettings
from baja_studio.audio.stores import LinkStore, create_audio_tables
from baja_studio.core.database import Database
from baja_studio.modules.audio.queries import interesting_addresses, send_query_addresses


def links(tmp_path: Path, matrix: dict[str, dict[str, str]]) -> tuple[LinkStore, Database]:
    database = Database(tmp_path / "db.sqlite3")
    create_audio_tables(database)
    return LinkStore(database, matrix), database


def test_on_connect_every_strip_is_read_but_only_the_sends_something_needs(tmp_path: Path) -> None:
    store, database = links(tmp_path, {"1": {"bus1": "linked"}})
    settings = AudioSettings.model_validate(
        {**AudioSettings().model_dump(), "macros": [], "midi": {"buttons": {}}}
    )

    addresses = interesting_addresses(settings, store, selected_bus=None)
    database.close()

    assert len([a for a in addresses if a.endswith("/mix/fader")]) == 24
    assert len([a for a in addresses if a.endswith("/mix/on")]) == 24
    assert [a for a in addresses if a.endswith("/level")] == ["/ch/01/mix/01/level"]


def test_the_2026_setup_reads_far_fewer_than_all_96_sends(tmp_path: Path) -> None:
    from baja_studio.audio import defaults

    store, database = links(tmp_path, defaults.LINKS)
    sends = [
        a for a in interesting_addresses(AudioSettings(), store, "bus2") if a.endswith("/level")
    ]
    database.close()

    assert len(sends) == len(set(sends))
    assert len(sends) < 96


def test_a_buss_sends_on_demand_cover_the_visible_and_mapped_inputs_only() -> None:
    addresses = send_query_addresses(AudioSettings(), "bus3")

    assert addresses == [
        "/ch/01/mix/03/level",
        "/ch/02/mix/03/level",
        "/ch/03/mix/03/level",
        "/ch/04/mix/03/level",
        "/ch/05/mix/03/level",
        "/ch/06/mix/03/level",
        "/ch/09/mix/03/level",
        "/rtn/aux/mix/03/level",
    ]
