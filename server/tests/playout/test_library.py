"""Tests for the clip library (playout/library.py).

The library is a table of clips. The scanner tells it which files exist
(`apply_scan`), the orchestrator sync tells it what they are
(`merge_manifest`), and operators edit labels, notes and played state.
"""

from pathlib import Path, PurePosixPath
from typing import Any

import pytest

from baja_studio.core.database import Database
from baja_studio.playout.library import (
    SOURCE_MANUAL,
    SOURCE_ORCHESTRATOR,
    LibraryError,
    LibraryStore,
    ManifestClip,
    ScannedFile,
)
from tests.conftest import FakeClock

ROOT = str(Path("/clips"))


def scanned(relative_path: str, size: int = 100, mtime: float = 1.0) -> ScannedFile:
    """A file under ROOT as the scanner would report it."""
    return ScannedFile(
        path=str(Path(ROOT) / PurePosixPath(relative_path)),
        relative_path=relative_path,
        folder_label="Delivery",
        size=size,
        mtime=mtime,
    )


def manifest_clip(
    relative_path: str = "enduro/42/3.mp4", team: str | None = "Cerrado"
) -> ManifestClip:
    """One manifest entry for car 42, pass 3 of Enduro."""
    return ManifestClip(
        job_id="job0001",
        prova="Enduro",
        car_number=42,
        team_name=team,
        passagem=3,
        relative_path=relative_path,
        sha256="a" * 64,
        size_bytes=100,
        delivered_at="2026-11-01T12:00:00+00:00",
    )


@pytest.fixture
def library(database: Database, fake_clock: FakeClock) -> LibraryStore:
    """An empty library on the fake clock."""
    return LibraryStore(database, fake_clock)


def only_clip(library: LibraryStore) -> dict[str, Any]:
    clips = library.all()
    assert len(clips) == 1
    return clips[0]


def test_a_new_file_becomes_a_manual_clip_named_after_the_file(library: LibraryStore) -> None:
    # The scanner found one file nobody has told us anything about.
    changed = library.apply_scan([scanned("sponsors/Patrocinador.mp4")])

    clip = only_clip(library)
    # It is a playable manual clip whose title is its file name without extension.
    assert changed == [clip["id"]]
    assert clip["source"] == SOURCE_MANUAL
    assert clip["title"] == "Patrocinador"
    assert clip["missing"] is False
    assert clip["queueable"] is True
    assert clip["duration_s"] is None


def test_scanning_the_same_files_again_changes_nothing(library: LibraryStore) -> None:
    library.apply_scan([scanned("a.mp4")])

    # The scan runs every 5 s; an unchanged folder must not republish anything.
    assert library.apply_scan([scanned("a.mp4")]) == []


def test_a_file_that_disappears_is_marked_missing_and_comes_back_when_it_returns(
    library: LibraryStore,
) -> None:
    library.apply_scan([scanned("a.mp4")])
    clip_id = only_clip(library)["id"]

    # The file is gone from the folder: the clip stays (notes, played count)
    # but cannot be queued.
    assert library.apply_scan([]) == [clip_id]
    assert only_clip(library)["missing"] is True
    assert only_clip(library)["queueable"] is False

    # It is back (the drive was replugged): same clip, playable again.
    assert library.apply_scan([scanned("a.mp4")]) == [clip_id]
    assert only_clip(library)["missing"] is False


def test_a_changed_file_loses_its_duration_and_thumbnail_so_it_is_probed_again(
    library: LibraryStore,
) -> None:
    # A clip was probed, then reprocessed by the pipeline: same path, new bytes.
    library.apply_scan([scanned("a.mp4", size=100, mtime=1.0)])
    clip_id = only_clip(library)["id"]
    library.set_probe_result(clip_id, 12.0, "/cache/1-1.jpg", None)
    assert library.needing_probe() == []

    library.apply_scan([scanned("a.mp4", size=200, mtime=2.0)])

    # The old duration and thumbnail belong to the old file, so they are cleared
    # and the clip is waiting for the prober again.
    clip = only_clip(library)
    assert clip["duration_s"] is None
    assert clip["has_thumbnail"] is False
    assert [pending["id"] for pending in library.needing_probe()] == [clip_id]


def test_a_probe_failure_leaves_the_clip_present_without_duration_and_not_retried(
    library: LibraryStore,
) -> None:
    library.apply_scan([scanned("broken.mp4")])
    clip_id = only_clip(library)["id"]

    # ffprobe could not read the file: the error is recorded instead of a duration.
    library.set_probe_result(clip_id, None, None, "Invalid data found")

    # The clip is still in the library and queueable; the prober will not loop on it.
    clip = only_clip(library)
    assert clip["probe_error"] == "Invalid data found"
    assert clip["duration_s"] is None
    assert clip["queueable"] is True
    assert library.needing_probe() == []

    # After the ffprobe path is fixed, the errors are forgotten and it is tried again.
    library.clear_probe_errors()
    assert [pending["id"] for pending in library.needing_probe()] == [clip_id]


def test_orchestrator_metadata_attaches_to_the_file_at_the_same_relative_path(
    library: LibraryStore,
) -> None:
    # The file is already in the delivery folder.
    library.apply_scan([scanned("enduro/42/3.mp4")])

    changed = library.merge_manifest([manifest_clip()], ROOT, "Delivery")

    # The same clip now carries prova, car, team and pass, and is named by them.
    clip = only_clip(library)
    assert changed == [clip["id"]]
    assert clip["source"] == SOURCE_ORCHESTRATOR
    assert (clip["prova"], clip["car_number"], clip["team"], clip["passagem"]) == (
        "Enduro",
        42,
        "Cerrado",
        3,
    )
    assert clip["title"] == "Enduro · carro 42 [Cerrado] · passagem 3"
    assert clip["delivered_at"] == "2026-11-01T12:00:00+00:00"
    # Merging the same manifest again (every poll) changes nothing.
    assert library.merge_manifest([manifest_clip()], ROOT, "Delivery") == []


def test_a_clip_announced_before_its_file_arrives_is_missing_until_the_scan_finds_it(
    library: LibraryStore,
) -> None:
    # The manifest lists a clip whose file is still on its way.
    library.merge_manifest([manifest_clip()], ROOT, "Delivery")
    clip = only_clip(library)
    assert clip["missing"] is True
    assert clip["queueable"] is False

    # The file lands: the scan fills in the same row instead of adding a second.
    library.apply_scan([scanned("enduro/42/3.mp4")])

    clip = only_clip(library)
    assert clip["missing"] is False
    assert clip["source"] == SOURCE_ORCHESTRATOR
    assert clip["prova"] == "Enduro"


def test_a_manual_clip_can_be_renamed_but_an_orchestrator_clip_cannot(
    library: LibraryStore,
) -> None:
    library.apply_scan([scanned("intro.mp4"), scanned("enduro/42/3.mp4")])
    library.merge_manifest([manifest_clip()], ROOT, "Delivery")
    clips = {clip["relative_path"]: clip for clip in library.all()}
    manual_id, pipeline_id = clips["intro.mp4"]["id"], clips["enduro/42/3.mp4"]["id"]
    assert isinstance(manual_id, int) and isinstance(pipeline_id, int)

    library.set_label(manual_id, "  Abertura 2026  ")

    # The label is trimmed and becomes the title.
    renamed = library.get(manual_id)
    assert renamed is not None
    assert renamed["title"] == "Abertura 2026"
    # Pipeline clips are named by their metadata; renaming is refused.
    with pytest.raises(LibraryError, match="only manual clips"):
        library.set_label(pipeline_id, "Something else")
    # An empty label goes back to the file name.
    library.set_label(manual_id, "")
    reset = library.get(manual_id)
    assert reset is not None and reset["title"] == "intro"


def test_a_title_without_a_team_or_pass_leaves_them_out(library: LibraryStore) -> None:
    library.merge_manifest([manifest_clip(team=None)], ROOT, "Delivery")

    assert only_clip(library)["title"] == "Enduro · carro 42 · passagem 3"


def test_notes_and_played_state_are_kept_per_clip(
    library: LibraryStore, fake_clock: FakeClock
) -> None:
    library.apply_scan([scanned("a.mp4")])
    clip_id = only_clip(library)["id"]
    assert isinstance(clip_id, int)

    # Notes are replaced; airing a clip counts it and stamps the time.
    library.set_notes(clip_id, "great crash at the end")
    library.record_played(clip_id)
    library.record_played(clip_id)
    clip = only_clip(library)
    assert clip["notes"] == "great crash at the end"
    assert clip["played_count"] == 2
    assert clip["last_played_at"] == fake_clock.now_utc().isoformat()

    # Marking it unplayed by hand resets both; marking played again counts one.
    library.set_played(clip_id, False)
    assert (only_clip(library)["played_count"], only_clip(library)["last_played_at"]) == (0, None)
    library.set_played(clip_id, True)
    assert only_clip(library)["played_count"] == 1


def test_the_summary_counts_clips_per_prova_missing_and_unplayed(library: LibraryStore) -> None:
    # One pipeline clip (present), one announced but missing, one manual clip.
    library.apply_scan([scanned("enduro/42/3.mp4"), scanned("intro.mp4")])
    library.merge_manifest(
        [manifest_clip(), manifest_clip(relative_path="enduro/7/1.mp4")], ROOT, "Delivery"
    )

    summary = library.summary()

    assert summary == {
        "total": 3,
        "missing": 1,
        "unplayed": 3,
        "per_prova": {"Enduro": 2, "Manual": 1},
    }


def test_only_present_clips_can_be_queued(library: LibraryStore) -> None:
    library.merge_manifest([manifest_clip()], ROOT, "Delivery")
    clip_id = only_clip(library)["id"]
    assert isinstance(clip_id, int)

    # The file has not arrived, so queueing it is refused with a reason.
    with pytest.raises(LibraryError, match="its file is missing"):
        library.require_queueable(clip_id)
    with pytest.raises(LibraryError, match="there is no clip 999"):
        library.require_queueable(999)
