"""Where a seat's microphone is heard, worked out from the mixer's values alone (WP9b §2).

Never from "which macro was pressed last": a change made in Mixing Station,
on the desk or by a snapshot shows the same way.

A channel *reaches* a destination when it is unmuted, and

- for the main mix (LR): its LR assign is on and its fader is above -inf;
- for an aux bus: its send to that bus is on and above -inf, and, when the
  send is taken after the fader (`POST_FADER_TAPS`), the fader is above -inf.

Each answer is True, False or None (unknown: the mixer has not said yet).
One known "no" is enough for False; otherwise any unknown makes it None. A
send's on switch the mixer never reported counts as on (the XR18 may not
have one; `adapters/xr18_addresses.py`).

A seat is **live** when its channel reaches LR, **to_production** when it
does not reach LR but reaches the production bus, **muted** when it reaches
neither, and **not_mapped** without a channel. **production → you** is any
production microphone reaching the seat's in-ear bus.
"""

from baja_studio.adapters.xr18_addresses import POST_FADER_TAPS
from baja_studio.audio.mixer_cache import MixerCache

LIVE = "live"
TO_PRODUCTION = "to_production"
MUTED = "muted"
NOT_MAPPED = "not_mapped"


def all_of(*answers: bool | None) -> bool | None:
    """Three-valued AND: False if any is False, else None if any is unknown, else True."""
    if any(answer is False for answer in answers):
        return False
    if any(answer is None for answer in answers):
        return None
    return True


def _above_off(level: float | None) -> bool | None:
    return None if level is None else level > 0.0


def _unmuted(cache: MixerCache, channel: str) -> bool | None:
    muted = cache.mutes.get(channel)
    return None if muted is None else not muted


def reaches_lr(cache: MixerCache, channel: str) -> bool | None:
    """Whether `channel` is heard on the main mix."""
    return all_of(
        _unmuted(cache, channel),
        cache.lr_assigns.get(channel),
        _above_off(cache.faders.get(channel)),
    )


def reaches_bus(cache: MixerCache, channel: str, bus: str) -> bool | None:
    """Whether `channel` is heard on aux bus `bus`."""
    key = (channel, bus)
    tap = cache.send_taps.get(key)
    fader_open = _above_off(cache.faders.get(channel))
    if tap is None:
        # Not told where the send is taken: an open fader makes it irrelevant.
        fader_needed: bool | None = True if fader_open else None
    elif tap in POST_FADER_TAPS:
        fader_needed = fader_open
    else:
        fader_needed = True
    return all_of(
        _unmuted(cache, channel),
        cache.send_ons.get(key, True),
        _above_off(cache.sends.get(key)),
        fader_needed,
    )


def seat_state(cache: MixerCache, channel: str | None, production_bus: str) -> str | None:
    """live, to_production, muted or not_mapped; None while the mixer has not said enough."""
    if channel is None:
        return NOT_MAPPED
    on_air = reaches_lr(cache, channel)
    if on_air is True:
        return LIVE
    to_production = reaches_bus(cache, channel, production_bus)
    if on_air is False and to_production is not None:
        return TO_PRODUCTION if to_production else MUTED
    return None


def production_to_seat(cache: MixerCache, production_channels: list[str], iem_bus: str) -> bool:
    """Whether some production microphone is heard in the seat's in-ears (unknown counts as no)."""
    return any(reaches_bus(cache, channel, iem_bus) is True for channel in production_channels)
