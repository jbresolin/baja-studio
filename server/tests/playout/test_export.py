"""Tests for the aired log export (playout/export.py)."""

from datetime import UTC, datetime
from typing import Any

from baja_studio.playout.export import build_aired_export, format_offset

STREAM_START = datetime(2026, 11, 7, 14, 0, 0, tzinfo=UTC)


def airing(title: str, started_at: str) -> dict[str, Any]:
    """One row as `QueueStore.aired()` returns it."""
    return {"id": 1, "clip_id": 1, "title": title, "started_at": started_at, "ended_at": None}


def test_offsets_are_written_the_way_youtube_chapters_expect() -> None:
    # Under an hour YouTube writes M:SS; from an hour on, H:MM:SS.
    assert format_offset(0) == "0:00"
    assert format_offset(65.9) == "1:05"
    assert format_offset(3723) == "1:02:03"


def test_without_a_marker_there_are_no_offsets_and_no_chapters() -> None:
    # Nobody pressed "mark stream start", so there is nothing to count from.
    export = build_aired_export([airing("Enduro", "2026-11-07T14:05:00+00:00")], None)

    # The airings are still listed (for the post-event log), with empty offsets.
    assert export["stream_start_marker"] is None
    assert export["chapters"] is None
    assert export["entries"][0]["offset"] is None
    assert export["entries"][0]["offset_s"] is None


def test_with_a_marker_each_airing_gets_its_offset_and_a_chapter_line() -> None:
    # Two clips aired 5 min and 1 h 2 min 3 s after the stream started.
    airings = [
        airing("Enduro · carro 42", "2026-11-07T14:05:00+00:00"),
        airing("Suspensão · carro 7", "2026-11-07T15:02:03+00:00"),
    ]

    export = build_aired_export(airings, STREAM_START)

    # Offsets are counted from the marker; YouTube needs a chapter at 0:00,
    # so "Stream start" is added in front of the first clip.
    assert [entry["offset"] for entry in export["entries"]] == ["5:00", "1:02:03"]
    assert export["entries"][0]["offset_s"] == 300.0
    assert export["chapters"] == (
        "0:00 Stream start\n5:00 Enduro · carro 42\n1:02:03 Suspensão · carro 7"
    )
    assert export["stream_start_marker"] == STREAM_START.isoformat()


def test_a_clip_that_aired_right_at_the_start_needs_no_extra_chapter() -> None:
    # The first clip itself is the 0:00 chapter.
    export = build_aired_export([airing("Intro", "2026-11-07T14:00:00+00:00")], STREAM_START)

    assert export["chapters"] == "0:00 Intro"


def test_airings_before_the_marker_get_no_offset_and_no_chapter() -> None:
    # A clip tested before going live must not turn into a negative chapter.
    airings = [
        airing("Rehearsal", "2026-11-07T13:50:00+00:00"),
        airing("Enduro", "2026-11-07T14:10:00+00:00"),
    ]

    export = build_aired_export(airings, STREAM_START)

    assert export["entries"][0]["offset"] is None
    assert export["entries"][1]["offset"] == "10:00"
    assert export["chapters"] == "0:00 Stream start\n10:00 Enduro"
