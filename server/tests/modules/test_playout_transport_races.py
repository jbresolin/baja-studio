"""Live speed changes against an OBS whose timing varies (modules/playout/transport.py).

The WP5c bench on OBS 32 (swap mode): a speed change on air sometimes
worked, sometimes changed nothing, sometimes went black, sometimes restarted
the clip from 0, and every one was logged as a success. Those are timing
races, so these tests run the same changes against the fake OBS with random
delays drawn from 200 seeds (`tests/playout/obs_simulation.py`): files take
50 to 500 ms to show a first frame (status already says "playing" before it),
a seek before that frame is ignored, seeks land up to 100 ms late, and
events arrive up to 150 ms late, interleaved between the two sources.

For each seed the clip on air goes 100 → 50 → 100 → 50 %, playing a second
between changes. After each change we check what an operator would see:
program plays at the new speed, it never showed black (swap: the whole
point of the double buffer), and the clip is where it should be: where it
was before, plus the time that passed at the old speed until the switch and
at the new speed after it, within one frame.
"""

import pytest

from baja_studio.playout.switcher import SwitchError
from tests.playout.obs_simulation import SimulatedStudio, simulated_studio

SEEDS = range(200)
# From 100 % (the clip was taken at normal speed).
SPEED_CHANGES = (50, 100, 50)
# The simulated clip's frame rate: one frame is 40 ms.
ONE_FRAME = 1 / 25
PLAY_BETWEEN_CHANGES_SECONDS = 1.0


def check_one_change(studio: SimulatedStudio, target: int, method: str) -> str | None:
    """Make one speed change on air; returns what went wrong, or None."""
    clock, timeline = studio.clock, studio.fake.timeline
    old_speed = studio.program_media().speed
    started_at, position_before = clock.monotonic(), studio.program_cursor()
    cuts_before, black_before = len(timeline.program_cuts), len(timeline.black_on_program)
    try:
        studio.transport.set_program_speed(target, "test")
    except SwitchError as error:
        return f"refused: {error}"
    finished_at = clock.monotonic()
    # When program switched to the new speed: the cut (swap), else the reload request.
    cuts = timeline.program_cuts[cuts_before:]
    switched_at = cuts[-1][0] if cuts else started_at
    expected = (
        position_before
        + old_speed * (switched_at - started_at)
        + target / 100 * (finished_at - switched_at)
    )
    media, program = studio.program_media(), studio.switcher.program
    problems = []
    if program is None or studio.switcher.slot(program.slot).scene != studio.fake.program_scene:
        problems.append("playout lost track of which source is on air")
    if media.speed != target / 100:
        problems.append(f"program plays at {media.speed * 100:.0f} %")
    if not studio.is_playing():
        problems.append(f"program is {media.state} (frame: {media.has_frame})")
    if abs(studio.program_cursor() - expected) > ONE_FRAME:
        problems.append(f"at {studio.program_cursor():.3f} s instead of {expected:.3f} s")
    if method == "swap" and len(timeline.black_on_program) > black_before:
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


def first_problem(seed: int, method: str) -> str | None:
    """Run 100 → 50 → 100 → 50 with one seed; the first thing that went wrong, if any."""
    with simulated_studio(seed, method) as studio:
        studio.clock.let_time_pass(PLAY_BETWEEN_CHANGES_SECONDS)
        speed = 100
        for target in SPEED_CHANGES:
            problem = check_one_change(studio, target, method)
            if problem is not None:
                return f"seed {seed}, {speed} → {target} %: {problem}"
            speed = target
    return None


@pytest.mark.parametrize("method", ["swap", "reload"])
def test_live_speed_changes_are_right_for_every_seed(method: str) -> None:
    # Reload changes the source on air, so OBS reopens the file there and
    # program is black until its first frame: black is checked for swap only.
    problems = [problem for seed in SEEDS if (problem := first_problem(seed, method))]

    shown = "\n".join(problems[:10])
    assert not problems, f"{len(problems)} of {len(SEEDS)} seeds went wrong ({method}):\n{shown}"
