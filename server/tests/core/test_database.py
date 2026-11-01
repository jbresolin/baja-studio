"""Tests for the SQLite wrapper (core/database.py)."""

from pathlib import Path

import pytest

from baja_studio.core.database import Database


def test_opening_creates_the_file_its_folder_and_the_tables(tmp_path: Path) -> None:
    database = Database(tmp_path / "new-folder" / "studio.sqlite3")

    with database.transaction() as connection:
        tables = {row["name"] for row in connection.execute("SELECT name FROM sqlite_master")}

    assert {"settings", "commands"} <= tables
    assert (tmp_path / "new-folder" / "studio.sqlite3").is_file()


def test_a_failed_transaction_is_rolled_back(tmp_path: Path) -> None:
    database = Database(tmp_path / "studio.sqlite3")

    # Insert a row and then fail inside the same transaction...
    with pytest.raises(RuntimeError), database.transaction() as connection:
        connection.execute("INSERT INTO settings VALUES ('x', 1, '{}', 'now')")
        raise RuntimeError("something went wrong half way")

    # ...so the row must not have been kept.
    with database.transaction() as connection:
        assert connection.execute("SELECT COUNT(*) FROM settings").fetchone()[0] == 0
