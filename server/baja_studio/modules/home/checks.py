"""The home module's readiness checks: the PC and the service themselves.

Version, a writable data folder with room on its drive, a clock that agrees
with NTP, and a CPU and memory that are not flat out. Free space on the
playout folders' drives is playout's own check.
"""

from collections.abc import Callable
from pathlib import Path

from baja_studio import __version__
from baja_studio.core.clock import Clock
from baja_studio.core.readiness import CheckOutcome, ReadinessCheck, failed, passed, warning
from baja_studio.core.system_probes import SystemProbes, disk_space_outcome, writable_problem

CLOCK_WARN_SECONDS = 2.0
CLOCK_FAIL_SECONDS = 60.0
BUSY_PERCENT = 90.0


def home_checks(data_directory: Path, probes: SystemProbes, clock: Clock) -> list[ReadinessCheck]:
    """The checks, built afresh for each run (the probes are read when they run)."""
    return [
        ReadinessCheck(
            "version",
            "Studio version",
            lambda: passed(f"Baja Studio {__version__}"),
            fix="",
        ),
        ReadinessCheck(
            "data_folder",
            "Data folder is writable",
            lambda: _writable(data_directory),
            fix=(
                f"The studio cannot save in {data_directory}: check the drive is connected, "
                "not full and not read-only, then restart the Baja Studio service."
            ),
        ),
        ReadinessCheck(
            "data_disk",
            "Free space for the data folder",
            lambda: disk_space_outcome(
                f"the data folder ({data_directory})", probes.free_disk_bytes(data_directory)
            ),
            fix="Free space on that drive (old recordings, the Downloads folder) or move them.",
        ),
        ReadinessCheck(
            "clock",
            "PC clock is right",
            _clock_check(probes),
            fix=(
                "Windows Settings → Time & language → Date & time: turn on 'Set time "
                "automatically' and press 'Sync now'."
            ),
        ),
        ReadinessCheck(
            "load",
            "CPU and memory are not flat out",
            lambda: _load(probes, clock),
            fix=(
                "Task Manager → Processes: sort by CPU and by Memory and close what does not "
                "belong to the show (never OBS)."
            ),
        ),
    ]


def _writable(folder: Path) -> CheckOutcome:
    problem = writable_problem(folder)
    return failed(problem) if problem else passed(str(folder))


def _clock_check(probes: SystemProbes) -> Callable[[], CheckOutcome]:
    def check() -> CheckOutcome:
        try:
            offset = probes.ntp_offset(probes.ntp_server)
        except OSError:
            # Offline at the venue is normal; it says nothing about the clock.
            return passed(f"not checked: no answer from {probes.ntp_server} (no internet?)")
        detail = f"{abs(offset):.1f} s {'behind' if offset > 0 else 'ahead of'} {probes.ntp_server}"
        if abs(offset) >= CLOCK_FAIL_SECONDS:
            return failed(detail)
        if abs(offset) >= CLOCK_WARN_SECONDS:
            return warning(detail)
        return passed(detail)

    return check


def _load(probes: SystemProbes, clock: Clock) -> CheckOutcome:
    load = probes.system_load(clock)
    parts, busy = [], False
    for name, percent in (("CPU", load.cpu_percent), ("memory", load.memory_percent)):
        if percent is not None:
            parts.append(f"{name} {percent:.0f}%")
            busy = busy or percent >= BUSY_PERCENT
    if not parts:
        return passed("not measurable on this system")
    detail = ", ".join(parts)
    return warning(detail) if busy else passed(detail)
