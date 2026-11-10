"""The audio side of the hosts' seats: whose microphone is which channel, whose in-ears which bus.

Part of the audio settings (WP9b §2). Audio owns the audio facts of a seat;
the hosts module owns its name and colour, and the hosts screen joins the
two by `seat_id` (1-4, the hosts module's seats).

- `channel`: the input of the seat's microphone; unset until someone maps
  it (the card then says "not mapped").
- `iem_bus`: the aux bus feeding the seat's in-ears: a copy of the main mix
  plus the seat's own offsets (`audio_offsets`, the personal-mix overlay).
"""

from pydantic import Field

from baja_studio.audio.config_base import AuxBusId, SendSourceId, StrictModel

SEAT_IDS = (1, 2, 3, 4)
# How far a seat may move a source in its own in-ears, relative to the main mix.
MIN_OFFSET_DB = -30.0
MAX_OFFSET_DB = 10.0


class SeatAudio(StrictModel):
    """One seat's microphone channel and in-ear bus."""

    seat_id: int = Field(ge=1, le=4)
    channel: SendSourceId | None = Field(
        default=None, description="The input of the seat's microphone; unset: not mapped."
    )
    iem_bus: AuxBusId = Field(description="The aux bus feeding this seat's in-ears.")


def default_seats() -> list[SeatAudio]:
    """Seats 1-4 on IEM buses 1-4 (the 2026 patch), microphones not mapped yet."""
    return [
        SeatAudio.model_validate({"seat_id": seat_id, "iem_bus": f"bus{seat_id}"})
        for seat_id in SEAT_IDS
    ]


def check_seats(seats: list[SeatAudio]) -> list[SeatAudio]:
    """Seats 1-4 once each, each on its own in-ear bus; returned in seat order."""
    if sorted(seat.seat_id for seat in seats) != list(SEAT_IDS):
        raise ValueError("there must be exactly the seats 1, 2, 3 and 4, each once")
    buses = [seat.iem_bus for seat in seats]
    shared = sorted({bus for bus in buses if buses.count(bus) > 1})
    if shared:
        raise ValueError(f"each seat needs its own in-ear bus; shared: {', '.join(shared)}")
    return sorted(seats, key=lambda seat: seat.seat_id)
