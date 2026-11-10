"""Audio's card on the hosts screen: where each seat's microphone is heard, and talkback.

The card renders `audio.hosts_view` (`hosts_view.py`), joined with the hosts
module's seats by `seat_id` for names and colours (WP9b §2). Per seat:
LIVE, TO PRODUCTION, MUTED or "not mapped", and "production → you" when a
production microphone is open into the seat's in-ears; tapping the card
opens the seat's personal-mix overlay.

Talkback is "some macro that opens a send to the camera operator's bus is
active"; the macros are found here from the settings (`talkback_macros`),
the bus is the `talkback_bus` setting (bus 6 in the 2026 patch).
"""

from baja_studio.audio.config import (
    MacroConfig,
    MacroStep,
    RestoreLevelsStep,
    SetAuxStep,
)
from baja_studio.core.hosts_cards import HostsCard


def audio_hosts_card() -> HostsCard:
    """The "Audio" card; everything it shows is in `audio.hosts_view`."""
    return HostsCard(
        id="audio",
        title="Audio",
        priority=50,
        state_path="audio.hosts_view",
        component="seat_audio",
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
