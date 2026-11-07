"""Who holds port 80 or 443 when Caddy cannot start.

Caddy exits at once when another program listens on a port it needs, and
NSSM keeps retrying it; the only trace is a line in `caddy-stderr.log`. When
Caddy's admin API does not answer, the gateway asks Windows which processes
listen on those ports (`netstat -ano`, then `tasklist /SVC` for each one) so
the home page can name the culprit and the fix.

Neither program's output is parsed for words: Windows translates them
("LISTENING" is "ESCUTANDO" in Portuguese). A listener is a TCP row whose
remote address is `0.0.0.0:0` or `[::]:0`, and `tasklist` is read as CSV.
"""

import csv
import subprocess
import sys
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from typing import Any

from baja_studio.core.system_services import CommandRunner, run_hidden

PORT_QUERY_TIMEOUT_SECONDS = 10.0
# HTTP.sys, the kernel's web listener (IIS and anything registering URLs with Windows), runs as
# the System process.
SYSTEM_PID = 4
LISTENING_REMOTE_ADDRESSES = frozenset({"0.0.0.0:0", "[::]:0"})
GATEWAY_SERVICE_NAME = "Baja Studio gateway"
FIX_DOCUMENT = 'docs/install.md, "Port 80 or 443 in use"'


@dataclass(frozen=True)
class PortHolder:
    """A process other than Caddy listening on a port the gateway needs."""

    port: int
    pid: int
    image: str
    services: tuple[str, ...] = field(default=())

    @property
    def description(self) -> str:
        """Who it is, in words a person can look for in Task Manager or Services."""
        if self.pid == SYSTEM_PID:
            return "Windows HTTP.sys (usually IIS)"
        if self.services:
            return f"{self.image} (service {', '.join(self.services)})"
        return f"{self.image} (pid {self.pid})"

    @property
    def fix(self) -> str:
        """What to do about it, ending with how to bring the gateway back."""
        if self.pid == SYSTEM_PID:
            action = (
                'Stop and disable "World Wide Web Publishing Service" (IIS) in the Services app '
                "(if it is not there, `netsh http show servicestate` names who registered "
                f"port {self.port})"
            )
        elif self.services:
            action = f"Stop and disable {', '.join(self.services)} in the Services app"
        else:
            action = f"Close {self.image} (Task Manager, pid {self.pid}) or change its port"
        return (
            f'{action}, then start "{GATEWAY_SERVICE_NAME}" in the Services app ({FIX_DOCUMENT}).'
        )

    def as_state(self) -> dict[str, Any]:
        """For `state.gateway.caddy.port_conflicts`."""
        return {
            "port": self.port,
            "pid": self.pid,
            "image": self.image,
            "services": list(self.services),
            "holder": self.description,
            "fix": self.fix,
        }


PortHolderFinder = Callable[[Iterable[int]], list[PortHolder]]


def parse_listening_pids(netstat_output: str, ports: Iterable[int]) -> dict[int, list[int]]:
    """`netstat -ano` → {port: [pids listening on it]}, only for `ports`, in order of appearance."""
    wanted = set(ports)
    listeners: dict[int, list[int]] = {}
    for line in netstat_output.splitlines():
        columns = line.split()
        # TCP  <local>  <remote>  <state, translated>  <pid>
        if len(columns) != 5 or columns[0] != "TCP":
            continue
        if columns[2] not in LISTENING_REMOTE_ADDRESSES:
            continue
        _, _, port_text = columns[1].rpartition(":")
        if not (port_text.isdigit() and columns[4].isdigit()):
            continue
        port, pid = int(port_text), int(columns[4])
        if port in wanted and pid not in listeners.setdefault(port, []):
            listeners[port].append(pid)
    return listeners


def parse_tasklist_row(tasklist_output: str, pid: int) -> tuple[str, tuple[str, ...]] | None:
    """`tasklist /SVC /FO CSV /NH /FI "PID eq <pid>"` → (image, services), or None if gone."""
    for row in csv.reader(tasklist_output.splitlines()):
        if len(row) >= 2 and row[1].strip() == str(pid):
            services = row[2].split(",") if len(row) > 2 else []
            # "N/A" (translated) when the process hosts no service.
            names = tuple(name.strip() for name in services if name.strip() and "/" not in name)
            return row[0].strip(), names
    return None


def find_port_holders(ports: Iterable[int], run: CommandRunner = run_hidden) -> list[PortHolder]:
    """The processes other than Caddy listening on `ports`; empty when Windows cannot say."""
    try:
        netstat = run(["netstat", "-ano"], PORT_QUERY_TIMEOUT_SECONDS)
    except (OSError, subprocess.TimeoutExpired):
        return []
    if netstat.returncode != 0:
        return []
    listeners = parse_listening_pids(netstat.stdout.decode("utf-8", errors="replace"), ports)
    holders = []
    for port, pids in sorted(listeners.items()):
        for pid in pids:
            image, services = _describe_process(pid, run)
            if image.lower() == "caddy.exe":
                continue
            holders.append(PortHolder(port, pid, image, services))
    return holders


def _describe_process(pid: int, run: CommandRunner) -> tuple[str, tuple[str, ...]]:
    if pid == SYSTEM_PID:
        return "System", ()
    try:
        result = run(
            ["tasklist", "/SVC", "/FO", "CSV", "/NH", "/FI", f"PID eq {pid}"],
            PORT_QUERY_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.TimeoutExpired):
        return f"pid {pid}", ()
    row = parse_tasklist_row(result.stdout.decode("utf-8", errors="replace"), pid)
    return row if row is not None else (f"pid {pid}", ())


def no_port_holders(ports: Iterable[int]) -> list[PortHolder]:
    """Away from Windows there is no gateway service to block."""
    return []


installed_port_holders: PortHolderFinder = (
    find_port_holders if sys.platform == "win32" else no_port_holders
)
