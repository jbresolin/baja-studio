"""The mixer-facing half of the audio controller: the link, what the mixer says, mirroring.

`AudioController` (controller.py) builds the operations on top of this
class. Here: the parts every change shares, whoever made it.

- The mixer worker attaches its `Xr18Link`, says when the mixer answers,
  and hands over what the mixer pushed (`receive_from_mixer`), which is
  filtered by write protection and applied under the lock.
- A main-fader change is mirrored to the sends *linked* to it (any source,
  the mixer included), except a send that is closed (has a stored level):
  a talkback route a macro closed stays closed when the fader moves.
- After every change the derived state (macro active states) is worked
  out again and the change listeners (MIDI LEDs, takeover) are told.

One re-entrant lock covers the cache and every decision; nothing under it
waits on another thread (writes are queued in the `Xr18Link`, MIDI in the
outbox).
"""

import logging
import threading
from collections.abc import Callable

from baja_studio.adapters.osc import OscMessage
from baja_studio.adapters.xr18_addresses import (
    MAIN_POINT,
    SEND_SOURCES,
    parse_address,
    point_address,
    send_address,
)
from baja_studio.adapters.xr18_link import Xr18Link
from baja_studio.audio.config import AudioSettings
from baja_studio.audio.macros import is_active
from baja_studio.audio.mixer_cache import MixerCache
from baja_studio.audio.stores import LinkStore, StoredLevels
from baja_studio.core.module import CommandError
from baja_studio.modules.audio.publisher import AudioPublisher
from baja_studio.modules.audio.queries import interesting_addresses

MIXER_SOURCE = "mixer"

# Called with the channels that changed and who changed them (MIDI feedback listens).
ChangeListener = Callable[[set[str], str], None]


class _View:
    """The `MixerView` macros plan against: the cache, stored levels and links."""

    def __init__(self, controller: "MixerSide") -> None:
        self._controller = controller

    def level(self, channel: str, bus: str) -> float | None:
        return self._controller.cache.level(channel, bus)

    def muted(self, channel: str) -> bool | None:
        return self._controller.cache.mutes.get(channel)

    def stored(self, point: str) -> float | None:
        return self._controller.stored.get(point)

    def link_state(self, channel: str, bus: str) -> str:
        return self._controller.links.state(channel, bus)


class MixerSide:
    """The link to the mixer, the cache of what it is set to, and the rules every change follows."""

    def __init__(
        self,
        settings: Callable[[], AudioSettings],
        stored: StoredLevels,
        links: LinkStore,
        publisher: AudioPublisher,
        logger: logging.LoggerAdapter[logging.Logger],
        wake_mixer: Callable[[], None],
    ) -> None:
        """Wire the parts; the mixer worker attaches its link when it connects."""
        self.settings = settings
        self.stored = stored
        self.links = links
        self.publisher = publisher
        self.cache = MixerCache()
        self.view = _View(self)
        self.lock = threading.RLock()
        self._logger = logger
        self._wake_mixer = wake_mixer
        self._link: Xr18Link | None = None
        self._online = False
        self._macro_active: dict[str, bool] = {}
        self._listeners: list[ChangeListener] = []
        self.selected_bus: str | None = None

    def add_change_listener(self, listener: ChangeListener) -> None:
        """Call `listener(channels, source)` after every change (under the lock)."""
        self._listeners.append(listener)

    # --- the mixer worker's side ------------------------------------------------

    def attach_link(self, link: Xr18Link | None, forget_values: bool) -> None:
        """A new link (or none) to the mixer; `forget_values` when the address changed."""
        with self.lock:
            self._link, self._online = link, False
            if forget_values:
                self.cache.clear()
                self.publisher.all_channels(self.cache)
                self._refresh_derived(set(), MIXER_SOURCE)

    def set_online(self, online: bool) -> None:
        """The mixer answered `/xinfo` again, or stopped answering."""
        with self.lock:
            came_back = online and not self._online
            self._online = online
            if came_back and self._link is not None:
                # It may have rebooted or loaded a snapshot meanwhile: read it all again.
                self._link.query(
                    interesting_addresses(self.settings(), self.links, self.selected_bus)
                )

    @property
    def online(self) -> bool:
        """Whether the mixer is answering."""
        return self._online

    def receive_from_mixer(self, link: Xr18Link, datagrams: list[bytes], now: float) -> None:
        """Datagrams from the mixer: filtered by write protection and applied, under the lock.

        Both happen under the controller's lock, so a write made on another
        thread cannot slip in between the check and the apply (which would
        let a stale echo overwrite it).
        """
        with self.lock:
            messages = [
                message for datagram in datagrams for message in link.receive(datagram, now)
            ]
            if messages:
                self.on_mixer_messages(messages)

    def on_mixer_messages(self, messages: list[OscMessage]) -> None:
        """Values the mixer pushed or answered (write protection already applied)."""
        with self.lock:
            changed: set[str] = set()
            for message in messages:
                parsed = parse_address(message.address)
                if parsed is None or message.first is None:
                    continue
                change = self.cache.apply(parsed, message.first)
                if change is None:
                    continue
                changed.add(change.channel)
                # Only a real move is mirrored: the first reading after connecting
                # (old is None) must not overwrite every linked IEM send.
                if (
                    change.kind == "fader"
                    and change.old is not None
                    and isinstance(change.new, float)
                ):
                    self._mirror(change.channel, change.new, MIXER_SOURCE)
            if changed:
                for channel in changed:
                    self.publisher.channel(self.cache, channel)
                self._refresh_derived(changed, MIXER_SOURCE)

    # --- shared by every operation --------------------------------------------------

    def _require_link(self) -> Xr18Link:
        if self._link is None or not self._online:
            raise CommandError(
                "the XR18 is offline: check it is on, its network cable, and the mixer "
                "address in the audio setup"
            )
        return self._link

    def _write_level(
        self, link: Xr18Link, channel: str, bus: str, level: float, source: str
    ) -> None:
        link.write(point_address(channel, bus), level)
        self.cache.set_level(channel, bus, level)
        self.publisher.last_source(f"{channel}:{bus}", source)
        if bus == MAIN_POINT:
            self._mirror(channel, level, source)

    def _mirror(self, channel: str, level: float, source: str) -> None:
        """Copy a main-fader level to the linked, open sends of `channel`."""
        if self._link is None or channel not in SEND_SOURCES:
            return
        for bus in self.links.linked_buses(channel):
            if self.stored.get(f"{channel}:{bus}") is not None:
                continue  # closed by a macro or button: stays closed
            if self.cache.sends.get((channel, bus)) == level:
                continue
            self._link.write(send_address(channel, bus), level)
            self.cache.sends[(channel, bus)] = level

    def _after_writes(self, channels: set[str], source: str) -> None:
        for channel in channels:
            self.publisher.channel(self.cache, channel)
        self._refresh_derived(channels, source)
        self._wake_mixer()

    def _refresh_derived(self, channels: set[str], source: str) -> None:
        """Re-derive macro states, then tell listeners (MIDI LEDs, takeover)."""
        settings = self.settings()
        active = {macro.id: is_active(macro, self.view) for macro in settings.macros}
        if active != self._macro_active:
            self._macro_active = active
            self.publisher.publish(
                "macros", {macro_id: {"active": state} for macro_id, state in active.items()}
            )
        for listener in self._listeners:
            listener(channels, source)
