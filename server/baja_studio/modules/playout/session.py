"""What playout keeps across a restart of the service: program, cue and toggles.

The clip on air and the cued clip are stored as (clip id, slot, playlist,
speed) in the session table; the slot used last, and the auto-play-next toggle,
too. On start the switcher is put back as it was; the OBS worker then
checks it against OBS on connect (`PlayoutController.on_connected`).
"""

from baja_studio.modules.playout.publisher import PlayoutPublisher
from baja_studio.playout.library import LibraryStore
from baja_studio.playout.parking import NORMAL_SPEED_PERCENT
from baja_studio.playout.queue_store import QueueStore
from baja_studio.playout.switcher import Cued, OnAir, ProgramSwitcher, SlotName


def restore_switcher(
    switcher: ProgramSwitcher,
    queue: QueueStore,
    library: LibraryStore,
    publisher: PlayoutPublisher,
) -> None:
    """Put back program, cue, last slot and auto-play next as they were saved."""
    saved_program = queue.session_value("program")
    if saved_program and (clip := library.get(saved_program["clip_id"])):
        switcher.program = OnAir(
            clip,
            slot_name(saved_program["slot"]),
            saved_program["started_at"],
            saved_program["aired_id"],
            saved_program.get("playlist_id"),
            saved_program.get("speed_percent", NORMAL_SPEED_PERCENT),
        )
    saved_cue = queue.session_value("cued")
    if saved_cue and (clip := library.get(saved_cue["clip_id"])):
        switcher.cued = Cued(
            clip,
            slot_name(saved_cue["slot"]),
            saved_cue.get("playlist_id"),
            saved_cue.get("speed_percent", NORMAL_SPEED_PERCENT),
        )
    switcher.last_slot = slot_name(queue.session_value("last_slot", "b"))
    publisher.auto_play_next = bool(queue.session_value("auto_play_next", False))


def save_switcher(switcher: ProgramSwitcher, queue: QueueStore) -> None:
    """Store program, cue and last slot (after every change to them)."""
    program, cued = switcher.program, switcher.cued
    queue.set_session_value(
        "program",
        {
            "clip_id": program.clip["id"],
            "slot": program.slot,
            "started_at": program.started_at,
            "aired_id": program.aired_id,
            "playlist_id": program.playlist_id,
            "speed_percent": program.speed_percent,
        }
        if program
        else None,
    )
    queue.set_session_value(
        "cued",
        {
            "clip_id": cued.clip["id"],
            "slot": cued.slot,
            "playlist_id": cued.playlist_id,
            "speed_percent": cued.speed_percent,
        }
        if cued
        else None,
    )
    queue.set_session_value("last_slot", switcher.last_slot)


def slot_name(value: object) -> SlotName:
    """A stored slot name back as a `SlotName` ("b" for anything unexpected)."""
    return "a" if value == "a" else "b"
