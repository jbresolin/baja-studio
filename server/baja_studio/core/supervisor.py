"""Runs each module's workers in their own threads and keeps them alive.

A worker is a function that loops until asked to stop. If it raises, the
failure is logged, the module's health becomes `degraded` (or `offline` if the
worker raised `DeviceOfflineError`), and the worker is restarted after an
exponential backoff. A worker that then runs for ten seconds without failing
is considered healthy again. The service process itself never goes down
because of a worker.

A worker that looks after several devices and keeps running while some of
them are away (the agents module, for one) reports its health itself with
`WorkerContext.report_health` instead of raising.
"""

import logging
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum

from baja_studio.core.clock import Clock, TimerHandle

logger = logging.getLogger(__name__)

FIRST_RESTART_DELAY_SECONDS = 1.0
MAX_RESTART_DELAY_SECONDS = 30.0
STABLE_RUN_SECONDS = 10.0
DEFAULT_JOIN_TIMEOUT_SECONDS = 5.0


class HealthState(StrEnum):
    """How a module is doing, worst last except `stopped`, which is deliberate."""

    OK = "ok"
    DEGRADED = "degraded"
    OFFLINE = "offline"
    STOPPED = "stopped"


# Used to pick the most worrying worker state when summarising a module.
_SEVERITY = {HealthState.OK: 0, HealthState.DEGRADED: 1, HealthState.OFFLINE: 2}


@dataclass(frozen=True)
class Health:
    """A health state plus the message that explains it (empty when ok)."""

    state: HealthState
    message: str = ""


class DeviceOfflineError(Exception):
    """Raised by a worker when its device or child process is absent.

    The supervisor reports `offline` instead of `degraded` and keeps retrying.
    """


class WorkerContext:
    """What a running worker uses to sleep, notice stop requests and report recovery."""

    def __init__(
        self,
        module: str,
        worker_name: str,
        clock: Clock,
        on_online: Callable[[], None],
        on_report: Callable[[Health], None] | None = None,
    ) -> None:
        """Created by the supervisor for each worker; workers never build one."""
        self.module = module
        self.worker_name = worker_name
        self.clock = clock
        self.stop_requested = threading.Event()
        self._wake_requested = threading.Event()
        self._on_online = on_online
        self._on_report = on_report

    @property
    def should_stop(self) -> bool:
        """True once the supervisor wants this worker to return."""
        return self.stop_requested.is_set()

    def sleep(self, seconds: float) -> None:
        """Pause for `seconds`, returning early if woken or asked to stop."""
        self.clock.wait(self._wake_requested, seconds)
        self._wake_requested.clear()

    def wake(self) -> None:
        """Cut the current `sleep` short, e.g. after a settings change or command."""
        self._wake_requested.set()

    def request_stop(self) -> None:
        """Ask the worker to finish; also wakes it so it notices promptly."""
        self.stop_requested.set()
        self._wake_requested.set()

    def mark_online(self) -> None:
        """Report that the device is reachable again, so `offline` turns back to `ok`."""
        self._on_online()

    def report_health(self, state: HealthState, message: str = "") -> None:
        """Set this worker's health while it keeps running (e.g. 1 of 3 devices away).

        The reported health stays until the worker reports again or fails; the
        ten-second "ran stably" rule does not override it.
        """
        if self._on_report is not None:
            self._on_report(Health(state, message))


WorkerFunction = Callable[[WorkerContext], None]


@dataclass(frozen=True)
class WorkerSpec:
    """A worker a module asks the supervisor to run: a name and a function.

    `starts_offline` is for workers that talk to a device or child process:
    the module shows `offline` until the worker calls `mark_online`, rather
    than claiming `ok` before the device has answered.
    """

    name: str
    run: WorkerFunction
    starts_offline: bool = False


class RestartPolicy:
    """Exponential backoff between restarts: 1 s, 2 s, 4 s ... capped at 30 s."""

    def __init__(
        self,
        first_delay: float = FIRST_RESTART_DELAY_SECONDS,
        max_delay: float = MAX_RESTART_DELAY_SECONDS,
    ) -> None:
        """Start at the shortest delay."""
        self._first_delay = first_delay
        self._max_delay = max_delay
        self._next_delay = first_delay

    def next_delay(self) -> float:
        """The delay to wait before this restart; each call doubles the next one."""
        delay = self._next_delay
        self._next_delay = min(self._next_delay * 2, self._max_delay)
        return delay

    def reset(self) -> None:
        """Go back to the shortest delay after a worker has run stably."""
        self._next_delay = self._first_delay


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


HealthListener = Callable[[str, Health], None]


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
        for worker in running:
            worker.context.request_stop()
            if worker.stability_timer is not None:
                worker.stability_timer.cancel()
        for worker in running:
            worker.thread.join(self._join_timeout_seconds)
            if worker.thread.is_alive():
                logger.warning(
                    "worker did not stop in time",
                    extra={"studio_module": module, "worker": worker.spec.name},
                )
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
            healths = [worker.health for worker in self._workers.get(module, {}).values()]
        if not healths:
            return Health(HealthState.OK)
        return max(healths, key=lambda health: _SEVERITY[health.state])

    def all_health(self) -> dict[str, Health]:
        """Summary health of every registered module, for `/api/health`."""
        with self._lock:
            modules = list(self._workers)
        return {module: self.module_health(module) for module in modules}

    def _start_worker(self, module: str, spec: WorkerSpec) -> None:
        context = WorkerContext(
            module,
            spec.name,
            self._clock,
            on_online=lambda: self._worker_came_online(module, spec.name),
            on_report=lambda health: self._worker_reported(module, spec.name, health),
        )
        worker = _RunningWorker(spec, context)
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
                logger.warning(
                    "device offline: %s",
                    error,
                    extra={"studio_module": module, "worker": worker_name},
                )
                self._set_worker_health(module, worker, Health(HealthState.OFFLINE, str(error)))
            except Exception as error:
                logger.exception(
                    "worker failed", extra={"studio_module": module, "worker": worker_name}
                )
                message = f"{type(error).__name__}: {error}"
                self._set_worker_health(module, worker, Health(HealthState.DEGRADED, message))
            finally:
                if worker.stability_timer is not None:
                    worker.stability_timer.cancel()
            if context.should_stop:
                break
            with self._lock:
                delay = worker.policy.next_delay()
            logger.info(
                "restarting worker in %.0f s",
                delay,
                extra={"studio_module": module, "worker": worker_name},
            )
            self._clock.wait(context.stop_requested, delay)

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
            if worker.health_was_reported:
                return
        if worker.health.state != HealthState.OK:
            logger.info(
                "worker recovered", extra={"studio_module": module, "worker": worker.spec.name}
            )
        self._set_worker_health(module, worker, Health(HealthState.OK))

    def _worker_came_online(self, module: str, worker_name: str) -> None:
        with self._lock:
            worker = self._workers.get(module, {}).get(worker_name)
        if worker is not None and worker.health.state == HealthState.OFFLINE:
            logger.info(
                "device back online", extra={"studio_module": module, "worker": worker_name}
            )
            self._set_worker_health(module, worker, Health(HealthState.OK))

    def _worker_reported(self, module: str, worker_name: str, health: Health) -> None:
        with self._lock:
            worker = self._workers.get(module, {}).get(worker_name)
        if worker is not None and not worker.context.should_stop:
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
