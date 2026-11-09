"""Tests for playout's commands (modules/playout/commands.py): arguments, refusals, the log.

Switching itself is tested in test_playout.py; here the point is that every
command checks its arguments, refuses with a message an operator can act
on, and is logged with who sent it.
"""

import pytest

from baja_studio.core.module import CommandError
from baja_studio.fakes.fake_obs import FakeObs
from tests.modules.playout_studio import (
    OBS_HOST,
    OBS_PASSWORD,
    OBS_PORT,
    PlayoutStudio,
    ready_studio,
)


def test_an_unknown_command_is_refused(playout: PlayoutStudio) -> None:
    with pytest.raises(CommandError, match="no command 'dance'"):
        playout.command("dance")


@pytest.mark.parametrize(
    ("command", "args", "message"),
    [
        ("queue_add", {}, "'clip_ids' must be a non-empty list"),
        ("queue_add", {"clip_ids": ["1"]}, "'clip_ids' must list whole numbers"),
        ("queue_move", {"entry_id": 1}, "'to_index' must be a whole number"),
        ("queue_remove", {"entry_ids": [True]}, "'entry_ids' must list whole numbers"),
        ("set_notes", {"clip_id": 1}, "'notes' must be text"),
        ("mark_played", {"clip_id": 1, "played": "yes"}, "'played' must be true or false"),
        ("set_auto_play_next", {}, "'enabled' must be true or false"),
        ("mark_stream_start", {"at": "yesterday"}, "ISO date and time"),
    ],
)
def test_bad_arguments_are_refused_with_a_message_naming_the_argument(
    playout: PlayoutStudio, command: str, args: dict[str, object], message: str
) -> None:
    with pytest.raises(CommandError, match=message):
        playout.services.registry.dispatch_command("playout", command, args, "web:alice")


def test_queue_commands_reorder_remove_and_clear_by_entry_id(playout: PlayoutStudio) -> None:
    first, second, third = ready_studio(playout)
    entry_ids = playout.command("queue_add", clip_ids=[first, second, third])["entry_ids"]

    # Drag the third entry to the top.
    playout.command("queue_move", entry_id=entry_ids[2], to_index=0)
    assert [entry["clip_id"] for entry in playout.state("queue")] == [third, first, second]

    playout.command("queue_remove", entry_ids=[entry_ids[0]])
    assert [entry["clip_id"] for entry in playout.state("queue")] == [third, second]
    # The queue's estimated total is the sum of the remaining durations.
    assert playout.state("queue_total_s") == 20.0

    # Removing an entry another browser already removed says so.
    with pytest.raises(CommandError, match="no longer in the queue"):
        playout.command("queue_remove", entry_ids=[entry_ids[0]])

    playout.command("queue_clear")
    assert playout.state("queue") == []


def test_notes_played_and_label_edits_reach_the_library_state(playout: PlayoutStudio) -> None:
    first, _, _ = ready_studio(playout)

    playout.command("set_notes", clip_id=first, notes="great jump at 0:04")
    playout.command("mark_played", clip_id=first, played=True)
    playout.command("set_label", clip_id=first, label="Opening")

    clip = playout.state(f"library.{first}")
    assert clip["notes"] == "great jump at 0:04"
    assert clip["played_count"] == 1
    assert clip["title"] == "Opening"

    playout.command("mark_played", clip_id=first, played=False)
    assert playout.state(f"library.{first}.played_count") == 0


def test_a_clip_that_does_not_exist_is_refused_by_name(playout: PlayoutStudio) -> None:
    with pytest.raises(CommandError, match="there is no clip 999"):
        playout.command("set_notes", clip_id=999, notes="x")


def test_switching_commands_say_obs_is_away_when_it_is(playout: PlayoutStudio) -> None:
    first, _, _ = ready_studio(playout)
    playout.command("queue_add", clip_ids=[first])
    playout.network.unplug(OBS_HOST, OBS_PORT)
    playout.soon(lambda: playout.state("obs.connection") == "offline")

    with pytest.raises(CommandError, match="OBS is not connected"):
        playout.command("cue_next")

    # The clip went back to the head of the queue, ready for when OBS returns.
    assert [entry["clip_id"] for entry in playout.state("queue")] == [first]
    playout.network.plug(
        OBS_HOST, OBS_PORT, FakeObs.with_playout_scenes(playout.clock, password=OBS_PASSWORD)
    )


def test_return_without_a_return_scene_is_refused_and_the_clip_stays_on_air(
    playout: PlayoutStudio,
) -> None:
    first, _, _ = ready_studio(playout)
    # "Return to: chosen scene" with no scene picked yet.
    playout.command("set_return_target", mode="chosen")
    playout.command("queue_add", clip_ids=[first])
    playout.command("take", cue_if_needed=True)

    with pytest.raises(CommandError, match="no return scene chosen"):
        playout.command("return_to_scene")

    assert playout.state("program")["clip"]["id"] == first


def test_replay_last_before_anything_aired_is_refused(playout: PlayoutStudio) -> None:
    with pytest.raises(CommandError, match="nothing has aired yet"):
        playout.command("replay_last")


def test_mark_stream_start_can_set_and_clear_an_explicit_time(playout: PlayoutStudio) -> None:
    playout.command("mark_stream_start", at="2026-11-07T13:00:00+00:00")
    assert playout.state("export.stream_start_marker") == "2026-11-07T13:00:00+00:00"

    playout.command("mark_stream_start", at=None)
    assert playout.state("export.stream_start_marker") is None


def test_every_command_is_logged_with_the_client_that_sent_it(playout: PlayoutStudio) -> None:
    first, _, _ = ready_studio(playout)

    playout.command("queue_add", source="web:alice", clip_ids=[first])
    playout.command("cue_next", source="web:bob")
    with pytest.raises(CommandError):
        playout.command("cue_next", source="web:bob")

    log = playout.log()
    assert ("web:alice", "queue_add", "ok") in log
    assert ("web:bob", "cue_next", "ok") in log
    assert any(
        source == "web:bob" and result.startswith("error: a clip is already cued: take it or drop")
        for source, _, result in log
    )
    assert playout.state("queue") == []
    assert playout.state("preview")["clip"]["id"] == first
