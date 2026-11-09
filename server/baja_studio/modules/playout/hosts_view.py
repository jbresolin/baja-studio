"""The compact now/next summary for the hosts screen (WP5b §5, consumed by WP9).

`playout.hosts_view`:

    {on_air: {label, remaining_s, duration_s} | null,
     next: [{label, duration_s}, ...]   (up to 3: the cued clip, then the queue),
     queue_remaining_s}                 (known durations of everything after the clip on air)

Remaining times are whole seconds, so the value changes (and is sent) once a
second while a clip plays, and not at the 5 Hz of the position poll.
"""

import math
from typing import Any

NEXT_SHOWN = 3


def build_hosts_view(
    program: dict[str, Any] | None,
    cued_clip: dict[str, Any] | None,
    queue_entries: list[dict[str, Any]],
) -> dict[str, Any]:
    """The hosts view from the published program, the cued clip and the queue entries."""
    upcoming: list[dict[str, Any]] = []
    if cued_clip is not None:
        upcoming.append({"label": cued_clip["title"], "duration_s": cued_clip.get("duration_s")})
    upcoming.extend(
        {"label": entry["title"], "duration_s": entry.get("duration_s")} for entry in queue_entries
    )
    on_air = None
    if program is not None:
        on_air = {
            "label": program["clip"]["title"],
            "remaining_s": _whole_seconds(program.get("remaining_s")),
            "duration_s": program.get("duration_s"),
        }
    return {
        "on_air": on_air,
        "next": upcoming[:NEXT_SHOWN],
        "queue_remaining_s": float(sum(item["duration_s"] or 0.0 for item in upcoming)),
    }


def _whole_seconds(seconds: float | None) -> int | None:
    # Rounded up, as a countdown reads: 0.4 s left still shows 1.
    return math.ceil(seconds) if seconds is not None else None
