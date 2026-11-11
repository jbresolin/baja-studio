"""Tests for flash-free program switching (playout/switcher.py).

The switcher drives a real `ObsWebsocketClient`, connected to the fake OBS
through the in-process network: every message goes through the real
obs-websocket protocol, but no port is opened. The fake OBS is set up as the
bench guide says: scenes "Main", "Playback A" (source baja_program_a) and
"Playback B" (source baja_program_b), with "Main" on program.

The fake records every request it answered (`fake.requests`), which is how
the tests check what the switcher did and, as importantly, did not do.
"""

import threading
from collections.abc import Iterator
from typing import Any

import pytest

from baja_studio.adapters.obs_protocol import MediaState
from baja_studio.adapters.obs_transport import ObsAddress
from baja_studio.adapters.obs_websocket import ObsWebsocketClient
from baja_studio.fakes.fake_obs import FakeObs
from baja_studio.fakes.fake_obs_in_process import InProcessObsNetwork
from baja_studio.playout.config import ObsSettings
from baja_studio.playout.switcher import CueError, ProgramSwitcher, SwitchError
from tests.conftest import FakeClock, eventually

HOST, PORT = "obs.test", 4455


def clip(clip_id: int) -> dict[str, Any]:
    """A clip as the library would hand it over (only the fields switching uses)."""
    return {"id": clip_id, "title": f"clip {clip_id}", "file_path": f"/clips/{clip_id}.mp4"}


class Rig:
    """The fake OBS, a connected client, and a switcher with editable settings."""

    def __init__(self, clock: FakeClock) -> None:
        self.clock = clock
        self.fake = FakeObs.with_playout_scenes(clock)
        self.network = InProcessObsNetwork()
        self.network.plug(HOST, PORT, self.fake)
        self.client = ObsWebsocketClient(clock, self.network.connect)
        self.client.connect(ObsAddress(HOST, PORT), "")
        self.settings = ObsSettings(host=HOST)
        self.switcher = ProgramSwitcher(self.client, lambda: self.settings, clock)

    def requests_since(self, start: int) -> list[tuple[str, dict[str, Any]]]:
        """The requests the fake answered after the first `start` ones."""
        return [(record.request_type, record.data) for record in self.fake.requests[start:]]

    def media(self, source: str) -> Any:
        return self.fake.media_of(source)

    def override_of(self, scene: str) -> tuple[str | None, int | None] | None:
        """The transition override the fake holds for `scene`, if any."""
        return self.fake.transition_overrides.get(scene)


@pytest.fixture
def rig(fake_clock: FakeClock) -> Iterator[Rig]:
    rig = Rig(fake_clock)
    yield rig
    rig.client.close()


def test_the_first_cue_loads_the_clip_into_source_a_parked_on_its_first_frame(rig: Rig) -> None:
    # Nothing is on air yet ("Main" is on program).
    cued = rig.switcher.cue(clip(1))

    # The clip went into slot a: loaded, paused, and at position 0, so a take
    # shows its first frame. Program did not change.
    media = rig.media("baja_program_a")
    assert cued.slot == "a"
    assert media.file == "/clips/1.mp4"
    assert media.state == MediaState.PAUSED
    assert media.cursor(rig.clock.monotonic()) == 0.0
    assert rig.fake.program_scene == "Main"
    # The source is set to play once and keep its last frame.
    settings = rig.fake.inputs["baja_program_a"].settings
    assert settings["looping"] is False
    assert settings["clear_on_media_end"] is False


def test_a_cue_waits_for_obs_to_start_the_source_before_pausing_and_rewinding(rig: Rig) -> None:
    # Regression, bench 2026-11-08: OBS refused SetMediaInputCursor ("media must
    # be playing or paused") because the cue rewound a source that had not
    # started yet, and only the second take worked. The fake now behaves like
    # OBS: a loaded or restarted file is "opening" until OBS starts it, and
    # seeking it before then is refused.
    rig.switcher.cue(clip(1))

    # The order of what was asked (parking.py): read what the source holds,
    # load the file, ask the status until OBS's start event came, pause and
    # confirm it, rewind and read the position back.
    asked = [
        (kind, data.get("mediaAction", ""))
        for kind, data in rig.requests_since(0)
        if kind != "SetSceneSceneTransitionOverride"
    ]
    assert asked == [
        ("GetInputSettings", ""),
        ("SetInputSettings", ""),
        ("GetMediaInputStatus", ""),
        ("TriggerMediaInputAction", "OBS_WEBSOCKET_MEDIA_INPUT_ACTION_PAUSE"),
        ("GetMediaInputStatus", ""),
        ("SetMediaInputCursor", ""),
        ("GetMediaInputStatus", ""),
    ]
    # And the clip sits on its first frame, ready for a take.
    assert rig.media("baja_program_a").state == MediaState.PAUSED
    assert rig.media("baja_program_a").cursor(rig.clock.monotonic()) == 0.0


def test_a_cue_whose_source_never_starts_fails_after_three_seconds_naming_its_state(
    rig: Rig,
) -> None:
    # This file never finishes opening in the fake (a file OBS cannot start).
    rig.fake.stuck_files.add("/clips/1.mp4")
    failures: list[Exception] = []

    def cue_in_background() -> None:
        try:
            rig.switcher.cue(clip(1))
        except Exception as error:
            failures.append(error)

    def waiting_between_polls() -> bool:
        # Asleep in the 0.1 s wait between two status questions (not inside a
        # request, which waits up to 2 s for OBS's answer).
        wake_ups = rig.clock.wake_up_times()
        return bool(wake_ups) and wake_ups[0] <= rig.clock.monotonic() + 0.1 + 1e-6

    # The cue waits on the fake clock, so it runs on its own thread while the
    # test moves time in 0.1 s steps.
    worker = threading.Thread(target=cue_in_background)
    worker.start()
    for _ in range(40):
        eventually(lambda: waiting_between_polls() or not worker.is_alive())
        if not worker.is_alive():
            break
        rig.clock.advance(0.1)
    worker.join(timeout=5)

    # It gave up after about 3 s, saying what OBS reported, and nothing is cued.
    assert len(failures) == 1
    assert isinstance(failures[0], CueError)
    assert "did not start" in str(failures[0])
    assert "OBS_MEDIA_STATE_OPENING" in str(failures[0])
    assert rig.clock.monotonic() == pytest.approx(3.0, abs=0.15)
    assert rig.switcher.cued is None


def test_cueing_while_a_clip_is_on_air_never_touches_the_source_on_air(rig: Rig) -> None:
    # Clip 1 is on air in slot a, playing.
    rig.switcher.cue(clip(1))
    rig.switcher.take()
    before = len(rig.fake.requests)

    # Cue the next clip while clip 1 plays.
    cued = rig.switcher.cue(clip(2))

    # It went into slot b. Not one request named slot a's source or scene, and
    # program was not changed: nothing can flash on the output.
    assert cued.slot == "b"
    for request_type, data in rig.requests_since(before):
        assert request_type != "SetCurrentProgramScene"
        assert "baja_program_a" not in data.values()
        assert "Playback A" not in data.values()
    assert rig.media("baja_program_a").state == MediaState.PLAYING
    assert rig.media("baja_program_a").file == "/clips/1.mp4"


def test_a_take_is_one_scene_change_that_plays_the_cued_clip_and_stops_the_old_one(
    rig: Rig,
) -> None:
    # Clip 1 on air in slot a, clip 2 cued in slot b.
    rig.switcher.cue(clip(1))
    rig.switcher.take()
    rig.switcher.cue(clip(2))
    before = len(rig.fake.requests)

    on_air, previous = rig.switcher.take()

    # Exactly one program change, to Playback B; no file was loaded during the take.
    requests = rig.requests_since(before)
    scene_changes = [data for kind, data in requests if kind == "SetCurrentProgramScene"]
    assert scene_changes == [{"sceneName": "Playback B"}]
    assert not any(kind == "SetInputSettings" for kind, _ in requests)
    # Clip 2 plays on air; clip 1, now off air, was stopped.
    assert rig.fake.program_scene == "Playback B"
    assert rig.media("baja_program_b").state == MediaState.PLAYING
    assert rig.media("baja_program_a").state == MediaState.STOPPED
    assert (on_air.clip["id"], on_air.slot) == (2, "b")
    assert previous is not None and previous.clip["id"] == 1
    assert rig.switcher.cued is None


def test_a_take_without_a_cue_is_refused(rig: Rig) -> None:
    with pytest.raises(SwitchError, match="nothing is cued"):
        rig.switcher.take()
    # And OBS was not asked to do anything.
    assert rig.fake.requests_of("SetCurrentProgramScene") == []


def test_clips_alternate_between_the_two_slots(rig: Rig) -> None:
    slots = []
    for clip_id in (1, 2, 3):
        slots.append(rig.switcher.cue(clip(clip_id)).slot)
        rig.switcher.take()

    assert slots == ["a", "b", "a"]


def test_after_a_clip_left_the_air_the_next_cue_avoids_its_slot(rig: Rig) -> None:
    # Clip 1 aired in slot a and ended; nothing is on air now.
    rig.switcher.cue(clip(1))
    rig.switcher.take()
    rig.switcher.end_program()

    assert rig.switcher.cue(clip(2)).slot == "b"


def test_preparing_the_scenes_makes_transitions_into_them_cuts(rig: Rig) -> None:
    # Whatever transition the operator picked, a take must be a cut, or a
    # crossfade would show the previous clip.
    rig.switcher.prepare_scenes()

    assert rig.fake.transition_overrides == {
        "Playback A": ("Cut", None),
        "Playback B": ("Cut", None),
    }


def test_returning_with_a_cut_switches_to_the_return_scene(rig: Rig) -> None:
    rig.switcher.go_to_scene("Main", fade=False)

    assert rig.fake.program_scene == "Main"
    assert "Main" not in rig.fake.transition_overrides


def test_returning_with_a_fade_uses_a_temporary_override_on_the_return_scene(rig: Rig) -> None:
    rig.settings = ObsSettings(host=HOST, fade_ms=1500)

    rig.switcher.go_to_scene("Main", fade=True)

    # During the fade, transitions into Main are a 1.5 s fade.
    assert rig.fake.program_scene == "Main"
    assert rig.override_of("Main") == ("Fade", 1500)
    # Once the fade is over (plus a margin), the override is cleared again so
    # the operator's own switches to Main behave as before.
    rig.clock.advance(1.5)
    assert rig.override_of("Main") == ("Fade", 1500)
    rig.clock.advance(1.0)
    assert rig.override_of("Main") == (None, None)


def test_only_the_two_playback_scenes_count_as_playback_scenes(rig: Rig) -> None:
    # The return target is never a playback scene; this is how it is told apart.
    assert rig.switcher.is_playback_scene("Playback A") is True
    assert rig.switcher.is_playback_scene("Playback B") is True
    assert rig.switcher.is_playback_scene("Main") is False
    assert rig.switcher.is_playback_scene(None) is False


def test_scene_events_we_caused_are_recognised_in_order(rig: Rig) -> None:
    # Two quick takes: A then B. OBS will report "A" and then "B", and the "A"
    # report may arrive after the take to B was already made.
    rig.switcher.cue(clip(1))
    rig.switcher.take()
    rig.switcher.cue(clip(2))
    rig.switcher.take()

    # Both echoes are ours, so neither reads as "the operator cut away".
    assert rig.switcher.is_our_scene_change("Playback A") is True
    assert rig.switcher.is_our_scene_change("Playback B") is True
    # Each echo is consumed once: a later change to A is the operator's.
    assert rig.switcher.is_our_scene_change("Playback A") is False
    # A scene we never asked for is always the operator's.
    assert rig.switcher.is_our_scene_change("Main") is False


def test_a_lost_echo_is_skipped_when_a_later_one_arrives(rig: Rig) -> None:
    # We asked for A and then Main; only the Main report arrives.
    rig.switcher.cue(clip(1))
    rig.switcher.take()
    rig.switcher.go_to_scene("Main", fade=False)

    assert rig.switcher.is_our_scene_change("Main") is True
    # The forgotten "A" was dropped with it, so a real cut to A is not swallowed.
    assert rig.switcher.is_our_scene_change("Playback A") is False


def test_the_program_status_reads_the_source_on_air(rig: Rig) -> None:
    assert rig.switcher.program_status() is None

    # The fake plays every file for 10 s; three seconds after the take...
    rig.switcher.cue(clip(1))
    rig.switcher.take()
    rig.clock.advance(3.0)

    status = rig.switcher.program_status()

    assert status is not None
    assert status.state == MediaState.PLAYING
    assert status.cursor_s == pytest.approx(3.0)
    assert status.duration_s == pytest.approx(10.0)


def test_discarding_the_cue_stops_its_off_air_source(rig: Rig) -> None:
    rig.switcher.cue(clip(1))

    dropped = rig.switcher.discard_cue()

    assert dropped is not None and dropped.clip["id"] == 1
    assert rig.switcher.cued is None
    assert rig.media("baja_program_a").state == MediaState.STOPPED
