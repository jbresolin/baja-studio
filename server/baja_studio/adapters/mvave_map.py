"""The M-VAVE SMC-Mixer's MIDI messages, as sniffed in 2026, as data.

Eight strips, each with a fader, a knob and four buttons:

- **Buttons** are notes on channel 1. A strip's base note is its index
  (strip 1 is 0x00): R (`rec`) = base + 0x00, S (`solo`) = base + 0x08,
  M (`mute`) = base + 0x10, □ (`sel`) = base + 0x18. A press is Note On with
  velocity > 0; a release is Note Off (or Note On with velocity 0).
- **Bus-select buttons** along the bottom: notes 0x2E, 0x2F, 0x60, 0x61,
  0x62, 0x63 select aux buses 1-6.
- **Knobs** are CC 0x10-0x17 (strip 1-8), *relative*: 0x01 is one step up,
  0x41 one step down (more generally 0x01-0x3F up by that many, 0x41-0x7F
  down by value - 0x40).
- **Faders** are pitch bend, MIDI channel 1-8 for strips 1-8, 0..16383.

**LEDs:** sending Note On (velocity 127) for a button's note lights it,
velocity 0 turns it off. The device *clears a button's LED itself on Note
Off*, i.e. when the button is released, so after every release the studio
sends the LED again; and bus selection is shown on the page, not on the
hardware.
"""

from dataclasses import dataclass

STRIP_COUNT = 8
BUTTON_ROWS = ("rec", "solo", "mute", "sel")
ROW_OFFSETS = {"rec": 0x00, "solo": 0x08, "mute": 0x10, "sel": 0x18}
BUS_SELECT_NOTES = {
    0x2E: "bus1",
    0x2F: "bus2",
    0x60: "bus3",
    0x61: "bus4",
    0x62: "bus5",
    0x63: "bus6",
}
KNOB_FIRST_CC = 0x10
PITCH_BEND_MAX = 16383

NOTE_ON = 0x90
NOTE_OFF = 0x80
CONTROL_CHANGE = 0xB0
PITCH_BEND = 0xE0


@dataclass(frozen=True)
class FaderMoved:
    """A strip's fader moved to `level` (0..1)."""

    strip: int
    level: float

    @property
    def control(self) -> str:
        """The control's name in mappings and MIDI learn: "fader:3"."""
        return f"fader:{self.strip}"


@dataclass(frozen=True)
class KnobTurned:
    """A strip's knob turned by `steps` (negative is down)."""

    strip: int
    steps: int

    @property
    def control(self) -> str:
        """ "knob:3"."""
        return f"knob:{self.strip}"


@dataclass(frozen=True)
class ButtonPressed:
    """A strip button went down. `row` is one of `BUTTON_ROWS`."""

    strip: int
    row: str

    @property
    def control(self) -> str:
        """ "button:3_solo"; the part after the colon is the key in `midi.buttons`."""
        return f"button:{button_key(self.strip, self.row)}"


@dataclass(frozen=True)
class ButtonReleased:
    """A strip button came up (the device has just cleared its LED)."""

    strip: int
    row: str

    @property
    def control(self) -> str:
        """Same name as the press."""
        return f"button:{button_key(self.strip, self.row)}"


@dataclass(frozen=True)
class BusSelectPressed:
    """One of the bottom bus-select buttons went down."""

    bus: str

    @property
    def control(self) -> str:
        """ "bus:bus2"."""
        return f"bus:{self.bus}"


@dataclass(frozen=True)
class UnknownMessage:
    """Anything else the device sent (kept for MIDI learn and the log)."""

    raw: tuple[int, ...]

    @property
    def control(self) -> str:
        """ "raw:90 2a 7f"."""
        return "raw:" + " ".join(f"{byte:02x}" for byte in self.raw)


MvaveEvent = (
    FaderMoved | KnobTurned | ButtonPressed | ButtonReleased | BusSelectPressed | UnknownMessage
)


def button_key(strip: int, row: str) -> str:
    """The key of a button in the settings: "3_solo"."""
    return f"{strip}_{row}"


def button_note(strip: int, row: str) -> int:
    """The note a strip button sends (and that lights its LED)."""
    return (strip - 1) + ROW_OFFSETS[row]


_NOTE_TO_BUTTON = {
    button_note(strip, row): (strip, row)
    for strip in range(1, STRIP_COUNT + 1)
    for row in BUTTON_ROWS
}


def decode(raw: list[int] | tuple[int, ...]) -> MvaveEvent:
    """What one incoming MIDI message means on the M-VAVE."""
    message = tuple(raw)
    if len(message) != 3:
        return UnknownMessage(message)
    status, data1, data2 = message
    kind, channel = status & 0xF0, status & 0x0F
    if kind == PITCH_BEND and channel < STRIP_COUNT:
        return FaderMoved(channel + 1, ((data2 << 7) | data1) / PITCH_BEND_MAX)
    if kind == CONTROL_CHANGE and KNOB_FIRST_CC <= data1 < KNOB_FIRST_CC + STRIP_COUNT:
        steps = data2 if data2 < 0x40 else -(data2 - 0x40)
        return KnobTurned(data1 - KNOB_FIRST_CC + 1, steps)
    is_press = kind == NOTE_ON and data2 > 0
    is_release = kind == NOTE_OFF or (kind == NOTE_ON and data2 == 0)
    if is_press and data1 in BUS_SELECT_NOTES:
        return BusSelectPressed(BUS_SELECT_NOTES[data1])
    if (is_press or is_release) and data1 in _NOTE_TO_BUTTON:
        strip, row = _NOTE_TO_BUTTON[data1]
        return ButtonPressed(strip, row) if is_press else ButtonReleased(strip, row)
    return UnknownMessage(message)


def led_message(strip: int, row: str, lit: bool) -> list[int]:
    """Note On that lights (velocity 127) or clears (0) a button's LED."""
    return [NOTE_ON, button_note(strip, row), 127 if lit else 0]


def fader_message(strip: int, level: float) -> list[int]:
    """Pitch bend that tells the device where a strip's fader is (0..1)."""
    value = round(min(1.0, max(0.0, level)) * PITCH_BEND_MAX)
    return [PITCH_BEND | (strip - 1), value & 0x7F, (value >> 7) & 0x7F]
