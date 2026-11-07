"""Cheap, read-only looks at the PC itself, for the readiness checks.

Free disk space, whether a folder takes a new file, the clock's offset from
an NTP server, and how busy the CPU and memory are. Each real probe is a
plain function; modules take them through `SystemProbes`, so tests pass
fakes and never depend on the disk or network of the machine running them.
"""

import ctypes
import shutil
import socket
import struct
import sys
import tempfile
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from baja_studio.core.clock import Clock
from baja_studio.core.readiness import CheckOutcome, failed, passed, warning

GIGABYTE = 1024**3
DISK_FAIL_BELOW_BYTES = 20 * GIGABYTE
DISK_WARN_BELOW_BYTES = 50 * GIGABYTE
DEFAULT_NTP_SERVER = "pool.ntp.org"
NTP_PORT = 123
NTP_TIMEOUT_SECONDS = 2.0
# Seconds between 1900-01-01 (NTP's epoch) and 1970-01-01 (Unix's).
NTP_EPOCH_OFFSET = 2_208_988_800
CPU_SAMPLE_SECONDS = 0.5


def free_disk_bytes(path: Path) -> int:
    """Free bytes on the drive holding `path` (or its nearest existing parent folder)."""
    candidate = path
    while not candidate.exists() and candidate.parent != candidate:
        candidate = candidate.parent
    return shutil.disk_usage(candidate).free


def disk_space_outcome(where: str, free_bytes: int) -> CheckOutcome:
    """`fail` under 20 GB free, `warn` under 50 GB, else `ok`; `where` names the drive's use."""
    detail = f"{free_bytes / GIGABYTE:.1f} GB free on the drive of {where}"
    if free_bytes < DISK_FAIL_BELOW_BYTES:
        return failed(detail)
    if free_bytes < DISK_WARN_BELOW_BYTES:
        return warning(detail)
    return passed(detail)


def writable_problem(folder: Path) -> str | None:
    """Why a new file cannot be written in `folder`, or None when it can.

    Writes one byte to a temporary file, which is deleted at once: the only
    way to know for sure (permissions, a full or read-only drive).
    """
    if not folder.is_dir():
        return f"{folder} does not exist"
    try:
        with tempfile.TemporaryFile(dir=folder, prefix=".baja-readiness-") as probe:
            probe.write(b"x")
    except OSError as error:
        return f"cannot write in {folder}: {error.strerror or error}"
    return None


def sntp_request() -> bytes:
    """A 48-byte SNTP client request (version 4, mode 3)."""
    return bytes([0x23]) + bytes(47)


def sntp_offset_seconds(response: bytes, sent_at: float, received_at: float) -> float:
    """How far this PC's clock is behind the server (positive: ours is slow).

    `sent_at` and `received_at` are this PC's Unix times around the exchange;
    the response carries the server's receive and transmit times (RFC 4330).
    """
    if len(response) < 48:
        raise ValueError("the NTP answer is too short")
    server_received = _ntp_time(response[32:40])
    server_sent = _ntp_time(response[40:48])
    return ((server_received - sent_at) + (server_sent - received_at)) / 2


def _ntp_time(field: bytes) -> float:
    seconds, fraction = struct.unpack("!II", field)
    return float(seconds - NTP_EPOCH_OFFSET + fraction / 2**32)


def query_ntp_offset(server: str = DEFAULT_NTP_SERVER) -> float:
    """Ask `server` once over UDP (2 s limit); raises `OSError` when there is no answer."""
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as udp:
        udp.settimeout(NTP_TIMEOUT_SECONDS)
        sent_at = time.time()
        udp.sendto(sntp_request(), (server, NTP_PORT))
        response, _ = udp.recvfrom(512)
        received_at = time.time()
    try:
        return sntp_offset_seconds(response, sent_at, received_at)
    except ValueError as error:
        raise OSError(str(error)) from error


@dataclass(frozen=True)
class SystemLoad:
    """CPU and memory use in percent; None where this system cannot say."""

    cpu_percent: float | None
    memory_percent: float | None


def cpu_busy_percent(before: tuple[int, int], after: tuple[int, int]) -> float | None:
    """Busy share between two (idle, total) CPU time readings, in percent."""
    idle = after[0] - before[0]
    total = after[1] - before[1]
    if total <= 0:
        return None
    return round(100.0 * (total - idle) / total, 1)


def parse_proc_stat(text: str) -> tuple[int, int] | None:
    """(idle, total) jiffies from Linux's /proc/stat "cpu" line (idle includes iowait)."""
    for line in text.splitlines():
        parts = line.split()
        if parts and parts[0] == "cpu":
            values = [int(value) for value in parts[1:]]
            idle = values[3] + (values[4] if len(values) > 4 else 0)
            return idle, sum(values)
    return None


def parse_proc_meminfo(text: str) -> float | None:
    """Memory in use, in percent, from Linux's /proc/meminfo."""
    fields = {}
    for line in text.splitlines():
        name, _, rest = line.partition(":")
        if rest.split():
            fields[name] = int(rest.split()[0])
    total, available = fields.get("MemTotal"), fields.get("MemAvailable")
    if not total or available is None:
        return None
    return round(100.0 * (total - available) / total, 1)


def read_system_load(clock: Clock) -> SystemLoad:
    """Sample the CPU twice, half a second apart, and read memory use."""
    before = _cpu_times()
    clock.wait(threading.Event(), CPU_SAMPLE_SECONDS)
    after = _cpu_times()
    cpu = cpu_busy_percent(before, after) if before and after else None
    return SystemLoad(cpu, _memory_percent())


def _cpu_times() -> tuple[int, int] | None:
    if sys.platform == "win32":
        idle, kernel, user = (ctypes.c_ulonglong() for _ in range(3))
        if not ctypes.windll.kernel32.GetSystemTimes(
            ctypes.byref(idle), ctypes.byref(kernel), ctypes.byref(user)
        ):
            return None
        # Kernel time includes idle time.
        return idle.value, kernel.value + user.value
    try:
        return parse_proc_stat(Path("/proc/stat").read_text(encoding="ascii"))
    except OSError:
        return None


class _MemoryStatus(ctypes.Structure):
    _fields_ = [
        ("dwLength", ctypes.c_ulong),
        ("dwMemoryLoad", ctypes.c_ulong),
        ("ullTotalPhys", ctypes.c_ulonglong),
        ("ullAvailPhys", ctypes.c_ulonglong),
        ("ullTotalPageFile", ctypes.c_ulonglong),
        ("ullAvailPageFile", ctypes.c_ulonglong),
        ("ullTotalVirtual", ctypes.c_ulonglong),
        ("ullAvailVirtual", ctypes.c_ulonglong),
        ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
    ]


def _memory_percent() -> float | None:
    if sys.platform == "win32":
        status = _MemoryStatus()
        status.dwLength = ctypes.sizeof(_MemoryStatus)
        if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
            return None
        return float(status.dwMemoryLoad)
    try:
        return parse_proc_meminfo(Path("/proc/meminfo").read_text(encoding="ascii"))
    except OSError:
        return None


@dataclass(frozen=True)
class SystemProbes:
    """Every probe of the PC a readiness check may use; tests pass fakes."""

    free_disk_bytes: Callable[[Path], int] = free_disk_bytes
    ntp_offset: Callable[[str], float] = query_ntp_offset
    system_load: Callable[[Clock], SystemLoad] = read_system_load
    ntp_server: str = DEFAULT_NTP_SERVER
