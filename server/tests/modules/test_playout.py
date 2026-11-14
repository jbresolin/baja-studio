"""Tests for the playout module end to end (modules/playout/__init__.py and controller.py).

Each test runs the service with only the playout module on the fake clock
(the `playout` fixture, tests/modules/playout_studio.py): a fake OBS set up
with the two playback scenes and a "Main" scene, a fake orchestrator, a fake
ffprobe, and a temporary clips folder. Commands are sent the way the page
sends them, and the checks read the module's state (what browsers see), the
fake OBS (what went to air), the webhook posts and the database.
"""

import threading
from pathlib import Path

import pytest

from baja_studio.adapters.obs_protocol import MediaState
from baja_studio.core.module import CommandError
from baja_studio.core.supervisor import HealthState
from baja_studio.core.system_services import INSTALL_DIRECTORY_ENVIRONMENT_VARIABLE
from tests.conftest import eventually
from tests.modules.playout_studio import (
    CLIP_FPS,
    CLIP_SECONDS,
    HOOK_URL,
    PlayoutStudio,
    ready_studio,
)
from tests.modules.readiness_runner import run_module_checks


def play_through(playout: PlayoutStudio) -> None:
    """Let the clip on air play to its end (plus a little), in fake time."""
    playout.soon(lambda: playout.state("program") is None, within_seconds=CLIP_SECONDS + 2)


# --- starting up ----------------------------------------------------------------------------


def test_a_fresh_module_says_obs_is_not_configured_and_is_offline(playout: PlayoutStudio) -> None:
    # Nothing is set up yet: OBS has no address. The module must say so
    # rather than pretend to be fine, and the library must still be empty.
    eventually(lambda: playout.state("obs.connection") == "not_configured")
    eventually(
        lambda: "not configured" in playout.services.supervisor.module_health("playout").message
    )

    assert playout.services.supervisor.module_health("playout").state == HealthState.OFFLINE
    assert playout.state("library") == {}


def test_with_obs_configured_the_module_connects_and_reads_the_program_scene(
    playout: PlayoutStudio,
) -> None:
    playout.connect_obs()

    # The fake OBS starts on "Main", so that is what playout reports.
    assert playout.state("obs.current_scene") == "Main"
    eventually(lambda: playout.services.supervisor.module_health("playout").state == HealthState.OK)
    # Connecting also forces cuts into both playback scenes, so a take is
    # never a crossfade that would show the previous clip.
    assert playout.obs.transition_overrides["Playback A"] == ("Cut", None)
    assert playout.obs.transition_overrides["Playback B"] == ("Cut", None)


# --- cue and take ---------------------------------------------------------------------------


def test_cue_loads_the_head_of_the_queue_into_playback_a_without_touching_program(
    playout: PlayoutStudio,
) -> None:
    first, _, _ = ready_studio(playout)
    playout.command("queue_add", clip_ids=[first])

    playout.command("cue_next")

    # The clip sits paused on its first frame in the off-air source...
    media = playout.obs.media_of("baja_program_a")
    assert media.file.endswith("a.mp4")
    assert media.state == MediaState.PAUSED
    assert media.cursor(playout.clock.monotonic()) == 0.0
    # ...program has not moved, the preview shows it, and the queue is empty.
    assert playout.obs.program_scene == "Main"
    assert playout.state("preview")["clip"]["id"] == first
    assert playout.state("queue") == []


def test_take_is_one_cut_and_the_clip_plays_on_air(playout: PlayoutStudio) -> None:
    first, _, _ = ready_studio(playout)
    playout.command("queue_add", clip_ids=[first])
    playout.command("cue_next")
    cuts_before = len(playout.obs.requests_of("SetCurrentProgramScene"))

    result = playout.command("take")

    assert result["status"] == "on_air"
    assert len(playout.obs.requests_of("SetCurrentProgramScene")) == cuts_before + 1
    assert playout.obs.program_scene == "Playback A"
    assert playout.obs.media_of("baja_program_a").state == MediaState.PLAYING
    assert playout.state("program")["clip"]["id"] == first
    assert playout.state("preview") is None


def test_the_first_take_of_the_day_works_first_time(playout: PlayoutStudio) -> None:
    # Regression, bench 2026-11-08: on a freshly started OBS the first Take
    # failed with "media must be playing or paused" and only the second one
    # worked. The fake OBS now refuses a seek on a source that has not started,
    # as real OBS does, so the old cue sequence fails here.
    first, _, _ = ready_studio(playout)
    playout.command("queue_add", clip_ids=[first])

    result = playout.command("take", cue_if_needed=True)

    assert result["status"] == "on_air"
    assert playout.obs.program_scene == "Playback A"
    assert playout.state("cue_problem") is None
    # Nothing was refused on the way.
    assert all(result == "ok" for _, _, result in playout.log())


def test_a_cue_obs_cannot_start_is_refused_with_obs_words_and_the_module_is_degraded(
    playout: PlayoutStudio,
) -> None:
    first, second, _ = ready_studio(playout)
    playout.obs.stuck_files.add(str(playout.clips_folder / "a.mp4"))
    playout.command("queue_add", clip_ids=[first, second])
    failures: list[Exception] = []

    def cue() -> None:
        try:
            playout.command("cue_next")
        except CommandError as error:
            failures.append(error)

    # The cue waits up to 3 s of fake time for OBS to start the file, so it
    # runs on its own thread while fake time passes. It holds playout's lock
    # meanwhile (the OBS worker waits for it), so time is moved whenever the
    # cue thread is in its 0.1 s wait between two status questions (not while
    # it waits for OBS to answer one).
    worker = threading.Thread(target=cue)
    worker.start()

    def between_polls() -> bool:
        wakes_at = playout.clock.wake_up_time_of(worker.ident)
        return wakes_at is not None and wakes_at <= playout.clock.monotonic() + 0.1 + 1e-6

    for _ in range(40):
        eventually(lambda: between_polls() or not worker.is_alive())
        if not worker.is_alive():
            break
        playout.clock.advance(0.1)
    worker.join(timeout=5)

    # Refused, saying what OBS reported; the clip is back at the head of the
    # queue; and the module is degraded with the same words until a cue works.
    assert len(failures) == 1
    assert "did not start" in str(failures[0])
    assert "OBS_MEDIA_STATE_OPENING" in str(failures[0])
    assert [entry["clip_id"] for entry in playout.state("queue")] == [first, second]
    assert "did not start" in playout.state("cue_problem")
    playout.soon(
        lambda: playout.services.supervisor.module_health("playout").state == HealthState.DEGRADED
    )

    # The operator drops the bad clip; the next one cues, and all is well again.
    playout.obs.stuck_files.clear()
    playout.command("queue_remove", entry_ids=[playout.state("queue")[0]["entry_id"]])
    playout.command("cue_next")
    assert playout.state("cue_problem") is None
    playout.soon(
        lambda: playout.services.supervisor.module_health("playout").state == HealthState.OK
    )


def test_the_second_clip_goes_into_the_other_source_and_the_first_is_never_reloaded(
    playout: PlayoutStudio,
) -> None:
    # This is the flash-free rule: while clip 1 plays in A, clip 2 is
    # loaded into B. Nothing is sent to A until clip 2 is on air.
    first, second, _ = ready_studio(playout)
    playout.command("queue_add", clip_ids=[first, second])
    playout.command("cue_next")
    playout.command("take")
    requests_before = len(playout.obs.requests)

    playout.command("cue_next")

    during_cue = playout.obs.requests[requests_before:]
    assert all(r.data.get("inputName") != "baja_program_a" for r in during_cue)
    assert all(r.request_type != "SetCurrentProgramScene" for r in during_cue)
    assert playout.obs.media_of("baja_program_b").file.endswith("b.mp4")

    playout.command("take")

    assert playout.obs.program_scene == "Playback B"
    # The old source is stopped once it is off air.
    assert playout.obs.media_of("baja_program_a").state == MediaState.STOPPED


def test_take_without_a_cue_is_refused_unless_asked_to_cue_first(playout: PlayoutStudio) -> None:
    first, _, _ = ready_studio(playout)
    playout.command("queue_add", clip_ids=[first])

    with pytest.raises(CommandError, match="nothing is cued"):
        playout.command("take")

    # Space on the page sends `take {cue_if_needed: true}`: cue, then take.
    playout.command("take", cue_if_needed=True)
    assert playout.state("program")["clip"]["id"] == first


def test_a_missing_file_cannot_be_queued(playout: PlayoutStudio) -> None:
    first, _, _ = ready_studio(playout)
    (playout.clips_folder / "a.mp4").unlink()
    playout.soon(lambda: playout.clip("a.mp4")["missing"])  # type: ignore[index]

    with pytest.raises(CommandError, match="file is missing"):
        playout.command("queue_add", clip_ids=[first])


# --- end of clip ----------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("post_clip", "expected_scene"),
    [("return", "Main"), ("hold", "Playback A"), ("fade", "Main")],
)
def test_when_a_clip_ends_the_post_clip_setting_decides_what_program_shows(
    playout: PlayoutStudio, post_clip: str, expected_scene: str
) -> None:
    first, _, _ = ready_studio(playout)
    playout.connect_obs(post_clip=post_clip, fade_ms=500)
    playout.command("queue_add", clip_ids=[first])
    playout.command("take", cue_if_needed=True)

    play_through(playout)

    assert playout.obs.program_scene == expected_scene
    if post_clip == "fade":
        # The fade used a temporary override on the return scene, cleared afterwards.
        playout.soon(lambda: playout.obs.transition_overrides.get("Main") == (None, None))


def test_a_cued_next_clip_does_not_play_by_itself_unless_auto_play_next_is_on(
    playout: PlayoutStudio,
) -> None:
    first, second, third = ready_studio(playout)
    playout.command("queue_add", clip_ids=[first, second, third])
    playout.command("take", cue_if_needed=True)
    playout.command("cue_next")

    # Off (the default): passes are usually followed by hosts talking.
    play_through(playout)
    assert playout.obs.program_scene == "Main"
    assert playout.state("preview")["clip"]["id"] == second

    # On: the cued clip goes to air as soon as the clip on air ends, and
    # then the head of the queue is cued and played the same way.
    playout.command("set_auto_play_next", enabled=True)
    playout.command("take")
    playout.soon(
        lambda: (playout.state("program") or {}).get("clip", {}).get("id") == third,
        within_seconds=CLIP_SECONDS + 2,
    )
    assert playout.obs.program_scene in ("Playback A", "Playback B")


def test_a_clip_ending_with_auto_play_on_and_a_cued_next_puts_it_on_air_in_one_cut(
    playout: PlayoutStudio,
) -> None:
    # Regression, bench 2026-11-09: auto-play next never fired. The clip on air
    # ends by itself (the fake OBS plays it to the end) with the next one cued.
    first, second, _ = ready_studio(playout)
    playout.command("set_auto_play_next", enabled=True)
    playout.command("queue_add", clip_ids=[first, second])
    playout.command("take", cue_if_needed=True)
    playout.command("cue_next")
    cuts_before = list(playout.obs.program_history)

    playout.soon(
        lambda: (playout.state("program") or {}).get("clip", {}).get("id") == second,
        within_seconds=CLIP_SECONDS + 2,
    )

    # Exactly one scene change, straight to the next clip's scene (no return
    # scene in between), and the clip plays.
    assert playout.obs.program_history[len(cuts_before) :] == ["Playback B"]
    assert playout.obs.media_of("baja_program_b").state == MediaState.PLAYING


def test_an_auto_play_chain_of_three_clips_returns_to_the_scene_before_the_first(
    playout: PlayoutStudio,
) -> None:
    # Bench step: auto-play chain of three. Program was on "Main" before the
    # first clip, so that is where it goes after the third.
    first, second, third = ready_studio(playout)
    playout.command("set_auto_play_next", enabled=True)
    playout.command("queue_add", clip_ids=[first, second, third])

    playout.command("take", cue_if_needed=True)
    # Generous: fake time also passes while playout waits for each OBS answer.
    playout.soon(lambda: playout.state("program") is None, within_seconds=3 * CLIP_SECONDS + 15)

    aired = [entry["clip_id"] for entry in playout.command("export_aired")["entries"]]
    assert aired == [first, second, third]
    assert playout.obs.program_scene == "Main"
    assert playout.obs.program_history[-1] == "Main"


def test_when_auto_play_cannot_cue_the_next_clip_post_clip_applies_with_a_notice(
    playout: PlayoutStudio,
) -> None:
    # The next clip's file vanished after it was queued: auto-play must not
    # leave program sitting on a finished clip.
    first, second, _ = ready_studio(playout)
    playout.command("set_auto_play_next", enabled=True)
    playout.command("queue_add", clip_ids=[first, second])
    playout.command("take", cue_if_needed=True)
    (playout.clips_folder / "b.mp4").unlink()
    playout.soon(lambda: (playout.clip("b.mp4") or {}).get("missing") is True)

    play_through(playout)

    assert playout.obs.program_scene == "Main"
    assert "auto-play next failed" in playout.state("notice")["message"]


# --- replay, skip, return ------------------------------------------------------------------


def test_replay_last_requeues_the_last_aired_clip_and_cues_it(playout: PlayoutStudio) -> None:
    first, _, _ = ready_studio(playout)
    playout.command("queue_add", clip_ids=[first])
    playout.command("take", cue_if_needed=True)
    play_through(playout)

    playout.command("replay_last")

    assert playout.state("preview")["clip"]["id"] == first


@pytest.mark.parametrize(
    ("post_clip", "expected_scene"),
    [("return", "Main"), ("hold", "Playback A"), ("fade", "Main")],
)
def test_skip_ends_the_clip_on_air_now_and_applies_the_post_clip_setting(
    playout: PlayoutStudio, post_clip: str, expected_scene: str
) -> None:
    # "Skip" is the operator saying "this clip is dragging, get me out": the
    # clip on air ends at once, exactly as if it had reached its last frame.
    first, _, _ = ready_studio(playout)
    playout.connect_obs(post_clip=post_clip, fade_ms=500)
    playout.command("queue_add", clip_ids=[first])
    playout.command("take", cue_if_needed=True)

    playout.command("skip")

    assert playout.obs.program_scene == expected_scene
    assert playout.state("program") is None
    # The source is paused, so "hold" keeps a still frame instead of playing on.
    assert playout.obs.media_of("baja_program_a").state == MediaState.PAUSED
    # The aired log records the clip as ended now, long before its natural end.
    (aired,) = playout.command("export_aired")["entries"]
    assert aired["ended_at"] is not None


def test_with_auto_play_next_on_skip_puts_the_next_clip_on_air(playout: PlayoutStudio) -> None:
    # Skip is "as if the clip had finished", and a finished clip with
    # auto-play on is followed by the next one: skipping a dull pass brings
    # the next pass, not the return scene.
    first, second, third = ready_studio(playout)
    playout.command("set_auto_play_next", enabled=True)
    playout.command("queue_add", clip_ids=[first, second, third])
    playout.command("take", cue_if_needed=True)
    playout.command("cue_next")

    playout.command("skip")

    assert playout.state("program")["clip"]["id"] == second
    assert playout.obs.program_scene == "Playback B"
    # The skipped clip's source is off air now, so it is stopped like after any take.
    assert playout.obs.media_of("baja_program_a").state == MediaState.STOPPED

    # With nothing cued, the head of the queue is cued and played the same way.
    playout.command("skip")
    assert playout.state("program")["clip"]["id"] == third

    # And with nothing left to play, post-clip applies.
    playout.command("skip")
    assert playout.state("program") is None
    assert playout.obs.program_scene == "Main"


def test_skip_with_nothing_on_air_is_refused(playout: PlayoutStudio) -> None:
    with pytest.raises(CommandError, match="nothing is on air"):
        playout.command("skip")


def test_drop_next_drops_the_cued_clip_and_cues_the_next_one(playout: PlayoutStudio) -> None:
    first, second, _ = ready_studio(playout)
    playout.command("queue_add", clip_ids=[first, second])
    playout.command("cue_next")

    playout.command("drop_next")

    assert playout.state("preview")["clip"]["id"] == second
    assert playout.obs.program_scene == "Main"  # drop_next never changes program


def test_drop_next_without_a_cue_drops_the_head_of_the_queue(playout: PlayoutStudio) -> None:
    first, second, third = ready_studio(playout)
    playout.command("queue_add", clip_ids=[first, second, third])
    playout.command("take", cue_if_needed=True)

    playout.command("drop_next")

    # The second clip is gone; the third is cued; the first is still on air.
    assert playout.state("queue") == []
    assert playout.state("preview")["clip"]["id"] == third
    assert playout.state("program")["clip"]["id"] == first


def test_drop_next_with_nothing_queued_is_refused(playout: PlayoutStudio) -> None:
    with pytest.raises(CommandError, match="nothing to drop"):
        playout.command("drop_next")


def test_return_takes_the_clip_off_air_at_once(playout: PlayoutStudio) -> None:
    first, _, _ = ready_studio(playout)
    playout.command("queue_add", clip_ids=[first])
    playout.command("take", cue_if_needed=True)

    playout.command("return_to_scene")

    assert playout.obs.program_scene == "Main"
    assert playout.state("program") is None
    assert playout.obs.media_of("baja_program_a").state == MediaState.STOPPED


# --- take confirmation ------------------------------------------------------------------


def test_with_confirmation_on_a_second_take_within_three_seconds_fires(
    playout: PlayoutStudio,
) -> None:
    first, _, _ = ready_studio(playout)
    playout.configure(operators={"require_take_confirmation": True})
    playout.command("queue_add", clip_ids=[first])
    playout.command("cue_next", source="web:alice")

    # Bob's first press only arms the take; nothing goes to air.
    assert playout.command("take", source="web:bob")["status"] == "confirm"
    assert playout.state("take_confirmation")["requested_by"] == "web:bob"
    assert playout.obs.program_scene == "Main"

    # A second press inside the window fires it.
    playout.clock.advance(2.0)
    assert playout.command("take", source="web:bob")["status"] == "on_air"
    assert playout.state("take_confirmation") is None


def test_with_confirmation_on_a_late_second_take_only_arms_again(playout: PlayoutStudio) -> None:
    first, _, _ = ready_studio(playout)
    playout.configure(operators={"require_take_confirmation": True})
    playout.command("queue_add", clip_ids=[first])
    playout.command("cue_next")
    playout.command("take")

    # The window passes: the prompt disappears by itself.
    playout.clock.advance(3.5)
    assert playout.state("take_confirmation") is None

    assert playout.command("take")["status"] == "confirm"
    assert playout.obs.program_scene == "Main"


# --- aired log and hooks --------------------------------------------------------------------


def test_airing_a_clip_counts_it_played_logs_it_and_posts_the_webhook(
    playout: PlayoutStudio,
) -> None:
    first, _, _ = ready_studio(playout)
    playout.configure(hooks={"on_air_webhook_url": HOOK_URL})
    playout.command("queue_add", clip_ids=[first])
    playout.command("take", cue_if_needed=True)
    play_through(playout)

    clip = playout.state(f"library.{first}")
    assert clip["played_count"] == 1
    # Both posts arrive (the hooks worker is woken for each one).
    playout.soon(lambda: len(playout.hook_posts) == 2)
    on_air, off_air = (post["body"] for post in playout.hook_posts)
    assert on_air["clip"]["id"] == first and "started_at" in on_air
    assert off_air["clip"]["id"] == first and "ended_at" in off_air
    assert "file_path" not in on_air["clip"]

    exported = playout.command("export_aired")
    assert [entry["clip_id"] for entry in exported["entries"]] == [first]
    assert exported["entries"][0]["ended_at"] is not None


def test_mark_stream_start_gives_the_export_offsets_and_chapters(playout: PlayoutStudio) -> None:
    first, second, _ = ready_studio(playout)
    playout.command("mark_stream_start")
    playout.command("queue_add", clip_ids=[first, second])
    playout.clock.advance(65.0)
    playout.command("take", cue_if_needed=True)
    play_through(playout)
    playout.command("take", cue_if_needed=True)

    exported = playout.command("export_aired")

    assert exported["stream_start_marker"] is not None
    offsets = [entry["offset"] for entry in exported["entries"]]
    assert offsets[0] == "1:05"
    assert exported["chapters"].splitlines()[0] == "0:00 Stream start"
    assert playout.state("export.stream_start_marker") == exported["stream_start_marker"]


# --- persistence ----------------------------------------------------------------------------


def test_queue_cue_and_program_survive_a_module_restart(playout: PlayoutStudio) -> None:
    first, second, third = ready_studio(playout)
    playout.command("queue_add", clip_ids=[first, second, third])
    playout.command("take", cue_if_needed=True)
    playout.command("cue_next")

    # Turning the module off and on is a restart as far as playout knows.
    playout.configure(enabled=False)
    playout.configure(enabled=True)
    eventually(lambda: playout.state("obs.connection") == "connected")

    assert playout.state("program")["clip"]["id"] == first
    assert playout.state("preview")["clip"]["id"] == second
    assert [entry["clip_id"] for entry in playout.state("queue")] == [third]


# --- tools ----------------------------------------------------------------------------------


def test_missing_ffprobe_shows_one_banner_and_a_fixed_path_clears_it(
    playout: PlayoutStudio,
) -> None:
    # Regression, bench 2026-11-09: ffprobe was not on PATH, and the page
    # showed dashes and grey boxes without saying why.
    playout.tools_found.discard("ffprobe")
    playout.configure(tools={"ffprobe_path": "ffprobe-missing", "ffmpeg_path": "ffmpeg"})

    assert playout.state("tools.problem") == (
        "durations and thumbnails unavailable — ffprobe not found; "
        "run the installer again, or set the path in Playout → Settings"
    )

    playout.tools_found.add("ffprobe")
    playout.configure(tools={"ffprobe_path": "ffprobe", "ffmpeg_path": "ffmpeg"})

    assert playout.state("tools.problem") is None
    assert playout.state("tools.ffprobe") == "/opt/ffmpeg/ffprobe"


@pytest.mark.parametrize("bare_names", [True, False], ids=["bare names", "blank fields"])
def test_with_the_bundled_copies_installed_there_is_no_banner(
    playout: PlayoutStudio,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    bare_names: bool,
) -> None:
    # Regression, v0.2.0 bench (2026-11-09): with the bundled copies installed
    # and nothing on PATH, the banner said "not found" (it asked PATH alone)
    # while the scanner and the readiness check used the bundled copies.
    # Bare names and blank fields both mean "the bundled copy".
    playout.tools_found.clear()
    bundled = tmp_path / "program" / "ffmpeg"
    bundled.mkdir(parents=True)
    for name in ("ffprobe", "ffmpeg"):
        (bundled / f"{name}.exe").write_bytes(b"")
    # First a typed path that does not exist: the banner shows.
    missing = str(tmp_path / "nowhere" / "ffprobe.exe")
    playout.configure(tools={"ffprobe_path": missing, "ffmpeg_path": missing})
    assert playout.state("tools.problem") is not None

    # The installer announces its folder; the fields go back to automatic.
    monkeypatch.setenv(INSTALL_DIRECTORY_ENVIRONMENT_VARIABLE, str(tmp_path / "program"))
    automatic = {"ffprobe_path": "ffprobe", "ffmpeg_path": "ffmpeg"} if bare_names else {}
    playout.configure(tools=automatic)

    # The banner, the readiness line and the prober all see the same copies.
    assert playout.state("tools.problem") is None
    assert playout.state("tools.ffprobe") == str(bundled / "ffprobe.exe")
    assert playout.state("tools.sources") == {"ffprobe": "bundled", "ffmpeg": "bundled"}
    tools_check = run_module_checks(playout.services, "playout", playout.clock)["tools"]
    assert tools_check.detail.startswith(f"ffprobe: bundled ({bundled / 'ffprobe.exe'})")
    assert playout.module.resolved_tools().ffmpeg.command == str(bundled / "ffmpeg.exe")


# --- rescan ---------------------------------------------------------------------------------


def test_rescan_forgets_durations_and_thumbnails_and_measures_every_file_again(
    playout: PlayoutStudio,
) -> None:
    # Two files scanned, measured and given a thumbnail.
    ready_studio(playout, ("a.mp4", "b.mp4"))
    thumbnail_folder = playout.prober.thumbnails[0].parent
    # A thumbnail left over from a clip that no longer exists: nothing would
    # ever make it again, so it shows that the cache was emptied.
    stale = thumbnail_folder / "99-0.jpg"
    stale.write_bytes(b"old")
    probes_before = len(playout.prober.probed)

    result = playout.command("rescan_library")

    # At once: both files are waiting to be measured again (the settings page
    # shows `library_summary.pending_probe` counting down from this as the
    # rescan's progress), and the cache is emptied.
    assert result == {"files": 2}
    assert not stale.exists()
    # Then the prober measures both again and makes their thumbnails again,
    # and the count goes back to 0.
    playout.soon(lambda: playout.state("library_summary.pending_probe") == 0)
    assert len(playout.prober.probed) == probes_before + 2
    assert len(playout.prober.thumbnails) == 4
    assert all(clip["has_thumbnail"] and clip["duration_s"] for clip in playout.clips())
    assert ("web:alice", "rescan_library", "ok") in playout.log()


def test_a_clip_s_frame_rate_comes_from_ffprobe(playout: PlayoutStudio) -> None:
    # The fake prober says 25 fps (one frame is 40 ms).
    ready_studio(playout, ("a.mp4",))

    assert playout.clip("a.mp4")["fps"] == CLIP_FPS  # type: ignore[index]
