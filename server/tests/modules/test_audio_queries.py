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


def bare_settings(**changes: object) -> AudioSettings:
    """The default settings with nothing that needs a send read: no macros, buttons,
    production microphones or inputs shown on the page; `changes` on top."""
    hidden = {channel: {"visible": False} for channel in AudioSettings().channels}
    return AudioSettings.model_validate(
        {
            **AudioSettings().model_dump(),
            "macros": [],
            "midi": {"buttons": {}},
            "production_channels": [],
            "channels": hidden,
            **changes,
        }
    )


def test_on_connect_every_strip_is_read_but_only_the_sends_something_needs(tmp_path: Path) -> None:
    store, database = links(tmp_path, {"1": {"bus1": "linked"}})

    addresses = interesting_addresses(bare_settings(), store, selected_bus=None)
    database.close()

    assert len([a for a in addresses if a.endswith("/mix/fader")]) == 24
    assert len([a for a in addresses if a.endswith("/mix/on")]) == 24
    assert [a for a in addresses if a.endswith("/level")] == ["/ch/01/mix/01/level"]


def test_the_hosts_card_reads_where_each_seat_mic_and_production_mic_is_heard(
    tmp_path: Path,
) -> None:
    # Seat 1's microphone is channel 1; PROD 1 (channel 5) is a production
    # microphone. The card needs: channel 1's LR assign and its send to the
    # production bus (bus5), and PROD 1's send to each seat's in-ear bus
    # (bus1-bus4), each send with its on switch and tap. Nothing else.
    store, database = links(tmp_path, {})
    settings = bare_settings(
        production_channels=["5"],
        seats=[
            {"seat_id": 1, "channel": "1", "iem_bus": "bus1"},
            {"seat_id": 2, "iem_bus": "bus2"},
            {"seat_id": 3, "iem_bus": "bus3"},
            {"seat_id": 4, "iem_bus": "bus4"},
        ],
    )

    addresses = interesting_addresses(settings, store, selected_bus=None)
    database.close()

    beyond_strips = [a for a in addresses if "/mix/0" in a or a.endswith("/mix/lr")]
    heard = ["/ch/01/mix/05", *(f"/ch/05/mix/0{bus}" for bus in range(1, 5))]
    assert sorted(beyond_strips) == sorted(
        [f"{send}/{what}" for send in heard for what in ("level", "on", "tap")] + ["/ch/01/mix/lr"]
    )


def test_a_seats_personal_mix_reads_the_sends_it_shows(tmp_path: Path) -> None:
    # PC AUDIO (aux) is shown on the audio page, so each seat's overlay
    # offers it: its send to each seat's in-ear bus is read on connect.
    store, database = links(tmp_path, {})
    settings = bare_settings(channels={"aux": {"visible": True}})

    addresses = interesting_addresses(settings, store, selected_bus=None)
    database.close()

    assert [a for a in addresses if a.endswith("/level")] == [
        f"/rtn/aux/mix/0{bus}/level" for bus in range(1, 5)
    ]


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
