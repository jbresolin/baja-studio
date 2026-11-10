"""The hosts module's pre-event checks: seats set up, and a hosts screen open for each.

Both only read the settings and the screens the module already tracks.
"""

from collections.abc import Callable

from baja_studio.core.readiness import CheckOutcome, ReadinessCheck, failed, passed, warning
from baja_studio.modules.hosts.config import HostsSettings

CONTROL_PAGE = "/hosts/control"


def hosts_checks(
    settings: HostsSettings, online_seats: Callable[[], dict[int, int]]
) -> list[ReadinessCheck]:
    """`seats` and `screens`."""
    return [
        ReadinessCheck(
            id="seats",
            label="Hosts seats",
            run=lambda: _check_seats(settings),
            fix="Open Hosts → Seats, switch on the seats in use and type their names.",
            link=CONTROL_PAGE,
        ),
        ReadinessCheck(
            id="screens",
            label="Hosts screens",
            run=lambda: _check_screens(settings, online_seats()),
            fix=(
                "On the hosts' monitor open /hosts, tap the seats it is for, and press Fullscreen."
            ),
            link="/hosts",
        ),
    ]


def _check_seats(settings: HostsSettings) -> CheckOutcome:
    enabled = [seat for seat in settings.seats if seat.enabled]
    if not enabled:
        return failed("no seat is switched on, so production cannot message anyone")
    unnamed = [str(seat.id) for seat in enabled if not seat.screen_name.strip()]
    if unnamed:
        return warning(f"seat {', '.join(unnamed)} has no screen name")
    return passed(", ".join(seat.screen_name for seat in enabled))


def _check_screens(settings: HostsSettings, online: dict[int, int]) -> CheckOutcome:
    enabled = [seat for seat in settings.seats if seat.enabled]
    if not enabled:
        return warning("not checked: no seat is switched on")
    if not online:
        return warning("no hosts screen is open")
    missing = [seat.screen_name or f"seat {seat.id}" for seat in enabled if seat.id not in online]
    if missing:
        return warning(f"no hosts screen shows {', '.join(missing)}")
    return passed(f"every seat in use is on a hosts screen ({len(enabled)})")
