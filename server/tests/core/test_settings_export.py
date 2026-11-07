"""Tests for settings export (core/settings_export.py).

The export writes every settings section to one JSON document, with each
secret replaced by "<secret>" and listed, so the file can be shared safely.
"""

import json
from pathlib import Path

from baja_studio import __version__
from baja_studio.core.database import Database
from baja_studio.core.settings import SettingsStore
from baja_studio.core.settings_export import EXPORTED_SECRET, export_document, export_file_name
from tests.conftest import FakeClock
from tests.core.demo_settings import exported, make_store, source

__all__ = ["source"]  # a fixture: pytest finds it by its name in this module


def test_the_export_holds_every_section_with_versions(
    source: SettingsStore, fake_clock: FakeClock
) -> None:
    document = export_document(source, fake_clock, "STREAM-PC")

    assert document["format"] == "baja-studio-settings"
    assert document["app_version"] == __version__
    assert document["hostname"] == "STREAM-PC"
    assert document["exported_at"] == "2026-10-01T12:00:00+00:00"
    assert set(document["modules"]) == {"demo", "other"}
    assert document["modules"]["demo"]["settings_version"] == 1
    assert document["modules"]["other"]["values"] == {"enabled": True, "colour": "blue"}


def test_secrets_are_replaced_and_listed_wherever_they_are(
    source: SettingsStore, fake_clock: FakeClock
) -> None:
    text = exported(source, fake_clock).decode("utf-8")
    document = json.loads(text)

    # No secret value appears anywhere in the file...
    for secret in ("obs-password", "token-a"):
        assert secret not in text
    demo = document["modules"]["demo"]["values"]
    assert demo["remote"]["password"] == EXPORTED_SECRET
    assert demo["devices"][0]["token"] == EXPORTED_SECRET
    # ...an empty secret stays empty (there is nothing to re-enter)...
    assert demo["devices"][1]["token"] == ""
    # ...and the operator is told which ones to enter again, items named by id.
    assert document["secrets_required"] == [
        "demo.remote.password",
        "demo.devices[laptop-a].token",
    ]


def test_the_export_holds_settings_only_never_other_tables(
    tmp_path: Path, fake_clock: FakeClock
) -> None:
    # Operational state (a queue, link states, the command log) lives in other
    # tables of the same database file: none of it may end up in the export.
    store = make_store(tmp_path / "pc", fake_clock)
    database = Database(tmp_path / "pc" / "studio.sqlite3")
    with database.transaction() as connection:
        connection.execute("CREATE TABLE playout_queue (title TEXT)")
        connection.execute("INSERT INTO playout_queue VALUES ('QUEUED-CLIP-MARKER')")
        connection.execute(
            "INSERT INTO commands VALUES ('t', 'audio', 'web:x', 'mute', '{}', 'ok-MARKER')"
        )
    database.close()

    text = exported(store, fake_clock).decode("utf-8")

    assert "MARKER" not in text


def test_the_file_name_carries_the_host_and_time(fake_clock: FakeClock) -> None:
    assert (
        export_file_name("STREAM PC/1", fake_clock)
        == "BajaStudio-settings-STREAM-PC-1-20261001-120000.json"
    )
