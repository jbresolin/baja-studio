"""Slow-motion replays on air against an OBS whose timing varies (modules/playout/transport.py).

The WP5c bench on OBS 32 and 33: speed changes on air sometimes worked,
sometimes changed nothing, went black or restarted the clip from 0. Part of
it was timing races; the rest was what the OBS probe found on the studio
PC (LIVESTACK-56): a seek lands on the keyframe at or before the point asked
for, and a file loaded into a stopped source never starts without PLAY.

These tests run the same changes against the fake OBS, which now behaves
like that, with random delays drawn from 200 seeds
(`tests/playout/obs_simulation.py`): files take 50 to 500 ms to show a first
frame, a seek before that frame is ignored, seeks land up to 100 ms late,
and events arrive up to 150 ms late, interleaved between the two sources.
Keyframes are every 0.5 s, as the pipeline renders them since LIVESTACK-56.

For each seed the clip on air (at 5 s) goes 100 → 50 → 100 → 25 %, playing
a second between changes:

- 100 → 50 is a *replay*: it jumps back 3 s, so it must land on the keyframe
  at or before that point;
- 50 → 100 *continues*: it lands on the keyframe at or before where the
  clip was, plus the half-second lead, so never more than half a second
  from where it was;
- 100 → 25 is a replay again.

After each change we check what an operator would see: program plays at the
new speed, never showed black (the change is made in the other source, and
only cut to once it plays), and the clip is in the right place. Positions
allow `SETTLE` on top: the new source plays a moment (slowly) while its seek
settles, before the cut.
"""

import pytest

from baja_studio.playout.switcher import SwitchError
from tests.playout.obs_simulation import SimulatedStudio, simulated_studio

SEEDS = range(200)
SPEED_CHANGES = (50, 100, 25)
KEYFRAME_INTERVAL = 0.5
JUMP_BACK = 3.0
# How far a source plays (in its own seconds) between landing and the cut, at most.
SETTLE = 0.35
# One frame of the simulated clip (25 fps).
ONE_FRAME = 1 / 25
PLAY_BETWEEN_CHANGES_SECONDS = 1.0


def keyframe_at_or_before(position_s: float) -> float:
    """Where OBS lands when asked for `position_s` (a keyframe every 0.5 s)."""
    return int(position_s / KEYFRAME_INTERVAL + 1e-9) * KEYFRAME_INTERVAL


def allowed_window(old_speed: int, target: int, position_before: float) -> tuple[float, float]:
    """Where program may be right after the change: (lowest, highest) in the clip's seconds."""
    if old_speed == 100 and target < 100:
        landing = keyframe_at_or_before(max(0.0, position_before - JUMP_BACK))
        return landing, landing + SETTLE
    # Continue: aimed half a second (of air) ahead; the clip on air moved meanwhile.
    return position_before - KEYFRAME_INTERVAL, position_before + 0.5 + 0.5 + SETTLE


def check_one_change(studio: SimulatedStudio, old_speed: int, target: int) -> str | None:
    """Make one speed change on air; returns what went wrong, or None."""
    clock, timeline = studio.clock, studio.fake.timeline
    position_before = studio.program_cursor()
    black_before = len(timeline.black_on_program)
    try:
        studio.transport.set_program_speed(target, "test")
    except SwitchError as error:
        return f"refused: {error}"
    media, program = studio.program_media(), studio.switcher.program
    problems = []
    if program is None or studio.switcher.slot(program.slot).scene != studio.fake.program_scene:
        problems.append("playout lost track of which source is on air")
    if media.speed != target / 100:
        problems.append(f"program plays at {media.speed * 100:.0f} %")
    if not studio.is_playing():
        problems.append(f"program is {media.state} (frame: {media.has_frame})")
    lowest, highest = allowed_window(old_speed, target, position_before)
    if not lowest - 0.001 <= studio.program_cursor() <= highest:
        problems.append(
            f"at {studio.program_cursor():.3f} s, not between {lowest:.3f} and {highest:.3f} s"
        )
    if len(timeline.black_on_program) > black_before:
        problems.append(f"black on program at {timeline.black_on_program[black_before][0]:.3f} s")
    if problems:
        return ", ".join(problems)
    # And it keeps playing at that speed.
    position, black_before = studio.program_cursor(), len(timeline.black_on_program)
    clock.let_time_pass(PLAY_BETWEEN_CHANGES_SECONDS)
    moved = studio.program_cursor() - position
    if abs(moved - target / 100 * PLAY_BETWEEN_CHANGES_SECONDS) > ONE_FRAME:
        return f"then moved {moved:.3f} s in {PLAY_BETWEEN_CHANGES_SECONDS} s at {target} %"
    if len(timeline.black_on_program) > black_before:
        return "then went black"
    return None


def first_problem(seed: int) -> str | None:
    """Run 100 → 50 → 100 → 25 with one seed; the first thing that went wrong, if any."""
    with simulated_studio(seed) as studio:
        studio.clock.let_time_pass(5.0)
        speed = 100
        for target in SPEED_CHANGES:
            problem = check_one_change(studio, speed, target)
            if problem is not None:
                return f"seed {seed}, {speed} → {target} %: {problem}"
            speed = target
    return None


def test_replays_and_continues_on_air_are_right_for_every_seed() -> None:
    problems = [problem for seed in SEEDS if (problem := first_problem(seed))]

    shown = "\n".join(problems[:10])
    assert not problems, f"{len(problems)} of {len(SEEDS)} seeds went wrong:\n{shown}"


@pytest.mark.parametrize("seed", [0, 1, 2])
def test_a_replay_of_an_old_clip_with_far_keyframes_still_plays(seed: int) -> None:
    # Clips rendered before LIVESTACK-56: a keyframe every 8.333 s. From 5 s,
    # 3 s back is 2 s; OBS can only go to 0. The replay plays from there.
    with simulated_studio(seed, keyframe_interval_s=8.333) as studio:
        studio.clock.let_time_pass(5.0)

        studio.transport.set_program_speed(50, "test")

        assert studio.program_cursor() <= SETTLE
        assert studio.is_playing()
        assert studio.program_media().speed == 0.5
        assert studio.fake.timeline.black_on_program == []
