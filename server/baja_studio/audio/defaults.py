"""First-start settings, seeded from the 2026 `xr18-control/mappings.json`.

Plain data (validated by `audio/config.py` through `validate_default`), so
it reads like the file it came from. What changed on the way:

- Channel ids: the stereo return is "aux" (2026: "17"); send points say
  "main" where 2026 said "lr".
- 2026 macros with `store-mute`/`restore` became `store_levels` /
  `restore_levels` steps. Their ids are kept, odd ones included
  ("macro-1774581643177"), because the D200 profile calls them by id.
- PROD 3 got the same macros as PROD 1 and 2 (the D200 images for PROD 3
  existed in 2026, the macros did not).
- 2026's "Prod N → Hosts" opens HOST 1's in-ears only ("→ Host 1", as its
  D200 image said), and "Prod N → Host 2" is new (2026-11-14, a D200 button
  per host).
- "Host N → Prod" takes the host off LR with the LR assign instead of
  closing the fader, and the guests got one each (WP9b, ADR 0013).
- The link matrix is not a setting any more; it seeds the `audio_links`
  table instead (`LINKS` below, see `audio/stores.py`), changed from 2026's
  for the hosts' personal mixes (WP9b).
- The macros and the link matrix follow a `Patch`: which channel is which
  seat's microphone, which are production microphones, which other inputs
  are part of the main mix (2026-11-14 bench: the studio's desk has the
  hosts on channels 5-8, so macros written for the 2026 patch held the
  hosts' sends closed). A fresh install uses `PATCH_2026`; Audio setup's
  "Restore defaults…" uses the patch of the saved settings (`audio/patch.py`).
"""

from dataclasses import dataclass, field
from typing import Any, NamedTuple

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


@dataclass(frozen=True)
class Patch:
    """Which input is what, as far as the default macros and links need to know.

    - `seats`: (seat id 1-4, microphone channel, in-ear bus) of each seat with
      a microphone; seats 1-2 are the hosts, 3-4 the guests.
    - `production`: the production microphones, PROD 1 first.
    - `others`: the other inputs of the main mix (a shotgun, a camera's
      sound, PC AUDIO): the in-ears copy them too.
    """

    seats: tuple[tuple[int, str, str], ...]
    production: tuple[str, ...]
    others: tuple[str, ...]
    production_bus: str = "bus5"
    talkback_bus: str = "bus6"
    hosts: tuple[int, ...] = field(default=(1, 2))

    def in_ear_buses(self) -> tuple[str, ...]:
        """Every seat's in-ear bus, seat order."""
        return tuple(bus for _seat, _channel, bus in self.seats)


# The 2026 event's patch: HOST 1-2 and GUEST 1-2 on 1-4, PROD 1-4 on 5-8,
# the shotgun on 9, PC AUDIO on the stereo return.
PATCH_2026 = Patch(
    seats=((1, "1", "bus1"), (2, "2", "bus2"), (3, "3", "bus3"), (4, "4", "bus4")),
    production=("5", "6", "7", "8"),
    others=("9", "aux"),
)
# The seats' microphones and the in-ear bus each one feeds, in the 2026 patch.
SEAT_CHANNELS = {channel: bus for _seat, channel, bus in PATCH_2026.seats}


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


def _talk_to_production(
    macro_id: str, label: str, colour: str, channel: str, others: list[str]
) -> dict[str, Any]:
    """A seat talks to production off air (WP9b, ADR 0013 decision 8).

    On: the channel leaves the main mix (LR assign off, the fader untouched),
    and the other seats stop hearing it: their in-ear sends (`others`) are
    paused, then closed with stored levels (paused first, so nothing
    re-mirrors them). The seat's own in-ears and the production and talkback
    buses stay linked, so the host hears themselves and production and the
    camera operator hear them.
    Off: back on LR; the other seats' sends restored, then linked again
    (back to the fader plus their offsets).
    """
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


# Seat id → the id, label and colour of its "talk to production" macro.
TALK_TO_PRODUCTION = {
    1: ("host1-talk-prod", "Host 1 → Prod", "#e74c3c"),
    2: ("host2-talk-prod", "Host 2 → Prod", "#e67e22"),
    3: ("guest1-talk-prod", "Guest 1 → Prod", "#f1c40f"),
    4: ("guest2-talk-prod", "Guest 2 → Prod", "#2ecc71"),
}


class ProductionMacros(NamedTuple):
    """The ids of one production microphone's macros, and their colour."""

    to_host1: str
    to_host2: str
    go_live: str
    to_cam: str
    colour: str


# PROD 1-3. The odd ids are 2026's. "→ Host 1" keeps 2026's "→ Hosts" id (the
# D200 drew it as TALK HOST 1); "→ Host 2" is new (2026-11-14, João: a D200
# button per host).
PRODUCTION_MACROS = (
    ProductionMacros(
        "prod1-talk-hosts",
        "prod1-talk-host2",
        "macro-1774582227201",
        "macro-1774582749617",
        "#3498db",
    ),
    ProductionMacros(
        "macro-1774581643177",
        "prod2-talk-host2",
        "macro-1774582340101",
        "macro-1774582877605",
        "#9b59b6",
    ),
    ProductionMacros(
        "prod3-talk-hosts", "prod3-talk-host2", "prod3-go-live", "prod3-talk-cam", "#1abc9c"
    ),
)


def macros(patch: Patch = PATCH_2026) -> list[dict[str, Any]]:
    """The default macros for `patch` (ids kept for the D200 profile).

    Every seat with a microphone gets "talk to production"; PROD 1-3 get
    "→ Host 1" and "→ Host 2" (into that host's in-ears), "Go Live" (onto the
    main mix) and "→ Cam" (into the camera operator's bus).
    """
    talk = []
    for seat, channel, own_bus in patch.seats:
        macro_id, label, colour = TALK_TO_PRODUCTION[seat]
        others = [bus for bus in patch.in_ear_buses() if bus != own_bus]
        talk.append(_talk_to_production(macro_id, label, colour, channel, others))
    producers = list(zip(PRODUCTION_MACROS, patch.production, strict=False))
    # "→ Host 1" and "→ Host 2": each opens one host's in-ears.
    in_ears = {seat: bus for seat, _channel, bus in patch.seats}
    to_hosts = []
    for host, seat in enumerate(patch.hosts, start=1):
        if seat not in in_ears:
            continue  # no microphone on that seat
        for n, (ids, channel) in enumerate(producers, start=1):
            macro_id = ids.to_host1 if host == 1 else ids.to_host2
            label = f"Prod {n} → Host {host}"
            to_hosts.append(
                _route(macro_id, label, ids.colour, channel, in_ears[seat], closed_when_on=False)
            )
    go_live = [
        _route(ids.go_live, f"Prod {n} Go Live", ids.colour, channel, "main", closed_when_on=False)
        for n, (ids, channel) in enumerate(producers, start=1)
    ]
    to_cam = [
        _route(
            ids.to_cam,
            f"Prod {n} → Cam",
            ids.colour,
            channel,
            patch.talkback_bus,
            closed_when_on=False,
        )
        for n, (ids, channel) in enumerate(producers, start=1)
    ]
    return [*talk, *to_hosts, *go_live, *to_cam]


# The link matrix a fresh install starts with (an existing install keeps its
# own until Audio setup → Link matrix → "Restore defaults…"). A missing pair is
# "unlinked"; in 2026 a missing entry silently meant "linked" (ADR 0006).
#
# - The in-ear buses are a copy of the main mix plus each seat's offsets
#   (LIVESTACK-34 topology), so every source of the main mix (the seats'
#   microphones and the other inputs of the main mix) is linked to them.
# - The production microphones are not main sources and are never linked
#   into the in-ears: they reach one only when production speaks to a host
#   ("Prod N → Hosts", which reopens them at their stored level). They are
#   *paused* on the production and talkback buses: kept at their own level
#   there, not following a main fader that is down while they are off air.
#   (2026 had PROD 3-4 linked there, so production heard them only on air.)
# - The production bus is main plus the production microphones: the seats and
#   the other main inputs, except PC AUDIO (clip sound), as in 2026.
# - The talkback bus (camera op) hears the seats, as in 2026.
CLIP_SOUND = "aux"


def links(patch: Patch = PATCH_2026) -> dict[str, dict[str, str]]:
    """The default link matrix for `patch`: channel → {bus: "linked" | "paused"}."""
    follow_in_ears = dict.fromkeys(patch.in_ear_buses(), "linked")
    matrix: dict[str, dict[str, str]] = {}
    for _seat, channel, _bus in patch.seats:
        matrix[channel] = {
            **follow_in_ears,
            patch.production_bus: "linked",
            patch.talkback_bus: "linked",
        }
    for channel in patch.production:
        matrix[channel] = {patch.production_bus: "paused", patch.talkback_bus: "paused"}
    for channel in patch.others:
        matrix[channel] = dict(follow_in_ears)
        if channel != CLIP_SOUND:
            matrix[channel][patch.production_bus] = "linked"
    return matrix


LINKS = links()
IEM_BUSES = PATCH_2026.in_ear_buses()
