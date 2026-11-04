"""The controller's recent frames, for the frame monitor on the camera configuration page.

The page shows the last few decoded frames (address, action, speeds), so
wiring and baud problems can be diagnosed from a browser, and offers the last
hundred raw frames as hex to replace the rebuilt test fixtures. A controller
can send dozens of frames a second, so the decoded list is published at most
ten times a second; the raw list is only read on request (`raw_frames`).
"""

from collections import deque
from typing import Any

from baja_studio.cameras.inputs.pelco_d import (
    SYNC,
    Action,
    CallButton,
    Frame,
    Motion,
    RecallPreset,
    StopAll,
    checksum,
)

SHOWN_FRAMES = 20
KEPT_RAW_FRAMES = 100
PUBLISH_INTERVAL_SECONDS = 0.1


def raw_bytes(frame: Frame) -> bytes:
    """The 7 bytes the frame arrived as (it passed the checksum, so they are known)."""
    body = bytes([SYNC, frame.address, frame.command1, frame.command2, frame.data1, frame.data2])
    return body + bytes([checksum(body + b"\x00")])


def describe(action: Action) -> dict[str, Any]:
    """What the page shows for one frame's meaning."""
    if isinstance(action, Motion):
        return {
            "action": "move",
            "address": action.address,
            "pan": round(action.pan, 3),
            "tilt": round(action.tilt, 3),
            "zoom": action.zoom_direction,
        }
    if isinstance(action, StopAll):
        return {"action": "stop", "address": action.address}
    if isinstance(action, RecallPreset):
        return {"action": "recall_preset", "address": action.address, "n": action.n}
    if isinstance(action, CallButton):
        return {"action": "call", "n": action.n}
    return {"action": "unknown", "address": action.frame.address}


class FrameMonitor:
    """Remembers recent frames and says when the decoded list is due to be published."""

    def __init__(self) -> None:
        """Empty; the first frame is due for publishing at once."""
        self._shown: deque[dict[str, Any]] = deque(maxlen=SHOWN_FRAMES)
        self._raw: deque[str] = deque(maxlen=KEPT_RAW_FRAMES)
        self._changed = False
        self._published_at = float("-inf")

    def record(self, frame: Frame, action: Action, ts: str) -> None:
        """Add one valid frame, with what it meant and when (ISO UTC) it arrived."""
        hex_bytes = raw_bytes(frame).hex(" ").upper()
        self._raw.append(hex_bytes)
        self._shown.appendleft({"ts": ts, "hex": hex_bytes, **describe(action)})
        self._changed = True

    def due(self, now: float) -> list[dict[str, Any]] | None:
        """The decoded frames, newest first, if they changed and were not published
        in the last 100 ms; otherwise None (try again on the next read)."""
        if not self._changed or now - self._published_at < PUBLISH_INTERVAL_SECONDS - 1e-6:
            return None
        self._changed, self._published_at = False, now
        return list(self._shown)

    def raw_frames(self) -> list[str]:
        """The last hundred frames as hex, oldest first."""
        return list(self._raw)
