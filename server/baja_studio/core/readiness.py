"""Pre-event readiness checks: what a module declares, and how the checks are run.

Every module may return checks from `Module.readiness_checks()`; the readiness
module (`modules/readiness`) collects them from every running module and runs
them all at once with `run_checks`. A check only looks: it reads state or
settings, asks for a device's existing health, or makes one cheap probe (a
DNS lookup, an HTTP GET, free disk space). It never starts, reconnects or
configures anything, so running the checks during the show is harmless.

A check answers `ok`, `warn` or `fail` with a detail line, and carries the
fix in operator language (what to click, which cable, which settings page).
"""

import logging
import threading
from collections.abc import Callable
from dataclasses import asdict, dataclass
from enum import StrEnum
from typing import Any

from baja_studio.core.clock import Clock

logger = logging.getLogger(__name__)

CHECK_TIMEOUT_SECONDS = 5.0
CRASHED_CHECK_FIX = "This is a bug in the studio: send a diagnostics bundle (home page)."


class CheckStatus(StrEnum):
    """How one check came out; `warn` never makes the studio "not ready"."""

    OK = "ok"
    WARN = "warn"
    FAIL = "fail"


@dataclass(frozen=True)
class CheckOutcome:
    """What a check's `run()` returns.

    `fix` replaces the check's own fix when the cause is more precise than
    what the check could say in advance (e.g. the program holding port 80).
    """

    status: CheckStatus
    detail: str = ""
    fix: str = ""


def passed(detail: str = "") -> CheckOutcome:
    """An `ok` outcome."""
    return CheckOutcome(CheckStatus.OK, detail)


def warning(detail: str, fix: str = "") -> CheckOutcome:
    """A `warn` outcome: worth a look, but the event can go ahead."""
    return CheckOutcome(CheckStatus.WARN, detail, fix)


def failed(detail: str, fix: str = "") -> CheckOutcome:
    """A `fail` outcome: something the event needs is not working."""
    return CheckOutcome(CheckStatus.FAIL, detail, fix)


@dataclass(frozen=True)
class ReadinessCheck:
    """One check a module declares.

    `id` is unique within the module (`camera:finish-ptz`); `link` is the page
    where the fix is made (`/cameras/config`), when there is one.
    """

    id: str
    label: str
    run: Callable[[], CheckOutcome]
    fix: str
    link: str | None = None


@dataclass(frozen=True)
class CheckResult:
    """A check's outcome as the readiness page shows it."""

    module: str
    id: str
    label: str
    status: CheckStatus
    detail: str
    # Empty when the check passed: there is nothing to fix.
    fix: str
    link: str | None
    duration_s: float

    @property
    def key(self) -> str:
        """`<module>.<id>`, unique across the studio."""
        return f"{self.module}.{self.id}"

    def as_state(self) -> dict[str, Any]:
        """JSON-ready, for `state.readiness.last_run`."""
        return {**asdict(self), "status": self.status.value, "key": self.key}


@dataclass(frozen=True)
class RunSummary:
    """The banner over a run's results."""

    fails: int
    warnings: int

    @property
    def ready(self) -> bool:
        """Warnings do not stop an event; failures do."""
        return self.fails == 0

    @property
    def banner(self) -> str:
        """ "READY" or "NOT READY (n fails, m warnings)"."""
        if self.ready:
            return "READY"
        return f"NOT READY ({_count(self.fails, 'fail')}, {_count(self.warnings, 'warning')})"


def summarise(results: list[CheckResult]) -> RunSummary:
    """Count the failures and warnings of a run."""
    return RunSummary(
        fails=sum(result.status == CheckStatus.FAIL for result in results),
        warnings=sum(result.status == CheckStatus.WARN for result in results),
    )


def newly_failed(previous: list[CheckResult], current: list[CheckResult]) -> list[str]:
    """Keys of the checks that were `ok` last time and `fail` now (the toast's lines)."""
    was_ok = {result.key for result in previous if result.status == CheckStatus.OK}
    return [
        result.key
        for result in current
        if result.status == CheckStatus.FAIL and result.key in was_ok
    ]


class _RunningCheck:
    """One check on its own thread, and what came of it."""

    def __init__(self, module: str, check: ReadinessCheck) -> None:
        self.module = module
        self.check = check
        self.result: CheckResult | None = None


def run_checks(
    checks: list[tuple[str, ReadinessCheck]],
    clock: Clock,
    timeout_seconds: float = CHECK_TIMEOUT_SECONDS,
    on_result: Callable[[CheckResult], None] | None = None,
) -> list[CheckResult]:
    """Run every `(module, check)` at once; return the results in the order given.

    Each check runs on its own daemon thread. One still running after
    `timeout_seconds` becomes `fail: timeout` and is left behind (a daemon,
    so it never holds up shutdown). A check that raises becomes a `fail` too.
    `on_result` hears about each result as it arrives, for the progress bar.
    """
    running = [_RunningCheck(module, check) for module, check in checks]
    finished = threading.Event()
    lock = threading.Lock()
    for item in running:
        thread = threading.Thread(
            target=_run_one,
            args=(item, clock, lock, finished),
            name=f"readiness:{item.module}.{item.check.id}",
            daemon=True,
        )
        thread.start()
    deadline = clock.monotonic() + timeout_seconds
    reported: set[int] = set()
    while True:
        # Clear before looking, so a check finishing in between still wakes the next wait.
        finished.clear()
        with lock:
            done = [(index, item.result) for index, item in enumerate(running) if item.result]
        for index, result in done:
            if index not in reported:
                reported.add(index)
                if on_result is not None:
                    on_result(result)
        remaining = deadline - clock.monotonic()
        if len(reported) == len(running) or remaining <= 1e-9:
            break
        clock.wait(finished, remaining)
    results = []
    with lock:
        for item in running:
            if item.result is None:
                item.result = _timed_out(item, timeout_seconds)
                if on_result is not None:
                    on_result(item.result)
            results.append(item.result)
    return results


def _run_one(
    item: _RunningCheck, clock: Clock, lock: threading.Lock, done: threading.Event
) -> None:
    check = item.check
    started = clock.monotonic()
    try:
        outcome = check.run()
    except Exception as error:
        logger.exception("readiness check %s.%s crashed", item.module, check.id)
        outcome = failed(f"the check itself failed: {type(error).__name__}: {error}")
        fix = CRASHED_CHECK_FIX
    else:
        fix = outcome.fix or check.fix
    result = CheckResult(
        module=item.module,
        id=check.id,
        label=check.label,
        status=outcome.status,
        detail=outcome.detail,
        fix="" if outcome.status == CheckStatus.OK else fix,
        link=check.link,
        duration_s=round(clock.monotonic() - started, 3),
    )
    with lock:
        # A check that answers after its timeout keeps the timeout result.
        if item.result is None:
            item.result = result
    done.set()


def _timed_out(item: _RunningCheck, timeout_seconds: float) -> CheckResult:
    return CheckResult(
        module=item.module,
        id=item.check.id,
        label=item.check.label,
        status=CheckStatus.FAIL,
        detail=f"timeout: no answer within {timeout_seconds:g} s",
        fix=item.check.fix,
        link=item.check.link,
        duration_s=timeout_seconds,
    )


def _count(number: int, word: str) -> str:
    return f"{number} {word}" + ("" if number == 1 else "s")
