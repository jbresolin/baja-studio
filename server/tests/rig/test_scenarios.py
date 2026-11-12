"""Tests for the rig's scenarios (rig/scenarios.py and rig/scenarios/*.json).

A scenario is a list of timed steps against the fakes. The runner plays it
on a `Clock`; here that is the fake clock, so "30 seconds later" happens the
moment the test moves time, and the test can see exactly when each step ran.
The steps are carried out by a recording stand-in instead of the real fakes.
"""

import itertools
import threading
from dataclasses import dataclass, field

import pytest
from pydantic import ValidationError

from baja_studio.rig.scenarios import (
    Scenario,
    ScenarioRunner,
    Step,
    load_scenario,
    scenario_names,
    timeline,
)
from tests.conftest import FakeClock, eventually

BRIEF_SCENARIOS = {
    "calm",
    "flaky-agent",
    "mixer-drop",
    "obs-restart",
    "stale-state",
    "post-fader-tap",
    "lr-assign-silent",
}


@dataclass
class RecordingControls:
    """Remembers each step and the fake time it was carried out at."""

    clock: FakeClock
    done: list[tuple[float, str]] = field(default_factory=list)
    refuse: str | None = None

    def perform(self, step: Step) -> None:
        if self.refuse and step.target == self.refuse:
            raise RuntimeError(f"{step.target} is not running")
        self.done.append((self.clock.monotonic(), step.describe()))


def start_runner(
    steps: list[Step], controls: RecordingControls, clock: FakeClock, time_scale: float = 1.0
) -> tuple[threading.Thread, threading.Event, list[str]]:
    """Play `steps` on a thread, as the rig does; returns the thread, its stop and its output."""
    printed: list[str] = []
    stop = threading.Event()
    runner = ScenarioRunner(steps, controls, clock, printed.append, time_scale)
    thread = threading.Thread(target=runner.run, args=(stop,), daemon=True)
    thread.start()
    return thread, stop, printed


def advance_to(clock: FakeClock, when: float) -> None:
    """Move the fake clock to `when`, once the runner is waiting (so it hears the move)."""
    eventually(lambda: clock.sleeping_count == 1, "the runner never waited")
    clock.advance(when - clock.monotonic())


def test_every_scenario_of_the_brief_is_shipped_and_loads() -> None:
    assert set(scenario_names()) == BRIEF_SCENARIOS
    for name in scenario_names():
        scenario = load_scenario(name)
        assert scenario.name == name and scenario.description


def test_an_unknown_scenario_names_the_ones_there_are() -> None:
    with pytest.raises(ValueError, match="flaky-agent"):
        load_scenario("earthquake")


def test_a_step_for_a_fake_that_does_not_exist_is_refused() -> None:
    with pytest.raises(ValidationError, match="no fake 'mixer'"):
        Step(at_s=1, target="mixer", action="console", args={"line": "drop on"})
    with pytest.raises(ValidationError, match="xr18 only"):
        Step(at_s=1, target="obs", action="console", args={"line": "drop on"})


def test_flaky_agent_silences_agent_two_at_30_s_and_brings_it_back_at_45_s(
    fake_clock: FakeClock,
) -> None:
    controls = RecordingControls(fake_clock)
    steps = timeline(load_scenario("flaky-agent"), seed=1)
    thread, _, printed = start_runner(steps, controls, fake_clock)

    # Just before 30 s nothing has happened yet.
    advance_to(fake_clock, 29.9)
    assert controls.done == []
    # At 30 s agent 2 stops answering (slow HTTP, silent WebSocket) ...
    advance_to(fake_clock, 30.0)
    eventually(lambda: len(controls.done) == 2)
    assert all(when == 30.0 and "agent2" in what for when, what in controls.done)
    # ... and at 45 s it answers again; then the scenario ends.
    advance_to(fake_clock, 45.0)
    thread.join(timeout=5)
    assert [when for when, _ in controls.done] == [30.0, 30.0, 45.0, 45.0]
    assert controls.done[-1][1] == 'agent2: /_fake/silence {"silent": false}'
    assert printed[-1] == "scenario finished"


def test_a_compressed_run_plays_the_same_steps_sooner(fake_clock: FakeClock) -> None:
    # time_scale 0.1: obs-restart's 20 s and 30 s become 2 s and 3 s.
    controls = RecordingControls(fake_clock)
    steps = timeline(load_scenario("obs-restart"), seed=1)
    thread, _, _ = start_runner(steps, controls, fake_clock, time_scale=0.1)
    advance_to(fake_clock, 2.0)
    advance_to(fake_clock, 3.0)
    thread.join(timeout=5)
    assert controls.done == [(2.0, "obs: stop"), (3.0, "obs: start")]


def test_a_step_at_zero_runs_at_once(fake_clock: FakeClock) -> None:
    # lr-assign-silent acts before the studio first connects to the mixer.
    controls = RecordingControls(fake_clock)
    thread, _, _ = start_runner(
        timeline(load_scenario("lr-assign-silent"), 1), controls, fake_clock
    )
    thread.join(timeout=5)
    assert controls.done == [(0.0, "xr18: forget /ch/01/mix/lr")]


def test_a_failed_step_is_reported_and_the_scenario_goes_on(fake_clock: FakeClock) -> None:
    controls = RecordingControls(fake_clock, refuse="obs")
    steps = [
        Step(at_s=0, target="obs", action="stop"),
        Step(at_s=0, target="xr18", action="console", args={"line": "drop on"}),
    ]
    thread, _, printed = start_runner(steps, controls, fake_clock)
    thread.join(timeout=5)
    assert "FAILED: obs is not running" in printed[0]
    assert controls.done == [(0.0, "xr18: drop on")]


def test_stopping_the_rig_stops_the_scenario_between_steps(fake_clock: FakeClock) -> None:
    controls = RecordingControls(fake_clock)
    thread, stop, _ = start_runner(timeline(load_scenario("flaky-agent"), 1), controls, fake_clock)
    eventually(lambda: fake_clock.sleeping_count == 1)
    stop.set()
    thread.join(timeout=5)
    assert not thread.is_alive() and controls.done == []


def test_a_lossy_stretch_becomes_drop_switches_inside_it_and_ends_with_drop_off() -> None:
    lossy = Step(
        at_s=30, target="xr18", action="lossy", args={"fraction": 0.3, "for_s": 30, "slot_s": 0.5}
    )
    steps = timeline(Scenario(steps=[lossy]), seed=7)

    lines = [step.args["line"] for step in steps]
    assert all(step.action == "console" and 30 <= step.at_s <= 60 for step in steps)
    # On and off alternate, starting with on and ending with off.
    assert lines[0] == "drop on" and lines[-1] == "drop off"
    assert all(first != second for first, second in itertools.pairwise(lines))
    # About 30 % of the 60 slots drop: count the time spent between "on" and "off".
    dropped = sum(off.at_s - on.at_s for on, off in zip(steps[::2], steps[1::2], strict=True))
    assert 0.15 * 30 < dropped < 0.45 * 30
    # The same seed gives the same switches, so a bench run can be replayed.
    assert timeline(Scenario(steps=[lossy]), seed=7) == steps


def test_mixer_drop_cuts_the_mixer_for_ten_seconds_before_the_lossy_stretch() -> None:
    steps = timeline(load_scenario("mixer-drop"), seed=1)
    assert (steps[0].at_s, steps[0].args["line"]) == (20, "drop on")
    assert (steps[1].at_s, steps[1].args["line"]) == (30, "drop off")
    assert steps[-1].at_s <= 60
