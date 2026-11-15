"""Tests for reading MediaMTX's log (modules/feeds/service_log.py).

The log lines below are copied from the real MediaMTX v1.21.3's output.
MediaMTX tells about a sender on an unknown name nowhere but in its log, so
the Feeds page reads it to show "refused: unknown feed name".
"""

from pathlib import Path

from baja_studio.modules.feeds.service_log import (
    RefusedSender,
    log_path,
    read_log_tail,
    refused_senders,
)

# Real lines are long; a few are kept whole on purpose, as MediaMTX printed them.
REAL_LINES = [
    "2026/11/15 19:43:14 INF [RTMP] started with listener on :11935 (TCP/RTMP)",
    "2026/11/15 19:43:31 INF [RTMP] [conn 127.0.0.1:60774] opened",
    "2026/11/15 19:43:31 INF [RTMP] [conn 127.0.0.1:60762] closed: path 'unknownfeed' is not configured",  # noqa: E501
    "2026/11/15 19:43:31 WAR [SRT] [conn 127.0.0.1:36177] failed to authenticate: authentication failed",  # noqa: E501
    "2026/11/15 19:43:32 INF [RTMP] [conn 127.0.0.1:60774] is publishing to path 'drone'",
    "2026/11/15 19:45:02 INF [RTMP] [conn 127.0.0.1:60800] closed: path 'dorne' is not configured",
    "2026/11/15 19:46:10 INF [RTMP] [conn 127.0.0.1:60801] closed: path 'unknownfeed' is not configured",  # noqa: E501
]


def test_the_log_is_where_nssm_writes_mediamtx_output(tmp_path: Path) -> None:
    assert log_path(tmp_path) == tmp_path / "logs" / "mediamtx-stdout.log"


def test_unknown_names_are_listed_newest_first_once_each() -> None:
    assert refused_senders(REAL_LINES, {"drone"}) == [
        RefusedSender("unknownfeed", "2026/11/15 19:46:10"),
        RefusedSender("dorne", "2026/11/15 19:45:02"),
    ]


def test_a_name_that_has_since_become_a_feed_is_no_longer_listed() -> None:
    # The operator saw "dorne" refused and added it as a feed: it is fine now.
    assert [sender.name for sender in refused_senders(REAL_LINES, {"drone", "dorne"})] == [
        "unknownfeed"
    ]


def test_only_the_end_of_a_big_log_is_read(tmp_path: Path) -> None:
    # NSSM lets the file grow to 5 MB: read the last bytes only, and drop the
    # first line, which the cut may have split in the middle.
    path = tmp_path / "mediamtx-stdout.log"
    path.write_text("".join(f"line {number:04d}\n" for number in range(1000)), encoding="utf-8")
    tail = read_log_tail(path, max_bytes=100)
    assert tail[-1] == "line 0999"
    assert all(line.startswith("line ") and len(line) == 9 for line in tail)
    assert len(tail) < 11


def test_a_missing_log_is_just_empty(tmp_path: Path) -> None:
    # Run from a checkout there is no service, so no log.
    assert read_log_tail(tmp_path / "nothing.log") == []
