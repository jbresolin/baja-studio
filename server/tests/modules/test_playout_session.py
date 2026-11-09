"""Tests for what playout keeps across a restart (modules/playout/session.py).

Program and cue are saved as clip id, slot and playlist; restoring reads
the clips back from the library. The whole module restart is tested in
test_playout.py (`test_queue_cue_and_program_survive_a_module_restart`).
"""

from pathlib import Path
from typing import Any

from baja_studio.core.database import Database
from baja_studio.modules.playout.publisher import PlayoutPublisher
from baja_studio.modules.playout.session import restore_switcher, save_switcher, slot_name
from baja_studio.playout.config import ObsSettings
from baja_studio.playout.library import LibraryStore, ScannedFile
from baja_studio.playout.playlists import PlaylistStore
from baja_studio.playout.queue_store import QueueStore
from baja_studio.playout.schema import create_playout_tables
from baja_studio.playout.switcher import Cued, OnAir, ProgramSwitcher
from tests.conftest import FakeClock


def stores(tmp_path: Path, clock: FakeClock) -> tuple[Database, LibraryStore, QueueStore]:
    database = Database(tmp_path / "session.sqlite3")
    create_playout_tables(database)
    return database, LibraryStore(database, clock), QueueStore(database, clock)


def switcher(clock: FakeClock) -> ProgramSwitcher:
    # No OBS is needed: saving and restoring never makes a request.
    return ProgramSwitcher(None, lambda: ObsSettings(), clock)  # type: ignore[arg-type]


def test_program_cue_and_playlists_come_back_after_a_restart(
    tmp_path: Path, fake_clock: FakeClock
) -> None:
    database, library, queue = stores(tmp_path, fake_clock)
    library.apply_scan([ScannedFile(f"/clips/{n}.mp4", f"{n}.mp4", "Clips", 1, 1.0) for n in "ab"])
    first, second = (clip["id"] for clip in library.all())
    before = switcher(fake_clock)
    on_air: dict[str, Any] = library.get(first) or {}
    cued: dict[str, Any] = library.get(second) or {}
    before.program = OnAir(on_air, "a", "2026-11-09T12:00:00+00:00", aired_id=4, playlist_id=7)
    before.cued = Cued(cued, "b", playlist_id=None)
    before.last_slot = "a"
    save_switcher(before, queue)

    after = switcher(fake_clock)
    publisher = PlayoutPublisher(
        lambda path, value: None, library, queue, PlaylistStore(database, fake_clock)
    )
    restore_switcher(after, queue, library, publisher)
    database.close()

    assert after.program is not None and after.cued is not None
    assert (after.program.clip["id"], after.program.slot, after.program.playlist_id) == (
        first,
        "a",
        7,
    )
    assert after.program.aired_id == 4
    assert (after.cued.clip["id"], after.cued.slot) == (second, "b")
    assert after.last_slot == "a"


def test_an_unexpected_stored_slot_reads_as_b() -> None:
    assert slot_name("a") == "a"
    assert slot_name("b") == "b"
    assert slot_name(None) == "b"
