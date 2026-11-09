"""Tests for playlists through the module: the playlist commands and queueing one in one action.

(The commands live in modules/playout/commands.py and queueing in
controller.py; the store itself is tested in tests/playout/test_playlists.py.)

The `playout` fixture watches one folder; tests that need a second labelled
folder ("Patrocinadores") add it next to the delivery folder.
"""

from pathlib import Path
from typing import Any

import pytest

from baja_studio.core.module import CommandError
from tests.modules.playout_studio import CLIP_SECONDS, PlayoutStudio, ready_studio


def watch_sponsors(playout: PlayoutStudio, tmp_path: Path) -> list[int]:
    """Add a "Patrocinadores" folder with two clips; returns their ids once probed."""
    sponsors = tmp_path / "sponsors"
    sponsors.mkdir()
    for name in ("logo-a.mp4", "logo-b.mp4"):
        (sponsors / name).write_bytes(b"video")
    playout.configure(
        sources={
            "local_folders": [
                {"path": str(playout.clips_folder), "label": "Delivery", "receiver_root": True},
                {"path": str(sponsors), "label": "Patrocinadores"},
            ]
        }
    )
    return [playout.wait_for_clip(name)["id"] for name in ("logo-a.mp4", "logo-b.mp4")]


def test_a_playlist_made_from_a_selection_is_published_for_every_page(
    playout: PlayoutStudio,
) -> None:
    first, second, _ = ready_studio(playout)

    created = playout.command("playlist_create", name="Abertura", clip_ids=[second, first])

    assert created["clip_ids"] == [second, first]
    assert [(p["name"], p["clip_ids"]) for p in playout.state("playlists")] == [
        ("Abertura", [second, first])
    ]


def test_a_playlist_made_from_a_folder_group_takes_its_clips_and_its_name(
    playout: PlayoutStudio, tmp_path: Path
) -> None:
    ready_studio(playout)
    logos = watch_sponsors(playout, tmp_path)

    created = playout.command("playlist_create", group="Patrocinadores")

    assert created["name"] == "Patrocinadores"
    assert sorted(created["clip_ids"]) == sorted(logos)


def test_a_sponsor_playlist_is_queued_in_one_click_and_aired_under_its_name(
    playout: PlayoutStudio, tmp_path: Path
) -> None:
    # Bench step: sponsor playlist queued in one click.
    ready_studio(playout)
    logos = watch_sponsors(playout, tmp_path)
    playlist = playout.command("playlist_create", group="Patrocinadores")

    playout.command("playlist_queue", playlist_id=playlist["id"])

    queue = playout.state("queue")
    assert [entry["clip_id"] for entry in queue] == playlist["clip_ids"]
    assert {entry["playlist"] for entry in queue} == {"Patrocinadores"}
    # Played through, the aired log names the playlist each clip came from.
    playout.command("set_auto_play_next", enabled=True)
    playout.command("take", cue_if_needed=True)
    playout.soon(lambda: playout.state("program") is None, within_seconds=2 * CLIP_SECONDS + 10)
    aired = playout.command("export_aired")["entries"]
    assert [(entry["clip_id"], entry["playlist"]) for entry in aired] == [
        (clip_id, "Patrocinadores") for clip_id in playlist["clip_ids"]
    ]
    assert sorted(playlist["clip_ids"]) == sorted(logos)


def test_queueing_a_playlist_skips_clips_whose_file_is_missing(playout: PlayoutStudio) -> None:
    first, second, _ = ready_studio(playout)
    playlist = playout.command("playlist_create", name="Two", clip_ids=[first, second])
    (playout.clips_folder / "a.mp4").unlink()
    playout.soon(lambda: (playout.clip("a.mp4") or {}).get("missing") is True)

    playout.command("playlist_queue", playlist_id=playlist["id"])

    assert [entry["clip_id"] for entry in playout.state("queue")] == [second]


def test_a_playlist_with_no_playable_clip_is_refused(playout: PlayoutStudio) -> None:
    first, _, _ = ready_studio(playout)
    playlist = playout.command("playlist_create", name="Lonely", clip_ids=[first])
    (playout.clips_folder / "a.mp4").unlink()
    playout.soon(lambda: (playout.clip("a.mp4") or {}).get("missing") is True)

    with pytest.raises(CommandError, match="no clip of playlist 'Lonely' has its file"):
        playout.command("playlist_queue", playlist_id=playlist["id"])


def test_a_playlist_can_be_renamed_reordered_shuffled_and_deleted(playout: PlayoutStudio) -> None:
    first, second, third = ready_studio(playout)
    playlist = playout.command("playlist_create", name="Old", clip_ids=[first, second])

    updated = playout.command(
        "playlist_update",
        playlist_id=playlist["id"],
        name="New",
        clip_ids=[third, first],
        shuffle=True,
    )
    assert (updated["name"], updated["clip_ids"], updated["shuffle"]) == (
        "New",
        [third, first],
        True,
    )

    playout.command("playlist_delete", playlist_id=playlist["id"])
    assert playout.state("playlists") == []


@pytest.mark.parametrize(
    ("command", "args", "message"),
    [
        ("playlist_create", {"name": "X"}, "'clip_ids' must be a non-empty list"),
        ("playlist_create", {"group": "Nowhere"}, "the library group 'Nowhere' has no clips"),
        ("playlist_update", {"playlist_id": 1, "shuffle": "yes"}, "'shuffle' must be true"),
        (
            "playlist_update",
            {"playlist_id": 1, "on_air": {"on_clip_start": [{"module": "audio"}]}},
            "'on_air': on_clip_start.0.command: Field required",
        ),
        ("playlist_queue", {"playlist_id": 42}, "there is no playlist 42"),
    ],
)
def test_bad_playlist_arguments_are_refused_with_a_reason(
    playout: PlayoutStudio, command: str, args: dict[str, Any], message: str
) -> None:
    with pytest.raises(CommandError, match=message):
        playout.command(command, **args)
