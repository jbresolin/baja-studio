"""ffmpeg's warnings and errors: a short tail kept for the page, secrets removed, and why it died.

The output URL carries the feed's publish password (MediaMTX's
`streamid=publish:<feed>:<user>:<password>`), and ffmpeg repeats the URL in
its errors. Secrets never reach a log or a status (docs/conventions.md), so
every line is scrubbed before it is kept.

When a run ends, `explain_failure` turns its last lines into operator words
("camera not found: …", "SRT receiver unreachable: …"); the patterns are the
messages of ffmpeg 6.1-9.0's DirectShow input, SRT protocol, encoders and
zmq filter.
"""

import re
import threading
from collections import deque
from dataclasses import dataclass
from enum import StrEnum
from urllib.parse import parse_qs, urlsplit

LOG_TAIL_LINES = 40
REDACTED = "***"
# DirectShow's warning when its real-time buffer overflows (one per frame lost).
CAPTURE_DROP_MARKER = "frame dropped!"
# `streamid=publish:<path>:<user>:<password>` (MediaMTX); the password is the 4th field.
_STREAM_ID_PASSWORD = re.compile(r"(streamid=[^:&\s]+:[^:&\s]+:[^:&\s]+:)([^&\s]+)")
_SECRET_PARAMETERS = ("passphrase", "pass", "password")


def url_secrets(url: str) -> list[str]:
    """The secret strings in an output URL: an SRT stream id's password, a passphrase."""
    secrets = []
    try:
        query = parse_qs(urlsplit(url).query)
    except ValueError:
        return []
    for stream_id in query.get("streamid", []):
        fields = stream_id.split(":")
        if len(fields) >= 4 and fields[3]:
            secrets.append(":".join(fields[3:]))
    for name in _SECRET_PARAMETERS:
        secrets.extend(value for value in query.get(name, []) if value)
    return secrets


def redact(text: str, secrets: list[str]) -> str:
    """`text` with every secret replaced by `***` (and any stream id's password, known or not)."""
    for secret in secrets:
        text = text.replace(secret, REDACTED)
    return _STREAM_ID_PASSWORD.sub(rf"\g<1>{REDACTED}", text)


def receiver_address(url: str) -> str:
    """`host:port` of the output URL, which is safe to show (no password)."""
    try:
        parts = urlsplit(url)
        return f"{parts.hostname}:{parts.port}" if parts.port else str(parts.hostname)
    except ValueError:
        return "the configured address"


class LogTail:
    """The last `LOG_TAIL_LINES` lines ffmpeg wrote (not progress), secrets removed."""

    def __init__(self, secrets: list[str]) -> None:
        """Lines are scrubbed of `secrets` as they arrive."""
        self._secrets = secrets
        self._lock = threading.Lock()
        self._lines: deque[str] = deque(maxlen=LOG_TAIL_LINES)

    def add(self, line: str) -> str:
        """Keep a line (scrubbed); returns what was kept."""
        clean = redact(line.rstrip(), self._secrets)
        with self._lock:
            self._lines.append(clean)
        return clean

    def lines(self) -> list[str]:
        """The kept lines, oldest first."""
        with self._lock:
            return list(self._lines)


class FailureKind(StrEnum):
    """What a failure was about; the pipeline re-tests encoders only after an encoder one."""

    CAMERA = "camera"
    RECEIVER = "receiver"
    ENCODER = "encoder"
    CONTROL = "control"
    OTHER = "other"


@dataclass(frozen=True)
class Failure:
    """Why a run ended, for the operator."""

    kind: FailureKind
    reason: str


_CAMERA_MISSING = ("Could not find video device", "Could not enumerate video devices")
_CAMERA_BUSY = ("Could not run graph", "already in use")
_CAMERA_MODE = ("Could not set video options",)
_CAMERA_GONE = ("Error during demuxing", "Error retrieving a packet from demuxer")
_RECEIVER_UNREACHABLE = ("Connection to srt", "Connection setup failure", "Error opening output")
_RECEIVER_LOST = ("Error submitting a packet to the muxer", "Error writing trailer")
_ENCODER = (
    "Error while opening encoder",
    "Error initializing output stream",
    "Cannot load nvcuda",
    "No capable devices found",
    "OpenEncodeSessionEx failed",
    "MFX session",
    "AMF",
)
_CONTROL = ("ZMQ socket", "ZMQ context")
_NO_DIRECTSHOW = ("Unknown input format",)


def _first_line_with(lines: list[str], markers: tuple[str, ...]) -> str | None:
    for line in lines:
        if any(marker in line for marker in markers):
            return line
    return None


def _message_of(line: str) -> str:
    """A log line without ffmpeg's `[component @ 0x...]` prefix."""
    return re.sub(r"^(\[[^\]]*\]\s*)+", "", line).strip()


def explain_failure(exit_code: int | None, lines: list[str], device: str, url: str) -> Failure:
    """The operator-words reason a run with these last (scrubbed) lines ended."""
    where = receiver_address(url)
    checks: list[tuple[tuple[str, ...], FailureKind, str]] = [
        (_CAMERA_MISSING, FailureKind.CAMERA, f"camera not found: {device}"),
        (_NO_DIRECTSHOW, FailureKind.CAMERA, "this ffmpeg cannot capture cameras (no DirectShow)"),
        (_CAMERA_BUSY, FailureKind.CAMERA, f"camera busy (is another program using it?): {device}"),
        (_CAMERA_MODE, FailureKind.CAMERA, f"camera refused the mode: {device}"),
        (_CONTROL, FailureKind.CONTROL, "the window control port could not be opened"),
        (_ENCODER, FailureKind.ENCODER, "encoder failed"),
        (_RECEIVER_UNREACHABLE, FailureKind.RECEIVER, f"SRT receiver unreachable: {where}"),
        (_RECEIVER_LOST, FailureKind.RECEIVER, f"SRT receiver lost: {where}"),
        (_CAMERA_GONE, FailureKind.CAMERA, f"camera stopped sending: {device}"),
    ]
    for markers, kind, reason in checks:
        line = _first_line_with(lines, markers)
        if line is None:
            continue
        if kind in (FailureKind.ENCODER, FailureKind.CONTROL):
            reason = f"{reason}: {_message_of(line)}"
        return Failure(kind, reason)
    code = "" if exit_code is None else f" (exit code {exit_code})"
    last = _message_of(lines[-1]) if lines else ""
    return Failure(FailureKind.OTHER, f"ffmpeg stopped{code}" + (f": {last}" if last else ""))
