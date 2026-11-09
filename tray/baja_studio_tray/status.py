"""From `/api/health` to the badge colour and the tooltip.

The service answers `{status, version, modules: [{name, title, state, message}]}`
with `status` "ok" or "degraded". The tray shows:

- green: `ok`;
- amber: `degraded` (a module is degraded or offline);
- red: the service does not answer, or any other status (a future `fail`).
"""

from dataclasses import dataclass
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


def status_from_health(health: dict[str, Any] | None, labels: Labels) -> TrayStatus:
    """The badge and one-line summary for a health answer (None: no answer)."""
    if health is None:
        return TrayStatus(Badge.RED, None, labels.service_unreachable)
    version = health.get("version")
    version = str(version) if version else None
    status = health.get("status")
    if status == "ok":
        return TrayStatus(Badge.GREEN, version, labels.all_ok)
    if status == "degraded":
        titles = [
            str(module.get("title") or module.get("name"))
            for module in health.get("modules") or []
            if module.get("state") in PROBLEM_STATES
        ]
        return TrayStatus(Badge.AMBER, version, labels.modules_with_problems(titles))
    return TrayStatus(Badge.RED, version, labels.unexpected_status(str(status)))
