"""Tests for finding who holds MediaMTX's ports (modules/feeds/ports.py).

On the streaming PC this reads `netstat -ano` and `tasklist`. The outputs
below are shaped like Windows' (in Portuguese, as on the streaming PC: no
word is parsed, only columns), and the commands are answered by a fake
runner, so nothing is run.
"""

import subprocess

from baja_studio.modules.feeds.config import FeedsSettings
from baja_studio.modules.feeds.ports import (
    FeedPort,
    FeedPortHolder,
    feed_ports,
    find_port_holders_windows,
    parse_netstat_listeners,
)

NETSTAT = """
Conexões ativas

  Proto  Endereço local         Endereço externo       Estado         PID
  TCP    0.0.0.0:1935           0.0.0.0:0              ESCUTANDO       4321
  TCP    127.0.0.1:8554         0.0.0.0:0              ESCUTANDO       5000
  TCP    192.168.10.20:50000    192.168.10.50:1935     ESTABELECIDA    4321
  TCP    [::]:1935              [::]:0                 ESCUTANDO       4321
  UDP    0.0.0.0:8890           *:*                                    6000
  UDP    [::]:5353              *:*                                    1200
"""
TASKLIST = {
    4321: '"obs64.exe","4321","N/A"\r\n',
    5000: '"mediamtx.exe","5000","BajaStudioFeeds"\r\n',
    6000: '"svchost.exe","6000","SSDPSRV,upnphost"\r\n',
}


def fake_run(arguments: list[str], timeout: float) -> "subprocess.CompletedProcess[bytes]":
    """Answers `netstat -ano` and `tasklist ... /FI "PID eq N"` like Windows."""
    if arguments[0] == "netstat":
        return subprocess.CompletedProcess(arguments, 0, NETSTAT.encode("utf-8"), b"")
    pid = int(arguments[-1].split()[-1])
    return subprocess.CompletedProcess(arguments, 0, TASKLIST[pid].encode("utf-8"), b"")


def test_mediamtx_needs_rtmp_srt_rtsp_its_api_and_webrtc() -> None:
    assert [port.describe() for port in feed_ports(FeedsSettings())] == [
        "TCP 1935 (RTMP)",
        "UDP 8890 (SRT)",
        "TCP 8554 (RTSP)",
        "TCP 9997 (control API)",
        "TCP 8889 (WebRTC)",
        "UDP 8189 (WebRTC media)",
    ]


def test_netstat_gives_tcp_listeners_and_bound_udp_ports_with_their_pids() -> None:
    # An established TCP connection (remote port 1935) is not a listener.
    assert parse_netstat_listeners(NETSTAT) == {
        ("TCP", 1935): [4321],
        ("TCP", 8554): [5000],
        ("UDP", 8890): [6000],
        ("UDP", 5353): [1200],
    }


def test_holders_are_named_with_their_services_and_mediamtx_itself_is_left_out() -> None:
    holders = find_port_holders_windows(feed_ports(FeedsSettings()), fake_run)
    # 8554 belongs to MediaMTX (our own service): not a clash.
    assert [(holder.port.port, holder.holder) for holder in holders] == [
        (1935, "obs64.exe (pid 4321)"),
        (8890, "svchost.exe (service SSDPSRV, upnphost)"),
    ]


def test_the_message_says_what_to_do_and_which_setting_to_change() -> None:
    holder = FeedPortHolder(FeedPort("UDP", 8890, "SRT", "srt_port"), "vlc.exe (pid 9)")
    assert holder.message == (
        "UDP 8890 (SRT) is in use by vlc.exe (pid 9): close it, or pick another port "
        "(srt_port) in the feeds settings"
    )
    assert holder.as_state()["port"] == 8890


def test_when_windows_cannot_say_nobody_is_blamed() -> None:
    def failing(arguments: list[str], timeout: float) -> "subprocess.CompletedProcess[bytes]":
        raise OSError("netstat not found")

    assert find_port_holders_windows(feed_ports(FeedsSettings()), failing) == []
