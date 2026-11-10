"""Audio's part of the settings file: the link matrix as designed and the seats' offsets (WP9b §4).

The settings export (WP8) carries settings only; link states and offsets
live in audio's tables. The Settings → Export / import page adds this
section to the file it saves (`export_routing`) and, on import, sends it
back (`import_routing`, with `dry_run` first for the page's preview):

    export_routing                       → {links: {channel: [buses]}, offsets: {"5:bus1": dB}}
    import_routing {links, offsets, dry_run?}
                                         → {added, removed, offsets_changed, applied}

Pauses are not part of it (they happen during the show): exported pairs
are imported as linked, pairs not in the file are unlinked, and offsets
not in the file go back to 0 dB.
"""

from typing import Any

from baja_studio.adapters.xr18_addresses import AUX_BUSES, SEND_SOURCES
from baja_studio.audio.seat_config import MAX_OFFSET_DB, MIN_OFFSET_DB
from baja_studio.core.module import CommandError
from baja_studio.modules.audio.controller import AudioController

ROUTING_COMMANDS = ("export_routing", "import_routing")


def run_routing_command(
    controller: AudioController, command: str, args: dict[str, Any], source: str
) -> Any:
    """Export the routing, or check and (unless `dry_run`) apply an imported one."""
    if command == "export_routing":
        return controller.routing()
    links = check_links(args.get("links"))
    offsets = check_offsets(args.get("offsets"))
    current = controller.routing()
    pairs = _pairs(links)
    current_pairs = _pairs(current["links"])
    changed = sorted(
        point
        for point in set(offsets) | set(current["offsets"])
        if offsets.get(point, 0.0) != current["offsets"].get(point, 0.0)
    )
    applied = not args.get("dry_run")
    if applied:
        controller.replace_routing(links, offsets, source)
    return {
        "added": sorted(pairs - current_pairs),
        "removed": sorted(current_pairs - pairs),
        "offsets_changed": changed,
        "applied": applied,
    }


def check_links(value: Any) -> dict[str, list[str]]:
    """`{channel: [buses]}` from a file, or `CommandError` saying what is wrong."""
    if not isinstance(value, dict):
        raise CommandError("the audio routing's 'links' must be {channel: [buses]}")
    links: dict[str, list[str]] = {}
    for channel, buses in value.items():
        if channel not in SEND_SOURCES:
            raise CommandError(f"the audio routing links unknown input {channel!r}")
        if not isinstance(buses, list) or any(bus not in AUX_BUSES for bus in buses):
            raise CommandError(f"the audio routing links {channel} to something not bus1-bus6")
        links[channel] = sorted(set(buses))
    return links


def check_offsets(value: Any) -> dict[str, float]:
    """`{"channel:bus": dB}` from a file, or `CommandError` saying what is wrong."""
    if not isinstance(value, dict):
        raise CommandError("the audio routing's 'offsets' must be {\"channel:bus\": dB}")
    offsets: dict[str, float] = {}
    for point, offset_db in value.items():
        channel, _, bus = str(point).partition(":")
        if channel not in SEND_SOURCES or bus not in AUX_BUSES:
            raise CommandError(f"the audio routing has an offset for {point!r}, not an input:bus")
        if isinstance(offset_db, bool) or not isinstance(offset_db, int | float):
            raise CommandError(f"the offset for {point} is not a number")
        if not MIN_OFFSET_DB <= offset_db <= MAX_OFFSET_DB:
            raise CommandError(
                f"the offset for {point} ({offset_db} dB) is outside "
                f"{MIN_OFFSET_DB:g}…{MAX_OFFSET_DB:g} dB"
            )
        if offset_db != 0:
            offsets[point] = float(offset_db)
    return offsets


def _pairs(links: dict[str, list[str]]) -> set[str]:
    return {f"{channel}:{bus}" for channel, buses in links.items() for bus in buses}
