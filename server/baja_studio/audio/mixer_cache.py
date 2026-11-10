"""What the studio believes the mixer's controls are set to.

Filled by the mixer's pushes and query answers (after write protection,
`adapters/xr18_link.py`) and by our own writes, optimistically, the moment
they are queued. A value the mixer has not told us yet is None: the pages
show it as unknown rather than guessing.

Not thread-safe on its own: the audio controller holds its lock around
every use.
"""

from dataclasses import dataclass
from typing import Any

from baja_studio.adapters.osc import OscArgument
from baja_studio.adapters.xr18_addresses import MAIN_POINT, ParsedAddress


@dataclass(frozen=True)
class CacheChange:
    """One value that changed: what (`kind` as in `ParsedAddress`), and from/to."""

    kind: str
    channel: str
    bus: str | None
    old: float | bool | str | None
    new: float | bool | str


class MixerCache:
    """Faders, mutes, names, pans and sends, by channel id.

    Also what the hosts' audio card needs to tell where a microphone is
    heard (`audio/seat_states.py`): each strip's LR assign, and each send's
    on switch and tap.
    """

    def __init__(self) -> None:
        """Nothing known yet."""
        self.faders: dict[str, float] = {}
        self.mutes: dict[str, bool] = {}
        self.names: dict[str, str] = {}
        self.pans: dict[str, float] = {}
        self.sends: dict[tuple[str, str], float] = {}
        self.lr_assigns: dict[str, bool] = {}
        self.send_ons: dict[tuple[str, str], bool] = {}
        self.send_taps: dict[tuple[str, str], int] = {}

    def apply(self, parsed: ParsedAddress, value: OscArgument) -> CacheChange | None:
        """Take a value from the mixer; returns the change, or None if nothing changed.

        `mix/on` is inverted here: the cache keeps *muted*.
        """
        if parsed.kind in ("fader", "pan") and isinstance(value, int | float):
            table = self.faders if parsed.kind == "fader" else self.pans
            old_value = table.get(parsed.channel)
            if _same(old_value, float(value)):
                return None
            table[parsed.channel] = float(value)
            return CacheChange(parsed.kind, parsed.channel, None, old_value, float(value))
        if parsed.kind == "send" and parsed.bus is not None and isinstance(value, int | float):
            key = (parsed.channel, parsed.bus)
            old_send = self.sends.get(key)
            if _same(old_send, float(value)):
                return None
            self.sends[key] = float(value)
            return CacheChange("send", parsed.channel, parsed.bus, old_send, float(value))
        if parsed.kind == "lr_assign" and isinstance(value, int | float):
            return _set(self.lr_assigns, parsed.channel, parsed, int(value) != 0)
        if parsed.kind == "send_on" and parsed.bus is not None and isinstance(value, int | float):
            return _set(self.send_ons, (parsed.channel, parsed.bus), parsed, int(value) != 0)
        if parsed.kind == "send_tap" and parsed.bus is not None and isinstance(value, int | float):
            return _set(self.send_taps, (parsed.channel, parsed.bus), parsed, int(value))
        if parsed.kind == "mute" and isinstance(value, int | float):
            muted = int(value) == 0
            old = self.mutes.get(parsed.channel)
            if old == muted:
                return None
            self.mutes[parsed.channel] = muted
            return CacheChange("mute", parsed.channel, None, old, muted)
        if parsed.kind == "name" and isinstance(value, str):
            old_name = self.names.get(parsed.channel)
            if old_name == value:
                return None
            self.names[parsed.channel] = value
            return CacheChange("name", parsed.channel, None, old_name, value)
        return None

    def level(self, channel: str, bus: str) -> float | None:
        """A send point's level: the fader for "main", else the send to `bus`."""
        if bus == MAIN_POINT:
            return self.faders.get(channel)
        return self.sends.get((channel, bus))

    def set_level(self, channel: str, bus: str, level: float) -> None:
        """Optimistic update after we queued a write."""
        if bus == MAIN_POINT:
            self.faders[channel] = level
        else:
            self.sends[(channel, bus)] = level

    def clear(self) -> None:
        """Forget everything (the mixer changed address or came back from a reboot)."""
        self.faders.clear()
        self.mutes.clear()
        self.names.clear()
        self.pans.clear()
        self.sends.clear()
        self.lr_assigns.clear()
        self.send_ons.clear()
        self.send_taps.clear()


def _set[Key](
    table: dict[Key, Any], key: Key, parsed: ParsedAddress, value: bool | int
) -> CacheChange | None:
    """Store a switch or an enum; the change, or None when it already had that value."""
    old = table.get(key)
    if old == value:
        return None
    table[key] = value
    return CacheChange(parsed.kind, parsed.channel, parsed.bus, old, value)


def _same(old: float | None, new: float) -> bool:
    return old is not None and abs(old - new) < 1e-6
