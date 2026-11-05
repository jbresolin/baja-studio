"""First-start settings, seeded from the 2026 `xr18-control/mappings.json`.

Plain data (validated by `audio/config.py` through `validate_default`), so
it reads like the file it came from. What changed on the way:

- Channel ids: the stereo return is "aux" (2026: "17"); send points say
  "main" where 2026 said "lr".
- 2026 macros with `store-mute`/`restore` became `store_levels` /
  `restore_levels` steps. Their ids are kept, odd ones included
  ("macro-1774581643177"), because the D200 profile calls them by id.
- PROD 3 got the same three macros as PROD 1 and 2 (the D200 images for
  PROD 3 existed in 2026, the macros did not).
- The link matrix is not a setting any more; its 2026 contents seed the
  `audio_links` table instead (`LINKS` below, see `audio/links.py`).
"""

from typing import Any

COLOURS = {
    "1": ("HOST 1", "#e74c3c"),
    "2": ("HOST 2", "#e67e22"),
    "3": ("GUEST 1", "#f1c40f"),
    "4": ("GUEST 2", "#2ecc71"),
    "5": ("PROD 1", "#3498db"),
    "6": ("PROD 2", "#9b59b6"),
    "7": ("PROD 3", "#1abc9c"),
    "8": ("PROD 4", "#e91e63"),
    "9": ("SHOTGUN", "#5b7fff"),
    "aux": ("PC AUDIO", "#5b7fff"),
    "lr": ("MAIN", "#ecf0f1"),
    "bus1": ("IEM HOST 1", "#e74c3c"),
    "bus2": ("IEM HOST 2", "#e67e22"),
    "bus3": ("IEM GUEST 1", "#f1c40f"),
    "bus4": ("IEM GUEST 2", "#2ecc71"),
    "bus5": ("PRODUCTION", "#3498db"),
    "bus6": ("CAMERA OP", "#9b59b6"),
}
VISIBLE = ("1", "2", "3", "4", "5", "6", "9", "aux")


def channels() -> dict[str, Any]:
    """Labels and colours of the 2026 event; unlabelled inputs stay hidden."""
    return {
        channel: {"label": label, "color": colour, "visible": channel in VISIBLE}
        for channel, (label, colour) in COLOURS.items()
    }


def fader_channels() -> dict[str, Any]:
    """M-VAVE strip → channel, as in 2026 (strip 5 is the shotgun, 6 the PC audio)."""
    return {"1": "1", "2": "2", "3": "3", "4": "4", "5": "9", "6": "aux", "7": "5", "8": "6"}


def row_buses() -> dict[str, str]:
    """The send each button row toggles: S the main mix, R production, □ IEM host 1."""
    return {"solo": "main", "rec": "bus5", "sel": "bus1"}


def buttons() -> dict[str, Any]:
    """M mutes; S, R and □ close/open the strip's send on their row's bus."""
    actions: dict[str, Any] = {}
    for strip in range(1, 9):
        actions[f"{strip}_mute"] = {"type": "mute"}
        for row in ("solo", "rec", "sel"):
            actions[f"{strip}_{row}"] = {"type": "send_toggle"}
    return actions


def knobs() -> dict[str, Any]:
    """Every knob turns its strip's send to the bus picked with the bus-select buttons."""
    return {str(strip): {"target": "aux_send"} for strip in range(1, 9)}


def _points(channel: str, *buses: str) -> list[dict[str, str]]:
    return [{"channel": channel, "bus": bus} for bus in buses]


def _route(
    macro_id: str, label: str, colour: str, channel: str, *buses: str, closed_when_on: bool
) -> dict[str, Any]:
    """A toggle that opens (or, with `closed_when_on`, closes) a channel's send points."""
    close = {"op": "store_levels", "points": _points(channel, *buses)}
    open_ = {"op": "restore_levels", "points": _points(channel, *buses)}
    on, off = (close, open_) if closed_when_on else (open_, close)
    return {"id": macro_id, "label": label, "color": colour, "on_steps": [on], "off_steps": [off]}


def macros() -> list[dict[str, Any]]:
    """The 2026 macros (ids kept for the D200 profile), plus PROD 3's."""
    return [
        # A host talks to production only: off the main mix while held.
        _route("host1-talk-prod", "Host 1 → Prod", "#e74c3c", "1", "main", closed_when_on=True),
        _route("host2-talk-prod", "Host 2 → Prod", "#e67e22", "2", "main", closed_when_on=True),
        # A producer talks into both hosts' in-ears.
        _route(
            "prod1-talk-hosts",
            "Prod 1 → Hosts",
            "#3498db",
            "5",
            "bus1",
            "bus2",
            closed_when_on=False,
        ),
        _route(
            "macro-1774581643177",
            "Prod 2 → Hosts",
            "#9b59b6",
            "6",
            "bus1",
            "bus2",
            closed_when_on=False,
        ),
        _route(
            "prod3-talk-hosts",
            "Prod 3 → Hosts",
            "#1abc9c",
            "7",
            "bus1",
            "bus2",
            closed_when_on=False,
        ),
        # A producer goes on air.
        _route(
            "macro-1774582227201", "Prod 1 Go Live", "#3498db", "5", "main", closed_when_on=False
        ),
        _route(
            "macro-1774582340101", "Prod 2 Go Live", "#9b59b6", "6", "main", closed_when_on=False
        ),
        _route("prod3-go-live", "Prod 3 Go Live", "#1abc9c", "7", "main", closed_when_on=False),
        # A producer talks to the camera operator.
        _route("macro-1774582749617", "Prod 1 → Cam", "#3498db", "5", "bus6", closed_when_on=False),
        _route("macro-1774582877605", "Prod 2 → Cam", "#9b59b6", "6", "bus6", closed_when_on=False),
        _route("prod3-talk-cam", "Prod 3 → Cam", "#1abc9c", "7", "bus6", closed_when_on=False),
    ]


# 2026's explicit link-matrix entries. Missing ones are "unlinked" now; in
# 2026 a missing entry silently meant "linked" (ADR 0006).
_ALL_BUSES = ("bus1", "bus2", "bus3", "bus4", "bus5", "bus6")
LINKS: dict[str, dict[str, str]] = {
    **{channel: dict.fromkeys(_ALL_BUSES, "linked") for channel in ("1", "2", "3", "4", "7", "8")},
    **{
        channel: {
            "bus1": "linked",
            "bus2": "linked",
            "bus3": "linked",
            "bus4": "paused",
            "bus5": "paused",
            "bus6": "paused",
        }
        for channel in ("5", "6")
    },
    "9": {"bus1": "linked", "bus4": "linked", "bus5": "linked"},
}
