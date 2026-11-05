"""Playout's tables in the shared SQLite file.

The brief's three tables (`clips`, `queue`, `session`) plus `aired`, the log
of what went to air and when, which the export needs. `clips` has a few
columns beyond the brief: `relative_path` (the path under its folder, which
is how orchestrator clips are matched), `folder_label`, `missing` and
`probe_error` (so a file ffprobe cannot read is not retried forever).
"""

from baja_studio.core.database import Database

SCHEMA_STATEMENTS = (
    """CREATE TABLE IF NOT EXISTS playout_clips (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        source TEXT NOT NULL,
        file_path TEXT NOT NULL UNIQUE,
        relative_path TEXT NOT NULL,
        folder_label TEXT NOT NULL DEFAULT '',
        file_size INTEGER,
        mtime REAL,
        missing INTEGER NOT NULL DEFAULT 0,
        duration_s REAL,
        probe_error TEXT,
        prova TEXT,
        car_number INTEGER,
        team TEXT,
        passagem INTEGER,
        label TEXT,
        show_pass INTEGER NOT NULL DEFAULT 1,
        notes TEXT NOT NULL DEFAULT '',
        played_count INTEGER NOT NULL DEFAULT 0,
        last_played_at TEXT,
        thumbnail_path TEXT,
        metadata_json TEXT NOT NULL DEFAULT '{}'
    )""",
    """CREATE TABLE IF NOT EXISTS playout_queue (
        entry_id INTEGER PRIMARY KEY AUTOINCREMENT,
        position INTEGER NOT NULL,
        clip_id INTEGER NOT NULL,
        added_by TEXT NOT NULL,
        added_at TEXT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS playout_session (
        key TEXT PRIMARY KEY,
        value TEXT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS playout_aired (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        clip_id INTEGER NOT NULL,
        title TEXT NOT NULL,
        started_at TEXT NOT NULL,
        ended_at TEXT
    )""",
)


def create_playout_tables(database: Database) -> None:
    """Create the tables if they do not exist; called when the module starts."""
    with database.transaction() as connection:
        for statement in SCHEMA_STATEMENTS:
            connection.execute(statement)
