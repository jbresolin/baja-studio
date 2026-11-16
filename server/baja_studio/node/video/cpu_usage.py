"""How busy the laptop's processors are, for the benchmark (no extra library).

Both systems keep counters of time spent idle and in total since boot;
the share of non-idle time between two readings is the CPU use over that
interval, all cores together (100 % = every core busy):

- Windows: `GetSystemTimes` (its kernel time includes the idle time);
- Linux: the first line of `/proc/stat`.

Anywhere else `SystemCpu.percent()` is None.
"""

import sys
from pathlib import Path

PROC_STAT = Path("/proc/stat")

if sys.platform == "win32":
    import ctypes
    from ctypes import wintypes

    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

    def _filetime_value(filetime: wintypes.FILETIME) -> int:
        return (filetime.dwHighDateTime << 32) | filetime.dwLowDateTime

    def read_cpu_times() -> tuple[int, int] | None:
        """(idle, total) time counters since boot, or None if Windows refused."""
        idle, kernel, user = wintypes.FILETIME(), wintypes.FILETIME(), wintypes.FILETIME()
        answered = _kernel32.GetSystemTimes(
            ctypes.byref(idle), ctypes.byref(kernel), ctypes.byref(user)
        )
        if not answered:
            return None
        return _filetime_value(idle), _filetime_value(kernel) + _filetime_value(user)

else:

    def read_cpu_times() -> tuple[int, int] | None:
        """(idle, total) jiffies since boot from /proc/stat, or None without it."""
        try:
            first_line = PROC_STAT.read_text(encoding="ascii").splitlines()[0]
        except (OSError, IndexError):
            return None
        return parse_proc_stat_line(first_line)


def parse_proc_stat_line(line: str) -> tuple[int, int] | None:
    """(idle, total) from `cpu  user nice system idle iowait irq softirq steal …`."""
    fields = line.split()
    if not fields or fields[0] != "cpu" or len(fields) < 5:
        return None
    values = [int(value) for value in fields[1:9]]
    idle = values[3] + (values[4] if len(values) > 4 else 0)  # idle + iowait
    return idle, sum(values)


def busy_percent(before: tuple[int, int], after: tuple[int, int]) -> float:
    """The share of non-idle time between two (idle, total) readings, in percent."""
    idle = after[0] - before[0]
    total = after[1] - before[1]
    if total <= 0:
        return 0.0
    return max(0.0, min(100.0, 100.0 * (1 - idle / total)))


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
        return busy_percent(before, now)
