"""The readiness module: every module's pre-event checks, run in one go (LIVESTACK-12, WP8).

Checks belong to the modules (`Module.readiness_checks`); this module only
collects them from every running module, runs them in parallel with a 5 s
limit each (`core/readiness.run_checks`) and publishes the outcome. The
Readiness page (`/readiness`) shows it; the home page shows its banner.

State (`state.readiness`):
- `running`, `progress`: {module: {done, total}} while a run is going on
- `last_run`: {number, trigger ("manual" | "scheduled"), source, started_at,
  duration_s, ready, banner, fails, warnings, newly_failed[], modules: [{name,
  title, checks: [{key, id, label, status, detail, fix, link, duration_s}]}]}
- `schedule`: {enabled, interval_minutes, next_run_at}

Command: `run` (returns at once; the run happens on the `runner` worker).
Route: `GET /api/readiness/last` gives `{last_run}` (null before the first
run) without starting one; the tray polls it for its badge (WP10).
A scheduled run (setting, off by default) repeats every N minutes; the page
toasts the checks in `newly_failed` (green last time, red now). Nothing is
ever fixed automatically.
"""

import threading
from datetime import timedelta
from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel, Field

from baja_studio.core.module import CommandError, Module, ModuleContext
from baja_studio.core.readiness import (
    CheckResult,
    ReadinessCheck,
    failed,
    newly_failed,
    run_checks,
    summarise,
)
from baja_studio.core.registry import ModuleRegistry
from baja_studio.core.settings import ModuleSettings
from baja_studio.core.supervisor import WorkerContext, WorkerSpec

RUNNER_WORKER = "runner"
# How long the runner sleeps when nothing is scheduled; a command or a settings edit wakes it.
IDLE_SLEEP_SECONDS = 3600.0


class ReadinessSettings(ModuleSettings):
    """Settings of the readiness module."""

    scheduled_run: bool = Field(
        default=False,
        description=(
            "Run every check every few minutes during the event; the pages show a "
            "message when a check that passed starts failing."
        ),
    )
    interval_minutes: int = Field(
        default=5, ge=1, le=120, description="Minutes between scheduled runs."
    )


class ReadinessModule(Module):
    """Collects, runs and publishes every module's readiness checks."""

    name = "readiness"
    title = "Readiness"
    SettingsModel = ReadinessSettings

    def __init__(self, registry: ModuleRegistry) -> None:
        """`registry` is where the checks come from (every running module)."""
        super().__init__()
        self._registry = registry
        self._lock = threading.Lock()
        self._requested_by: str | None = None
        self._running = False
        self._run_number = 0
        self._previous: list[CheckResult] = []
        self._last_run: dict[str, Any] | None = None
        self._next_scheduled_at: float | None = None
        self._subscribed = False

    def initial_state(self) -> dict[str, Any]:
        """Never run yet."""
        return {
            "running": False,
            "progress": {},
            "last_run": None,
            "schedule": {"enabled": False, "interval_minutes": 5, "next_run_at": None},
        }

    def start(self, context: ModuleContext) -> None:
        """Follow the schedule settings."""
        super().start(context)
        self._next_scheduled_at = None
        if not self._subscribed:
            self._subscribed = True
            context.settings.subscribe(self.name, self._settings_changed)

    def workers(self) -> list[WorkerSpec]:
        """One worker runs every check run, asked for or scheduled."""
        return [WorkerSpec(RUNNER_WORKER, self._run_loop)]

    def handle_command(self, command: str, args: dict[str, Any], source: str) -> Any:
        """`run`: start a run now (the page follows `state.readiness`)."""
        if command != "run":
            raise CommandError(f"readiness has no command {command!r}; try 'run'")
        with self._lock:
            if self._running or self._requested_by is not None:
                return {"started": False, "message": "a run is already going on"}
            self._requested_by = source
        self.context.supervisor.wake(self.name, RUNNER_WORKER)
        return {"started": True}

    def routes(self) -> APIRouter:
        """`GET /last`: the last finished run, read-only (open like `/api/health`)."""
        router = APIRouter()

        @router.get("/last")
        def last_run() -> dict[str, Any]:
            # Never starts a run: the tray asks every 5 s. Before the first run
            # the answer is an empty one, not a 404, so callers need no special case.
            with self._lock:
                return {"last_run": self._last_run}

        return router

    # --- the runner worker -----------------------------------------------------------

    def _run_loop(self, worker: WorkerContext) -> None:
        while not worker.should_stop:
            self._plan_schedule()
            with self._lock:
                source, self._requested_by = self._requested_by, None
            if source is not None:
                self.run_now("manual", source)
            elif self._scheduled_run_due():
                self.run_now("scheduled", "schedule")
            self._plan_schedule()
            worker.sleep(self._seconds_until_next_run())

    def run_now(self, trigger: str, source: str) -> dict[str, Any]:
        """Run every check of every running module; publish and return `last_run`.

        Called on the runner worker (tests call it directly too).
        """
        with self._lock:
            self._running = True
            self._run_number += 1
            number = self._run_number
        clock = self.context.clock
        started_at, started = clock.now_utc(), clock.monotonic()
        titles, checks = self._collect()
        progress = {name: {"done": 0, "total": 0} for name in titles}
        for module, _ in checks:
            progress[module]["total"] += 1
        self.context.update_state("progress", progress)
        self.context.update_state("running", True)

        def count(result: CheckResult) -> None:
            progress[result.module]["done"] += 1
            self.context.update_state(f"progress.{result.module}", dict(progress[result.module]))

        results = run_checks(checks, clock, on_result=count)
        summary = summarise(results)
        last_run = {
            "number": number,
            "trigger": trigger,
            "source": source,
            "started_at": started_at.isoformat(),
            "duration_s": round(clock.monotonic() - started, 3),
            "ready": summary.ready,
            "banner": summary.banner,
            "fails": summary.fails,
            "warnings": summary.warnings,
            "newly_failed": newly_failed(self._previous, results),
            "modules": [
                {
                    "name": name,
                    "title": title,
                    "checks": [r.as_state() for r in results if r.module == name],
                }
                for name, title in titles.items()
                if any(result.module == name for result in results)
            ],
        }
        self._previous = results
        self.context.update_state("last_run", last_run)
        self.context.update_state("running", False)
        with self._lock:
            self._running = False
            self._last_run = last_run
        log = self.context.logger.info if summary.ready else self.context.logger.warning
        log("readiness run %d (%s): %s", number, trigger, summary.banner)
        return last_run

    def _collect(self) -> tuple[dict[str, str], list[tuple[str, ReadinessCheck]]]:
        """Every running module's checks; a module that cannot list them gets a failing line."""
        titles: dict[str, str] = {}
        checks: list[tuple[str, ReadinessCheck]] = []
        for module in self._registry.modules():
            if module.name == self.name or not self._registry.is_running(module.name):
                continue
            try:
                declared = module.readiness_checks()
            except Exception as error:
                self.context.logger.exception("%s could not list its checks", module.name)
                declared = [_broken_module_check(error)]
            if declared:
                titles[module.name] = module.title
                checks.extend((module.name, check) for check in declared)
        return titles, checks

    # --- the schedule ----------------------------------------------------------------

    def _plan_schedule(self) -> None:
        """Keep the next scheduled run in line with the settings; publish it."""
        settings = self._settings()
        clock = self.context.clock
        if not settings.scheduled_run:
            self._next_scheduled_at = None
        elif self._next_scheduled_at is None:
            self._next_scheduled_at = clock.monotonic() + settings.interval_minutes * 60
        next_run_at = None
        if self._next_scheduled_at is not None:
            wait = max(0.0, self._next_scheduled_at - clock.monotonic())
            next_run_at = (clock.now_utc() + timedelta(seconds=wait)).isoformat()
        self.context.update_state(
            "schedule",
            {
                "enabled": settings.scheduled_run,
                "interval_minutes": settings.interval_minutes,
                "next_run_at": next_run_at,
            },
        )

    def _scheduled_run_due(self) -> bool:
        due = self._next_scheduled_at
        if due is None or self.context.clock.monotonic() < due - 1e-6:
            return False
        self._next_scheduled_at = None
        return True

    def _seconds_until_next_run(self) -> float:
        if self._next_scheduled_at is None:
            return IDLE_SLEEP_SECONDS
        return max(0.0, self._next_scheduled_at - self.context.clock.monotonic())

    def _settings_changed(self, values: BaseModel) -> None:
        """Settings subscriber: a new interval counts from now."""
        if self._context is None:
            return
        self._next_scheduled_at = None
        self.context.supervisor.wake(self.name, RUNNER_WORKER)

    def _settings(self) -> ReadinessSettings:
        settings = self.context.current_settings()
        assert isinstance(settings, ReadinessSettings)
        return settings


def _broken_module_check(error: Exception) -> ReadinessCheck:
    detail = f"could not list its checks: {type(error).__name__}: {error}"
    return ReadinessCheck(
        id="checks",
        label="Readiness checks",
        run=lambda: failed(detail),
        fix="This is a bug in the studio: send a diagnostics bundle (home page).",
    )
