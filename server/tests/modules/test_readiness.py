"""Tests for the readiness module (modules/readiness).

It collects the checks of every running module, runs them on its `runner`
worker when asked (the `run` command) or on a schedule, and publishes the
outcome in `state.readiness`. These tests use made-up modules whose checks
answer whatever the test says, so they are about collecting, running and
publishing, not about any real device.
"""

from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from typing import Any

import pytest
from fastapi.testclient import TestClient

from baja_studio.core.module import CommandError, Module
from baja_studio.core.readiness import CheckOutcome, ReadinessCheck, failed, passed, warning
from baja_studio.core.registry import ModuleRegistry
from baja_studio.core.services import StudioServices
from baja_studio.modules.readiness import ReadinessModule
from tests.conftest import (
    AppFactory,
    FakeClock,
    eventually,
    module_worker_threads,
    step_fake_time,
    wait_until_workers_sleep,
)

RUNNER = module_worker_threads("readiness")


@dataclass
class Answers:
    """What each made-up check answers right now; tests change it between runs."""

    outcomes: dict[str, CheckOutcome] = field(default_factory=dict)

    def check(self, check_id: str) -> ReadinessCheck:
        return ReadinessCheck(
            check_id, f"Check {check_id}", lambda: self.outcomes[check_id], fix=f"fix {check_id}"
        )


class MadeUpModule(Module):
    """A module whose checks come from `declare()`."""

    def __init__(self, name: str, title: str, declare: Callable[[], list[ReadinessCheck]]):
        super().__init__()
        self.name = name  # type: ignore[misc]
        self.title = title  # type: ignore[misc]
        self._declare = declare

    def readiness_checks(self) -> list[ReadinessCheck]:
        return self._declare()


class NoChecksModule(Module):
    name = "quiet"
    title = "Quiet"


@dataclass
class Readiness:
    services: StudioServices
    answers: Answers
    clock: FakeClock

    def state(self, path: str = "") -> Any:
        return self.services.state.get("readiness", path)

    def run(self, source: str = "web:alice") -> Any:
        return self.services.registry.dispatch_command("readiness", "run", {}, source)

    def run_and_wait(self) -> dict[str, Any]:
        """Ask for a run and wait until a new result is published."""
        before = (self.state("last_run") or {}).get("number", 0)
        assert self.run() == {"started": True}
        eventually(lambda: (self.state("last_run") or {}).get("number", 0) > before)
        result: dict[str, Any] = self.state("last_run")
        return result


@pytest.fixture
def readiness(make_app: AppFactory, fake_clock: FakeClock) -> Iterator[Readiness]:
    """Readiness plus two made-up modules with checks and one without."""
    answers = Answers(
        {
            "mixer": passed("XR18"),
            "mvave": warning("unplugged"),
            "camera": passed("ok"),
        }
    )

    def modules(registry: ModuleRegistry) -> list[Module]:
        return [
            ReadinessModule(registry),
            MadeUpModule(
                "audio", "Audio", lambda: [answers.check("mixer"), answers.check("mvave")]
            ),
            MadeUpModule("cameras", "Cameras", lambda: [answers.check("camera")]),
            NoChecksModule(),
        ]

    app = make_app(modules)
    with TestClient(app):
        yield Readiness(app.state.services, answers, fake_clock)


def test_a_run_collects_every_running_modules_checks_grouped_by_module(
    readiness: Readiness,
) -> None:
    last_run = readiness.run_and_wait()

    assert [module["name"] for module in last_run["modules"]] == ["audio", "cameras"]
    audio = last_run["modules"][0]
    assert audio["title"] == "Audio"
    assert [(c["key"], c["status"], c["detail"]) for c in audio["checks"]] == [
        ("audio.mixer", "ok", "XR18"),
        ("audio.mvave", "warn", "unplugged"),
    ]
    # A module without checks contributes nothing, not an empty group.
    assert "quiet" not in [module["name"] for module in last_run["modules"]]


def test_the_run_publishes_the_banner_counts_and_who_asked(readiness: Readiness) -> None:
    readiness.answers.outcomes["camera"] = failed("offline")

    last_run = readiness.run_and_wait()

    assert last_run["ready"] is False
    assert last_run["banner"] == "NOT READY (1 fail, 1 warning)"
    assert (last_run["fails"], last_run["warnings"]) == (1, 1)
    assert (last_run["trigger"], last_run["source"]) == ("manual", "web:alice")
    assert last_run["started_at"] == "2026-10-01T12:00:00+00:00"
    assert readiness.state("running") is False
    assert readiness.state("progress") == {
        "audio": {"done": 2, "total": 2},
        "cameras": {"done": 1, "total": 1},
    }


def test_warnings_alone_mean_ready(readiness: Readiness) -> None:
    last_run = readiness.run_and_wait()

    assert (last_run["ready"], last_run["banner"]) == (True, "READY")


def test_a_rerun_after_a_fix_turns_the_line_green(readiness: Readiness) -> None:
    readiness.answers.outcomes["mixer"] = failed("offline")
    first = readiness.run_and_wait()
    readiness.answers.outcomes["mixer"] = passed("XR18")

    second = readiness.run_and_wait()

    assert first["number"] == 1 and second["number"] == 2
    assert first["modules"][0]["checks"][0]["status"] == "fail"
    assert second["modules"][0]["checks"][0]["status"] == "ok"


def test_newly_failed_lists_what_was_green_last_time(readiness: Readiness) -> None:
    readiness.run_and_wait()
    readiness.answers.outcomes["camera"] = failed("cable pulled")

    last_run = readiness.run_and_wait()

    assert last_run["newly_failed"] == ["cameras.camera"]


def test_a_stopped_module_contributes_nothing(readiness: Readiness) -> None:
    readiness.services.settings.update("cameras", {"enabled": False})

    last_run = readiness.run_and_wait()

    assert [module["name"] for module in last_run["modules"]] == ["audio"]


def test_a_module_whose_checks_cannot_be_listed_gets_a_failing_line(
    make_app: AppFactory,
) -> None:
    def broken() -> list[ReadinessCheck]:
        raise RuntimeError("settings not loaded")

    app = make_app(
        lambda registry: [ReadinessModule(registry), MadeUpModule("audio", "Audio", broken)]
    )
    with TestClient(app):
        services: StudioServices = app.state.services
        services.registry.dispatch_command("readiness", "run", {}, "web:t")
        eventually(lambda: services.state.get("readiness", "last_run") is not None)
        check = services.state.get("readiness", "last_run")["modules"][0]["checks"][0]

    assert check["status"] == "fail"
    assert check["detail"] == "could not list its checks: RuntimeError: settings not loaded"
    assert check["fix"]


def test_only_commands_the_module_has_are_accepted(readiness: Readiness) -> None:
    with pytest.raises(CommandError, match="try 'run'"):
        readiness.services.registry.dispatch_command("readiness", "fix_everything", {}, "web:t")


def test_scheduled_runs_are_off_by_default(readiness: Readiness) -> None:
    wait_until_workers_sleep(readiness.clock, RUNNER)
    step_fake_time(readiness.clock, 600.0, RUNNER, step=10.0)

    assert readiness.state("last_run") is None
    assert readiness.state("schedule") == {
        "enabled": False,
        "interval_minutes": 5,
        "next_run_at": None,
    }


def test_a_scheduled_run_happens_every_interval_once_switched_on(readiness: Readiness) -> None:
    readiness.services.settings.update("readiness", {"scheduled_run": True, "interval_minutes": 2})
    eventually(lambda: readiness.state("schedule.enabled") is True)
    assert readiness.state("schedule.next_run_at") == "2026-10-01T12:02:00+00:00"

    # Not before two minutes have passed...
    step_fake_time(readiness.clock, 110.0, RUNNER, step=10.0)
    assert readiness.state("last_run") is None
    # ...then once, and once again two minutes later.
    step_fake_time(readiness.clock, 10.0, RUNNER, step=10.0)
    eventually(lambda: (readiness.state("last_run") or {}).get("number") == 1)
    assert readiness.state("last_run.trigger") == "scheduled"
    step_fake_time(readiness.clock, 120.0, RUNNER, step=10.0)
    eventually(lambda: readiness.state("last_run.number") == 2)


def test_switching_the_schedule_off_stops_the_runs(readiness: Readiness) -> None:
    readiness.services.settings.update("readiness", {"scheduled_run": True, "interval_minutes": 1})
    eventually(lambda: readiness.state("schedule.next_run_at") is not None)

    readiness.services.settings.update("readiness", {"scheduled_run": False})
    eventually(lambda: readiness.state("schedule.next_run_at") is None)
    step_fake_time(readiness.clock, 180.0, RUNNER, step=10.0)

    assert readiness.state("last_run") is None


def test_a_scheduled_run_flags_a_check_that_went_red(readiness: Readiness) -> None:
    # What the page's toast is made from: green on one run, red on the next.
    readiness.services.settings.update("readiness", {"scheduled_run": True, "interval_minutes": 1})
    eventually(lambda: readiness.state("schedule.next_run_at") is not None)
    step_fake_time(readiness.clock, 60.0, RUNNER, step=10.0)
    eventually(lambda: readiness.state("last_run.number") == 1)

    readiness.answers.outcomes["mixer"] = failed("network cable pulled")
    step_fake_time(readiness.clock, 60.0, RUNNER, step=10.0)
    eventually(lambda: readiness.state("last_run.number") == 2)

    assert readiness.state("last_run.newly_failed") == ["audio.mixer"]
    assert readiness.state("last_run.trigger") == "scheduled"
