"""Runs each module's workers in their own threads and keeps them alive.

A worker is a function that loops until asked to stop. If it raises, the
failure is logged, the module's health becomes `degraded` (or `offline` if the
worker raised `DeviceOfflineError`), and the worker is restarted after an
exponential backoff. A worker that then runs for ten seconds without failing
is considered healthy again. The service process itself never goes down
because of a worker.

A worker failing again and again with the same error is logged once, at its
usual level; the repeats go to DEBUG with a running count, a different error
is logged again, and recovery is logged once with how many failures it ended
(ADR 0014). Health follows every failure as before.

A worker that looks after several devices and keeps running while some of
them are away (the agents module, for one) reports its health itself with
`WorkerContext.report_health` instead of raising.

A module whose devices come and go with its settings (cameras) starts and
stops workers one at a time with `start_worker` / `stop_worker`, and may set a
`HealthSummary` that turns its workers' healths into the one its card shows.
"""

import logging
import threading
from collections.abc import Callable
from dataclasses import dataclass, field

from baja_studio.core.clock import Clock, TimerHandle
from baja_studio.core.worker import (
    FIRST_RESTART_DELAY_SECONDS,
    HEALTH_SEVERITY,
    MAX_RESTART_DELAY_SECONDS,
    DeviceOfflineError,
    FailureStreak,
    Health,
    HealthState,
    RestartPolicy,
    WorkerContext,
    WorkerFunction,
    WorkerSpec,
)

# Re-exported: modules import these from here, as they did before worker.py existed.
__all__ = [
    "FIRST_RESTART_DELAY_SECONDS",
    "MAX_RESTART_DELAY_SECONDS",
    "DeviceOfflineError",
    "Health",
    "HealthState",
    "HealthSummary",
    "RestartPolicy",
    "Supervisor",
    "WorkerContext",
    "WorkerFunction",
    "WorkerSpec",
]

logger = logging.getLogger(__name__)

STABLE_RUN_SECONDS = 10.0
DEFAULT_JOIN_TIMEOUT_SECONDS = 5.0


@dataclass
class _RunningWorker:
    spec: WorkerSpec
    context: WorkerContext
    thread: threading.Thread = field(init=False)
    policy: RestartPolicy = field(default_factory=RestartPolicy)
    health: Health = field(default_factory=lambda: Health(HealthState.OK))
    run_number: int = 0
    stability_timer: TimerHandle | None = None
    # True while `health` came from `report_health` rather than from a failure.
    health_was_reported: bool = False
    failures: FailureStreak = field(default_factory=FailureStreak)


HealthListener = Callable[[str, Health], None]
# Turns {worker name: health} into the module's one health; see `set_health_summary`.
HealthSummary = Callable[[dict[str, Health]], Health]


class Supervisor:
    """Owns all worker threads and the health of every module."""

    def __init__(
        self, clock: Clock, join_timeout_seconds: float = DEFAULT_JOIN_TIMEOUT_SECONDS
    ) -> None:
        """Create a supervisor with no modules yet."""
        self._clock = clock
        self._join_timeout_seconds = join_timeout_seconds
        self._lock = threading.RLock()
        self._workers: dict[str, dict[str, _RunningWorker]] = {}
        self._stopped_modules: set[str] = set()
        self._last_reported: dict[str, Health] = {}
        self._listeners: list[HealthListener] = []
        self._summaries: dict[str, HealthSummary | None] = {}

    def add_health_listener(self, listener: HealthListener) -> None:
        """Call `listener(module, health)` whenever a module's summary health changes."""
        with self._lock:
            self._listeners.append(listener)

    def register_module(self, module: str) -> None:
        """Start tracking a module as stopped; `start_module` makes it ok."""
        with self._lock:
            self._workers.setdefault(module, {})
            self._stopped_modules.add(module)
        self._report_if_changed(module)

    def start_module(self, module: str, specs: list[WorkerSpec]) -> None:
        """Mark the module running and start a thread for each of its workers."""
        with self._lock:
            self._stopped_modules.discard(module)
            for spec in specs:
                self._start_worker(module, spec)
        self._report_if_changed(module)

    def stop_module(self, module: str) -> None:
        """Ask the module's workers to stop, wait for them, and mark it stopped."""
        with self._lock:
            running = list(self._workers.get(module, {}).values())
            self._workers[module] = {}
            self._stopped_modules.add(module)
        self._stop_and_join(module, running)
        self._report_if_changed(module)

    def start_worker(self, module: str, spec: WorkerSpec) -> None:
        """Start one more worker for a running module (e.g. a camera just added).

        A worker of the same name must not be running; stop it first. Does
        nothing for a stopped module: its workers start with the module.
        """
        with self._lock:
            if module in self._stopped_modules:
                return
            if spec.name in self._workers.get(module, {}):
                raise ValueError(f"worker {module}.{spec.name} is already running")
            self._start_worker(module, spec)
        self._report_if_changed(module)

    def stop_worker(self, module: str, worker_name: str) -> None:
        """Stop one worker and wait for it (a camera removed, a COM port changed).

        Its health no longer counts towards the module's. Unknown names are ignored.
        """
        with self._lock:
            worker = self._workers.get(module, {}).pop(worker_name, None)
        if worker is not None:
            self._stop_and_join(module, [worker])
        self._report_if_changed(module)

    def worker_names(self, module: str) -> list[str]:
        """Names of the module's running workers, in the order they were started."""
        with self._lock:
            return list(self._workers.get(module, {}))

    def set_health_summary(self, module: str, summary: HealthSummary | None) -> None:
        """Let `summary({worker name: health})` decide the module's health.

        Without one, a module is as healthy as its most worrying worker. A
        module with one worker per device sets one so that, say, one camera
        away of three reads `degraded` and names the camera.
        """
        with self._lock:
            self._summaries[module] = summary
        self._report_if_changed(module)

    def stop_all(self) -> None:
        """Stop every module; called at service shutdown."""
        with self._lock:
            modules = list(self._workers)
        for module in modules:
            self.stop_module(module)

    def wake(self, module: str, worker_name: str) -> None:
        """Interrupt a worker's sleep (e.g. to apply a new setting right away)."""
        with self._lock:
            worker = self._workers.get(module, {}).get(worker_name)
        if worker is not None:
            worker.context.wake()

    def module_health(self, module: str) -> Health:
        """Summary health of one module: stopped, or its most worrying worker."""
        with self._lock:
            if module in self._stopped_modules:
                return Health(HealthState.STOPPED)
            healths = {
                name: worker.health for name, worker in self._workers.get(module, {}).items()
            }
            summary = self._summaries.get(module)
        if summary is not None:
            return summary(healths)
        if not healths:
            return Health(HealthState.OK)
        return max(healths.values(), key=lambda health: HEALTH_SEVERITY[health.state])

    def all_health(self) -> dict[str, Health]:
        """Summary health of every registered module, for `/api/health`."""
        with self._lock:
            modules = list(self._workers)
        return {module: self.module_health(module) for module in modules}

    def _stop_and_join(self, module: str, workers: list[_RunningWorker]) -> None:
        for worker in workers:
            worker.context.request_stop()
            if worker.stability_timer is not None:
                worker.stability_timer.cancel()
        for worker in workers:
            worker.thread.join(self._join_timeout_seconds)
            if worker.thread.is_alive():
                logger.warning(
                    "worker did not stop in time",
                    extra={"studio_module": module, "worker": worker.spec.name},
                )

    def _start_worker(self, module: str, spec: WorkerSpec) -> None:
        # The callbacks find their worker through this cell, not by name: a worker
        # stopped with `stop_worker` and started again under the same name is a
        # new record, and late calls from the old one must not change it.
        record: list[_RunningWorker] = []
        context = WorkerContext(
            module,
            spec.name,
            self._clock,
            on_online=lambda: self._worker_came_online(module, record[0]),
            on_report=lambda health: self._worker_reported(module, record[0], health),
        )
        worker = _RunningWorker(spec, context)
        record.append(worker)
        if spec.starts_offline:
            worker.health = Health(HealthState.OFFLINE, "starting")
        # The thread gets its record directly rather than looking it up, because
        # a quick stop_module may already have removed it from self._workers.
        worker.thread = threading.Thread(
            target=self._run_with_restarts,
            args=(module, worker),
            name=f"{module}.{spec.name}",
            daemon=True,
        )
        self._workers[module][spec.name] = worker
        worker.thread.start()

    def _run_with_restarts(self, module: str, worker: _RunningWorker) -> None:
        """Body of each worker thread: run, and after any failure back off and run again."""
        worker_name = worker.spec.name
        context = worker.context
        while not context.should_stop:
            self._begin_run(module, worker)
            try:
                worker.spec.run(context)
                if context.should_stop:
                    break
                raise RuntimeError("worker returned without being asked to stop")
            except DeviceOfflineError as error:
                news = self._log_failure(module, worker, error)
                self._set_worker_health(module, worker, Health(HealthState.OFFLINE, str(error)))
            except Exception as error:
                news = self._log_failure(module, worker, error)
                message = f"{type(error).__name__}: {error}"
                self._set_worker_health(module, worker, Health(HealthState.DEGRADED, message))
            finally:
                if worker.stability_timer is not None:
                    worker.stability_timer.cancel()
            if context.should_stop:
                break
            with self._lock:
                delay = worker.policy.next_delay()
            logger.log(
                logging.INFO if news else logging.DEBUG,
                "restarting worker in %.0f s",
                delay,
                extra={"studio_module": module, "worker": worker_name},
            )
            self._clock.wait(context.stop_requested, delay)

    def _log_failure(self, module: str, worker: _RunningWorker, error: Exception) -> bool:
        """Log a failure if it is news (see the module docstring); True when it was."""
        extra = {"studio_module": module, "worker": worker.spec.name}
        offline = isinstance(error, DeviceOfflineError)
        with self._lock:
            news = worker.failures.record(error)
            in_a_row = worker.failures.same_in_a_row
        if news and offline:
            logger.warning("device offline: %s", error, extra=extra)
        elif news:
            # An unexpected exception is a bug: ERROR with its traceback, once.
            logger.error("worker failed", exc_info=error, extra=extra)
        else:
            what = "device offline" if offline else f"worker failed: {type(error).__name__}"
            logger.debug("%s again (%d in a row): %s", what, in_a_row, error, extra=extra)
        return news

    def _begin_run(self, module: str, worker: _RunningWorker) -> None:
        with self._lock:
            worker.run_number += 1
            run_number = worker.run_number
        worker.stability_timer = self._clock.call_later(
            STABLE_RUN_SECONDS, lambda: self._worker_ran_stably(module, worker, run_number)
        )

    def _worker_ran_stably(self, module: str, worker: _RunningWorker, run_number: int) -> None:
        """Timer callback: the same run has lasted STABLE_RUN_SECONDS without failing."""
        with self._lock:
            if worker.run_number != run_number or worker.context.should_stop:
                return
            worker.policy.reset()
            failures = worker.failures.recover()
            reported = worker.health_was_reported
        if failures:
            logger.info(
                "recovered after %s",
                _failures(failures),
                extra={"studio_module": module, "worker": worker.spec.name},
            )
        if reported:
            return
        self._set_worker_health(module, worker, Health(HealthState.OK))

    def _is_current(self, module: str, worker: _RunningWorker) -> bool:
        with self._lock:
            return self._workers.get(module, {}).get(worker.spec.name) is worker

    def _worker_came_online(self, module: str, worker: _RunningWorker) -> None:
        if self._is_current(module, worker) and worker.health.state == HealthState.OFFLINE:
            with self._lock:
                failures = worker.failures.recover()
            extra = {"studio_module": module, "worker": worker.spec.name}
            if failures:
                logger.info(
                    "device back online: recovered after %s", _failures(failures), extra=extra
                )
            else:
                logger.info("device back online", extra=extra)
            self._set_worker_health(module, worker, Health(HealthState.OK))

    def _worker_reported(self, module: str, worker: _RunningWorker, health: Health) -> None:
        if self._is_current(module, worker) and not worker.context.should_stop:
            self._set_worker_health(module, worker, health, reported=True)

    def _set_worker_health(
        self, module: str, worker: _RunningWorker, health: Health, reported: bool = False
    ) -> None:
        with self._lock:
            worker.health = health
            worker.health_was_reported = reported
        self._report_if_changed(module)

    def _report_if_changed(self, module: str) -> None:
        with self._lock:
            health = self.module_health(module)
            if self._last_reported.get(module) == health:
                return
            self._last_reported[module] = health
            listeners = list(self._listeners)
        for listener in listeners:
            try:
                listener(module, health)
            except Exception:
                logger.exception("health listener failed", extra={"studio_module": module})


def _failures(count: int) -> str:
    return "1 failure" if count == 1 else f"{count} failures"
