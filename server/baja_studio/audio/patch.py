"""The studio's patch, read from the audio settings, for the default macros and links.

"Restore defaults…" (Audio setup) builds the macros and the link matrix for
the desk as it is patched, not for the 2026 patch: on 2026-11-14 the studio
PC's XR18 had the hosts on channels 5-8, and the 2026 macros, which took 5-8
for production microphones, held the hosts' in-ear sends closed.

What each input is comes from settings João already fills in:

- **Seats** (Audio setup → Seats): each seat's microphone channel and in-ear
  bus. While no seat has a microphone yet, the 2026 patch's channels 1-4
  stand in (a fresh install), on the seats' in-ear buses.
- **Production microphones**: PROD 1, 2, … in that order.
- **The other inputs of the main mix**: every input shown on the audio page
  (Channels → "On the audio page") that is neither a seat's nor a
  production microphone, e.g. SHOTGUN, CAMERA 1 and PC AUDIO.
- The production and camera operator's (talkback) buses.
"""

from baja_studio.adapters.xr18_addresses import SEND_SOURCES
from baja_studio.audio import defaults
from baja_studio.audio.config import AudioSettings
from baja_studio.audio.defaults import Patch


def patch_of(settings: AudioSettings) -> Patch:
    """The patch the default macros and links are built for."""
    seats: tuple[tuple[int, str, str], ...] = tuple(
        (seat.seat_id, seat.channel, seat.iem_bus)
        for seat in settings.seats
        if seat.channel is not None
    )
    if not seats:
        # Nothing mapped yet: the 2026 seat channels, on this setup's in-ear buses.
        buses: dict[int, str] = {seat.seat_id: seat.iem_bus for seat in settings.seats}
        seats = tuple(
            (seat, channel, buses.get(seat, bus))
            for seat, channel, bus in defaults.PATCH_2026.seats
        )
    seat_channels = {channel for _seat, channel, _bus in seats}
    production = tuple(
        channel for channel in settings.production_channels if channel not in seat_channels
    )
    others = tuple(
        channel
        for channel in SEND_SOURCES
        if settings.channels[channel].visible
        and channel not in seat_channels
        and channel not in production
    )
    return Patch(
        seats=seats,
        production=production,
        others=others,
        production_bus=settings.production_bus,
        talkback_bus=settings.talkback_bus,
    )
