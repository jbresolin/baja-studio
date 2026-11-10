"""Audio's card on the hosts screen (WP9 §3): seat microphones live or muted, and talkback.

The card renders from `audio` state: each seat's channel (the hosts module's
seats name it) is live or muted in the seat's colour. Talkback is "some
macro that opens a send to the camera operator's bus is active": the
macros are found here, from the settings, so the page only looks their ids
up in `audio.macros`. The camera operator's bus is the `talkback_bus`
setting (bus 6 in the 2026 patch).
"""

from baja_studio.audio.config import (
    AudioSettings,
    MacroConfig,
    MacroStep,
    RestoreLevelsStep,
    SetAuxStep,
)
from baja_studio.core.hosts_cards import HostsCard


def audio_hosts_card(settings: AudioSettings) -> HostsCard:
    """The "Audio" card, with the talkback macros and the bus's label as options."""
    bus = settings.talkback_bus
    return HostsCard(
        id="audio",
        title="Audio",
        priority=50,
        state_path="audio",
        component="audio_state",
        options={
            "talkback_macros": talkback_macros(settings.macros, bus),
            "talkback_label": settings.channels[bus].label or "camera op",
        },
    )


def talkback_macros(macros: list[MacroConfig], bus: str) -> list[str]:
    """Ids of the macros whose "on" steps open a send to `bus`."""
    return [macro.id for macro in macros if any(_opens(step, bus) for step in macro.on_steps)]


def _opens(step: MacroStep, bus: str) -> bool:
    if isinstance(step, RestoreLevelsStep):
        return any(point.bus == bus for point in step.points)
    if isinstance(step, SetAuxStep):
        return step.bus == bus and step.level > 0
    return False
