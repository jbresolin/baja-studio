"""Tests for the PC probes the readiness checks use (core/system_probes.py).

Disk space thresholds, the writable-folder probe on a real temporary folder,
and the parsers behind the clock and load probes. Nothing here uses the
network: the NTP answer is built byte by byte in the test.
"""

import os
import struct
import sys
from pathlib import Path

import pytest

from baja_studio.core.clock import SystemClock
from baja_studio.core.readiness import CheckStatus
from baja_studio.core.system_probes import (
    GIGABYTE,
    NTP_EPOCH_OFFSET,
    cpu_busy_percent,
    disk_space_outcome,
    free_disk_bytes,
    parse_proc_meminfo,
    parse_proc_stat,
    read_system_load,
    sntp_offset_seconds,
    sntp_request,
    writable_problem,
)
from baja_studio.core.system_probes import _cpu_times as cpu_times
from baja_studio.core.system_probes import _memory_percent as memory_percent

windows_only = pytest.mark.skipif(
    sys.platform != "win32",
    reason="reads Windows' own counters (GetSystemTimes, GlobalMemoryStatusEx)",
)


@pytest.mark.parametrize(
    ("free_gigabytes", "expected"),
    [
        (19.9, CheckStatus.FAIL),
        (20, CheckStatus.WARN),
        (49.9, CheckStatus.WARN),
        (50, CheckStatus.OK),
    ],
)
def test_disk_space_fails_under_20_gb_and_warns_under_50_gb(
    free_gigabytes: float, expected: CheckStatus
) -> None:
    outcome = disk_space_outcome("the data folder", int(free_gigabytes * GIGABYTE))

    assert outcome.status == expected


def test_disk_space_says_how_much_is_free_and_where() -> None:
    outcome = disk_space_outcome("the clips (D:\\clips)", 75 * GIGABYTE)

    assert outcome.detail == "75.0 GB free on the drive of the clips (D:\\clips)"


def test_free_space_of_a_folder_not_made_yet_is_read_from_its_parent(tmp_path: Path) -> None:
    # A clip folder that does not exist yet still lives on some drive.
    assert free_disk_bytes(tmp_path / "not" / "made" / "yet") > 0


def test_a_normal_folder_is_writable_and_keeps_no_probe_file(tmp_path: Path) -> None:
    assert writable_problem(tmp_path) is None
    assert list(tmp_path.iterdir()) == []


def test_a_missing_folder_is_not_writable(tmp_path: Path) -> None:
    assert writable_problem(tmp_path / "gone") == f"{tmp_path / 'gone'} does not exist"


@pytest.mark.skipif(
    sys.platform == "win32" or getattr(os, "geteuid", lambda: 0)() == 0,
    reason="read-only folders need POSIX permissions and a user that respects them",
)
def test_a_read_only_folder_is_not_writable(tmp_path: Path) -> None:
    folder = tmp_path / "locked"
    folder.mkdir()
    folder.chmod(0o500)
    try:
        problem = writable_problem(folder)
    finally:
        folder.chmod(0o700)

    assert problem is not None
    assert problem.startswith(f"cannot write in {folder}")


def ntp_answer(server_received: float, server_sent: float) -> bytes:
    """A 48-byte NTP answer carrying the server's receive and transmit times."""

    def timestamp(unix_time: float) -> bytes:
        ntp_time = unix_time + NTP_EPOCH_OFFSET
        seconds = int(ntp_time)
        return struct.pack("!II", seconds, int((ntp_time - seconds) * 2**32))

    return bytes(32) + timestamp(server_received) + timestamp(server_sent)


def test_the_ntp_request_is_a_version_4_client_packet() -> None:
    request = sntp_request()

    assert len(request) == 48
    # Leap indicator 0, version 4, mode 3 (client): 00 100 011.
    assert request[0] == 0b00_100_011


def test_the_clock_offset_is_worked_out_from_the_four_timestamps() -> None:
    # We sent at 1000.0 and heard back at 1000.2 (our clock). The server, whose
    # clock is 5 s ahead of ours, received at 1005.1 and answered at 1005.1.
    answer = ntp_answer(server_received=1_700_001_005.1, server_sent=1_700_001_005.1)

    offset = sntp_offset_seconds(answer, sent_at=1_700_001_000.0, received_at=1_700_001_000.2)

    # Positive: our clock is 5 s behind.
    assert offset == pytest.approx(5.0, abs=1e-3)


def test_a_short_ntp_answer_is_refused() -> None:
    with pytest.raises(ValueError, match="too short"):
        sntp_offset_seconds(bytes(20), 0.0, 0.0)


def test_cpu_use_is_the_busy_share_between_two_readings() -> None:
    # (idle, total) CPU time: between the readings, 100 ticks passed, 25 of them idle.
    assert cpu_busy_percent((1000, 4000), (1025, 4100)) == 75.0
    # No time passed: nothing can be said.
    assert cpu_busy_percent((1000, 4000), (1000, 4000)) is None


def test_linux_cpu_times_count_iowait_as_idle() -> None:
    # Columns: user nice system idle iowait irq softirq.
    text = "cpu  100 0 50 800 50 0 0\ncpu0 100 0 50 800 50 0 0\n"

    assert parse_proc_stat(text) == (850, 1000)


def test_linux_memory_use_counts_what_is_not_available() -> None:
    text = "MemTotal:       16000000 kB\nMemFree:  1000000 kB\nMemAvailable:    4000000 kB\n"

    assert parse_proc_meminfo(text) == 75.0
    assert parse_proc_meminfo("MemFree: 10 kB\n") is None


# --- the real Windows readings (CI runs these on windows-latest) ----------------------


@windows_only
def test_windows_cpu_times_are_idle_within_total() -> None:
    # GetSystemTimes: idle time is part of kernel time, so idle never exceeds the total.
    times = cpu_times()

    assert times is not None
    idle, total = times
    assert 0 <= idle <= total
    assert total > 0


@windows_only
def test_windows_memory_in_use_is_a_plausible_percentage() -> None:
    # GlobalMemoryStatusEx: some memory is in use, and never all of it.
    percent = memory_percent()

    assert percent is not None
    assert 0 < percent < 100


@windows_only
def test_windows_system_load_reads_cpu_and_memory() -> None:
    # The real probe samples the CPU twice, half a second apart (real time:
    # nothing here depends on how long it takes, only on the readings).
    load = read_system_load(SystemClock())

    assert load.cpu_percent is not None
    assert 0 <= load.cpu_percent <= 100
    assert load.memory_percent is not None
    assert 0 < load.memory_percent < 100
