"""The M-VAVE SMC-Mixer as a control surface: its messages in, its LEDs out.

Incoming events (decoded by `adapters/mvave_map.py`) become controller
operations according to `settings.midi`:

- **Faders** set the main fader of the strip's channel. With `pickup`
  takeover (the default), a fader whose channel was changed elsewhere (page,
  D200, Mixing Station) does nothing until it reaches the mixer's level, so
  a host's mic cannot jump 20 dB because the physical fader was left low.
  `jump` is the 2026 behaviour.
- **Knobs** nudge the strip channel's send to the knob's bus or the selected
  bus (by `knob_step_db` per detent), or its pan.
- **Buttons** mute, close/open a send point, or run a macro.
- **Bus-select buttons** choose the knobs' bus (pressing it again clears it).

**LEDs** show state derived from the mixer: M lit when muted, a send
toggle lit when its route is closed (silent), a macro button lit when the
macro is active. They are re-sent after every change, after every release
(the device clears a button's LED itself on Note Off), when the port opens,
and every 10 s.

While **MIDI learn** is armed, the next control touched is captured
(published in `audio.midi.learn`) instead of acting.
"""

import logging
import threading
from collections.abc import Callable
from typing import Any

from baja_studio.adapters.mvave_map import (
    BUTTON_ROWS,
    STRIP_COUNT,
    BusSelectPressed,
    ButtonPressed,
    ButtonReleased,
    FaderMoved,
    KnobTurned,
    MvaveEvent,
    button_key,
    fader_message,
    led_message,
)
from baja_studio.adapters.xr18_addresses import MAIN_POINT
from baja_studio.audio.config import ButtonAction
from baja_studio.core.clock import Clock
from baja_studio.core.commandlog import device_source
from baja_studio.core.module import CommandError
from baja_studio.modules.audio.controller import AudioController

MVAVE_SOURCE = device_source("mvave")
PICKUP_WINDOW = 0.02
PAN_STEP = 0.02
FULL_REFRESH_SECONDS = 10.0

ActionLogger = Callable[[str, dict[str, Any], str], None]


class MvaveSurface:
    """Turns M-VAVE events into operations and mixer state into LEDs."""

    def __init__(
        self,
        controller: AudioController,
        clock: Clock,
        logger: logging.LoggerAdapter[logging.Logger],
        log_action: ActionLogger,
        wake_midi: Callable[[], None],
    ) -> None:
        """Listen to the controller's changes; MIDI goes out through `take_outgoing`."""
        self._controller = controller
        self._clock = clock
        self._logger = logger
        self._log_action = log_action
        self._wake_midi = wake_midi
        self._lock = threading.Lock()
        self._outgoing: list[list[int]] = []
        self._leds: dict[tuple[int, str], bool] = {}
        self._fader_positions: dict[int, float] = {}
        self._physical: dict[int, float] = {}
        # Strips whose fader has picked up its channel's level, and the level each last set.
        self._engaged: set[int] = set()
        self._applied: dict[int, float] = {}
        self._learn_armed_by: str | None = None
        self._last_full_refresh = clock.monotonic()
        controller.add_change_listener(self._on_mixer_changed)

    # --- events from the device (MIDI worker thread) ------------------------------

    def handle(self, event: MvaveEvent) -> None:
        """Act on one event from the device."""
        if self._capture_for_learn(event):
            return
        try:
            if isinstance(event, FaderMoved):
                self._fader(event.strip, event.level)
            elif isinstance(event, KnobTurned):
                self._knob(event.strip, event.steps)
            elif isinstance(event, ButtonPressed):
                self._button(event.strip, event.row)
            elif isinstance(event, ButtonReleased):
                self._forget_led(event.strip, event.row)
                self.refresh()
            elif isinstance(event, BusSelectPressed):
                selected = None if self._controller.selected_bus == event.bus else event.bus
                self._controller.select_bus(selected, MVAVE_SOURCE)
                self._log_action("select_bus", {"bus": selected}, "ok")
        except CommandError as refused:
            # The mixer is offline, say: the page shows that; one line in the log is enough.
            self._logger.warning("M-VAVE %s ignored: %s", event.control, refused)

    def arm_learn(self, source: str) -> None:
        """Capture the next control touched instead of acting on it."""
        with self._lock:
            self._learn_armed_by = source
        self._controller.publisher.midi(learn={"armed": True, "by": source, "captured": None})

    def cancel_learn(self) -> None:
        """Stop waiting for a control."""
        with self._lock:
            self._learn_armed_by = None
        self._controller.publisher.midi(learn={"armed": False, "by": None, "captured": None})

    def _capture_for_learn(self, event: MvaveEvent) -> bool:
        if isinstance(event, ButtonReleased):
            return False
        with self._lock:
            armed_by, self._learn_armed_by = self._learn_armed_by, None
        if armed_by is None:
            return False
        captured = {"control": event.control, "at": self._clock.now_utc().isoformat()}
        self._controller.publisher.midi(
            learn={"armed": False, "by": armed_by, "captured": captured}
        )
        return True

    def _strip_channel(self, strip: int) -> str | None:
        return self._controller.settings().midi.faders.get(str(strip))

    def _fader(self, strip: int, level: float) -> None:
        channel = self._strip_channel(strip)
        if channel is None:
            return
        midi = self._controller.settings().midi
        with self._controller.lock:
            previous = self._physical.get(strip)
            self._physical[strip] = level
            if midi.fader_takeover == "pickup" and strip not in self._engaged:
                current = self._controller.cache.faders.get(channel)
                crossed = (
                    previous is not None
                    and current is not None
                    and ((previous - current) * (level - current) <= 0)
                )
                if current is not None and not crossed and abs(level - current) > PICKUP_WINDOW:
                    self._controller.publisher.midi(pickup={str(strip): round(current, 3)})
                    return
                self._engaged.add(strip)
                self._controller.publisher.midi(pickup={})
            self._applied[strip] = self._controller.set_level(
                channel, MAIN_POINT, level, MVAVE_SOURCE
            )

    def _knob(self, strip: int, steps: int) -> None:
        channel = self._strip_channel(strip)
        midi = self._controller.settings().midi
        knob = midi.knobs.get(str(strip))
        if channel is None or knob is None or knob.target == "none":
            return
        if knob.target == "pan":
            current = self._controller.cache.pans.get(channel, 0.5)
            self._controller.set_pan(channel, current + steps * PAN_STEP, MVAVE_SOURCE)
            return
        bus = knob.bus or self._controller.selected_bus
        if bus is None:
            return  # no bus selected: the knobs have nothing to turn (as in 2026)
        self._controller.nudge(channel, bus, steps * midi.knob_step_db, MVAVE_SOURCE)

    def _button(self, strip: int, row: str) -> None:
        midi = self._controller.settings().midi
        key = button_key(strip, row)
        action = midi.buttons.get(key, ButtonAction())
        channel = action.channel or self._strip_channel(strip)
        if action.type == "mute" and channel is not None:
            muted = self._controller.set_mute(channel, None, MVAVE_SOURCE)
            self._log_action("set_mute", {"channel": channel, "button": key}, f"muted={muted}")
        elif action.type == "send_toggle" and channel is not None:
            bus = action.bus or midi.row_buses.get(row)
            if bus is not None:
                opened = self._controller.toggle_send(channel, bus, MVAVE_SOURCE)
                self._log_action(
                    "toggle_send",
                    {"point": f"{channel}:{bus}", "button": key},
                    "opened" if opened else "closed",
                )
        elif action.type == "macro" and action.macro:
            active = self._controller.run_macro(action.macro, MVAVE_SOURCE)
            self._log_action(
                "run_macro", {"macro": action.macro, "button": key}, f"active={active}"
            )

    # --- feedback to the device -----------------------------------------------------

    def _on_mixer_changed(self, channels: set[str], source: str) -> None:
        """Controller listener (under its lock): drop takeover, then refresh LEDs and faders."""
        if source != MVAVE_SOURCE:
            for strip in list(self._engaged):
                channel = self._strip_channel(strip)
                if channel not in channels:
                    continue
                # Only the fader itself moving elsewhere loses the pickup, not a send.
                level = self._controller.cache.faders.get(channel)
                applied = self._applied.get(strip)
                if level is None or applied is None or abs(level - applied) > PICKUP_WINDOW / 4:
                    self._engaged.discard(strip)
        self.refresh(feedback_faders=source != MVAVE_SOURCE)

    def port_opened(self) -> None:
        """The device (re)appeared: it knows nothing, so send everything."""
        with self._lock:
            self._leds.clear()
            self._fader_positions.clear()
            self._outgoing.clear()  # queued while it was away; superseded by the full refresh
        self.refresh(feedback_faders=True)

    def refresh(self, feedback_faders: bool = False) -> None:
        """Queue the LEDs (and fader positions) that differ from what the device shows."""
        messages = []
        with self._controller.lock, self._lock:
            now = self._clock.monotonic()
            if now - self._last_full_refresh >= FULL_REFRESH_SECONDS:
                self._last_full_refresh = now
                self._leds.clear()
            midi = self._controller.settings().midi
            for strip in range(1, STRIP_COUNT + 1):
                for row in BUTTON_ROWS:
                    lit = self._led_lit(strip, row)
                    if self._leds.get((strip, row)) != lit:
                        self._leds[(strip, row)] = lit
                        messages.append(led_message(strip, row, lit))
                channel = self._strip_channel(strip)
                level = None if channel is None else self._controller.cache.faders.get(channel)
                if (
                    midi.send_fader_feedback
                    and feedback_faders
                    and level is not None
                    and self._fader_positions.get(strip) != level
                ):
                    self._fader_positions[strip] = level
                    messages.append(fader_message(strip, level))
            self._outgoing += messages
        if messages:
            self._wake_midi()

    def pending_outgoing(self) -> int:
        """How many messages wait to be sent (tests wait for zero)."""
        with self._lock:
            return len(self._outgoing)

    def take_outgoing(self) -> list[list[int]]:
        """The MIDI messages waiting to go to the device (the MIDI worker sends them)."""
        with self._lock:
            messages, self._outgoing = self._outgoing, []
        return messages

    def _forget_led(self, strip: int, row: str) -> None:
        with self._lock:
            self._leds.pop((strip, row), None)

    def _led_lit(self, strip: int, row: str) -> bool:
        midi = self._controller.settings().midi
        action = midi.buttons.get(button_key(strip, row), ButtonAction())
        channel = action.channel or self._strip_channel(strip)
        cache = self._controller.cache
        if action.type == "mute" and channel is not None:
            return cache.mutes.get(channel) is True
        if action.type == "send_toggle" and channel is not None:
            bus = action.bus or midi.row_buses.get(row)
            return bus is not None and cache.level(channel, bus) == 0.0
        if action.type == "macro" and action.macro:
            return self._controller.macro_states().get(action.macro, False)
        return False
