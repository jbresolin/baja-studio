"""Playlists: named, ordered lists of clips, queued in one action (WP5b §3).

They live in the database, not in the settings, because they point at clip
rows (local files), and the settings export must not carry show state. A
playlist can shuffle when queued (sponsor loops) and can override the
on-air actions for its clips (`on_air` is an `OnAirActions` dict, or None to
use the settings').
"""

import json
import random
from typing import Any

from baja_studio.core.clock import Clock
from baja_studio.core.database import Database
from baja_studio.playout.config import OnAirActions

MAX_NAME_LENGTH = 60


class PlaylistError(Exception):
    """A playlist change was refused; the message is for the operator."""


class PlaylistStore:
    """Reads and writes `playout_playlists` and `playout_playlist_items`."""

    def __init__(
        self, database: Database, clock: Clock, shuffle: random.Random | None = None
    ) -> None:
        """Use the shared database; `shuffle` is seeded by tests for a known order."""
        self._database = database
        self._clock = clock
        self._random = shuffle or random.Random()

    def all(self) -> list[dict[str, Any]]:
        """Every playlist with its clip ids in order, oldest first."""
        with self._database.transaction() as connection:
            rows = connection.execute("SELECT * FROM playout_playlists ORDER BY id").fetchall()
            items = connection.execute(
                "SELECT playlist_id, clip_id FROM playout_playlist_items "
                "ORDER BY playlist_id, position"
            ).fetchall()
        clips: dict[int, list[int]] = {}
        for item in items:
            clips.setdefault(item["playlist_id"], []).append(item["clip_id"])
        return [_public(row, clips.get(row["id"], [])) for row in rows]

    def get(self, playlist_id: int) -> dict[str, Any]:
        """One playlist, or `PlaylistError`."""
        for playlist in self.all():
            if playlist["id"] == playlist_id:
                return playlist
        raise PlaylistError(f"there is no playlist {playlist_id}")

    def create(self, name: str, clip_ids: list[int]) -> int:
        """A new playlist holding `clip_ids` in that order; names are unique."""
        name = _checked_name(name)
        with self._database.transaction() as connection:
            if connection.execute(
                "SELECT 1 FROM playout_playlists WHERE name = ?", (name,)
            ).fetchone():
                raise PlaylistError(f"a playlist named {name!r} already exists")
            cursor = connection.execute(
                "INSERT INTO playout_playlists (name, created_at) VALUES (?, ?)",
                (name, self._clock.now_utc().isoformat()),
            )
            playlist_id = int(cursor.lastrowid or 0)
            _write_items(connection, playlist_id, clip_ids)
        return playlist_id

    def update(
        self,
        playlist_id: int,
        name: str | None = None,
        clip_ids: list[int] | None = None,
        shuffle: bool | None = None,
        set_on_air: bool = False,
        on_air: OnAirActions | None = None,
    ) -> None:
        """Change what is given; with `set_on_air`, `on_air` replaces the override (None clears)."""
        self.get(playlist_id)
        with self._database.transaction() as connection:
            if name is not None:
                name = _checked_name(name)
                clash = connection.execute(
                    "SELECT 1 FROM playout_playlists WHERE name = ? AND id != ?",
                    (name, playlist_id),
                ).fetchone()
                if clash:
                    raise PlaylistError(f"a playlist named {name!r} already exists")
                connection.execute(
                    "UPDATE playout_playlists SET name = ? WHERE id = ?", (name, playlist_id)
                )
            if shuffle is not None:
                connection.execute(
                    "UPDATE playout_playlists SET shuffle = ? WHERE id = ?",
                    (int(shuffle), playlist_id),
                )
            if set_on_air:
                value = on_air.model_dump_json() if on_air is not None else None
                connection.execute(
                    "UPDATE playout_playlists SET on_air_json = ? WHERE id = ?",
                    (value, playlist_id),
                )
            if clip_ids is not None:
                connection.execute(
                    "DELETE FROM playout_playlist_items WHERE playlist_id = ?", (playlist_id,)
                )
                _write_items(connection, playlist_id, clip_ids)

    def delete(self, playlist_id: int) -> None:
        """Remove a playlist (its clips stay in the library)."""
        self.get(playlist_id)
        with self._database.transaction() as connection:
            connection.execute(
                "DELETE FROM playout_playlist_items WHERE playlist_id = ?", (playlist_id,)
            )
            connection.execute("DELETE FROM playout_playlists WHERE id = ?", (playlist_id,))

    def play_order(self, playlist_id: int) -> list[int]:
        """The clip ids to queue: in order, or shuffled when the playlist says so."""
        playlist = self.get(playlist_id)
        clip_ids = list(playlist["clip_ids"])
        if playlist["shuffle"]:
            self._random.shuffle(clip_ids)
        return clip_ids

    def on_air_override(self, playlist_id: int | None) -> OnAirActions | None:
        """The playlist's on-air actions, or None (no playlist, deleted, or no override)."""
        if playlist_id is None:
            return None
        with self._database.transaction() as connection:
            row = connection.execute(
                "SELECT on_air_json FROM playout_playlists WHERE id = ?", (playlist_id,)
            ).fetchone()
        if row is None or row["on_air_json"] is None:
            return None
        return OnAirActions.model_validate_json(row["on_air_json"])

    def name_of(self, playlist_id: int | None) -> str | None:
        """A playlist's name for the aired log (None when not from a playlist, or deleted)."""
        if playlist_id is None:
            return None
        with self._database.transaction() as connection:
            row = connection.execute(
                "SELECT name FROM playout_playlists WHERE id = ?", (playlist_id,)
            ).fetchone()
        return str(row["name"]) if row is not None else None


def _checked_name(name: str) -> str:
    name = name.strip()
    if not name:
        raise PlaylistError("a playlist needs a name")
    if len(name) > MAX_NAME_LENGTH:
        raise PlaylistError(f"playlist names are at most {MAX_NAME_LENGTH} characters")
    return name


def _write_items(connection: Any, playlist_id: int, clip_ids: list[int]) -> None:
    if not clip_ids:
        raise PlaylistError("a playlist needs at least one clip")
    connection.executemany(
        "INSERT INTO playout_playlist_items (playlist_id, position, clip_id) VALUES (?, ?, ?)",
        [(playlist_id, position, clip_id) for position, clip_id in enumerate(clip_ids)],
    )


def _public(row: Any, clip_ids: list[int]) -> dict[str, Any]:
    return {
        "id": row["id"],
        "name": row["name"],
        "shuffle": bool(row["shuffle"]),
        "on_air": json.loads(row["on_air_json"]) if row["on_air_json"] else None,
        "clip_ids": clip_ids,
        "created_at": row["created_at"],
    }
