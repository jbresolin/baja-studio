"""Shared building blocks of the audio settings models: strict models and channel ids.

Used by `audio/config.py` (the whole settings), `audio/midi_config.py`
(the M-VAVE) and `audio/seat_config.py` (the hosts' seats).
"""

from typing import Annotated, Literal

from pydantic import AfterValidator, BaseModel, ConfigDict

from baja_studio.adapters.xr18_addresses import CHANNEL_IDS, SEND_SOURCES

AuxBusId = Literal["bus1", "bus2", "bus3", "bus4", "bus5", "bus6"]


class StrictModel(BaseModel):
    """Unknown keys are errors, so a typo in a saved document is caught."""

    model_config = ConfigDict(extra="forbid")


def check_channel(value: str) -> str:
    """`value` if it is a channel id ("1"…"16", "aux", "lr", "bus1"…"bus6"), else ValueError."""
    if value not in CHANNEL_IDS:
        raise ValueError(f"unknown channel {value!r}; use 1-16, aux, lr or bus1-bus6")
    return value


def check_send_source(value: str) -> str:
    """`value` if it is an input with sends ("1"…"16", "aux"), else ValueError."""
    if value not in SEND_SOURCES:
        raise ValueError(f"channel {value!r} has no sends; use 1-16 or aux")
    return value


ChannelId = Annotated[str, AfterValidator(check_channel)]
SendSourceId = Annotated[str, AfterValidator(check_send_source)]
