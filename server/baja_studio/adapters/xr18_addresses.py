"""Where things live on the XR18: OSC addresses as data, with the quirks spelled out.

The studio names mixer strips with short ids, used everywhere (settings,
state, commands):

- `"1"` … `"16"`: the input channels.
- `"aux"`: the stereo aux return, which the XR18 surface and the 2026 tool
  showed as channels 17-18 (both numbers mean the same strip).
- `"lr"`: the main stereo bus.
- `"bus1"` … `"bus6"`: the aux buses (in this studio: IEMs, production, camera op).

A *send point* is where a level lives for one input: its own fader (`"main"`)
or its send to an aux bus (`"bus1"` … `"bus6"`). Macros and stored levels
work on send points.
"""

import re
from dataclasses import dataclass

XR18_PORT = 10024

INPUT_CHANNELS = tuple(str(number) for number in range(1, 17))
AUX_RETURN = "aux"
MAIN_BUS = "lr"
AUX_BUSES = tuple(f"bus{number}" for number in range(1, 7))
# Inputs have aux sends; buses and the main mix do not.
SEND_SOURCES = (*INPUT_CHANNELS, AUX_RETURN)
CHANNEL_IDS = (*INPUT_CHANNELS, AUX_RETURN, MAIN_BUS, *AUX_BUSES)

MAIN_POINT = "main"
SEND_POINT_BUSES = (MAIN_POINT, *AUX_BUSES)

# Keep-alive: /xremote subscribes us to every change for 10 s (renewed every 8 s).
SUBSCRIBE = "/xremote"
# Answered with (ip, name, model, firmware); used to tell the mixer is there.
INFO = "/xinfo"
# Loads one of the mixer's 64 snapshots (1-based). Replaces the whole mix.
SNAPSHOT_LOAD = "/-snap/load"


def channel_prefix(channel: str) -> str:
    """The OSC prefix of a strip.

    - Inputs use two digits: `/ch/01` … `/ch/16`.
    - The stereo aux return is `/rtn/aux`, not `/ch/17` (the 2026 quirk).
    - The main bus is `/lr`.
    - Aux buses are `/bus/1` … `/bus/6`, one digit on the X Air family (the
      X32 uses two). The 2026 tool sent `/bus/01`; we accept both when
      parsing, and the bench (docs/bench/wp4.md) confirms which one the
      XR18 obeys.
    """
    if channel in INPUT_CHANNELS:
        return f"/ch/{int(channel):02d}"
    if channel == AUX_RETURN:
        return "/rtn/aux"
    if channel == MAIN_BUS:
        return "/lr"
    if channel in AUX_BUSES:
        return f"/bus/{channel.removeprefix('bus')}"
    raise ValueError(f"Unknown channel: {channel}")


def fader_address(channel: str) -> str:
    """Fader level, a float 0..1 on the XR18 curve (see `xr18_levels`)."""
    return f"{channel_prefix(channel)}/mix/fader"


def mute_address(channel: str) -> str:
    """`mix/on`: int 1 means *playing* and 0 means *muted* (inverted sense)."""
    return f"{channel_prefix(channel)}/mix/on"


def name_address(channel: str) -> str:
    """The name typed on the mixer (scribble strip), a string."""
    return f"{channel_prefix(channel)}/config/name"


def pan_address(channel: str) -> str:
    """Pan, a float 0..1 with 0.5 the centre; inputs only."""
    _require_send_source(channel)
    return f"{channel_prefix(channel)}/mix/pan"


def send_address(channel: str, bus: str) -> str:
    """Level of an input's send to an aux bus: `/ch/01/mix/03/level` (two digits)."""
    _require_send_source(channel)
    if bus not in AUX_BUSES:
        raise ValueError(f"bus must be one of {', '.join(AUX_BUSES)}, not {bus!r}")
    return f"{channel_prefix(channel)}/mix/{int(bus.removeprefix('bus')):02d}/level"


def point_address(channel: str, bus: str) -> str:
    """The address holding a send point's level: the fader for "main", else the send."""
    return fader_address(channel) if bus == MAIN_POINT else send_address(channel, bus)


def _require_send_source(channel: str) -> None:
    if channel not in SEND_SOURCES:
        raise ValueError(f"channel {channel} has no sends or pan; only inputs and aux do")


def normalise_channel(text: str) -> str:
    """A channel id from the forms people (and the 2026 D200 URLs) use.

    "1" … "16" (or "01"), "17"/"18"/"aux", "lr", "bus1" … "bus6". Raises
    `ValueError("Unknown channel: …")`, the 2026 wording, otherwise.
    """
    cleaned = text.strip().lower()
    if cleaned.isdigit():
        number = int(cleaned)
        if 1 <= number <= 16:
            return str(number)
        if number in (17, 18):
            return AUX_RETURN
    if cleaned in (AUX_RETURN, MAIN_BUS, *AUX_BUSES):
        return cleaned
    raise ValueError(f"Unknown channel: {text}")


def normalise_point_bus(text: str) -> str:
    """A send point's bus: "main" (also "lr", the 2026 word) or "bus1" … "bus6"."""
    cleaned = text.strip().lower()
    if cleaned in (MAIN_POINT, MAIN_BUS):
        return MAIN_POINT
    if cleaned in AUX_BUSES:
        return cleaned
    raise ValueError(f"bus must be main or bus1-bus6, not {text!r}")


@dataclass(frozen=True)
class ParsedAddress:
    """What an incoming address refers to.

    `kind` is "fader", "mute", "name", "pan" or "send"; `bus` is set for sends.
    """

    kind: str
    channel: str
    bus: str | None = None


_PREFIX_PATTERN = r"(?:/ch/(?P<ch>\d{2})|/rtn/aux|/lr|/bus/(?P<bus>0?[1-6]))"
_ADDRESS_PATTERNS = {
    "fader": re.compile(_PREFIX_PATTERN + r"/mix/fader"),
    "mute": re.compile(_PREFIX_PATTERN + r"/mix/on"),
    "name": re.compile(_PREFIX_PATTERN + r"/config/name"),
    "pan": re.compile(_PREFIX_PATTERN + r"/mix/pan"),
    "send": re.compile(_PREFIX_PATTERN + r"/mix/(?P<send>\d{2})/level"),
}


def parse_address(address: str) -> ParsedAddress | None:
    """The strip and parameter an address is about, or None for anything we ignore.

    Used on every packet the mixer pushes, so unknown addresses (EQ,
    dynamics, meters) are dropped cheaply.
    """
    for kind, pattern in _ADDRESS_PATTERNS.items():
        match = pattern.fullmatch(address)
        if match is None:
            continue
        channel = _channel_from_match(address, match)
        if channel is None:
            return None
        if kind == "send":
            number = int(match.group("send"))
            if not 1 <= number <= len(AUX_BUSES):
                return None  # sends 7-10 go to the effects
            return ParsedAddress(kind, channel, f"bus{number}")
        return ParsedAddress(kind, channel)
    return None


def _channel_from_match(address: str, match: re.Match[str]) -> str | None:
    if match.group("ch") is not None:
        number = int(match.group("ch"))
        return str(number) if 1 <= number <= 16 else None
    if match.group("bus") is not None:
        return f"bus{int(match.group('bus'))}"
    return AUX_RETURN if address.startswith("/rtn/aux") else MAIN_BUS
