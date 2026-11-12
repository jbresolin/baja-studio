"""Scenarios: failures played against the fakes on a timeline, written as data.

A scenario is one JSON file in `scenarios/` (its name is the file's stem):

    {"description": "...",
     "steps": [{"at_s": 30, "target": "agent2", "action": "switch",
                "args": {"switch": "silence", "body": {"silent": true}}}, ...]}

`at_s` counts from when the fakes are up (before the studio starts, so a step
at 0 s happens before the studio first connects). Actions, all through the
fakes' existing control surfaces:

- `console` (xr18): type `args.line` on the fake XR18's console (`drop on`, `tap 1 bus1 post`).
- `switch` (agent1, agent2): POST `/_fake/<args.switch>` with `args.body`.
- `stop` / `start` (any fake): stop its process, or start it again on the same port.
- `lossy` (xr18): lose `args.fraction` of the time for `args.for_s` seconds, by
  typing `drop on` / `drop off` in `args.slot_s` slots (seeded, so repeatable);
  the fake has no partial loss of its own.

The runner is told the time by a `Clock`, so tests run it on the fake clock.
"""

import json
import random
import threading
from collections.abc import Callable
from dataclasses import dataclass
from importlib import resources
from typing import Any, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, model_validator

from baja_studio.core.clock import Clock

Action = Literal["console", "switch", "stop", "start", "lossy"]
FAKE_NAMES = ("obs", "xr18", "camera1", "camera2", "agent1", "agent2", "orchestrator")
DEFAULT_SCENARIO = "calm"
DEFAULT_LOSSY_SLOT_SECONDS = 0.5


class Step(BaseModel):
    """One timed action against one fake."""

    model_config = ConfigDict(extra="forbid")

    at_s: float = Field(ge=0.0)
    target: str
    action: Action
    args: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _makes_sense(self) -> "Step":
        if self.target not in FAKE_NAMES:
            raise ValueError(f"no fake {self.target!r}; the rig has {', '.join(FAKE_NAMES)}")
        if self.action in ("console", "lossy") and self.target != "xr18":
            raise ValueError(f"{self.action} is for the xr18 only")
        if self.action == "console" and not isinstance(self.args.get("line"), str):
            raise ValueError("a console step needs args.line")
        if self.action == "switch" and not self.target.startswith("agent"):
            raise ValueError("switch is for the agents only")
        if self.action == "switch" and not isinstance(self.args.get("switch"), str):
            raise ValueError("a switch step needs args.switch (e.g. silence)")
        if self.action == "lossy" and not (
            0 < float(self.args.get("fraction", 0)) < 1 and float(self.args.get("for_s", 0)) > 0
        ):
            raise ValueError("a lossy step needs 0 < args.fraction < 1 and args.for_s > 0")
        return self

    def describe(self) -> str:
        """For the terminal: `agent2: /_fake/silence {"silent": true}`."""
        if self.action == "console":
            return f"{self.target}: {self.args['line']}"
        if self.action == "switch":
            body = json.dumps(self.args.get("body", {}))
            return f"{self.target}: /_fake/{self.args['switch']} {body}"
        return f"{self.target}: {self.action}"


class Scenario(BaseModel):
    """A named timeline of steps."""

    model_config = ConfigDict(extra="forbid")

    name: str = ""
    description: str = ""
    steps: list[Step] = Field(default_factory=list)


def scenario_names() -> list[str]:
    """Every scenario shipped with the rig."""
    folder = resources.files("baja_studio.rig").joinpath("scenarios")
    return sorted(
        entry.name.removesuffix(".json")
        for entry in folder.iterdir()
        if entry.name.endswith(".json")
    )


def load_scenario(name: str) -> Scenario:
    """The scenario called `name`; `ValueError` naming the ones there are if none is."""
    if name not in scenario_names():
        raise ValueError(f"no scenario {name!r}; there are {', '.join(scenario_names())}")
    path = resources.files("baja_studio.rig").joinpath("scenarios", f"{name}.json")
    scenario = Scenario.model_validate_json(path.read_text(encoding="utf-8"))
    return scenario.model_copy(update={"name": name})


def timeline(scenario: Scenario, seed: int) -> list[Step]:
    """The steps in time order, each `lossy` step turned into console `drop` steps."""
    steps: list[Step] = []
    chance = random.Random(seed)
    for step in scenario.steps:
        if step.action != "lossy":
            steps.append(step)
            continue
        fraction, length = float(step.args["fraction"]), float(step.args["for_s"])
        slot = float(step.args.get("slot_s", DEFAULT_LOSSY_SLOT_SECONDS))
        dropping = False
        for index in range(round(length / slot)):
            wanted = chance.random() < fraction
            if wanted != dropping:
                dropping = wanted
                line = "drop on" if wanted else "drop off"
                steps.append(
                    Step(
                        at_s=step.at_s + index * slot,
                        target="xr18",
                        action="console",
                        args={"line": line},
                    )
                )
        if dropping:
            steps.append(
                Step(
                    at_s=step.at_s + length,
                    target="xr18",
                    action="console",
                    args={"line": "drop off"},
                )
            )
    return sorted(steps, key=lambda step: step.at_s)


class Controls(Protocol):
    """What the runner does a step with (`controls.FakeControls` for the real fakes)."""

    def perform(self, step: Step) -> None:
        """Carry out one step; raises with a readable message if the fake refuses."""


@dataclass
class ScenarioRunner:
    """Plays a timeline against the fakes, telling `report` about each step as it happens.

    `time_scale` below 1 plays it faster (a compressed run in the tests).
    """

    steps: list[Step]
    controls: Controls
    clock: Clock
    report: Callable[[str], None]
    time_scale: float = 1.0

    def run(self, stop: threading.Event) -> None:
        """Play every step on time, until the end or until `stop` is set."""
        started_at = self.clock.monotonic()
        for step in self.steps:
            due = started_at + step.at_s * self.time_scale
            while (wait := due - self.clock.monotonic()) > 1e-6:
                if self.clock.wait(stop, wait):
                    return
            if stop.is_set():
                return
            try:
                self.controls.perform(step)
                self.report(f"[{step.at_s:6.1f} s] {step.describe()}")
            except Exception as error:  # a scenario must not stop the rig
                self.report(f"[{step.at_s:6.1f} s] {step.describe()} FAILED: {error}")
        self.report("scenario finished")
