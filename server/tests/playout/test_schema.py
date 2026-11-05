"""Tests for playout's tables (playout/schema.py)."""

from pathlib import Path

from baja_studio.core.database import Database
from baja_studio.playout.schema import create_playout_tables


def test_creating_the_tables_twice_is_harmless(tmp_path: Path) -> None:
    # The module creates its tables every time it starts, so a second call on
    # an existing database must not fail or wipe anything.
    database = Database(tmp_path / "schema.sqlite3")
    try:
        create_playout_tables(database)
        with database.transaction() as connection:
            connection.execute("INSERT INTO playout_session (key, value) VALUES ('kept', '1')")

        create_playout_tables(database)

        with database.transaction() as connection:
            names = {
                row["name"]
                for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
            }
            kept = connection.execute("SELECT value FROM playout_session").fetchone()
    finally:
        database.close()

    # All four tables exist, and the row written before the second call survived.
    assert {"playout_clips", "playout_queue", "playout_session", "playout_aired"} <= names
    assert kept["value"] == "1"
