"""How a clip row looks to browsers: its title, its kind and its library group.

A clip is one of three kinds (WP5b §3):

- `pipeline`: the orchestrator's manifest announced it; grouped by prova.
- `unmatched`: a file inside the receiver root with no manifest entry (an
  old pipeline output, a hash-named file); grouped as "Unmatched", shown grey.
- `manual`: a file in any other folder; grouped by its folder's label
  (e.g. "SAE institucional", "Patrocinadores").
"""

import json
import sqlite3
from pathlib import PurePosixPath
from typing import Any, Literal

SOURCE_MANUAL = "manual"
SOURCE_ORCHESTRATOR = "orchestrator"
UNMATCHED_GROUP = "Unmatched"
# A manual clip whose folder has no label and no name (a drive root).
MANUAL_GROUP = "Manual"

ClipKind = Literal["pipeline", "unmatched", "manual"]


def clip_title(row: sqlite3.Row | dict[str, Any]) -> str:
    """The name shown for a clip: its label, else its metadata, else its file name."""
    if row["label"]:
        return str(row["label"])
    if row["prova"] is not None and row["car_number"] is not None:
        team = f" [{row['team']}]" if row["team"] else ""
        title = f"{row['prova']} · carro {row['car_number']}{team}"
        return f"{title} · passagem {row['passagem']}" if row["show_pass"] else title
    return PurePosixPath(str(row["relative_path"])).stem


def clip_kind(row: sqlite3.Row) -> ClipKind:
    """Pipeline (in the manifest), unmatched (receiver root, not in it) or manual."""
    if row["source"] == SOURCE_ORCHESTRATOR:
        return "pipeline"
    return "unmatched" if row["in_receiver_root"] else "manual"


def clip_group(row: sqlite3.Row, kind: ClipKind) -> str:
    """The library group: the prova, "Unmatched", or the folder's label."""
    if kind == "pipeline":
        return str(row["prova"] or UNMATCHED_GROUP)
    if kind == "unmatched":
        return UNMATCHED_GROUP
    return str(row["folder_label"] or MANUAL_GROUP)


def public_clip(row: sqlite3.Row) -> dict[str, Any]:
    """A clip as browsers see it (state and API); JSON-serialisable."""
    metadata = json.loads(row["metadata_json"] or "{}")
    kind = clip_kind(row)
    return {
        "id": row["id"],
        "source": row["source"],
        "kind": kind,
        "group": clip_group(row, kind),
        "title": clip_title(row),
        "file_path": row["file_path"],
        "relative_path": row["relative_path"],
        "folder_label": row["folder_label"],
        "missing": bool(row["missing"]),
        "queueable": not row["missing"],
        "duration_s": row["duration_s"],
        "fps": row["fps"],
        "probe_error": row["probe_error"],
        "prova": row["prova"],
        "car_number": row["car_number"],
        "team": row["team"],
        "passagem": row["passagem"],
        "label": row["label"],
        "show_pass": bool(row["show_pass"]),
        "notes": row["notes"],
        "played_count": row["played_count"],
        "last_played_at": row["last_played_at"],
        "has_thumbnail": row["thumbnail_path"] is not None,
        "delivered_at": metadata.get("delivered_at"),
    }
