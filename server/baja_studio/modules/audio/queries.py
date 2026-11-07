"""Which mixer addresses audio asks for, and when: never all the aux sends.

In 2026 polling every send of every channel hung the XR18. The mixer
pushes every change to subscribers (`/xremote`), so we only *ask* for:

- on connect (and after a snapshot load): each strip's fader, mute and
  name (24 strips, 72 queries), plus the sends something here depends on:
  linked or paused pairs, points used by macros and M-VAVE buttons, and the
  strips' sends to the bus selected for the knobs;
- on demand: one bus's sends for the visible and M-VAVE strips, when a page
  shows that bus or the knobs select it.

The link spreads the queries over its packet budget.
"""

from baja_studio.adapters.xr18_addresses import (
    CHANNEL_IDS,
    MAIN_POINT,
    SEND_SOURCES,
    fader_address,
    mute_address,
    name_address,
    pan_address,
    send_address,
)
from baja_studio.audio.config import AudioSettings, RestoreLevelsStep, SetAuxStep, StoreLevelsStep
from baja_studio.audio.stores import LinkStore


def interesting_addresses(
    settings: AudioSettings, links: LinkStore, selected_bus: str | None
) -> list[str]:
    """Everything to read after (re)connecting, in a useful order: strips first."""
    addresses = []
    for channel in CHANNEL_IDS:
        addresses += [fader_address(channel), mute_address(channel), name_address(channel)]
    points: set[tuple[str, str]] = set()
    for channel, buses in links.matrix().items():
        points.update((channel, bus) for bus in buses)
    points.update(_macro_points(settings))
    points.update(_button_points(settings))
    if selected_bus is not None:
        points.update((channel, selected_bus) for channel in _mvave_channels(settings))
    for channel, bus in sorted(points):
        if bus != MAIN_POINT and channel in SEND_SOURCES:
            addresses.append(send_address(channel, bus))
    for strip, knob in settings.midi.knobs.items():
        pan_channel = settings.midi.faders.get(strip)
        if knob.target == "pan" and pan_channel in SEND_SOURCES:
            addresses.append(pan_address(str(pan_channel)))
    return addresses


def send_query_addresses(settings: AudioSettings, bus: str) -> list[str]:
    """One bus's sends from the inputs on the page or the M-VAVE."""
    channels = {channel for channel, config in settings.channels.items() if config.visible} | set(
        _mvave_channels(settings)
    )
    return [send_address(channel, bus) for channel in sorted(channels) if channel in SEND_SOURCES]


def _mvave_channels(settings: AudioSettings) -> list[str]:
    return [channel for channel in settings.midi.faders.values() if channel is not None]


def _macro_points(settings: AudioSettings) -> set[tuple[str, str]]:
    points: set[tuple[str, str]] = set()
    for macro in settings.macros:
        for step in [*macro.on_steps, *macro.off_steps]:
            if isinstance(step, SetAuxStep):
                points.add((step.channel, step.bus))
            elif isinstance(step, StoreLevelsStep | RestoreLevelsStep):
                points.update((point.channel, point.bus) for point in step.points)
    return points


def _button_points(settings: AudioSettings) -> set[tuple[str, str]]:
    points: set[tuple[str, str]] = set()
    for key, action in settings.midi.buttons.items():
        if action.type != "send_toggle":
            continue
        strip, row = key.split("_", 1)
        channel = action.channel or settings.midi.faders.get(strip)
        bus = action.bus or settings.midi.row_buses.get(row)
        if channel is not None and bus is not None:
            points.add((channel, bus))
    return points
