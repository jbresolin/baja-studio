"""Playout's tables in the shared SQLite file.

The brief's three tables (`clips`, `queue`, `session`) plus `aired`, the log
of what went to air and when, which the export needs. `clips` has a few
columns beyond the brief: `relative_path` (the path under its folder, which
is how orchestrator clips are matched), `folder_label`, `missing` and
`probe_error` (so a file ffprobe cannot read is not retried forever).

WP5b added playlists (`playlists` and their `playlist_items`) and three
columns to existing tables: `clips.in_receiver_root` (to tell unmatched files
from manual ones), `queue.playlist_id` and `aired.playlist` (so on-air
overrides and the aired log know which playlist a clip came from). The
columns are added to an older file when the module starts.

WP5c added `clips.fps` (the video frame rate from ffprobe, for frame steps).
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
    """CREATE TABLE IF NOT EXISTS playout_playlists (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL UNIQUE,
        shuffle INTEGER NOT NULL DEFAULT 0,
        on_air_json TEXT,
        created_at TEXT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS playout_playlist_items (
        playlist_id INTEGER NOT NULL,
        position INTEGER NOT NULL,
        clip_id INTEGER NOT NULL,
        PRIMARY KEY (playlist_id, position)
    )""",
)

# (table, column, definition) added after the tables first shipped.
ADDED_COLUMNS = (
    ("playout_clips", "in_receiver_root", "INTEGER NOT NULL DEFAULT 0"),
    ("playout_queue", "playlist_id", "INTEGER"),
    ("playout_aired", "playlist", "TEXT"),
    ("playout_clips", "fps", "REAL"),
)


def create_playout_tables(database: Database) -> None:
    """Create the tables and add missing columns; called when the module starts."""
    with database.transaction() as connection:
        for statement in SCHEMA_STATEMENTS:
            connection.execute(statement)
        for table, column, definition in ADDED_COLUMNS:
            existing = {row[1] for row in connection.execute(f"PRAGMA table_info({table})")}
            if column not in existing:
                connection.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")
