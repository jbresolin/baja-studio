"""From `/api/health` and the last readiness run to the badge colour and the tooltip.

The service answers `{status, version, modules: [{name, title, state, message}]}`
with `status` "ok" or "degraded", and `/api/readiness/last` gives the last
finished readiness run (`{started_at, fails, warnings, ...}`) or nothing.
The tray shows:

- green: `ok`, and the last readiness run (if any) had no warning or failure;
- amber: `degraded` (a module is degraded or offline), or the last readiness
  run had warnings or failures; the tooltip then says how many, and when;
- red: the service does not answer, or any other status (a future `fail`).
"""

from dataclasses import dataclass
from datetime import datetime, tzinfo
from enum import StrEnum
from typing import Any

from baja_studio_tray.labels import Labels

# Windows cuts tray tooltips at 127 characters.
TOOLTIP_LIMIT = 127
PROBLEM_STATES = frozenset({"degraded", "offline"})


class Badge(StrEnum):
    """The status dot's colour."""

    GREEN = "green"
    AMBER = "amber"
    RED = "red"


@dataclass(frozen=True)
class TrayStatus:
    """What the icon shows after one poll."""

    badge: Badge
    version: str | None
    summary: str

    def tooltip(self, labels: Labels) -> str:
        """`Baja Studio 0.2.0: everything ok`, cut to what Windows shows."""
        name = f"Baja Studio {self.version}" if self.version else "Baja Studio"
        text = f"{name}: {self.summary}"
        if len(text) > TOOLTIP_LIMIT:
            text = text[: TOOLTIP_LIMIT - 1] + "…"
        return text


def status_from_health(
    health: dict[str, Any] | None,
    labels: Labels,
    last_readiness_run: dict[str, Any] | None = None,
    timezone: tzinfo | None = None,
) -> TrayStatus:
    """The badge and one-line summary for a health answer (None: no answer).

    `last_readiness_run` is `/api/readiness/last`'s run (None: none yet); its
    time is shown in `timezone` (None: this PC's).
    """
    if health is None:
        return TrayStatus(Badge.RED, None, labels.service_unreachable)
    version = health.get("version")
    version = str(version) if version else None
    status = health.get("status")
    readiness = readiness_summary(last_readiness_run, labels, timezone)
    if status == "ok":
        if readiness is None:
            return TrayStatus(Badge.GREEN, version, labels.all_ok)
        return TrayStatus(Badge.AMBER, version, readiness)
    if status == "degraded":
        titles = [
            str(module.get("title") or module.get("name"))
            for module in health.get("modules") or []
            if module.get("state") in PROBLEM_STATES
        ]
        summary = labels.modules_with_problems(titles)
        if readiness is not None:
            summary = f"{summary} · {readiness}"
        return TrayStatus(Badge.AMBER, version, summary)
    return TrayStatus(Badge.RED, version, labels.unexpected_status(str(status)))


def readiness_summary(
    last_run: dict[str, Any] | None, labels: Labels, timezone: tzinfo | None = None
) -> str | None:
    """`readiness at 14:32: 2 warnings, 1 failure`; None when the run found nothing to say."""
    if not last_run:
        return None
    fails = _count(last_run.get("fails"))
    warnings = _count(last_run.get("warnings"))
    if not fails and not warnings:
        return None
    counts = []
    if warnings:
        counts.append(labels.warnings_count(warnings))
    if fails:
        counts.append(labels.failures_count(fails))
    return labels.readiness_result(_clock_time(last_run.get("started_at"), timezone), counts)


def _count(value: Any) -> int:
    return value if isinstance(value, int) and value > 0 else 0


def _clock_time(timestamp: Any, timezone: tzinfo | None) -> str:
    """`14:32` in the PC's time; `?` for a missing or unreadable timestamp."""
    try:
        moment = datetime.fromisoformat(str(timestamp))
    except ValueError:
        return "?"
    if moment.tzinfo is None:
        return moment.strftime("%H:%M")
    return moment.astimezone(timezone).strftime("%H:%M")
