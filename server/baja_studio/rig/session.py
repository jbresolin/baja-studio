"""One run of the rig: clips, fakes, bundle, an optional studio and scenario, and the stop.

`__main__.py` drives it from the command line; the integration tests drive
it directly (with a short clip plan and their own in-process studio).
Whatever happens, `stop` leaves no child running: the scenario first, then
the studio, then every fake.
"""

import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from baja_studio.core.clock import SystemClock
from baja_studio.rig import clips
from baja_studio.rig.bundle import Bundle, build_bundle
from baja_studio.rig.controls import FakeControls, agent_ports
from baja_studio.rig.fakes import FakePorts, RigFake, build_fakes, wait_until_up
from baja_studio.rig.layout import RigLayout
from baja_studio.rig.processes import RigProcess
from baja_studio.rig.scenarios import Scenario, ScenarioRunner, timeline

Report = Callable[[str], None]


@dataclass
class RigSession:
    """Everything one run of the rig started, so it can all be stopped."""

    layout: RigLayout
    report: Report
    obs_seed: int | None
    clip_plan: tuple[clips.DemoClip, ...] = clips.DEFAULT_CLIPS
    ports: FakePorts = field(default_factory=FakePorts.pick)
    fakes: dict[str, RigFake] = field(default_factory=dict)
    studio: RigProcess | None = None
    _scenario_stop: threading.Event = field(default_factory=threading.Event)
    _scenario_thread: threading.Thread | None = None

    @property
    def log_directory(self) -> Path:
        """Where every child's output goes."""
        return self.layout.root / "logs"

    def make_clips(self) -> None:
        """The demo library, made once (skipped with a message when there is no ffmpeg)."""
        tools = clips.find_tools()
        clips.make_clip_library(
            self.layout.clips, tools.ffmpeg.found_at, self.report, self.clip_plan
        )

    def start_fakes(self) -> None:
        """Start every fake on its port and wait until each answers."""
        tools = clips.find_tools()
        self.fakes = build_fakes(
            self.ports,
            self.layout.clips / clips.DELIVERY_FOLDER,
            self.log_directory,
            self.obs_seed,
            tools.ffprobe.found_at,
        )
        for fake in self.fakes.values():
            fake.process.start()
        wait_until_up(self.fakes)

    def write_bundle(self) -> Bundle:
        """The settings bundle for these fakes, saved in the rig's folder."""
        bundle = build_bundle(self.ports, self.layout.clips)
        for warning in bundle.warnings:
            self.report(f"warning: {warning}")
        bundle.write(self.layout.bundle)
        return bundle

    def play(self, scenario: Scenario, seed: int, time_scale: float = 1.0) -> None:
        """Play `scenario` against the fakes on a thread of its own, from now."""
        runner = ScenarioRunner(
            timeline(scenario, seed),
            FakeControls(self.fakes, agent_ports(self.fakes, self.ports.agents)),
            SystemClock(),
            self.report,
            time_scale,
        )
        self._scenario_thread = threading.Thread(
            target=runner.run, args=(self._scenario_stop,), name="rig-scenario", daemon=True
        )
        self._scenario_thread.start()

    def wait_for_scenario(self, timeout_s: float) -> bool:
        """Wait for the scenario to finish; True if it did (tests)."""
        if self._scenario_thread is None:
            return True
        self._scenario_thread.join(timeout_s)
        return not self._scenario_thread.is_alive()

    def stop(self) -> None:
        """Stop the scenario, the studio and every fake (each waited for)."""
        self._scenario_stop.set()
        if self._scenario_thread is not None:
            self._scenario_thread.join(timeout=5.0)
        if self.studio is not None:
            self.studio.stop()
        for fake in self.fakes.values():
            fake.process.stop()
