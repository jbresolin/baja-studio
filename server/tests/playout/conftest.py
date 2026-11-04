"""Fixtures shared by the playout library tests."""

from collections.abc import Iterator
from pathlib import Path

import pytest

from baja_studio.core.database import Database
from baja_studio.playout.schema import create_playout_tables


@pytest.fixture
def database(tmp_path: Path) -> Iterator[Database]:
    """A fresh SQLite file with playout's tables, closed after the test."""
    database = Database(tmp_path / "playout.sqlite3")
    create_playout_tables(database)
    yield database
    database.close()
