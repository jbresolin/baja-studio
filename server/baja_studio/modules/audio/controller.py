"""The audio controller: every change to the mixer goes through here.

Pages, the M-VAVE, the D200 and macros all call these methods, so they are
peers: same rules, same state, one command log. The mixer's own pushes
(Mixing Station, the desk) come in through `on_mixer_messages`.

Rules applied to every change, whatever its source:

- A main-fader change is mirrored to the sends *linked* to it (any source,
  the mixer included), except a send that is closed (has a stored level):
  a talkback route a macro closed stays closed when the fader moves.
- Setting a linked send directly (page, knob, D200 nudge, a macro's
  `set_aux`) pauses its link, so the IEM tweak survives the next fader move.
- Whether each macro is active is recomputed from the result.

One re-entrant lock covers the cache and every decision; nothing under it
waits on another thread (writes are queued in the `Xr18Link`, MIDI in the
outbox).
"""

import logging
import threading
from collections.abc import Callable
from typing import Any

from baja_studio.adapters.osc import OscMessage
from baja_studio.adapters.xr18_addresses import (
    MAIN_POINT,
    SEND_SOURCES,
    SNAPSHOT_LOAD,
    mute_address,
    pan_address,
    parse_address,
    point_address,
    send_address,
)
from baja_studio.adapters.xr18_levels import clamp_level, nudge_level
from baja_studio.adapters.xr18_link import Xr18Link
from baja_studio.audio.config import AudioSettings, MacroStep
from baja_studio.audio.macros import (
    PlannedChange,
    is_active,
    plan_close,
    plan_open,
    plan_steps,
    steps_to_run,
)
from baja_studio.audio.mixer_cache import MixerCache
from baja_studio.audio.stores import LinkStore, StoredLevels
from baja_studio.core.module import CommandError
from baja_studio.modules.audio.publisher import AudioPublisher
from baja_studio.modules.audio.queries import interesting_addresses, send_query_addresses

MIXER_SOURCE = "mixer"

# Called with the channels that changed and who changed them (MIDI feedback listens).
ChangeListener = Callable[[set[str], str], None]


class _View:
    """The `MixerView` macros plan against: the cache, stored levels and links."""

    def __init__(self, controller: "AudioController") -> None:
        self._controller = controller

    def level(self, channel: str, bus: str) -> float | None:
        return self._controller.cache.level(channel, bus)

    def muted(self, channel: str) -> bool | None:
        return self._controller.cache.mutes.get(channel)

    def stored(self, point: str) -> float | None:
        return self._controller.stored.get(point)

    def link_state(self, channel: str, bus: str) -> str:
        return self._controller.links.state(channel, bus)


class AudioController:
    """Applies operations to the mixer and keeps audio's state up to date."""

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

    # --- operations ---------------------------------------------------------------

    def set_level(self, channel: str, bus: str, level: float, source: str) -> float:
        """Set a fader (`bus` "main") or a send; a direct send change pauses its link."""
        level = clamp_level(level)
        pauses = bus != MAIN_POINT
        self._run([PlannedChange("level", channel, bus, level, None, pauses)], source)
        return level

    def nudge(self, channel: str, bus: str, delta_db: float, source: str) -> float:
        """Move a fader or send by `delta_db` (unknown counts as unity, as in 2026)."""
        with self.lock:
            target = nudge_level(self.cache.level(channel, bus), delta_db)
            return self.set_level(channel, bus, target, source)

    def set_mute(self, channel: str, muted: bool | None, source: str) -> bool:
        """Mute, unmute, or toggle (`muted` None; unknown counts as unmuted). Returns muted."""
        with self.lock:
            if muted is None:
                muted = not bool(self.cache.mutes.get(channel))
            self._run([PlannedChange("mute", channel, value=muted)], source)
            return muted

    def set_pan(self, channel: str, pan: float, source: str) -> float:
        """Pan an input (0 left, 0.5 centre, 1 right)."""
        with self.lock:
            link = self._require_link()
            pan = clamp_level(pan)
            link.write(pan_address(channel), float(pan))
            self.cache.pans[channel] = pan
            self._after_writes({channel}, source)
            return pan

    def toggle_send(self, channel: str, bus: str, source: str) -> bool:
        """Close the point if it is open (store and silence), else open it. Returns open."""
        with self.lock:
            level = self.cache.level(channel, bus)
            closing = level is None or level > 0.0
            plan = (
                plan_close(self.view, channel, bus)
                if closing
                else plan_open(self.view, channel, bus)
            )
            self._run(plan, source)
            return not closing

    def run_macro(self, macro_id: str, source: str) -> bool:
        """Run a macro (toggle: on or off, worked out from the mixer); returns active after."""
        with self.lock:
            macro = self.settings().macro(macro_id)
            if macro is None:
                raise CommandError(f"no macro {macro_id!r}; see the audio setup page")
            self._run(plan_steps(steps_to_run(macro, self.view), self.view), source)
            return is_active(macro, self.view)

    def dry_run(self, steps: list[MacroStep]) -> list[PlannedChange]:
        """What `steps` would change now, without changing anything."""
        with self.lock:
            return plan_steps(steps, self.view)

    def recall_scene(self, index: int, source: str) -> None:
        """A configured scene (snapshot and/or macro); an unconfigured 1-64 loads that snapshot."""
        with self.lock:
            scene = next((s for s in self.settings().scenes if s.index == index), None)
            if scene is None and not 1 <= index <= 64:
                raise CommandError(f"no scene {index}; scenes and snapshots are numbered 1-64")
            snapshot = index if scene is None else scene.snapshot
            if snapshot is not None:
                self._run([PlannedChange("snapshot", value=snapshot)], source)
            if scene is not None and scene.macro:
                self.run_macro(scene.macro, source)

    def set_link(self, channel: str, bus: str, state: str, source: str) -> None:
        """Link, pause or unlink a send; linking brings the send to the fader's level now."""
        if channel not in SEND_SOURCES:
            raise CommandError(f"channel {channel} has no sends to link")
        with self.lock:
            try:
                self.links.set(channel, bus, state)
            except ValueError as error:
                raise CommandError(str(error)) from None
            self.publisher.publish("links", self.links.matrix())
            fader = self.cache.faders.get(channel)
            held = self.stored.get(f"{channel}:{bus}") is not None
            if state == "linked" and fader is not None and not held and self._link is not None:
                self._run([PlannedChange("level", channel, bus, fader)], source)
            else:
                self._refresh_derived({channel}, source)

    def select_bus(self, bus: str | None, source: str) -> str | None:
        """The bus the M-VAVE's knobs address (None: none); its sends are read now."""
        with self.lock:
            self.selected_bus = bus
            self.publisher.midi(selected_bus=bus)
            if bus is not None:
                self.request_sends(bus)
            self._refresh_derived(set(), source)
            return bus

    def request_sends(self, bus: str) -> int:
        """Ask the mixer for the visible and mapped inputs' sends to `bus` (on demand)."""
        with self.lock:
            if self._link is None:
                return 0
            addresses = send_query_addresses(self.settings(), bus)
            self._link.query(addresses)
        self._wake_mixer()
        return len(addresses)

    def macro_states(self) -> dict[str, bool]:
        """Every macro's active state as last worked out."""
        with self.lock:
            return dict(self._macro_active)

    def settings_changed(self) -> None:
        """Re-derive macro states and read what new mappings point at."""
        with self.lock:
            if self._link is not None and self._online:
                self._link.query(
                    interesting_addresses(self.settings(), self.links, self.selected_bus)
                )
            self._refresh_derived(set(), "settings")
        self._wake_mixer()

    # --- internals ------------------------------------------------------------------

    def _require_link(self) -> Xr18Link:
        if self._link is None or not self._online:
            raise CommandError(
                "the XR18 is offline: check it is on, its network cable, and the mixer "
                "address in the audio setup"
            )
        return self._link

    def _run(self, plan: list[PlannedChange], source: str) -> dict[str, Any]:
        """Apply planned changes in order; returns the levels written by point."""
        with self.lock:
            link = self._require_link()
            touched: set[str] = set()
            levels: dict[str, float] = {}
            for change in plan:
                touched.add(change.channel)
                if change.kind == "level" and isinstance(change.value, float | int):
                    self._write_level(link, change.channel, change.bus, float(change.value), source)
                    levels[change.point] = float(change.value)
                    if (
                        change.pauses_link
                        and self.links.state(change.channel, change.bus) == "linked"
                    ):
                        self.links.set(change.channel, change.bus, "paused")
                        self._logger.info("link %s paused by %s", change.point, source)
                elif change.kind == "mute" and isinstance(change.value, bool):
                    link.write(mute_address(change.channel), 0 if change.value else 1)
                    self.cache.mutes[change.channel] = change.value
                    self.publisher.last_source(f"{change.channel}:mute", source)
                elif change.kind == "store" and isinstance(change.value, float):
                    self.stored.store(change.point, change.value)
                elif change.kind == "forget":
                    self.stored.forget(change.point)
                elif change.kind == "snapshot" and isinstance(change.value, int):
                    link.write(SNAPSHOT_LOAD, change.value)
                    self._logger.warning("snapshot %s loaded by %s", change.value, source)
                    link.query(
                        interesting_addresses(self.settings(), self.links, self.selected_bus)
                    )
            touched.discard("")
            self.publisher.publish("stored_levels", self.stored.all())
            self.publisher.publish("links", self.links.matrix())
            self._after_writes(touched, source)
            return {"levels": levels}

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
