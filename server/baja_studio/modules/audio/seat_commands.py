"""The hosts screen's personal-mix commands, checked against the seat on the server (WP9b §3).

    set_seat_offset    {seat_id, channel, delta_db | value_db}
                       → {seat_id, channel, bus, linked, offset_db, level, db}
    reset_seat_offsets {seat_id} → {seat_id, bus, reset: [channels]}

A hosts screen is not trusted to say which bus it changes: the bus is
always the seat's own in-ear bus (`AudioSettings.seats`), a `bus` argument
naming another is refused, the source must be one the seat's overlay
offers (`hosts_view.mix_sources`), and the value must be within range.
With `hosts_can_adjust_mix` off, both are refused (the overlay is then
view-only).

A linked source's offset moves (-30…+10 dB) and the link stays; a source
that is not linked to the bus (shown as "independent") has its send set
directly (-90…+10 dB), as on the audio page.
"""

from typing import Any

from baja_studio.adapters.xr18_addresses import normalise_point_bus
from baja_studio.adapters.xr18_levels import MAX_DB, MIN_DB, db_to_level, format_db
from baja_studio.audio.config import AudioSettings
from baja_studio.audio.seat_config import MAX_OFFSET_DB, MIN_OFFSET_DB, SEAT_IDS, SeatAudio
from baja_studio.core.module import CommandError
from baja_studio.modules.audio.controller import AudioController
from baja_studio.modules.audio.hosts_view import mix_sources

SEAT_COMMANDS = ("set_seat_offset", "reset_seat_offsets")
# One + or - on the overlay is a few dB; anything bigger is not a nudge.
MAX_DELTA_DB = 10.0


def run_seat_command(
    controller: AudioController, command: str, args: dict[str, Any], source: str
) -> Any:
    """Carry out one of `SEAT_COMMANDS`; refusals are worded for the hosts and production."""
    settings = controller.settings()
    if not settings.hosts_can_adjust_mix:
        raise CommandError("production has made the personal mixes view-only (Audio setup → Seats)")
    seat = _seat_argument(settings, args)
    if command == "reset_seat_offsets":
        reset = controller.reset_offsets(seat.iem_bus, source)
        return {"seat_id": seat.seat_id, "bus": seat.iem_bus, "reset": reset}
    channel = _source_argument(controller, settings, seat, args)
    delta_db, value_db = _amount_arguments(args)
    bus = seat.iem_bus
    linked = controller.follows_with_offset(channel, bus)
    if linked:
        if delta_db is not None:
            level = controller.nudge_offset(channel, bus, delta_db, source, from_overlay=True)
        else:
            assert value_db is not None
            _within(value_db, MIN_OFFSET_DB, MAX_OFFSET_DB, "an offset")
            level = controller.set_offset(channel, bus, value_db, source, from_overlay=True)
    elif delta_db is not None:
        level = controller.nudge(channel, bus, delta_db, source)
    else:
        assert value_db is not None
        _within(value_db, MIN_DB, MAX_DB, "a send")
        level = controller.set_level(channel, bus, db_to_level(value_db), source)
    return {
        "seat_id": seat.seat_id,
        "channel": channel,
        "bus": bus,
        "linked": linked,
        "offset_db": controller.offsets.get(channel, bus) if linked else None,
        "level": level,
        "db": format_db(level),
    }


def _seat_argument(settings: AudioSettings, args: dict[str, Any]) -> SeatAudio:
    seat_id = args.get("seat_id")
    if isinstance(seat_id, bool) or seat_id not in SEAT_IDS:
        raise CommandError("'seat_id' must be a seat: 1, 2, 3 or 4")
    seat = settings.seat(seat_id)
    assert seat is not None  # the settings always hold seats 1-4
    if "bus" in args:
        try:
            asked = normalise_point_bus(str(args["bus"]))
        except ValueError:
            asked = None
        if asked != seat.iem_bus:
            raise CommandError(
                f"a seat changes only its own in-ears ({settings.label(seat.iem_bus)})"
            )
    return seat


def _source_argument(
    controller: AudioController, settings: AudioSettings, seat: SeatAudio, args: dict[str, Any]
) -> str:
    channel = str(args.get("channel", ""))
    if channel not in mix_sources(settings, controller.links, seat.iem_bus):
        raise CommandError(
            f"{channel or 'that'} is not in {settings.label(seat.iem_bus)}'s personal mix; "
            "production adds sources on the audio setup page (link matrix)"
        )
    return channel


def _amount_arguments(args: dict[str, Any]) -> tuple[float | None, float | None]:
    given = [name for name in ("delta_db", "value_db") if args.get(name) is not None]
    if len(given) != 1:
        raise CommandError("give either delta_db or value_db")
    value = args[given[0]]
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise CommandError(f"'{given[0]}' must be a number of dB")
    if given[0] == "delta_db":
        _within(float(value), -MAX_DELTA_DB, MAX_DELTA_DB, "one step")
        return float(value), None
    return None, float(value)


def _within(value: float, low: float, high: float, what: str) -> None:
    if not low <= value <= high:
        raise CommandError(f"{what} must be between {low:g} and {high:g} dB, not {value:g}")
