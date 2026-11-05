"""The aired log export: what went to air and when, with YouTube chapter offsets.

With a stream-start marker, each airing gets its offset from the start of
the stream and a chapter line (`1:02:03 Enduro · carro 42`). YouTube wants
the first chapter at 0:00, so a "Stream start" chapter is added when the
first clip aired later. Airings before the marker get no offset.
"""

from datetime import datetime
from typing import Any


def format_offset(seconds: float) -> str:
    """`H:MM:SS`, or `M:SS` under an hour, as YouTube chapters are written."""
    total = int(seconds)
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    return f"{hours}:{minutes:02d}:{secs:02d}" if hours else f"{minutes}:{secs:02d}"


def build_aired_export(
    airings: list[dict[str, Any]], stream_start: datetime | None
) -> dict[str, Any]:
    """The `export_aired` result: entries with offsets, plus chapter text when possible."""
    entries = []
    chapters: list[str] = []
    for airing in airings:
        entry = dict(airing)
        entry["offset_s"] = None
        entry["offset"] = None
        if stream_start is not None:
            offset = (datetime.fromisoformat(airing["started_at"]) - stream_start).total_seconds()
            if offset >= 0:
                entry["offset_s"] = offset
                entry["offset"] = format_offset(offset)
                chapters.append(f"{entry['offset']} {airing['title']}")
        entries.append(entry)
    if chapters and not chapters[0].startswith("0:00 "):
        chapters.insert(0, "0:00 Stream start")
    return {
        "stream_start_marker": stream_start.isoformat() if stream_start is not None else None,
        "entries": entries,
        "chapters": "\n".join(chapters) if stream_start is not None else None,
    }
