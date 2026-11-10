"""Tests for transport and slow motion on the clip on air (modules/playout/transport.py).

The service runs with the playout module, the fake OBS and the fake ffprobe
(every clip lasts 10 s at 25 fps, so one frame is 40 ms). Commands are sent
the way the page sends them. The fake OBS plays media against the fake clock:
positions only move when a test lets fake time pass (`pass_time`), so a
position read right after a command is exactly where the command left it.
"""

import pytest

from baja_studio.adapters.obs_protocol import MediaState
from baja_studio.core.module import CommandError
from tests.modules.playout_studio import CLIP_FPS, HOOK_URL, PlayoutStudio, ready_studio

ONE_FRAME = 1 / CLIP_FPS


def on_air(playout: PlayoutStudio, *files: str) -> list[int]:
    """Scan `files`, queue them all and take the first; returns their clip ids."""
    clip_ids = ready_studio(playout, files or ("a.mp4", "b.mp4"))
    playout.command("queue_add", clip_ids=clip_ids)
    playout.command("take", cue_if_needed=True)
    return clip_ids


def use_method(playout: PlayoutStudio, method: str) -> None:
    """Choose how a live speed change is made (after `on_air`, which sets OBS up afresh)."""
    playout.configure(obs=playout.obs_settings(live_speed_change=method))


def program_cursor(playout: PlayoutStudio) -> float:
    """Where the clip on air is, in its own seconds, as OBS would report it."""
    source = "baja_program_a" if playout.state("program.slot") == "a" else "baja_program_b"
    return float(playout.obs.media_of(source).cursor(playout.clock.monotonic()))


# --- transport ------------------------------------------------------------------------------


def test_a_pause_on_air_freezes_the_clip_and_is_not_an_end(playout: PlayoutStudio) -> None:
    # Auto-play next is on and a second clip is queued: if a pause counted as
    # the clip ending, the next clip would go to air.
    playout.configure(hooks={"on_air_webhook_url": HOOK_URL})
    first, _ = on_air(playout)
    playout.command("set_auto_play_next", enabled=True)
    playout.pass_time(2.0)

    playout.command("transport", source="web:bob", action="pause")
    # Much longer than the clip: a paused clip never reaches its end.
    playout.pass_time(15.0)

    # Still the same clip on air, frozen where it was paused, and the page
    # sees "paused". No end webhook was sent, nothing was logged as ended.
    assert playout.state("program")["clip"]["id"] == first
    assert playout.state("program.transport.state") == "paused"
    assert program_cursor(playout) == pytest.approx(2.0, abs=0.25)
    assert [post["body"].keys() for post in playout.hook_posts] == [{"clip", "started_at"}]
    (aired,) = playout.command("export_aired")["entries"]
    assert aired["ended_at"] is None
    # The command log says who paused it.
    assert ("web:bob", "transport", "ok") in playout.log()


def test_play_resumes_a_paused_clip_from_where_it_stopped(playout: PlayoutStudio) -> None:
    on_air(playout)
    playout.pass_time(1.0)
    playout.command("transport", action="pause")
    paused_at = program_cursor(playout)

    playout.command("transport", action="play")
    playout.pass_time(1.0)

    assert playout.state("program.transport.state") == "playing"
    assert program_cursor(playout) == pytest.approx(paused_at + 1.0, abs=0.25)


def test_toggle_pauses_a_playing_clip_and_plays_a_paused_one(playout: PlayoutStudio) -> None:
    # K on the keyboard.
    on_air(playout)

    first = playout.command("transport", action="toggle")
    second = playout.command("transport", action="toggle")

    assert (first["state"], second["state"]) == ("paused", "playing")


def test_a_frame_step_moves_one_frame_of_the_clip_while_paused(playout: PlayoutStudio) -> None:
    # The fake ffprobe says 25 fps, so one frame is 40 ms.
    on_air(playout)
    playout.pass_time(1.0)
    playout.command("transport", action="pause")
    paused_at = program_cursor(playout)

    playout.command("transport", action="step", frames=1)
    after_forward = program_cursor(playout)
    playout.command("transport", action="step", frames=-1)
    playout.command("transport", action="step", frames=-1)

    # The fake keeps milliseconds, as OBS's cursor does.
    assert after_forward == pytest.approx(paused_at + ONE_FRAME, abs=0.001)
    assert program_cursor(playout) == pytest.approx(paused_at - ONE_FRAME, abs=0.001)
    assert playout.state("program.transport.fps") == CLIP_FPS


def test_a_frame_step_while_playing_is_refused(playout: PlayoutStudio) -> None:
    on_air(playout)

    with pytest.raises(CommandError, match="pause first"):
        playout.command("transport", action="step", frames=1)


def test_a_clip_without_a_frame_rate_steps_at_30_fps(playout: PlayoutStudio) -> None:
    # ffprobe could not tell the rate (a variable-rate file, say).
    on_air(playout)
    playout.module.controller.switcher.program.clip["fps"] = None  # type: ignore[union-attr]
    playout.command("transport", action="pause")
    paused_at = program_cursor(playout)

    playout.command("transport", action="step", frames=1)

    assert program_cursor(playout) == pytest.approx(paused_at + 1 / 30, abs=0.001)


def test_seek_and_nudges_move_the_clip_but_never_onto_its_end(playout: PlayoutStudio) -> None:
    on_air(playout)

    playout.command("transport", action="seek", position_s=4.0)
    at_seek = program_cursor(playout)
    playout.command("transport", action="nudge", seconds=5.0)
    after_plus = program_cursor(playout)
    playout.command("transport", action="nudge", seconds=5.0)
    near_end = program_cursor(playout)
    playout.command("transport", action="nudge", seconds=-20.0)

    assert at_seek == pytest.approx(4.0)
    assert after_plus == pytest.approx(9.0)
    # +5 s from 9 s of a 10 s clip stops one frame before the end: ending a
    # clip is Skip's job, so a nudge never triggers auto-play or end actions.
    assert near_end == pytest.approx(10.0 - ONE_FRAME, abs=0.001)
    assert program_cursor(playout) == 0.0
    assert playout.state("program") is not None


def test_restart_plays_the_clip_on_air_from_the_top_without_a_new_airing(
    playout: PlayoutStudio,
) -> None:
    playout.configure(hooks={"on_air_webhook_url": HOOK_URL})
    on_air(playout)
    playout.pass_time(3.0)
    playout.command("transport", action="pause")
    playout.soon(lambda: len(playout.hook_posts) == 1)

    playout.command("transport", action="restart")

    # From 0 and playing; still one airing, and no second "on air" webhook.
    assert program_cursor(playout) == 0.0
    assert playout.state("program.transport.state") == "playing"
    playout.pass_time(1.0)
    assert len(playout.command("export_aired")["entries"]) == 1
    assert len(playout.hook_posts) == 1


def test_transport_never_touches_the_cued_clip(playout: PlayoutStudio) -> None:
    # The first clip is on air in slot a; the second is cued in slot b.
    on_air(playout)
    playout.command("cue_next")
    cued = playout.obs.media_of("baja_program_b")

    playout.command("transport", action="pause")
    playout.command("transport", action="seek", position_s=5.0)
    playout.command("transport", action="play")

    # The preview is still parked on its first frame.
    assert cued.state == MediaState.PAUSED
    assert cued.cursor(playout.clock.monotonic()) == 0.0


def test_transport_with_nothing_on_air_is_refused(playout: PlayoutStudio) -> None:
    ready_studio(playout, ("a.mp4",))

    with pytest.raises(CommandError, match="nothing from playout is on air"):
        playout.command("transport", action="pause")


@pytest.mark.parametrize(
    ("args", "message"),
    [
        ({"action": "rewind"}, "'action' must be one of"),
        ({"action": "seek"}, "'position_s' must be a number"),
        ({"action": "step", "frames": 0.5}, "'frames' must be a whole number"),
        ({"action": "nudge", "seconds": "5"}, "'seconds' must be a number"),
    ],
)
def test_bad_transport_arguments_are_refused(
    playout: PlayoutStudio, args: dict[str, object], message: str
) -> None:
    on_air(playout)

    with pytest.raises(CommandError, match=message):
        playout.services.registry.dispatch_command("playout", "transport", args, "web:alice")


# --- slow motion: the cued clip -------------------------------------------------------------


def test_a_slow_replay_is_cued_at_its_speed_with_its_audio_muted(playout: PlayoutStudio) -> None:
    # "Replay at 50 %": chosen before the cue, it is OBS's speed_percent on the
    # source, set before the restart so the clip opens at that speed.
    first, _ = ready_studio(playout, ("a.mp4", "b.mp4"))
    playout.command("queue_add", clip_ids=[first])
    playout.command("set_cue_speed", speed_percent=50)
    assert playout.state("cue_speed_percent") == 50

    playout.command("cue_next")

    source = playout.obs.inputs["baja_program_a"]
    assert source.settings["speed_percent"] == 50
    assert source.muted is True
    assert playout.state("preview.speed_percent") == 50
    # The speed was for that one cue: the next one is at 100 % again.
    assert playout.state("cue_speed_percent") == 100


def test_a_slow_clip_on_air_plays_at_half_speed(playout: PlayoutStudio) -> None:
    first, _ = ready_studio(playout, ("a.mp4", "b.mp4"))
    playout.command("queue_add", clip_ids=[first])
    playout.command("set_cue_speed", speed_percent=50)
    playout.command("take", cue_if_needed=True)

    playout.pass_time(2.0)

    # Two seconds of air moved half as far through the clip.
    assert playout.state("program.speed_percent") == 50
    assert program_cursor(playout) == pytest.approx(1.0, abs=0.15)


def test_choosing_a_speed_with_a_clip_cued_cues_it_again_at_that_speed(
    playout: PlayoutStudio,
) -> None:
    first, _ = ready_studio(playout, ("a.mp4", "b.mp4"))
    playout.command("queue_add", clip_ids=[first])
    playout.command("cue_next")

    playout.command("set_cue_speed", speed_percent=25)

    # Same clip, same off-air source, now at 25 % and muted, parked on its
    # first frame; program never moved.
    source = playout.obs.inputs["baja_program_a"]
    assert source.settings["speed_percent"] == 25
    assert source.muted is True
    assert source.media.state == MediaState.PAUSED
    assert playout.state("preview")["clip"]["id"] == first
    assert playout.state("preview.speed_percent") == 25
    assert playout.obs.program_scene == "Main"

    # Back to 100 %: unmuted again (playout muted it, so playout unmutes it).
    playout.command("set_cue_speed", speed_percent=100)
    assert source.muted is False


def test_a_source_muted_in_obs_by_the_operator_stays_muted_at_normal_speed(
    playout: PlayoutStudio,
) -> None:
    first, _ = ready_studio(playout, ("a.mp4", "b.mp4"))
    playout.obs.inputs["baja_program_a"].muted = True
    playout.command("queue_add", clip_ids=[first])

    playout.command("cue_next")

    assert playout.obs.inputs["baja_program_a"].muted is True
    assert playout.obs.requests_of("SetInputMute") == []


def test_only_the_offered_speeds_are_accepted(playout: PlayoutStudio) -> None:
    with pytest.raises(CommandError, match="speed must be one of 100, 50, 25"):
        playout.command("set_cue_speed", speed_percent=75)


# --- slow motion: the clip on air -----------------------------------------------------------


@pytest.mark.parametrize("method", ["reload", "swap"])
def test_a_live_speed_change_keeps_the_position_within_one_frame(
    playout: PlayoutStudio, method: str
) -> None:
    # 100 → 50 → 100 on the clip on air, by each method (bench step 6 decides
    # which one is clean on OBS 32; the default is swap).
    playout.configure(hooks={"on_air_webhook_url": HOOK_URL})
    on_air(playout)
    use_method(playout, method)
    playout.pass_time(2.0)
    before = program_cursor(playout)

    playout.command("set_program_speed", speed_percent=50)

    # Same frame as before the change, now at half speed and muted.
    assert program_cursor(playout) == pytest.approx(before, abs=ONE_FRAME)
    source = "baja_program_a" if playout.state("program.slot") == "a" else "baja_program_b"
    assert playout.obs.inputs[source].settings["speed_percent"] == 50
    assert playout.obs.inputs[source].muted is True
    assert playout.state("program.speed_percent") == 50
    assert playout.obs.media_of(source).state == MediaState.PLAYING
    playout.pass_time(2.0)
    halfway = program_cursor(playout)
    assert halfway == pytest.approx(before + 1.0, abs=0.15)

    playout.command("set_program_speed", speed_percent=100)

    assert program_cursor(playout) == pytest.approx(halfway, abs=ONE_FRAME)
    source = "baja_program_a" if playout.state("program.slot") == "a" else "baja_program_b"
    assert playout.obs.inputs[source].muted is False
    # Still one airing: a speed change is not a new take (no webhook, no log entry).
    playout.pass_time(0.5)
    assert len(playout.command("export_aired")["entries"]) == 1
    assert len(playout.hook_posts) == 1


def test_a_swap_cuts_to_the_other_source_and_stops_the_one_it_left(
    playout: PlayoutStudio,
) -> None:
    on_air(playout)
    playout.pass_time(1.0)
    cuts_before = len(playout.obs.requests_of("SetCurrentProgramScene"))

    playout.command("set_program_speed", speed_percent=50)

    # One cut, to the other playback scene; the source left behind is stopped.
    assert len(playout.obs.requests_of("SetCurrentProgramScene")) == cuts_before + 1
    assert playout.obs.program_scene == "Playback B"
    assert playout.state("program.slot") == "b"
    assert playout.obs.media_of("baja_program_a").state == MediaState.STOPPED
    # Program stays on air: the scene change was ours, not the operator's.
    playout.pass_time(1.0)
    assert playout.state("program") is not None


def test_a_reload_changes_the_source_on_air_without_a_cut(playout: PlayoutStudio) -> None:
    on_air(playout)
    use_method(playout, "reload")
    cuts_before = len(playout.obs.requests_of("SetCurrentProgramScene"))

    playout.command("set_program_speed", speed_percent=50)

    assert len(playout.obs.requests_of("SetCurrentProgramScene")) == cuts_before
    assert playout.state("program.slot") == "a"


def test_a_swap_with_a_clip_cued_cues_it_again_afterwards(playout: PlayoutStudio) -> None:
    # The swap needs the off-air source, where the next clip is cued.
    _, second = on_air(playout)
    playout.command("cue_next")

    playout.command("set_program_speed", speed_percent=50)

    # The clip on air moved to slot b; the cued clip is cued again in slot a,
    # parked on its first frame, at its own (normal) speed.
    assert playout.state("program.slot") == "b"
    assert playout.state("preview")["clip"]["id"] == second
    assert playout.state("preview.slot") == "a"
    cued = playout.obs.inputs["baja_program_a"]
    assert cued.media.state == MediaState.PAUSED
    assert cued.media.cursor(playout.clock.monotonic()) == 0.0
    assert cued.settings["speed_percent"] == 100


@pytest.mark.parametrize("method", ["reload", "swap"])
def test_a_paused_clip_stays_paused_through_a_speed_change(
    playout: PlayoutStudio, method: str
) -> None:
    on_air(playout)
    use_method(playout, method)
    playout.pass_time(1.0)
    playout.command("transport", action="pause")
    before = program_cursor(playout)

    playout.command("set_program_speed", speed_percent=25)
    playout.pass_time(1.0)

    assert program_cursor(playout) == pytest.approx(before, abs=ONE_FRAME)
    source = "baja_program_a" if playout.state("program.slot") == "a" else "baja_program_b"
    assert playout.obs.media_of(source).state == MediaState.PAUSED


def test_the_clip_on_air_and_its_speed_survive_a_restart(playout: PlayoutStudio) -> None:
    on_air(playout)
    playout.command("set_program_speed", speed_percent=50)

    playout.configure(enabled=False)
    playout.configure(enabled=True)

    assert playout.state("program.speed_percent") == 50
