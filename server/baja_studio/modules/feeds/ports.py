"""The ports MediaMTX needs, and who else holds one of them.

MediaMTX exits when one of its ports is taken, at start and on a config
reload alike (measured with v1.21.3), and NSSM keeps restarting it, so the
only trace is a line in its log. Two moments matter:

- MediaMTX is not answering: whoever listens on its ports is the likely
  cause, and the Feeds page names it with the fix;
- a settings change moves a port: the new port must be free *before* the
  config is written, or the reload would stop every feed.

On Windows the holders come from `netstat -ano` and `tasklist` (parsed as in
the gateway, without reading any translated word). Elsewhere (a developer's
checkout) a port is tried by binding it, which says "taken" but not by whom.
"""

import socket
import subprocess
import sys
from collections.abc import Callable, Iterable
from dataclasses import dataclass

from baja_studio.core.system_services import CommandRunner, run_hidden
from baja_studio.modules.feeds.config import FeedsSettings
from baja_studio.modules.gateway.ports import (
    LISTENING_REMOTE_ADDRESSES,
    PORT_QUERY_TIMEOUT_SECONDS,
    SYSTEM_PID,
    parse_tasklist_row,
)

FEEDS_SERVICE_NAME = "Baja Studio feeds"
MEDIAMTX_IMAGE = "mediamtx.exe"


@dataclass(frozen=True)
class FeedPort:
    """One port MediaMTX opens, and what for."""

    protocol: str  # "TCP" or "UDP"
    port: int
    purpose: str
    setting: str

    def describe(self) -> str:
        """e.g. "TCP 1935 (RTMP)"."""
        return f"{self.protocol} {self.port} ({self.purpose})"


def feed_ports(settings: FeedsSettings) -> list[FeedPort]:
    """Every port the config written from `settings` makes MediaMTX open."""
    return [
        FeedPort("TCP", settings.rtmp_port, "RTMP", "rtmp_port"),
        FeedPort("UDP", settings.srt_port, "SRT", "srt_port"),
        FeedPort("TCP", settings.rtsp_port, "RTSP", "rtsp_port"),
        FeedPort("TCP", settings.api_port, "control API", "api_port"),
    ]


@dataclass(frozen=True)
class FeedPortHolder:
    """Another program on a port MediaMTX needs."""

    port: FeedPort
    holder: str

    @property
    def message(self) -> str:
        """In operator words, with what to do."""
        return (
            f"{self.port.describe()} is in use by {self.holder}: close it, or pick another "
            f"port ({self.port.setting}) in the feeds settings"
        )

    def as_state(self) -> dict[str, object]:
        """For `state.feeds.service.port_conflicts`."""
        return {
            "protocol": self.port.protocol,
            "port": self.port.port,
            "purpose": self.port.purpose,
            "holder": self.holder,
            "message": self.message,
        }


PortHolderFinder = Callable[[list[FeedPort]], list[FeedPortHolder]]


def parse_netstat_listeners(netstat_output: str) -> dict[tuple[str, int], list[int]]:
    """`netstat -ano` -> {(protocol, port): [pids]} for TCP listeners and bound UDP sockets."""
    found: dict[tuple[str, int], list[int]] = {}
    for line in netstat_output.splitlines():
        columns = line.split()
        if len(columns) == 5 and columns[0] == "TCP" and columns[2] in LISTENING_REMOTE_ADDRESSES:
            local, pid_text = columns[1], columns[4]
        elif len(columns) == 4 and columns[0] == "UDP":
            # UDP  0.0.0.0:8890  *:*  <pid>: no state column.
            local, pid_text = columns[1], columns[3]
        else:
            continue
        port_text = local.rpartition(":")[2]
        if port_text.isdigit() and pid_text.isdigit():
            pids = found.setdefault((columns[0], int(port_text)), [])
            if int(pid_text) not in pids:
                pids.append(int(pid_text))
    return found


def find_port_holders_windows(
    ports: Iterable[FeedPort], run: CommandRunner = run_hidden
) -> list[FeedPortHolder]:
    """Programs other than MediaMTX on `ports`; empty when Windows cannot say."""
    try:
        netstat = run(["netstat", "-ano"], PORT_QUERY_TIMEOUT_SECONDS)
    except (OSError, subprocess.TimeoutExpired):
        return []
    if netstat.returncode != 0:
        return []
    listeners = parse_netstat_listeners(netstat.stdout.decode("utf-8", errors="replace"))
    holders = []
    for port in ports:
        for pid in listeners.get((port.protocol, port.port), []):
            image, services = _describe_process(pid, run)
            if image.lower() == MEDIAMTX_IMAGE:
                continue
            who = f"{image} (service {', '.join(services)})" if services else f"{image} (pid {pid})"
            holders.append(FeedPortHolder(port, who))
    return holders


def _describe_process(pid: int, run: CommandRunner) -> tuple[str, tuple[str, ...]]:
    if pid == SYSTEM_PID:
        return "Windows (System)", ()
    try:
        result = run(
            ["tasklist", "/SVC", "/FO", "CSV", "/NH", "/FI", f"PID eq {pid}"],
            PORT_QUERY_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.TimeoutExpired):
        return f"pid {pid}", ()
    row = parse_tasklist_row(result.stdout.decode("utf-8", errors="replace"), pid)
    return row if row is not None else (f"pid {pid}", ())


def find_port_holders_by_binding(ports: Iterable[FeedPort]) -> list[FeedPortHolder]:
    """Away from Windows: a port we cannot bind is held by someone we cannot name."""
    holders = []
    for port in ports:
        kind = socket.SOCK_STREAM if port.protocol == "TCP" else socket.SOCK_DGRAM
        with socket.socket(socket.AF_INET, kind) as probe:
            try:
                probe.bind(("0.0.0.0", port.port))
            except OSError:
                holders.append(FeedPortHolder(port, "another program"))
    return holders


installed_port_holders: PortHolderFinder = (
    find_port_holders_windows if sys.platform == "win32" else find_port_holders_by_binding
)
