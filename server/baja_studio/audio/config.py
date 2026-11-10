"""The audio module's settings: mixer address, labels, M-VAVE mapping, macros, scenes.

Everything an operator may need to change at the event, edited on the audio
setup page with explicit Save (WP0 versioning: a stale save is a conflict).
Live routing that changes during the show (link states, stored levels) is
not here but in the database (`audio/schema.py`), so a link paused by a knob
never conflicts with somebody's unsaved edit on the setup page (ADR 0006).

Ids used throughout: channels as in `adapters/xr18_addresses.py` ("1"…"16",
"aux", "lr", "bus1"…"bus6"); send points as (channel, "main" | "busN").
"""

import re
from typing import Annotated, Literal

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, field_validator, model_validator

from baja_studio.adapters.mvave_map import BUTTON_ROWS, STRIP_COUNT, button_key
from baja_studio.adapters.xr18_addresses import (
    AUX_BUSES,
    CHANNEL_IDS,
    SEND_POINT_BUSES,
    SEND_SOURCES,
    XR18_PORT,
)
from baja_studio.audio import defaults
from baja_studio.core.settings import ModuleSettings

COLOUR_PATTERN = r"^#[0-9a-fA-F]{6}$"
# Macro ids appear in the D200's URLs (/action/macro/<id>), so they stay URL-safe.
MACRO_ID_PATTERN = r"^[A-Za-z0-9_-]{1,64}$"
STRIPS = tuple(str(strip) for strip in range(1, STRIP_COUNT + 1))


class StrictModel(BaseModel):
    """Unknown keys are errors, so a typo in a saved document is caught."""

    model_config = ConfigDict(extra="forbid")


def _channel(value: str) -> str:
    if value not in CHANNEL_IDS:
        raise ValueError(f"unknown channel {value!r}; use 1-16, aux, lr or bus1-bus6")
    return value


def _send_source(value: str) -> str:
    if value not in SEND_SOURCES:
        raise ValueError(f"channel {value!r} has no sends; use 1-16 or aux")
    return value


ChannelId = Annotated[str, AfterValidator(_channel)]
SendSourceId = Annotated[str, AfterValidator(_send_source)]


class MixerSettings(StrictModel):
    """Where the XR18 is."""

    host: str = Field(default="", description="The XR18's IP address; empty: not configured.")
    port: int = Field(default=XR18_PORT, ge=1, le=65535)
    enabled: bool = Field(default=True, description="Off: do not talk to the mixer at all.")


class ChannelConfig(StrictModel):
    """How a strip is shown on the pages."""

    label: str = Field(default="", max_length=24)
    color: str = Field(default="#5b7fff", pattern=COLOUR_PATTERN)
    visible: bool = Field(default=False, description="Shown on the audio page.")


class SendPoint(StrictModel):
    """A level on the mixer: a channel's own fader ("main") or its send to a bus."""

    channel: ChannelId
    bus: str = "main"

    @model_validator(mode="after")
    def _check_point(self) -> "SendPoint":
        if self.bus not in SEND_POINT_BUSES:
            raise ValueError(f"bus must be main or bus1-bus6, not {self.bus!r}")
        if self.bus != "main" and self.channel not in SEND_SOURCES:
            raise ValueError(f"channel {self.channel} has no send to {self.bus}")
        return self

    @property
    def key(self) -> str:
        """The stored-levels key, "5:bus1" (the 2026 format, with "main" for "lr")."""
        return f"{self.channel}:{self.bus}"


class SetFaderStep(StrictModel):
    """Move a strip's fader (inputs, aux, buses or the main mix) to `level` (0..1)."""

    op: Literal["set_fader"] = "set_fader"
    channel: ChannelId
    level: float = Field(ge=0.0, le=1.0)


class SetMuteStep(StrictModel):
    """Mute or unmute a strip."""

    op: Literal["set_mute"] = "set_mute"
    channel: ChannelId
    muted: bool


class SetAuxStep(StrictModel):
    """Set an input's send to an aux bus to `level` (0..1)."""

    op: Literal["set_aux"] = "set_aux"
    channel: SendSourceId
    bus: Literal["bus1", "bus2", "bus3", "bus4", "bus5", "bus6"]
    level: float = Field(ge=0.0, le=1.0)


class StoreLevelsStep(StrictModel):
    """Remember the points' levels and (by default) silence them: "close" a route."""

    op: Literal["store_levels"] = "store_levels"
    points: list[SendPoint] = Field(min_length=1)
    silence: bool = True


class RestoreLevelsStep(StrictModel):
    """Bring the points back to their remembered level: "open" a route."""

    op: Literal["restore_levels"] = "restore_levels"
    points: list[SendPoint] = Field(min_length=1)


class RecallSnapshotStep(StrictModel):
    """Load one of the XR18's 64 snapshots (this replaces the whole mix)."""

    op: Literal["recall_scene"] = "recall_scene"
    snapshot: int = Field(ge=1, le=64)


MacroStep = Annotated[
    SetFaderStep
    | SetMuteStep
    | SetAuxStep
    | StoreLevelsStep
    | RestoreLevelsStep
    | RecallSnapshotStep,
    Field(discriminator="op"),
]


class MacroConfig(StrictModel):
    """A named list of steps, run from a button on the page, the M-VAVE or a D200.

    A `toggle` macro runs `on_steps` when it is not active and `off_steps`
    when it is; whether it is active is worked out from the mixer
    (`audio/macros.py`), never remembered. A `trigger` macro always runs
    `on_steps`.
    """

    id: str = Field(pattern=MACRO_ID_PATTERN)
    label: str = Field(default="", max_length=40)
    color: str = Field(default="#3498db", pattern=COLOUR_PATTERN)
    mode: Literal["toggle", "trigger"] = "toggle"
    on_steps: list[MacroStep] = Field(default_factory=list)
    off_steps: list[MacroStep] = Field(default_factory=list)


class SceneConfig(StrictModel):
    """An event phase (pre-show, race, interviews): a mixer snapshot and/or a macro."""

    index: int = Field(ge=1, le=64, description="Its number in /action/scene/<index>.")
    label: str = Field(default="", max_length=40)
    snapshot: int | None = Field(default=None, ge=1, le=64)
    macro: str | None = None

    @model_validator(mode="after")
    def _does_something(self) -> "SceneConfig":
        if self.snapshot is None and not self.macro:
            raise ValueError("a scene needs a snapshot, a macro, or both")
        return self


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
    bus: Literal["bus1", "bus2", "bus3", "bus4", "bus5", "bus6"] | None = None


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
                _channel(channel)
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


class D200Settings(StrictModel):
    """The Ulanzi D200 decks' HTTP actions (`/action/...` at the service root)."""

    enabled: bool = True
    state_poll_hint_s: float = Field(
        default=1.0, description="Documentation only: the D200 polls on its own schedule."
    )


class AudioSettings(ModuleSettings):
    """Everything the audio module reads from settings."""

    mixer: MixerSettings = Field(default_factory=MixerSettings)
    channels: dict[str, ChannelConfig] = Field(
        default_factory=defaults.channels, validate_default=True
    )
    midi: MidiSettings = Field(default_factory=MidiSettings)
    macros: list[MacroConfig] = Field(default_factory=defaults.macros, validate_default=True)
    scenes: list[SceneConfig] = Field(default_factory=list)
    d200: D200Settings = Field(default_factory=D200Settings)
    talkback_bus: Literal["bus1", "bus2", "bus3", "bus4", "bus5", "bus6"] = Field(
        default="bus6",
        description="The camera operator's bus: a macro opening a send to it is talkback.",
    )

    @field_validator("channels")
    @classmethod
    def _every_channel(cls, channels: dict[str, ChannelConfig]) -> dict[str, ChannelConfig]:
        for channel in channels:
            _channel(channel)
        # Strips missing from a saved document get defaults, in mixer order.
        return {channel: channels.get(channel, ChannelConfig()) for channel in CHANNEL_IDS}

    @model_validator(mode="after")
    def _check_references(self) -> "AudioSettings":
        macro_ids = [macro.id for macro in self.macros]
        duplicates = {macro_id for macro_id in macro_ids if macro_ids.count(macro_id) > 1}
        if duplicates:
            raise ValueError(f"macro ids must be unique; repeated: {', '.join(sorted(duplicates))}")
        scene_indexes = [scene.index for scene in self.scenes]
        if len(set(scene_indexes)) != len(scene_indexes):
            raise ValueError("scene numbers must be unique")
        for scene in self.scenes:
            if scene.macro and scene.macro not in macro_ids:
                raise ValueError(f"scene {scene.index} runs unknown macro {scene.macro!r}")
        for key, action in self.midi.buttons.items():
            if action.type == "macro" and action.macro not in macro_ids:
                raise ValueError(f"button {key} runs unknown macro {action.macro!r}")
        return self

    def macro(self, macro_id: str) -> MacroConfig | None:
        """The macro with this id, if any."""
        return next((macro for macro in self.macros if macro.id == macro_id), None)

    def label(self, channel: str) -> str:
        """The channel's label, or a plain name like "Ch 7" / "Bus 2"."""
        configured = self.channels.get(channel)
        if configured and configured.label:
            return configured.label
        return plain_name(channel)


def plain_name(channel: str) -> str:
    """ "Ch 7", "Aux", "Main", "Bus 2"."""
    if channel in AUX_BUSES:
        return f"Bus {channel.removeprefix('bus')}"
    return {"aux": "Aux", "lr": "Main"}.get(channel, f"Ch {channel}")


def is_valid_macro_id(text: str) -> bool:
    """Whether `text` could be a macro id (used by routes before looking it up)."""
    return re.fullmatch(MACRO_ID_PATTERN, text) is not None
