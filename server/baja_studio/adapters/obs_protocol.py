"""The obs-websocket v5 wire format: op codes, authentication, message builders.

Pure functions and constants, shared by the client (`obs_websocket.py`) and
the fake OBS (`fakes/fake_obs.py`), so both sides agree byte for byte.
Reference: the obs-websocket 5.x protocol document (`docs/generated/protocol.md`
in the obs-websocket repository).
"""

import base64
import hashlib
import json
from enum import IntEnum, IntFlag
from typing import Any

RPC_VERSION = 1
DEFAULT_PORT = 4455


class OpCode(IntEnum):
    """The `op` field of every message."""

    HELLO = 0
    IDENTIFY = 1
    IDENTIFIED = 2
    REIDENTIFY = 3
    EVENT = 5
    REQUEST = 6
    REQUEST_RESPONSE = 7


class EventSubscription(IntFlag):
    """Event categories a client asks for in `Identify.eventSubscriptions`."""

    GENERAL = 1 << 0
    CONFIG = 1 << 1
    SCENES = 1 << 2
    INPUTS = 1 << 3
    TRANSITIONS = 1 << 4
    FILTERS = 1 << 5
    OUTPUTS = 1 << 6
    SCENE_ITEMS = 1 << 7
    MEDIA_INPUTS = 1 << 8
    VENDORS = 1 << 9
    UI = 1 << 10
    # Everything except the high-volume categories (volume meters and the like).
    ALL = (1 << 11) - 1


class CloseCode(IntEnum):
    """WebSocket close codes OBS uses that the client explains to the operator."""

    AUTHENTICATION_FAILED = 4009
    UNSUPPORTED_RPC_VERSION = 4010


class RequestStatus(IntEnum):
    """`requestStatus.code` values the fake answers with (and the client may see)."""

    SUCCESS = 100
    MISSING_REQUEST_TYPE = 203
    UNKNOWN_REQUEST_TYPE = 204
    MISSING_REQUEST_FIELD = 300
    RESOURCE_NOT_FOUND = 600


class MediaAction:
    """`mediaAction` values for `TriggerMediaInputAction`."""

    PLAY = "OBS_WEBSOCKET_MEDIA_INPUT_ACTION_PLAY"
    PAUSE = "OBS_WEBSOCKET_MEDIA_INPUT_ACTION_PAUSE"
    STOP = "OBS_WEBSOCKET_MEDIA_INPUT_ACTION_STOP"
    RESTART = "OBS_WEBSOCKET_MEDIA_INPUT_ACTION_RESTART"


class MediaState:
    """`mediaState` values reported by `GetMediaInputStatus`."""

    NONE = "OBS_MEDIA_STATE_NONE"
    PLAYING = "OBS_MEDIA_STATE_PLAYING"
    OPENING = "OBS_MEDIA_STATE_OPENING"
    BUFFERING = "OBS_MEDIA_STATE_BUFFERING"
    PAUSED = "OBS_MEDIA_STATE_PAUSED"
    STOPPED = "OBS_MEDIA_STATE_STOPPED"
    ENDED = "OBS_MEDIA_STATE_ENDED"
    ERROR = "OBS_MEDIA_STATE_ERROR"


def authentication_string(password: str, salt: str, challenge: str) -> str:
    """The `authentication` value for Identify, as the protocol defines it.

    secret = base64(sha256(password + salt)); auth = base64(sha256(secret + challenge)).
    """
    secret = base64.b64encode(hashlib.sha256((password + salt).encode()).digest()).decode()
    return base64.b64encode(hashlib.sha256((secret + challenge).encode()).digest()).decode()


def encode(op: OpCode, data: dict[str, Any]) -> str:
    """One message as the JSON text sent over the socket."""
    return json.dumps({"op": int(op), "d": data})


def decode(text: str) -> tuple[int, dict[str, Any]]:
    """Split a received message into (op, d); raises ValueError if it is not one."""
    message = json.loads(text)
    if not isinstance(message, dict) or not isinstance(message.get("d"), dict):
        raise ValueError(f"not an obs-websocket message: {text[:200]!r}")
    op = message.get("op")
    if not isinstance(op, int):
        raise ValueError(f"message without an op code: {text[:200]!r}")
    return op, message["d"]


def identify_message(authentication: str | None, event_subscriptions: int) -> str:
    """The client's Identify (op 1), with authentication only when OBS asked for it."""
    data: dict[str, Any] = {"rpcVersion": RPC_VERSION, "eventSubscriptions": event_subscriptions}
    if authentication is not None:
        data["authentication"] = authentication
    return encode(OpCode.IDENTIFY, data)


def request_message(request_type: str, request_id: str, request_data: dict[str, Any]) -> str:
    """A Request (op 6)."""
    data: dict[str, Any] = {"requestType": request_type, "requestId": request_id}
    if request_data:
        data["requestData"] = request_data
    return encode(OpCode.REQUEST, data)
