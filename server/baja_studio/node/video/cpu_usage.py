"""How busy the laptop's processors are, for the benchmark.

The studio's readiness checks already read the system's CPU time counters
(`core/system_probes.read_cpu_times`: `GetSystemTimes` on Windows,
`/proc/stat` on Linux). The benchmark reads them once a second and keeps the
share of non-idle time in between: the CPU use over that second, all cores
together (100 % = every core busy).
"""

from baja_studio.core.system_probes import cpu_busy_percent, read_cpu_times


class SystemCpu:
    """CPU use between calls: `percent()` is the use since the previous call (or creation)."""

    def __init__(self) -> None:
        """Take the first reading."""
        self._last = read_cpu_times()

    def percent(self) -> float | None:
        """CPU use since the last reading, in percent; None where it cannot be read."""
        now = read_cpu_times()
        before, self._last = self._last, now
        if before is None or now is None:
            return None
        return cpu_busy_percent(before, now)
