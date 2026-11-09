"""The clip library: one row per video file, local or announced by the orchestrator.

Local files are the truth for what can play. The scanner adds and updates
rows for files it finds and marks the others missing; the orchestrator sync
adds metadata (prova, car, team, passagem) to the row whose path matches
the manifest's `relative_path` under the delivery folder, creating a
"missing" row when the file has not arrived yet. A file with no metadata is
an unmatched clip (inside the receiver root) or a manual one (any other
folder), with an editable label; see `clip_view.py`.
"""

import json
import sqlite3
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from baja_studio.core.clock import Clock
from baja_studio.core.database import Database
from baja_studio.playout.clip_view import SOURCE_MANUAL, SOURCE_ORCHESTRATOR, public_clip


class LibraryError(Exception):
    """A library change was refused; the message is for the operator."""


@dataclass(frozen=True)
class ScannedFile:
    """A video file found in a watched folder."""

    path: str
    relative_path: str
    folder_label: str
    size: int
    mtime: float
    # Found under the receiver root (so, without a manifest entry, "unmatched").
    in_receiver_root: bool = False


@dataclass(frozen=True)
class ManifestClip:
    """What the orchestrator says about one delivered clip (see `orchestrator.py`)."""

    job_id: str
    prova: str
    car_number: int
    team_name: str | None
    passagem: int
    relative_path: str
    sha256: str
    size_bytes: int
    delivered_at: str


class LibraryStore:
    """Reads and writes `playout_clips`."""

    def __init__(self, database: Database, clock: Clock) -> None:
        """Use the shared database; the tables must exist (`create_playout_tables`)."""
        self._database = database
        self._clock = clock

    def apply_scan(self, scanned: Iterable[ScannedFile]) -> list[int]:
        """Bring the table in line with one full scan; returns ids of rows that changed.

        New files are added as manual clips. A file whose size or mtime
        changed (a reprocessed clip) loses its duration and thumbnail so the
        prober looks at it again. Rows for files not found are marked missing.
        """
        changed: list[int] = []
        seen: set[str] = set()
        with self._database.transaction() as connection:
            rows = {row["file_path"]: row for row in connection.execute(_SELECT_ALL)}
            for found in scanned:
                seen.add(found.path)
                row = rows.get(found.path)
                if row is None:
                    cursor = connection.execute(
                        "INSERT INTO playout_clips (source, file_path, relative_path, "
                        "folder_label, file_size, mtime, in_receiver_root) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?)",
                        (
                            SOURCE_MANUAL,
                            found.path,
                            found.relative_path,
                            found.folder_label,
                            found.size,
                            found.mtime,
                            int(found.in_receiver_root),
                        ),
                    )
                    changed.append(int(cursor.lastrowid or 0))
                elif row["file_size"] != found.size or row["mtime"] != found.mtime:
                    connection.execute(
                        "UPDATE playout_clips SET file_size = ?, mtime = ?, missing = 0, "
                        "duration_s = NULL, probe_error = NULL, thumbnail_path = NULL, "
                        "folder_label = ?, in_receiver_root = ? WHERE id = ?",
                        (
                            found.size,
                            found.mtime,
                            found.folder_label,
                            int(found.in_receiver_root),
                            row["id"],
                        ),
                    )
                    changed.append(row["id"])
                elif (
                    row["missing"]
                    or row["folder_label"] != found.folder_label
                    or bool(row["in_receiver_root"]) != found.in_receiver_root
                ):
                    connection.execute(
                        "UPDATE playout_clips SET missing = 0, folder_label = ?, "
                        "in_receiver_root = ? WHERE id = ?",
                        (found.folder_label, int(found.in_receiver_root), row["id"]),
                    )
                    changed.append(row["id"])
            for path, row in rows.items():
                if path not in seen and not row["missing"]:
                    connection.execute(
                        "UPDATE playout_clips SET missing = 1 WHERE id = ?", (row["id"],)
                    )
                    changed.append(row["id"])
        return changed

    def merge_manifest(
        self, entries: Iterable[ManifestClip], delivery_root: str, delivery_label: str
    ) -> list[int]:
        """Attach orchestrator metadata to the matching files; returns changed ids.

        A clip is matched on `delivery_root / relative_path`. One whose file
        has not arrived yet gets a missing row, filled in when the scanner finds it.
        """
        changed: list[int] = []
        with self._database.transaction() as connection:
            for entry in entries:
                path = str(Path(delivery_root) / PurePosixPath(entry.relative_path))
                metadata = json.dumps(
                    {
                        "job_id": entry.job_id,
                        "sha256": entry.sha256,
                        "size_bytes": entry.size_bytes,
                        "delivered_at": entry.delivered_at,
                    }
                )
                values = (entry.prova, entry.car_number, entry.team_name, entry.passagem, metadata)
                row = connection.execute(
                    "SELECT id, source, prova, car_number, team, passagem, metadata_json "
                    "FROM playout_clips WHERE file_path = ?",
                    (path,),
                ).fetchone()
                if row is None:
                    cursor = connection.execute(
                        "INSERT INTO playout_clips (source, file_path, relative_path, "
                        "folder_label, missing, in_receiver_root, prova, car_number, team, "
                        "passagem, metadata_json) VALUES (?, ?, ?, ?, 1, 1, ?, ?, ?, ?, ?)",
                        (SOURCE_ORCHESTRATOR, path, entry.relative_path, delivery_label, *values),
                    )
                    changed.append(int(cursor.lastrowid or 0))
                elif (
                    row["source"],
                    row["prova"],
                    row["car_number"],
                    row["team"],
                    row["passagem"],
                    row["metadata_json"],
                ) != (SOURCE_ORCHESTRATOR, *values):
                    connection.execute(
                        "UPDATE playout_clips SET source = ?, prova = ?, car_number = ?, "
                        "team = ?, passagem = ?, metadata_json = ? WHERE id = ?",
                        (SOURCE_ORCHESTRATOR, *values, row["id"]),
                    )
                    changed.append(row["id"])
        return changed

    def needing_probe(self, limit: int = 10) -> list[dict[str, Any]]:
        """Present files with no duration yet and no recorded probe failure."""
        with self._database.transaction() as connection:
            rows = connection.execute(
                "SELECT id, file_path, mtime FROM playout_clips WHERE missing = 0 "
                "AND duration_s IS NULL AND probe_error IS NULL ORDER BY id LIMIT ?",
                (limit,),
            ).fetchall()
        return [dict(row) for row in rows]

    def set_probe_result(
        self,
        clip_id: int,
        duration_s: float | None,
        thumbnail_path: str | None,
        error: str | None,
    ) -> None:
        """Store what ffprobe/ffmpeg found (or why they failed)."""
        with self._database.transaction() as connection:
            connection.execute(
                "UPDATE playout_clips SET duration_s = ?, thumbnail_path = ?, probe_error = ? "
                "WHERE id = ?",
                (duration_s, thumbnail_path, error, clip_id),
            )

    def clear_probe_errors(self) -> None:
        """Forget recorded probe failures so the prober tries those files again."""
        with self._database.transaction() as connection:
            connection.execute(
                "UPDATE playout_clips SET probe_error = NULL WHERE probe_error IS NOT NULL"
            )

    def get(self, clip_id: int) -> dict[str, Any] | None:
        """One clip as `public_clip` shows it, or None."""
        row = self._row(clip_id)
        return public_clip(row) if row is not None else None

    def thumbnail_path(self, clip_id: int) -> str | None:
        """Where a clip's cached thumbnail is, if it has one."""
        row = self._row(clip_id)
        return row["thumbnail_path"] if row is not None else None

    def all(self) -> list[dict[str, Any]]:
        """Every clip, oldest row first."""
        with self._database.transaction() as connection:
            rows = connection.execute(_SELECT_ALL + " ORDER BY id").fetchall()
        return [public_clip(row) for row in rows]

    def set_label(self, clip_id: int, label: str) -> None:
        """Rename a manual clip (orchestrator clips are named by their metadata)."""
        row = self._require(clip_id)
        if row["source"] != SOURCE_MANUAL:
            raise LibraryError("only manual clips can be renamed; this one comes from the pipeline")
        self._update(clip_id, "label = ?", (label.strip() or None,))

    def set_notes(self, clip_id: int, notes: str) -> None:
        """Replace a clip's operator notes."""
        self._require(clip_id)
        self._update(clip_id, "notes = ?", (notes,))

    def record_played(self, clip_id: int) -> None:
        """Count one more airing of a clip, now."""
        self._update(
            clip_id,
            "played_count = played_count + 1, last_played_at = ?",
            (self._clock.now_utc().isoformat(),),
        )

    def set_played(self, clip_id: int, played: bool) -> None:
        """Mark a clip played or unplayed by hand."""
        self._require(clip_id)
        if played:
            self._update(
                clip_id,
                "played_count = MAX(played_count, 1), last_played_at = COALESCE(last_played_at, ?)",
                (self._clock.now_utc().isoformat(),),
            )
        else:
            self._update(clip_id, "played_count = 0, last_played_at = NULL", ())

    def summary(self) -> dict[str, Any]:
        """Counts for the page header: per group, missing, unplayed, unmatched, total."""
        clips = self.all()
        per_group = Counter(clip["group"] for clip in clips)
        return {
            "total": len(clips),
            "missing": sum(1 for clip in clips if clip["missing"]),
            "unplayed": sum(1 for clip in clips if clip["played_count"] == 0),
            "unmatched": sum(1 for clip in clips if clip["kind"] == "unmatched"),
            "per_group": dict(sorted(per_group.items())),
        }

    def require_queueable(self, clip_id: int) -> dict[str, Any]:
        """The clip, or `LibraryError` if it does not exist or its file is missing."""
        clip = public_clip(self._require(clip_id))
        if clip["missing"]:
            raise LibraryError(f"'{clip['title']}' cannot be queued: its file is missing")
        return clip

    def _row(self, clip_id: int) -> sqlite3.Row | None:
        with self._database.transaction() as connection:
            row: sqlite3.Row | None = connection.execute(
                _SELECT_ALL + " WHERE id = ?", (clip_id,)
            ).fetchone()
        return row

    def _require(self, clip_id: int) -> sqlite3.Row:
        row = self._row(clip_id)
        if row is None:
            raise LibraryError(f"there is no clip {clip_id}")
        return row

    def _update(self, clip_id: int, assignments: str, values: tuple[Any, ...]) -> None:
        with self._database.transaction() as connection:
            connection.execute(
                f"UPDATE playout_clips SET {assignments} WHERE id = ?", (*values, clip_id)
            )


_SELECT_ALL = "SELECT * FROM playout_clips"
