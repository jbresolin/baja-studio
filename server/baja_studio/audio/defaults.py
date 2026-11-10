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
- "Host N → Prod" takes the host off LR with the LR assign instead of
  closing the fader, and the guests got one each (WP9b, ADR 0013).
- The link matrix is not a setting any more; it seeds the `audio_links`
  table instead (`LINKS` below, see `audio/stores.py`), changed from 2026's
  for the hosts' personal mixes (WP9b).
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


# The seats' microphones and the in-ear bus each one feeds (HOST 1-2, GUEST 1-2).
SEAT_CHANNELS = {"1": "bus1", "2": "bus2", "3": "bus3", "4": "bus4"}


def _talk_to_production(macro_id: str, label: str, colour: str, channel: str) -> dict[str, Any]:
    """A seat talks to production off air (WP9b, ADR 0013 decision 8).

    On: the channel leaves the main mix (LR assign off, the fader untouched),
    and the other seats stop hearing it: their in-ear sends are paused, then
    closed with stored levels (paused first, so nothing re-mirrors them).
    The seat's own in-ears and buses 5-6 stay linked, so the host hears
    themselves and production and the camera operator hear them.
    Off: back on LR; the other seats' sends restored, then linked again
    (back to the fader plus their offsets).
    """
    others = [bus for seat, bus in SEAT_CHANNELS.items() if seat != channel]
    points = _points(channel, *others)
    pause = [
        {"op": "set_link", "channel": channel, "bus": bus, "state": "paused"} for bus in others
    ]
    relink = [
        {"op": "set_link", "channel": channel, "bus": bus, "state": "linked"} for bus in others
    ]
    return {
        "id": macro_id,
        "label": label,
        "color": colour,
        "on_steps": [
            {"op": "set_lr_assign", "channel": channel, "on": False},
            *pause,
            {"op": "store_levels", "points": points},
        ],
        "off_steps": [
            {"op": "set_lr_assign", "channel": channel, "on": True},
            {"op": "restore_levels", "points": points},
            *relink,
        ],
    }


def macros() -> list[dict[str, Any]]:
    """The 2026 macros (ids kept for the D200 profile), plus PROD 3's and the guests'."""
    return [
        # A seat talks to production only: off the main mix while on.
        _talk_to_production("host1-talk-prod", "Host 1 → Prod", "#e74c3c", "1"),
        _talk_to_production("host2-talk-prod", "Host 2 → Prod", "#e67e22", "2"),
        _talk_to_production("guest1-talk-prod", "Guest 1 → Prod", "#f1c40f", "3"),
        _talk_to_production("guest2-talk-prod", "Guest 2 → Prod", "#2ecc71", "4"),
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


# The link matrix a fresh install starts with (an existing install keeps its
# own until Audio setup → Link matrix → "Restore defaults…"). A missing pair is
# "unlinked"; in 2026 a missing entry silently meant "linked" (ADR 0006).
#
# - The in-ear buses 1-4 are a copy of the main mix plus each seat's offsets
#   (LIVESTACK-34 topology), so every source of the main mix (the seats'
#   microphones, the shotgun and PC AUDIO) is linked to them; 2026 left out
#   PC AUDIO and the shotgun on buses 2-3.
# - The production microphones (PROD 1-4) are not main sources and are never
#   linked into the in-ears: they reach one only when production speaks to a
#   host ("Prod N → Hosts", which then reopens them at their stored level).
#   2026 linked them to buses 1-4.
# - Buses 5-6 are 2026's: the production loop is main plus the production
#   microphones.
IEM_BUSES = ("bus1", "bus2", "bus3", "bus4")
_FOLLOW_IEMS = dict.fromkeys(IEM_BUSES, "linked")
LINKS: dict[str, dict[str, str]] = {
    **{channel: {**_FOLLOW_IEMS, "bus5": "linked", "bus6": "linked"} for channel in SEAT_CHANNELS},
    **{channel: {"bus5": "paused", "bus6": "paused"} for channel in ("5", "6")},
    **{channel: {"bus5": "linked", "bus6": "linked"} for channel in ("7", "8")},
    "9": {**_FOLLOW_IEMS, "bus5": "linked"},
    "aux": dict(_FOLLOW_IEMS),
}
