"""What MediaMTX printed: the tail of its log, and the senders it refused.

The BajaStudioFeeds service writes MediaMTX's output to
`<data>/logs/mediamtx-stdout.log` (NSSM's `AppStdout`). The Feeds page
shows its last lines while the service is not ok, and the names senders
tried that are not feeds ("refused: unknown feed name"), which MediaMTX
reports nowhere but in that log:

    2026/11/15 19:43:31 INF [RTMP] [conn 192.168.10.50:60762] closed: path 'dorne' is not configured

Only the end of the file is read (it can grow to 5 MB before NSSM rotates it).
"""

import re
from dataclasses import dataclass
from pathlib import Path

from baja_studio.core.logging import LOG_DIRECTORY_NAME

LOG_FILE_NAME = "mediamtx-stdout.log"
TAIL_BYTES = 64 * 1024
TAIL_LINES = 15
MAX_REFUSED = 10
REFUSED_PATTERN = re.compile(
    r"^(?P<at>\d{4}/\d\d/\d\d \d\d:\d\d:\d\d) .*closed: path '(?P<name>[^']+)' is not configured"
)


def log_path(data_directory: Path) -> Path:
    """Where NSSM writes MediaMTX's output."""
    return data_directory / LOG_DIRECTORY_NAME / LOG_FILE_NAME


def read_log_tail(path: Path, max_bytes: int = TAIL_BYTES) -> list[str]:
    """The last lines of the log (none if it cannot be read: not installed, rotated away)."""
    try:
        with path.open("rb") as log:
            log.seek(0, 2)
            size = log.tell()
            log.seek(max(0, size - max_bytes))
            data = log.read()
    except OSError:
        return []
    lines = data.decode("utf-8", errors="replace").splitlines()
    # The first line may be cut in the middle when the read did not start at the beginning.
    if len(data) == max_bytes and lines:
        lines = lines[1:]
    return [line for line in lines if line.strip()]


@dataclass(frozen=True)
class RefusedSender:
    """A sender MediaMTX turned away because its path name is not a feed."""

    name: str
    at: str  # MediaMTX's own local time stamp, e.g. 2026/11/15 19:43:31


def refused_senders(lines: list[str], known_names: set[str]) -> list[RefusedSender]:
    """The latest refusal per unknown name, newest first; names since added are left out."""
    latest: dict[str, str] = {}
    for line in lines:
        match = REFUSED_PATTERN.match(line)
        if match and match["name"] not in known_names:
            latest[match["name"]] = match["at"]
    ordered = sorted(latest.items(), key=lambda item: item[1], reverse=True)
    return [RefusedSender(name, at) for name, at in ordered[:MAX_REFUSED]]
