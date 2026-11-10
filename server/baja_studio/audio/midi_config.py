"""The M-VAVE SMC-Mixer's settings: which device, and what each fader, button and knob does.

Part of the audio settings (`audio/config.py`, `AudioSettings.midi`), kept
in its own file so each stays readable. Strips are "1"…"8"; buttons are
named "<strip>_<row>" ("3_solo"), as in `adapters/mvave_map.py`.
"""

from typing import Literal

from pydantic import Field, model_validator

from baja_studio.adapters.mvave_map import BUTTON_ROWS, STRIP_COUNT, button_key
from baja_studio.adapters.xr18_addresses import SEND_POINT_BUSES
from baja_studio.audio import defaults
from baja_studio.audio.config_base import AuxBusId, ChannelId, StrictModel, check_channel

STRIPS = tuple(str(strip) for strip in range(1, STRIP_COUNT + 1))


class ButtonAction(StrictModel):
    """What an M-VAVE strip button does.

    - `mute`: toggle the strip channel's mute.
    - `send_toggle`: close/open one send point of the strip channel (store
      and silence, or restore), on `bus` or else the row's bus.
    - `macro`: run the macro `macro`.
    """

    type: Literal["none", "mute", "send_toggle", "macro"] = "none"
    channel: ChannelId | None = Field(
        default=None, description="Instead of the strip's fader channel."
    )
    bus: str | None = None
    macro: str | None = None

    @model_validator(mode="after")
    def _check(self) -> "ButtonAction":
        if self.bus is not None and self.bus not in SEND_POINT_BUSES:
            raise ValueError(f"bus must be main or bus1-bus6, not {self.bus!r}")
        if self.type == "macro" and not self.macro:
            raise ValueError("a macro button needs the macro's id")
        return self


class KnobAction(StrictModel):
    """What a strip knob turns: its channel's send (to `bus`, else the selected bus) or pan."""

    target: Literal["none", "aux_send", "pan"] = "aux_send"
    bus: AuxBusId | None = None


class MidiSettings(StrictModel):
    """The M-VAVE SMC-Mixer: which device, and what each control does."""

    enabled: bool = True
    device_name_match: str = Field(
        default="SMC", min_length=1, description="Part of the port name."
    )
    faders: dict[str, str | None] = Field(
        default_factory=defaults.fader_channels, validate_default=True
    )
    buttons: dict[str, ButtonAction] = Field(
        default_factory=defaults.buttons, validate_default=True
    )
    knobs: dict[str, KnobAction] = Field(default_factory=defaults.knobs, validate_default=True)
    row_buses: dict[str, str] = Field(default_factory=defaults.row_buses, validate_default=True)
    knob_step_db: float = Field(default=1.5, gt=0.0, le=10.0)
    fader_takeover: Literal["pickup", "jump"] = Field(
        default="pickup",
        description="pickup: a fader takes over only once it reaches the mixer's level.",
    )
    send_fader_feedback: bool = Field(
        default=True, description="Send fader positions back to the device (pitch bend)."
    )

    @model_validator(mode="after")
    def _check_keys(self) -> "MidiSettings":
        for strip, channel in self.faders.items():
            if strip not in STRIPS:
                raise ValueError(f"no strip {strip!r} on the M-VAVE (1-8)")
            if channel is not None:
                check_channel(channel)
        valid_buttons = {button_key(int(strip), row) for strip in STRIPS for row in BUTTON_ROWS}
        for key in self.buttons:
            if key not in valid_buttons:
                raise ValueError(f"no button {key!r} on the M-VAVE (like 3_solo)")
        for strip in self.knobs:
            if strip not in STRIPS:
                raise ValueError(f"no knob {strip!r} on the M-VAVE (1-8)")
        for row, bus in self.row_buses.items():
            if row not in BUTTON_ROWS or bus not in SEND_POINT_BUSES:
                raise ValueError(f"row bus {row}={bus} is not a button row and main/bus1-bus6")
        return self
