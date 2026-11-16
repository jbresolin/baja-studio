"""A small client for MediaMTX's control API (v3), on 127.0.0.1 only.

The studio reads, never writes, through it: the config file is the only
way the studio changes MediaMTX (mediamtx_config.py), so a MediaMTX that
restarts comes back exactly as configured.

- `GET /v3/info`: version and start time; answering at all means MediaMTX runs.
- `GET /v3/paths/list`: one item per path that exists now (every configured
  feed, live or waiting, plus whatever the catch-all accepted), with its
  publisher (`source`), tracks, readers and byte counters.
- `GET /v3/config/global/get`: the configuration MediaMTX is running, to
  check that it reloaded the file the studio wrote (see controller.py).

The JSON shapes are those of MediaMTX v1.21.3, recorded from the real
binary in `tests/modules/feeds_fixtures/`. Passwords never come back: the
API answers `"<redacted>"` for them.
"""

from dataclasses import dataclass
from typing import Any

import httpx2

TIMEOUT = httpx2.Timeout(connect=2.0, read=2.0, write=2.0, pool=2.0)
# More than any number of feeds the studio will have; MediaMTX pages its lists.
ITEMS_PER_PAGE = 500
# `source.type` as MediaMTX names it -> the protocol an operator recognises.
SOURCE_PROTOCOLS = {
    "rtmpConn": "RTMP",
    "rtmpsConn": "RTMPS",
    "srtConn": "SRT",
    "rtspSession": "RTSP",
    "rtspsSession": "RTSPS",
    "webRTCSession": "WebRTC",
}


class MediamtxUnreachableError(Exception):
    """MediaMTX's control API did not answer: the feeds service is stopped or starting."""


@dataclass(frozen=True)
class VideoTrack:
    """The picture a publisher sends: codec and size (MediaMTX's `tracks2`)."""

    codec: str
    width: int | None
    height: int | None


@dataclass(frozen=True)
class PathSnapshot:
    """One item of `/v3/paths/list`, reduced to what the Feeds page shows."""

    name: str
    conf_name: str
    online: bool
    online_since: str | None
    protocol: str | None
    video: VideoTrack | None
    audio_codec: str | None
    readers: int
    inbound_bytes: int


@dataclass(frozen=True)
class MediamtxInfo:
    """`/v3/info`: which MediaMTX answers, and since when it runs."""

    version: str
    started: str | None


def path_snapshot(item: dict[str, Any]) -> PathSnapshot:
    """Turn one raw `/v3/paths/list` item into a `PathSnapshot`."""
    # `online` arrived in v1.15; before it, `ready` meant the same.
    online = bool(item.get("online", item.get("ready", False)))
    source = item.get("source") if isinstance(item.get("source"), dict) else None
    source_type = str(source.get("type", "")) if source else ""
    video: VideoTrack | None = None
    audio_codec: str | None = None
    for track in item.get("tracks2") or []:
        if not isinstance(track, dict):
            continue
        properties = track.get("codecProps") or {}
        codec = str(track.get("codec", ""))
        if "width" in properties and video is None:
            video = VideoTrack(
                codec, _int_or_none(properties.get("width")), _int_or_none(properties.get("height"))
            )
        elif "sampleRate" in properties and audio_codec is None:
            audio_codec = codec
    inbound = item.get("inboundBytes", item.get("bytesReceived", 0))
    return PathSnapshot(
        name=str(item.get("name", "")),
        conf_name=str(item.get("confName", "")),
        online=online,
        online_since=item.get("onlineTime", item.get("readyTime")) if online else None,
        protocol=SOURCE_PROTOCOLS.get(source_type, source_type or None) if online else None,
        video=video if online else None,
        audio_codec=audio_codec if online else None,
        readers=len(item.get("readers") or []),
        inbound_bytes=int(inbound or 0),
    )


def _int_or_none(value: Any) -> int | None:
    return value if isinstance(value, int) else None


class MediamtxApiClient:
    """Talks to one MediaMTX control API; `transport` lets tests plug in the fake."""

    def __init__(self, address: str, transport: httpx2.BaseTransport | None = None) -> None:
        """`address` is host:port, e.g. 127.0.0.1:9997."""
        self._base_url = f"http://{address}"
        self._transport = transport

    @property
    def address(self) -> str:
        """host:port, for messages."""
        return self._base_url.removeprefix("http://")

    def info(self) -> MediamtxInfo:
        """Version and start time; raises `MediamtxUnreachableError` when MediaMTX is down."""
        body = self._get("/v3/info")
        return MediamtxInfo(str(body.get("version", "")), body.get("started"))

    def paths(self) -> list[PathSnapshot]:
        """Every path MediaMTX has right now."""
        body = self._get(f"/v3/paths/list?itemsPerPage={ITEMS_PER_PAGE}")
        return [path_snapshot(item) for item in body.get("items") or [] if isinstance(item, dict)]

    def global_config(self) -> dict[str, Any]:
        """The global configuration MediaMTX runs (passwords redacted by MediaMTX)."""
        return self._get("/v3/config/global/get")

    def _get(self, path: str) -> dict[str, Any]:
        try:
            with httpx2.Client(
                base_url=self._base_url, timeout=TIMEOUT, transport=self._transport, trust_env=False
            ) as client:
                response = client.get(path)
        except httpx2.TransportError as error:
            raise MediamtxUnreachableError(
                f"no answer from MediaMTX at {self.address}: {error or type(error).__name__}"
            ) from error
        if response.status_code >= 400:
            raise MediamtxUnreachableError(
                f"MediaMTX at {self.address} answered HTTP {response.status_code} to {path}"
            )
        try:
            body = response.json()
        except ValueError as error:
            raise MediamtxUnreachableError(
                f"MediaMTX at {self.address} sent something that is not JSON"
            ) from error
        if not isinstance(body, dict):
            raise MediamtxUnreachableError(f"MediaMTX at {self.address} sent an unexpected answer")
        return body
