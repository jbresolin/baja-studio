"""Audio's live routing state in SQLite: stored levels and link states.

Both change during the show, from any control, many times an hour, so they
are kept apart from the versioned settings (an operator's unsaved edit on
the setup page must not conflict with a knob pausing a link). Each store
keeps a copy in memory for fast reads and writes every change through.

- **Stored levels** (`audio_stored_levels`): the level a send point had
  when it was closed by a macro or an M-VAVE button, keyed "channel:bus"
  ("5:bus1", "1:main"), so it can be opened again at the same level.
- **Links** (`audio_links`): which inputs' sends follow their main fader.
  A pair is `linked` (mirrored) or `paused` (kept, not mirrored); a pair
  without a row is `unlinked`. Seeded once from the 2026 matrix.
"""

import threading

from baja_studio.core.clock import Clock
from baja_studio.core.database import Database

LINK_STATES = ("linked", "paused", "unlinked")

SCHEMA_STATEMENTS = (
    """CREATE TABLE IF NOT EXISTS audio_stored_levels (
        point TEXT PRIMARY KEY,
        level REAL NOT NULL,
        stored_at TEXT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS audio_links (
        channel TEXT NOT NULL,
        bus TEXT NOT NULL,
        state TEXT NOT NULL,
        PRIMARY KEY (channel, bus)
    )""",
    """CREATE TABLE IF NOT EXISTS audio_meta (
        key TEXT PRIMARY KEY,
        value TEXT NOT NULL
    )""",
)
LINKS_SEEDED_KEY = "links_seeded"


def create_audio_tables(database: Database) -> None:
    """Create audio's tables if they do not exist; called when the module starts."""
    with database.transaction() as connection:
        for statement in SCHEMA_STATEMENTS:
            connection.execute(statement)


class StoredLevels:
    """Levels remembered for closed send points."""

    def __init__(self, database: Database, clock: Clock) -> None:
        """Load what was stored before the last restart."""
        self._database = database
        self._clock = clock
        self._lock = threading.Lock()
        with database.transaction() as connection:
            rows = connection.execute("SELECT point, level FROM audio_stored_levels").fetchall()
        self._levels: dict[str, float] = {row["point"]: row["level"] for row in rows}

    def get(self, point: str) -> float | None:
        """The level stored for "channel:bus", or None."""
        with self._lock:
            return self._levels.get(point)

    def all(self) -> dict[str, float]:
        """Every stored level, for the state tree."""
        with self._lock:
            return dict(self._levels)

    def store(self, point: str, level: float) -> None:
        """Remember `level` for `point`, replacing an older one."""
        with self._lock:
            self._levels[point] = level
        with self._database.transaction() as connection:
            connection.execute(
                "INSERT INTO audio_stored_levels (point, level, stored_at) VALUES (?, ?, ?) "
                "ON CONFLICT(point) DO UPDATE SET level = excluded.level, "
                "stored_at = excluded.stored_at",
                (point, level, self._clock.now_utc().isoformat()),
            )

    def forget(self, point: str) -> float | None:
        """Drop the level stored for `point`; returns it (None if there was none)."""
        with self._lock:
            level = self._levels.pop(point, None)
        if level is not None:
            with self._database.transaction() as connection:
                connection.execute("DELETE FROM audio_stored_levels WHERE point = ?", (point,))
        return level


class LinkStore:
    """Link states by (input channel, aux bus)."""

    def __init__(self, database: Database, seed: dict[str, dict[str, str]]) -> None:
        """Load the links; on the very first start, write `seed` (the 2026 matrix)."""
        self._database = database
        self._lock = threading.Lock()
        with database.transaction() as connection:
            seeded = connection.execute(
                "SELECT value FROM audio_meta WHERE key = ?", (LINKS_SEEDED_KEY,)
            ).fetchone()
            if seeded is None:
                for channel, buses in seed.items():
                    for bus, state in buses.items():
                        connection.execute(
                            "INSERT OR REPLACE INTO audio_links (channel, bus, state) "
                            "VALUES (?, ?, ?)",
                            (channel, bus, state),
                        )
                connection.execute(
                    "INSERT INTO audio_meta (key, value) VALUES (?, '1')", (LINKS_SEEDED_KEY,)
                )
            rows = connection.execute("SELECT channel, bus, state FROM audio_links").fetchall()
        self._links: dict[tuple[str, str], str] = {
            (row["channel"], row["bus"]): row["state"] for row in rows
        }

    def state(self, channel: str, bus: str) -> str:
        """ "linked", "paused" or "unlinked"."""
        with self._lock:
            return self._links.get((channel, bus), "unlinked")

    def linked_buses(self, channel: str) -> list[str]:
        """The buses whose send from `channel` follows its main fader right now."""
        with self._lock:
            return sorted(
                bus
                for (source, bus), state in self._links.items()
                if source == channel and state == "linked"
            )

    def matrix(self) -> dict[str, dict[str, str]]:
        """{channel: {bus: state}} for every pair that is linked or paused."""
        with self._lock:
            matrix: dict[str, dict[str, str]] = {}
            for (channel, bus), state in sorted(self._links.items()):
                matrix.setdefault(channel, {})[bus] = state
            return matrix

    def set(self, channel: str, bus: str, state: str) -> str:
        """Change one pair; returns its previous state. "unlinked" deletes the row."""
        if state not in LINK_STATES:
            raise ValueError(f"link state must be linked, paused or unlinked, not {state!r}")
        with self._lock:
            previous = self._links.get((channel, bus), "unlinked")
            if state == "unlinked":
                self._links.pop((channel, bus), None)
            else:
                self._links[(channel, bus)] = state
        with self._database.transaction() as connection:
            if state == "unlinked":
                connection.execute(
                    "DELETE FROM audio_links WHERE channel = ? AND bus = ?", (channel, bus)
                )
            else:
                connection.execute(
                    "INSERT OR REPLACE INTO audio_links (channel, bus, state) VALUES (?, ?, ?)",
                    (channel, bus, state),
                )
        return previous
