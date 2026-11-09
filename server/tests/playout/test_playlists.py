"""Tests for playlists (playout/playlists.py): named, ordered lists of clips.

The store only keeps clip ids; whether those clips exist or can play is the
controller's business (tests/modules/test_playout_commands.py).
"""

import random

import pytest

from baja_studio.core.database import Database
from baja_studio.playout.config import OnAirAction, OnAirActions
from baja_studio.playout.playlists import PlaylistError, PlaylistStore
from tests.conftest import FakeClock


@pytest.fixture
def playlists(database: Database, fake_clock: FakeClock) -> PlaylistStore:
    """An empty store whose shuffle is seeded, so a shuffled order is known."""
    return PlaylistStore(database, fake_clock, shuffle=random.Random(7))


def mute_trackside() -> OnAirActions:
    """The sponsor loop's override: run the "mute trackside" macro at each clip start."""
    return OnAirActions(
        on_clip_start=[OnAirAction(module="audio", command="run_macro", args={"macro": "mute"})]
    )


def test_a_playlist_keeps_its_name_and_clip_order(playlists: PlaylistStore) -> None:
    playlist_id = playlists.create("Patrocinadores", [3, 1, 2])

    playlist = playlists.get(playlist_id)

    assert playlist["name"] == "Patrocinadores"
    assert playlist["clip_ids"] == [3, 1, 2]
    assert playlist["shuffle"] is False and playlist["on_air"] is None
    assert playlists.play_order(playlist_id) == [3, 1, 2]


def test_names_are_unique_trimmed_and_required(playlists: PlaylistStore) -> None:
    playlists.create("Sponsors", [1])

    with pytest.raises(PlaylistError, match="already exists"):
        playlists.create("  Sponsors ", [2])
    with pytest.raises(PlaylistError, match="needs a name"):
        playlists.create("   ", [2])


def test_a_playlist_needs_at_least_one_clip_and_a_refused_one_leaves_nothing_behind(
    playlists: PlaylistStore,
) -> None:
    with pytest.raises(PlaylistError, match="at least one clip"):
        playlists.create("Empty", [])

    # The half-made row was rolled back with the refusal.
    assert playlists.all() == []


def test_a_shuffled_playlist_queues_every_clip_once_in_another_order(
    playlists: PlaylistStore,
) -> None:
    playlist_id = playlists.create("Loop", [1, 2, 3, 4, 5, 6])
    playlists.update(playlist_id, shuffle=True)

    order = playlists.play_order(playlist_id)

    assert sorted(order) == [1, 2, 3, 4, 5, 6]
    assert order != [1, 2, 3, 4, 5, 6]  # the seed makes this a known, different order
    # The stored order is untouched; only what gets queued is shuffled.
    assert playlists.get(playlist_id)["clip_ids"] == [1, 2, 3, 4, 5, 6]


def test_editing_changes_only_what_is_given(playlists: PlaylistStore) -> None:
    playlist_id = playlists.create("A", [1, 2])

    playlists.update(playlist_id, name="B")
    playlists.update(playlist_id, clip_ids=[2, 1, 3])

    playlist = playlists.get(playlist_id)
    assert (playlist["name"], playlist["clip_ids"]) == ("B", [2, 1, 3])


def test_an_on_air_override_is_stored_returned_and_cleared(playlists: PlaylistStore) -> None:
    playlist_id = playlists.create("Sponsors", [1])

    playlists.update(playlist_id, set_on_air=True, on_air=mute_trackside())
    stored = playlists.on_air_override(playlist_id)
    playlists.update(playlist_id, set_on_air=True, on_air=None)

    assert stored == mute_trackside()
    assert playlists.get(playlist_id)["on_air"] is None
    assert playlists.on_air_override(playlist_id) is None


def test_no_playlist_or_a_deleted_one_has_no_override_and_no_name(
    playlists: PlaylistStore,
) -> None:
    playlist_id = playlists.create("Gone soon", [1])
    playlists.update(playlist_id, set_on_air=True, on_air=mute_trackside())
    playlists.delete(playlist_id)

    # A queue entry may still point at it; that must not fail anything.
    assert playlists.on_air_override(playlist_id) is None
    assert playlists.name_of(playlist_id) is None
    assert playlists.on_air_override(None) is None and playlists.name_of(None) is None


def test_unknown_playlists_are_refused_with_their_id(playlists: PlaylistStore) -> None:
    with pytest.raises(PlaylistError, match="there is no playlist 9"):
        playlists.get(9)
    with pytest.raises(PlaylistError, match="there is no playlist 9"):
        playlists.delete(9)
