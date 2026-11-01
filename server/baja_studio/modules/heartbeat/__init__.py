"""Demo in-process module: a counter that ticks once per interval.

It exercises the patterns every real module uses: a settings change applied
without restart (`interval_s`), state broadcast (`count`), commands (`reset`),
and supervised recovery from a worker crash (`crash_once`).
"""

import threading
from typing import Any

from pydantic import BaseModel, Field

from baja_studio.core.module import CommandError, Module, ModuleContext
from baja_studio.core.settings import ModuleSettings
from baja_studio.core.supervisor import WorkerContext, WorkerSpec

TICKER_WORKER = "ticker"


class HeartbeatSettings(ModuleSettings):
    """Settings for the heartbeat demo."""

    interval_s: float = Field(default=1.0, ge=0.1, le=60.0, description="Seconds between ticks.")


class HeartbeatModule(Module):
    """Counts ticks in `state.heartbeat.count`."""

    name = "heartbeat"
    title = "Heartbeat (demo)"
    SettingsModel = HeartbeatSettings

    def __init__(self) -> None:
        """Start with a zero count and no crash requested."""
        super().__init__()
        self._count_lock = threading.Lock()
        self._count = 0
        self._crash_requested = threading.Event()
        self._subscribed = False

    def initial_state(self) -> dict[str, Any]:
        """Count zero, default interval."""
        return {"count": 0, "interval_s": HeartbeatSettings().interval_s}

    def start(self, context: ModuleContext) -> None:
        """Follow settings so a new interval applies at once."""
        super().start(context)
        context.update_state("interval_s", self._interval_seconds())
        if not self._subscribed:
            self._subscribed = True
            context.settings.subscribe(self.name, self._apply_new_settings)

    def workers(self) -> list[WorkerSpec]:
        """One worker: the ticker loop."""
        return [WorkerSpec(TICKER_WORKER, self._run_ticker)]

    def handle_command(self, command: str, args: dict[str, Any], source: str) -> Any:
        """`reset` sets the count to zero; `crash_once` makes the ticker raise once."""
        if command == "reset":
            with self._count_lock:
                self._count = 0
            self.context.update_state("count", 0)
            return {"count": 0}
        if command == "crash_once":
            self._crash_requested.set()
            self.context.supervisor.wake(self.name, TICKER_WORKER)
            return None
        raise CommandError(f"heartbeat has no command {command!r}; try 'reset' or 'crash_once'")

    def _run_ticker(self, worker: WorkerContext) -> None:
        """Worker body: add one to the count, publish it, sleep for the interval."""
        while not worker.should_stop:
            if self._crash_requested.is_set():
                self._crash_requested.clear()
                raise RuntimeError("crash requested by the crash_once command")
            with self._count_lock:
                self._count += 1
                count = self._count
            self.context.update_state("count", count)
            worker.sleep(self._interval_seconds())

    def _interval_seconds(self) -> float:
        settings = self.context.current_settings()
        assert isinstance(settings, HeartbeatSettings)
        return settings.interval_s

    def _apply_new_settings(self, values: BaseModel) -> None:
        """Settings subscriber: show the new interval and wake the ticker to use it."""
        assert isinstance(values, HeartbeatSettings)
        if self._context is None:
            return
        self.context.update_state("interval_s", values.interval_s)
        self.context.supervisor.wake(self.name, TICKER_WORKER)
