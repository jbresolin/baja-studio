"""The single SQLite file every core store writes to.

Workers run in threads, so one connection is shared behind a lock rather than
opened per thread; SQLite handles a single writer best anyway.
"""

import sqlite3
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS settings (
    module TEXT PRIMARY KEY,
    version INTEGER NOT NULL,
    body TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS commands (
    ts TEXT NOT NULL,
    module TEXT NOT NULL,
    source TEXT NOT NULL,
    command TEXT NOT NULL,
    args_json TEXT NOT NULL,
    result TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS commands_by_module_and_time ON commands (module, ts);
"""


class Database:
    """Owns the SQLite connection and creates the tables on first open."""

    def __init__(self, path: Path) -> None:
        """Open (or create) the database file at `path` and ensure the schema exists."""
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self._lock = threading.Lock()
        # check_same_thread=False because workers call in from their own
        # threads; the lock above is what makes that safe.
        self._connection = sqlite3.connect(path, check_same_thread=False)
        self._connection.row_factory = sqlite3.Row
        self._connection.execute("PRAGMA journal_mode=WAL")
        self._connection.executescript(SCHEMA)
        self._connection.commit()

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        """Yield the connection under the lock; commit on success, roll back on error."""
        with self._lock:
            try:
                yield self._connection
                self._connection.commit()
            except BaseException:
                self._connection.rollback()
                raise

    def close(self) -> None:
        """Close the connection at shutdown."""
        with self._lock:
            self._connection.close()
