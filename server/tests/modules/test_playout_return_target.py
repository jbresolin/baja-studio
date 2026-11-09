"""Tests for the "Return to" control (modules/playout/return_target.py, through the module).

Regression, bench 2026-11-09: with no return scene set, a finished clip left
program where it was and nothing said so. Now the page chooses between the
scene that was on before the clip ("previous", the default) and a scene
picked from OBS's list ("chosen"); when neither is known the strip warns.

The fake OBS starts on "Main"; tests add other scenes ("Interview") where
they need a second place to return to.
"""

import pytest

from baja_studio.adapters.obs_protocol import EventSubscription
from baja_studio.core.module import CommandError
from tests.modules.playout_studio import CLIP_SECONDS, PlayoutStudio, ready_studio


def play_through(playout: PlayoutStudio) -> None:
    playout.soon(lambda: playout.state("program") is None, within_seconds=CLIP_SECONDS + 3)


def test_by_default_a_clip_returns_to_the_scene_that_was_on_before_it(
    playout: PlayoutStudio,
) -> None:
    # The operator is on "Interview" when the clip is taken.
    playout.obs.add_scene("Interview")
    first, _, _ = ready_studio(playout)
    playout.obs.change_program_scene("Interview")
    playout.soon(lambda: playout.state("obs.current_scene") == "Interview")
    playout.command("queue_add", clip_ids=[first])

    playout.command("take", cue_if_needed=True)
    play_through(playout)

    # Back where it was, and the control shows what it remembered.
    assert playout.obs.program_scene == "Interview"
    target = playout.state("return_target")
    assert (target["mode"], target["previous_scene"], target["warning"]) == (
        "previous",
        "Interview",
        None,
    )


def test_previous_is_captured_at_the_first_take_of_a_run_not_at_later_ones(
    playout: PlayoutStudio,
) -> None:
    # Two takes in a row: the second happens while the first clip is on air
    # (program is a playback scene), so "previous" stays the scene before the run.
    first, second, _ = ready_studio(playout)
    playout.command("queue_add", clip_ids=[first, second])
    playout.command("take", cue_if_needed=True)
    playout.command("take", cue_if_needed=True)

    assert playout.state("return_target.previous_scene") == "Main"


def test_a_chosen_scene_wins_and_survives_a_restart(playout: PlayoutStudio) -> None:
    playout.obs.add_scene("Podium")
    first, _, _ = ready_studio(playout)
    playout.command("set_return_target", mode="chosen", scene="Podium")

    # Turning the module off and on is a restart; the choice is session state.
    playout.configure(enabled=False)
    playout.configure(enabled=True)
    playout.soon(lambda: playout.state("obs.connection") == "connected")
    playout.command("queue_add", clip_ids=[first])
    playout.command("take", cue_if_needed=True)
    play_through(playout)

    assert playout.state("return_target")["scene"] == "Podium"
    assert playout.obs.program_scene == "Podium"


def test_the_return_target_is_never_in_the_settings_export(playout: PlayoutStudio) -> None:
    # It is show state, not configuration: it must not travel to another PC.
    playout.command("set_return_target", mode="chosen", scene="Main")

    settings = playout.services.settings.get("playout").model_dump()

    assert "return_scene" not in settings["obs"]
    assert "Main" not in str(settings)


def test_with_no_return_target_the_clip_holds_and_the_page_is_told(
    playout: PlayoutStudio,
) -> None:
    # "Chosen" but nothing chosen: there is nowhere to go.
    first, _, _ = ready_studio(playout)
    playout.command("set_return_target", mode="chosen")
    playout.command("queue_add", clip_ids=[first])
    playout.command("take", cue_if_needed=True)

    play_through(playout)

    # Program holds the last frame, and both the strip warning and a notice say why.
    assert playout.obs.program_scene == "Playback A"
    assert playout.state("return_target.warning") == (
        "no return scene chosen: pick one under Return to"
    )
    assert "no return scene chosen" in playout.state("notice")["message"]


def test_before_any_take_previous_mode_warns_on_the_strip(playout: PlayoutStudio) -> None:
    playout.connect_obs()

    assert playout.state("return_target.warning") == (
        "no previous scene yet: after the clip, program stays where it is"
    )


def test_a_playback_scene_cannot_be_chosen_as_the_return_scene(playout: PlayoutStudio) -> None:
    playout.connect_obs()

    with pytest.raises(CommandError, match="playback scene cannot be the return scene"):
        playout.command("set_return_target", mode="chosen", scene="Playback A")


def test_an_unknown_mode_is_refused(playout: PlayoutStudio) -> None:
    with pytest.raises(CommandError, match="'mode' must be 'previous' or 'chosen'"):
        playout.command("set_return_target", mode="next")


def test_the_scene_dropdown_lists_obs_scenes_without_the_playback_ones(
    playout: PlayoutStudio,
) -> None:
    playout.obs.add_scene("Interview")

    playout.connect_obs()

    assert sorted(playout.state("obs.scenes")) == ["Interview", "Main"]


def test_a_scene_added_in_obs_appears_in_the_dropdown(playout: PlayoutStudio) -> None:
    playout.connect_obs()

    # OBS tells clients when a scene is created.
    playout.obs.add_scene("Podium")
    playout.obs.emit(
        "SceneCreated", {"sceneName": "Podium", "isGroup": False}, EventSubscription.SCENES
    )

    playout.soon(lambda: "Podium" in playout.state("obs.scenes"))
