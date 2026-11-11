"""Tests for the rig's demo clip library (rig/clips.py).

The library is made with ffmpeg the first time the rig runs. These tests
check it is made only once, that half-made files from an interrupted run
are cleaned up, that a missing ffmpeg is said plainly, and (when ffmpeg is
installed on this machine) that the clips have the length asked for.
"""

from pathlib import Path

import pytest

from baja_studio.playout.probe import MediaProber
from baja_studio.rig.clips import DEFAULT_CLIPS, DemoClip, ffmpeg_command, find_tools
from baja_studio.rig.clips import make_clip_library as make

TOOLS = find_tools()
needs_ffmpeg = pytest.mark.skipif(
    TOOLS.ffmpeg.found_at is None or TOOLS.ffprobe.found_at is None,
    reason="ffmpeg and ffprobe are not installed on this machine",
)
SHORT_PLAN = (
    DemoClip("delivery/enduro/42/1.mp4", 2, tone_hz=440),
    DemoClip("patrocinadores/vertical.mp4", 3, vertical=True),
)


def test_the_default_library_is_twelve_clips_of_five_to_forty_seconds() -> None:
    assert len(DEFAULT_CLIPS) == 12
    assert all(5 <= clip.duration_s <= 40 for clip in DEFAULT_CLIPS)
    # A few vertical, some with a tone, and one delivered file the manifest cannot match.
    assert any(clip.vertical for clip in DEFAULT_CLIPS)
    assert any(clip.tone_hz for clip in DEFAULT_CLIPS)
    assert any(clip.relative_path.endswith("extra-take.mp4") for clip in DEFAULT_CLIPS)


def test_the_command_adds_a_tone_only_to_clips_that_have_one() -> None:
    with_tone = ffmpeg_command("ffmpeg", SHORT_PLAN[0], Path("out.mp4"))
    silent = ffmpeg_command("ffmpeg", SHORT_PLAN[1], Path("out.mp4"))
    assert any("sine=frequency=440" in part for part in with_tone)
    assert not any("sine" in part for part in silent)
    assert any("size=360x640" in part for part in silent)


def test_without_ffmpeg_nothing_is_made_and_the_message_says_how_to_get_it(tmp_path: Path) -> None:
    messages: list[str] = []
    made = make(tmp_path, None, messages.append, SHORT_PLAN)
    assert made == []
    assert len(messages) == 1 and "ffmpeg not found" in messages[0]
    # The folders exist anyway, so the studio's folder checks still pass.
    assert (tmp_path / "delivery").is_dir() and (tmp_path / "patrocinadores").is_dir()


def test_clips_already_there_are_not_made_again(tmp_path: Path) -> None:
    # Both clips already exist (their content does not matter here).
    for clip in SHORT_PLAN:
        target = tmp_path / clip.relative_path
        target.parent.mkdir(parents=True)
        target.write_bytes(b"already made")
    messages: list[str] = []

    # Even with a made-up ffmpeg that could not run, nothing is attempted.
    assert make(tmp_path, "no-such-ffmpeg", messages.append, SHORT_PLAN) == []
    assert messages == []


def test_a_half_made_clip_from_an_interrupted_run_is_removed(tmp_path: Path) -> None:
    leftover = tmp_path / "delivery" / "enduro" / "42" / "1.mp4.part"
    leftover.parent.mkdir(parents=True)
    leftover.write_bytes(b"half")
    make(tmp_path, None, lambda message: None, SHORT_PLAN)
    assert not leftover.exists()


@needs_ffmpeg
def test_clips_are_made_with_the_length_asked_for_and_only_once(tmp_path: Path) -> None:
    assert TOOLS.ffmpeg.found_at is not None and TOOLS.ffprobe.found_at is not None
    messages: list[str] = []

    made = make(tmp_path, TOOLS.ffmpeg.found_at, messages.append, SHORT_PLAN)

    assert [path.relative_to(tmp_path).as_posix() for path in made] == [
        clip.relative_path for clip in SHORT_PLAN
    ]
    prober = MediaProber(TOOLS.ffprobe.found_at, TOOLS.ffmpeg.found_at)
    for clip in SHORT_PLAN:
        duration = prober.probe(str(tmp_path / clip.relative_path)).duration_s
        assert duration == pytest.approx(clip.duration_s, abs=0.2)
    # A second run finds everything there and makes nothing.
    assert make(tmp_path, TOOLS.ffmpeg.found_at, messages.append, SHORT_PLAN) == []
