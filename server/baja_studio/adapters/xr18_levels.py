"""The XR18's fader law: OSC level 0..1 to decibels and back.

Behringer's X Air and X32 mixers map the 0..1 float to dB in four straight
segments (dB is linear in the float within each one):

    level 0.5    .. 1.0     →  -10 dB .. +10 dB   (0.75 is 0 dB, "unity")
    level 0.25   .. 0.5     →  -30 dB .. -10 dB
    level 0.0625 .. 0.25    →  -60 dB .. -30 dB
    level 0      .. 0.0625  →  -90 dB .. -60 dB   (0 is shown as -inf: off)

The 2026 tool used `40·log10(level/0.75)`, which agrees at unity and +10 dB
but reads about 3 dB high mid-travel. Sends use the same law as faders.
"""

import math

UNITY_LEVEL = 0.75
MIN_DB = -90.0
MAX_DB = 10.0

# (level at the start of the segment, dB there, dB per unit of level)
_SEGMENTS = (
    (0.5, -10.0, 40.0),
    (0.25, -30.0, 80.0),
    (0.0625, -60.0, 160.0),
    (0.0, -90.0, 480.0),
)


def clamp_level(level: float) -> float:
    """`level` limited to 0..1 (NaN counts as 0)."""
    if math.isnan(level):
        return 0.0
    return min(1.0, max(0.0, level))


def level_to_db(level: float) -> float:
    """Decibels for an OSC level; 0 gives -90 (shown as -inf by `format_db`)."""
    level = clamp_level(level)
    for start, start_db, slope in _SEGMENTS:
        if level >= start:
            return start_db + (level - start) * slope
    return MIN_DB


def db_to_level(db: float) -> float:
    """The OSC level for `db`, clamped to -90..+10 dB."""
    db = min(MAX_DB, max(MIN_DB, db))
    for start, start_db, slope in _SEGMENTS:
        if db >= start_db:
            return start + (db - start_db) / slope
    return 0.0


def format_db(level: float) -> str:
    """The dB text the page and the D200 show: "-inf" for off, else "-7.5 dB".

    The format is the 2026 one (the D200 contract returns it in `db`).
    """
    if clamp_level(level) <= 0.0:
        return "-inf"
    return f"{level_to_db(level):.1f} dB"


def nudge_level(level: float | None, delta_db: float, unknown_level: float = UNITY_LEVEL) -> float:
    """`level` moved by `delta_db`, staying within -90..+10 dB.

    A level the mixer has not reported yet counts as `unknown_level` (unity,
    as in 2026); off counts as -90 dB, so a nudge up from off starts low.
    """
    start = unknown_level if level is None else level
    return db_to_level(level_to_db(start) + delta_db)
