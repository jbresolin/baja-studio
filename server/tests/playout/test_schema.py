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


def test_a_wp5_database_gains_the_new_columns_and_keeps_its_rows(tmp_path: Path) -> None:
    # A database made by WP5 (before playlists) is upgraded in place on start.
    database = Database(tmp_path / "old.sqlite3")
    with database.transaction() as connection:
        connection.execute(
            "CREATE TABLE playout_queue (entry_id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "position INTEGER NOT NULL, clip_id INTEGER NOT NULL, added_by TEXT NOT NULL, "
            "added_at TEXT NOT NULL)"
        )
        connection.execute(
            "INSERT INTO playout_queue (position, clip_id, added_by, added_at) "
            "VALUES (0, 7, 'web:a', 'now')"
        )

    create_playout_tables(database)

    with database.transaction() as connection:
        row = connection.execute("SELECT clip_id, playlist_id FROM playout_queue").fetchone()
        columns = {r[1] for r in connection.execute("PRAGMA table_info(playout_clips)")}
    database.close()
    assert (row["clip_id"], row["playlist_id"]) == (7, None)
    assert "in_receiver_root" in columns
