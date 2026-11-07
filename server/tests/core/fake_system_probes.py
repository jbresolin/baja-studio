"""A stand-in for the PC probes the readiness checks use (disk, clock, CPU and memory).

The real probes would read the disk and network of whatever machine runs the
tests (a CI runner has far less than 20 GB free). This fake answers from plain
attributes that a test sets: "the drive has 10 GB free", "NTP does not answer".
"""

from dataclasses import dataclass, field
from pathlib import Path

from baja_studio.core.clock import Clock
from baja_studio.core.system_probes import GIGABYTE, SystemLoad, SystemProbes


@dataclass
class FakeSystemProbes:
    """Answers set by the test; healthy by default."""

    free_bytes: int = 200 * GIGABYTE
    # Free space per folder, for tests about one drive in particular.
    free_bytes_by_path: dict[Path, int] = field(default_factory=dict)
    # Seconds the PC is behind NTP; None means NTP does not answer.
    ntp_offset_seconds: float | None = 0.2
    cpu_percent: float | None = 15.0
    memory_percent: float | None = 40.0
    asked_ntp_server: list[str] = field(default_factory=list)

    def free_disk_bytes(self, path: Path) -> int:
        return self.free_bytes_by_path.get(path, self.free_bytes)

    def ntp_offset(self, server: str) -> float:
        self.asked_ntp_server.append(server)
        if self.ntp_offset_seconds is None:
            raise OSError("timed out")
        return self.ntp_offset_seconds

    def system_load(self, clock: Clock) -> SystemLoad:
        return SystemLoad(self.cpu_percent, self.memory_percent)

    def probes(self) -> SystemProbes:
        """The bundle modules take, answering from this fake."""
        return SystemProbes(
            free_disk_bytes=self.free_disk_bytes,
            ntp_offset=self.ntp_offset,
            system_load=self.system_load,
        )
