"""Tests for finding who holds Caddy's ports (modules/gateway/ports.py).

`netstat` and `tasklist` only exist on Windows; the command runner is
replaced by one answering what they print there, including on a Windows in
Portuguese, whose words differ but whose columns do not.
"""

import subprocess

from baja_studio.core.system_services import CommandRunner
from baja_studio.modules.gateway.ports import (
    PortHolder,
    find_port_holders,
    no_port_holders,
    parse_listening_pids,
    parse_tasklist_row,
)

NETSTAT = """
Active Connections

  Proto  Local Address          Foreign Address        State           PID
  TCP    0.0.0.0:80             0.0.0.0:0              LISTENING       4
  TCP    0.0.0.0:135            0.0.0.0:0              LISTENING       1012
  TCP    0.0.0.0:443            0.0.0.0:0              LISTENING       7400
  TCP    0.0.0.0:2019           0.0.0.0:0              LISTENING       8100
  TCP    192.168.10.20:80       192.168.10.31:51544    TIME_WAIT       0
  TCP    192.168.10.20:51001    142.250.0.1:443        ESTABLISHED     3300
  TCP    [::]:80                [::]:0                 LISTENING       4
  UDP    0.0.0.0:443            *:*                                    7400
"""

NETSTAT_PORTUGUESE = """
Conexões ativas

  Proto  Endereço local         Endereço externo       Estado          PID
  TCP    0.0.0.0:80             0.0.0.0:0              ESCUTANDO       5120
  TCP    127.0.0.1:443          0.0.0.0:0              ESCUTANDO       8100
"""

TASKLIST = {
    "7400": '"Skype.exe","7400","N/A"\r\n',
    "5120": '"svchost.exe","5120","W3SVC,WAS"\r\n',
    "8100": '"caddy.exe","8100","N/D"\r\n',
}


def runner(netstat: str, calls: list[list[str]] | None = None) -> CommandRunner:
    """`netstat -ano` printing `netstat`, and `tasklist` knowing the processes in TASKLIST."""

    def run(arguments: list[str], timeout: float) -> "subprocess.CompletedProcess[bytes]":
        if calls is not None:
            calls.append(arguments)
        if arguments[0] == "netstat":
            output = netstat
        else:
            pid = arguments[-1].removeprefix("PID eq ")
            output = TASKLIST.get(pid, "INFO: No tasks are running which match the criteria.\r\n")
        return subprocess.CompletedProcess(arguments, 0, output.encode(), b"")

    return run


def test_listeners_are_found_by_their_empty_remote_address() -> None:
    assert parse_listening_pids(NETSTAT, [80, 443]) == {80: [4], 443: [7400]}


def test_a_translated_netstat_reads_the_same() -> None:
    assert parse_listening_pids(NETSTAT_PORTUGUESE, [80, 443]) == {80: [5120], 443: [8100]}


def test_a_tasklist_row_gives_the_image_and_its_services() -> None:
    assert parse_tasklist_row(TASKLIST["5120"], 5120) == ("svchost.exe", ("W3SVC", "WAS"))
    assert parse_tasklist_row(TASKLIST["7400"], 7400) == ("Skype.exe", ())
    assert parse_tasklist_row("INFO: No tasks are running.", 7400) is None


def test_the_holders_of_port_80_and_443_are_named() -> None:
    calls: list[list[str]] = []

    holders = find_port_holders([80, 443], runner(NETSTAT, calls))

    assert holders == [PortHolder(80, 4, "System"), PortHolder(443, 7400, "Skype.exe")]
    # HTTP.sys needs no tasklist: pid 4 is always the System process.
    assert [call[-1] for call in calls[1:]] == ["PID eq 7400"]


def test_caddy_itself_is_not_a_conflict() -> None:
    holders = find_port_holders([80, 443], runner(NETSTAT_PORTUGUESE))

    assert holders == [PortHolder(80, 5120, "svchost.exe", ("W3SVC", "WAS"))]


def test_a_process_gone_before_tasklist_is_named_by_its_pid() -> None:
    netstat = "  TCP    0.0.0.0:80    0.0.0.0:0    LISTENING    9999\n"

    assert find_port_holders([80], runner(netstat)) == [PortHolder(80, 9999, "pid 9999")]


def test_without_netstat_nothing_is_reported() -> None:
    def missing(arguments: list[str], timeout: float) -> "subprocess.CompletedProcess[bytes]":
        raise FileNotFoundError("netstat")

    assert find_port_holders([80], missing) == []
    assert no_port_holders([80]) == []


def test_each_holder_comes_with_its_fix() -> None:
    http_sys = PortHolder(80, 4, "System")
    service = PortHolder(80, 5120, "svchost.exe", ("W3SVC", "WAS"))
    program = PortHolder(443, 7400, "Skype.exe")

    assert http_sys.description == "Windows HTTP.sys (usually IIS)"
    assert http_sys.fix.startswith('Stop and disable "World Wide Web Publishing Service" (IIS)')
    assert service.description == "svchost.exe (service W3SVC, WAS)"
    assert service.fix.startswith("Stop and disable W3SVC, WAS in the Services app, then start")
    assert program.description == "Skype.exe (pid 7400)"
    assert program.fix.startswith("Close Skype.exe (Task Manager, pid 7400) or change its port")
    for holder in (http_sys, service, program):
        assert holder.fix.endswith(
            'then start "Baja Studio gateway" in the Services app '
            '(docs/install.md, "Port 80 or 443 in use").'
        )
        assert holder.as_state()["fix"] == holder.fix
