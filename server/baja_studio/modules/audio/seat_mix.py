"""Seats' personal in-ear mixes: a linked send on a seat's bus is main plus an offset (WP9b §3).

The IEM buses of the hosts' seats (`AudioSettings.seats`) are a copy of the
main mix plus each seat's own offsets. On those buses, every adjustment of
a *linked* send (a host's pad vol up/down, an M-VAVE knob, the page, the
hosts-screen overlay) changes the pair's offset and **keeps the link**, so
the send keeps following its fader. Elsewhere WP4's rule stands: a direct
change pauses the link.

An offset is stored even when the send cannot be written now (its fader is
unknown, or a macro closed the send): the next mirror or reopen applies it.

`AudioController` (controller.py) sends seat-bus changes here; the
overlay's commands are checked in `seat_commands.py` first.
"""

from typing import Any

from baja_studio.adapters.xr18_addresses import MAIN_POINT, send_address
from baja_studio.adapters.xr18_levels import level_to_db, offset_level
from baja_studio.audio.seat_config import MAX_OFFSET_DB, MIN_OFFSET_DB
from baja_studio.core.module import CommandError
from baja_studio.modules.audio.hosts_view import mix_sources
from baja_studio.modules.audio.mixer_side import MixerSide


def clamp_offset(offset_db: float) -> float:
    """An offset within the range a seat may use, rounded to 0.1 dB."""
    return round(min(MAX_OFFSET_DB, max(MIN_OFFSET_DB, offset_db)), 1)


class SeatMixing(MixerSide):
    """Offsets on the seats' in-ear buses, on top of the mixer-facing rules."""

    def follows_with_offset(self, channel: str, bus: str) -> bool:
        """Whether `channel`'s send to `bus` is a linked send on a seat's in-ear bus."""
        with self.lock:
            return (
                self.settings().seat_on_bus(bus) is not None
                and self.links.state(channel, bus) == "linked"
            )

    def nudge_offset(
        self, channel: str, bus: str, delta_db: float, source: str, from_overlay: bool = False
    ) -> float:
        """Move a seat send's offset by `delta_db` (clamped); returns the send's level."""
        with self.lock:
            offset = clamp_offset(self.offsets.get(channel, bus) + delta_db)
            return self.set_offset(channel, bus, offset, source, from_overlay)

    def set_send_as_offset(self, channel: str, bus: str, level: float, source: str) -> float:
        """A seat send set to an absolute level: kept as the offset from its fader."""
        with self.lock:
            main = self.cache.faders.get(channel)
            if main is None or main <= 0.0:
                raise CommandError(
                    f"{self.settings().label(channel)} in {self.settings().label(bus)} follows "
                    "its main fader, which is off: use + and - to set how far above or below "
                    "it this seat hears it"
                )
            offset = 0.0 if level <= 0.0 else level_to_db(level) - level_to_db(main)
            return self.set_offset(channel, bus, clamp_offset(offset), source)

    def set_offset(
        self, channel: str, bus: str, offset_db: float, source: str, from_overlay: bool = False
    ) -> float:
        """Store the offset and write main + offset to the send; returns the send's level.

        Anything but the overlay leaves a note for the hosts screen's toast
        (`pad_change`), so a host pressing a pad sees what changed.
        """
        with self.lock:
            link = self._require_link()
            self.offsets.set(channel, bus, offset_db)
            main = self.cache.faders.get(channel)
            closed = self.stored.get(f"{channel}:{bus}") is not None
            if main is not None and not closed:
                level = offset_level(main, offset_db)
                link.write(send_address(channel, bus), level)
                self.cache.set_level(channel, bus, level)
            self.publisher.last_source(f"{channel}:{bus}", source)
            self.publisher.publish("offsets", self.offsets.all())
            if not from_overlay:
                self._note_pad_change(channel, bus, offset_db)
            self._after_writes({channel}, source)
            return self.cache.level(channel, bus) or 0.0

    def reset_offsets(self, bus: str, source: str) -> list[str]:
        """Every linked source on `bus` back to the main mix (offset 0); returns them."""
        with self.lock:
            link = self._require_link()
            reset: list[str] = []
            for channel in mix_sources(self.settings(), self.links, bus):
                if self.links.state(channel, bus) != "linked":
                    continue
                reset.append(channel)
                self.offsets.set(channel, bus, 0.0)
                main = self.cache.level(channel, MAIN_POINT)
                if main is not None and self.stored.get(f"{channel}:{bus}") is None:
                    link.write(send_address(channel, bus), main)
                    self.cache.set_level(channel, bus, main)
                    self.publisher.last_source(f"{channel}:{bus}", source)
            self.publisher.publish("offsets", self.offsets.all())
            self._after_writes(set(reset), source)
            return reset

    def routing(self) -> dict[str, Any]:
        """The link matrix as designed and every offset, for the settings file (WP9b §4).

        `{"links": {channel: [buses]}, "offsets": {"channel:bus": dB}}`. A
        paused link is exported as linked: pauses happen during the show.
        """
        with self.lock:
            return {"links": self.links.definitions(), "offsets": self.offsets.all()}

    def replace_routing(
        self, links: dict[str, list[str]], offsets: dict[str, float], source: str
    ) -> None:
        """Make the link matrix exactly `links` (all linked) and the offsets exactly `offsets`.

        Called by the settings import with values already checked. Works with
        the mixer offline; when it is online, every linked send is brought to
        its fader plus its offset at once.
        """
        with self.lock:
            wanted = {(channel, bus) for channel, buses in links.items() for bus in buses}
            for channel, buses in self.links.definitions().items():
                for bus in buses:
                    if (channel, bus) not in wanted:
                        self.links.set(channel, bus, "unlinked")
            for channel, bus in sorted(wanted):
                self.links.set(channel, bus, "linked")
            for point in self.offsets.all():
                channel, bus = point.split(":")
                self.offsets.set(channel, bus, 0.0)
            for point, offset_db in offsets.items():
                channel, bus = point.split(":")
                self.offsets.set(channel, bus, offset_db)
            self._logger.info("link matrix and offsets replaced by %s", source)
            self.publisher.publish("links", self.links.matrix())
            self.publisher.publish("offsets", self.offsets.all())
            channels = {channel for channel, _ in wanted}
            if self._online:
                for channel in sorted(channels):
                    fader = self.cache.faders.get(channel)
                    if fader is not None:
                        self._mirror(channel, fader, source)
            self._after_writes(channels, source)

    def _note_pad_change(self, channel: str, bus: str, offset_db: float) -> None:
        seat = self.settings().seat_on_bus(bus)
        if seat is None:
            return
        previous: dict[str, Any] = self.pad_change or {"seq": 0}
        self.pad_change = {
            "seq": previous["seq"] + 1,
            "seat_id": seat.seat_id,
            "channel": channel,
            "label": self.settings().label(channel),
            "offset_db": offset_db,
        }
