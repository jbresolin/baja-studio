"""`audio.hosts_view`: what the hosts screen's audio card and personal-mix overlay read (WP9b).

Audio owns the audio facts; the hosts screen joins them with `hosts.seats`
by `seat_id` for names and colours. No module reads another's internals.

    audio.hosts_view
      mixer_online       false: the card says "mixer offline" and every state is null
      can_adjust         the `hosts_can_adjust_mix` setting (false: overlay read-only)
      step_db            one tap of + or - (`personal_mix_step_db`)
      talkback           {active, label}: production is talking to the camera operator
      seats.<id>         {seat_id, channel, channel_label, iem_bus, iem_label,
                          state: live | to_production | muted | not_mapped | null,
                          production_to_you}
      mixes.<id>         the seat's in-ear sources, in the order of the link matrix:
                          {channel, label, link: linked | paused | unlinked,
                           main_level, main_db, offset_db (null unless linked),
                           send_level, send_db}
      pad_change         {seq, seat_id, channel, label, offset_db} after a pad or knob
                          (not the overlay) moved a seat's offset: the screen's toast

Built again after every change (`MixerSide._refresh_derived`); the
publisher skips it when nothing in it changed.
"""

from typing import Any

from baja_studio.adapters.xr18_addresses import MAIN_POINT, SEND_SOURCES
from baja_studio.adapters.xr18_levels import format_db
from baja_studio.audio.config import AudioSettings
from baja_studio.audio.mixer_cache import MixerCache
from baja_studio.audio.seat_states import production_to_seat, seat_state
from baja_studio.audio.stores import LinkStore, OffsetStore
from baja_studio.modules.audio.hosts_card import talkback_macros


def mix_sources(settings: AudioSettings, links: LinkStore, bus: str) -> list[str]:
    """The sources a seat's personal mix offers for `bus`: the link matrix's rows for that bus.

    The inputs shown on the audio page, plus any linked (or paused) to `bus`
    though hidden, in mixer order, as the setup page's link matrix lists them.
    """
    on_bus = {channel for channel, buses in links.matrix().items() if bus in buses}
    return [
        channel
        for channel in SEND_SOURCES
        if settings.channels[channel].visible or channel in on_bus
    ]


def build_hosts_view(
    settings: AudioSettings,
    cache: MixerCache,
    links: LinkStore,
    offsets: OffsetStore,
    online: bool,
    macro_active: dict[str, bool],
    pad_change: dict[str, Any] | None,
) -> dict[str, Any]:
    """The whole `audio.hosts_view` value (see the module docstring)."""
    seats: dict[str, Any] = {}
    mixes: dict[str, Any] = {}
    for seat in settings.seats:
        seats[str(seat.seat_id)] = {
            "seat_id": seat.seat_id,
            "channel": seat.channel,
            "channel_label": None if seat.channel is None else settings.label(seat.channel),
            "iem_bus": seat.iem_bus,
            "iem_label": settings.label(seat.iem_bus),
            "state": seat_state(cache, seat.channel, settings.production_bus) if online else None,
            "production_to_you": online
            and production_to_seat(cache, settings.production_channels, seat.iem_bus),
        }
        mixes[str(seat.seat_id)] = [
            _mix_row(settings, cache, links, offsets, channel, seat.iem_bus, online)
            for channel in mix_sources(settings, links, seat.iem_bus)
        ]
    talkback_ids = talkback_macros(settings.macros, settings.talkback_bus)
    return {
        "mixer_online": online,
        "can_adjust": settings.hosts_can_adjust_mix,
        "step_db": settings.personal_mix_step_db,
        "talkback": {
            "active": online and any(macro_active.get(macro_id) for macro_id in talkback_ids),
            "label": settings.channels[settings.talkback_bus].label or "camera op",
        },
        "seats": seats,
        "mixes": mixes,
        "pad_change": pad_change,
    }


def _mix_row(
    settings: AudioSettings,
    cache: MixerCache,
    links: LinkStore,
    offsets: OffsetStore,
    channel: str,
    bus: str,
    known: bool,
) -> dict[str, Any]:
    link = links.state(channel, bus)
    main = cache.level(channel, MAIN_POINT) if known else None
    send = cache.level(channel, bus) if known else None
    return {
        "channel": channel,
        "label": settings.label(channel),
        "link": link,
        "main_level": main,
        "main_db": None if main is None else format_db(main),
        "offset_db": offsets.get(channel, bus) if link == "linked" else None,
        "send_level": send,
        "send_db": None if send is None else format_db(send),
    }
