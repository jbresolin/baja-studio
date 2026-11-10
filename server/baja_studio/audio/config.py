"""The audio module's settings: mixer address, labels, M-VAVE mapping, macros, scenes.

The M-VAVE's part is `audio/midi_config.py`; the strict base model and the
channel id types are in `audio/config_base.py`.

Everything an operator may need to change at the event, edited on the audio
setup page with explicit Save (WP0 versioning: a stale save is a conflict).
Live routing that changes during the show (link states, stored levels) is
not here but in the database (`audio/stores.py`), so a link paused by a knob
never conflicts with somebody's unsaved edit on the setup page (ADR 0006).

Ids used throughout: channels as in `adapters/xr18_addresses.py` ("1"…"16",
"aux", "lr", "bus1"…"bus6"); send points as (channel, "main" | "busN").
"""

import re
from typing import Annotated, Literal

from pydantic import Field, field_validator, model_validator

from baja_studio.adapters.xr18_addresses import (
    AUX_BUSES,
    CHANNEL_IDS,
    SEND_POINT_BUSES,
    SEND_SOURCES,
    XR18_PORT,
)
from baja_studio.audio import defaults
from baja_studio.audio.config_base import (
    AuxBusId,
    ChannelId,
    SendSourceId,
    StrictModel,
    check_channel,
)
from baja_studio.audio.midi_config import MidiSettings
from baja_studio.audio.seat_config import SeatAudio, check_seats, default_seats
from baja_studio.core.settings import ModuleSettings

COLOUR_PATTERN = r"^#[0-9a-fA-F]{6}$"
# PROD 1-4 in the 2026 patch (`audio/defaults.py`).
DEFAULT_PRODUCTION_CHANNELS = ("5", "6", "7", "8")
# Macro ids appear in the D200's URLs (/action/macro/<id>), so they stay URL-safe.
MACRO_ID_PATTERN = r"^[A-Za-z0-9_-]{1,64}$"


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
    bus: AuxBusId
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


class SetLinkStep(StrictModel):
    """Link, pause or unlink an input's send to an aux bus (as the link matrix does).

    Linking brings the send to its fader plus the pair's offset at once,
    unless a macro closed the send (it has a stored level).
    """

    op: Literal["set_link"] = "set_link"
    channel: SendSourceId
    bus: AuxBusId
    state: Literal["linked", "paused", "unlinked"]


class SetLrAssignStep(StrictModel):
    """Assign an input to the main mix (LR) or take it off, without touching its fader."""

    op: Literal["set_lr_assign"] = "set_lr_assign"
    channel: SendSourceId
    on: bool


MacroStep = Annotated[
    SetFaderStep
    | SetMuteStep
    | SetAuxStep
    | StoreLevelsStep
    | RestoreLevelsStep
    | RecallSnapshotStep
    | SetLinkStep
    | SetLrAssignStep,
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
    talkback_bus: AuxBusId = Field(
        default="bus6",
        description="The camera operator's bus: a macro opening a send to it is talkback.",
    )
    production_bus: AuxBusId = Field(
        default="bus5",
        description="The production loop: a host heard here but not on LR talks to production.",
    )
    production_channels: list[SendSourceId] = Field(
        default_factory=lambda: list(DEFAULT_PRODUCTION_CHANNELS),
        description="The production microphones (for the hosts' 'production → you').",
    )
    seats: list[SeatAudio] = Field(default_factory=default_seats)
    hosts_can_adjust_mix: bool = Field(
        default=True, description="Off: the hosts screen shows the personal mix, read-only."
    )
    personal_mix_step_db: float = Field(
        default=1.0, gt=0.0, le=6.0, description="How much one tap of + or - moves a source."
    )

    @field_validator("channels")
    @classmethod
    def _every_channel(cls, channels: dict[str, ChannelConfig]) -> dict[str, ChannelConfig]:
        for channel in channels:
            check_channel(channel)
        # Strips missing from a saved document get defaults, in mixer order.
        return {channel: channels.get(channel, ChannelConfig()) for channel in CHANNEL_IDS}

    @field_validator("production_channels")
    @classmethod
    def _each_production_channel_once(cls, channels: list[str]) -> list[str]:
        return sorted(set(channels), key=channels.index)

    @field_validator("seats")
    @classmethod
    def _seats_in_order(cls, seats: list[SeatAudio]) -> list[SeatAudio]:
        return check_seats(seats)

    @model_validator(mode="after")
    def _seats_off_the_production_bus(self) -> "AudioSettings":
        if self.seat_on_bus(self.production_bus) is not None:
            raise ValueError(
                f"{self.production_bus} is the production bus; it cannot be a seat's in-ears"
            )
        return self

    def seat(self, seat_id: int) -> SeatAudio | None:
        """The audio side of seat `seat_id`, if there is one."""
        return next((seat for seat in self.seats if seat.seat_id == seat_id), None)

    def seat_on_bus(self, bus: str) -> SeatAudio | None:
        """The seat whose in-ears `bus` feeds, if any."""
        return next((seat for seat in self.seats if seat.iem_bus == bus), None)

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
