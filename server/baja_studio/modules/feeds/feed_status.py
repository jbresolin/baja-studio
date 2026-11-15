"""Each feed's card on the Feeds page, computed from MediaMTX's path list at every poll.

MediaMTX reports, per path, whether a sender is publishing, since when, its
protocol and tracks, its readers, and a running count of bytes received.
The bitrate is that count's growth between two polls; it is unknown on the
first poll of a sender (one sample is not a rate) and starts again when the
count goes down (a new sender replaced the old one: its stream counts from
zero).

"Waiting since" is when the studio saw the feed stop, or when MediaMTX
started if the feed has not been live since then. Like the bitrate, it is
memory the tracker keeps between polls; everything else comes from the
current answer, so a restart of the studio cannot leave a card wrong.
"""

from dataclasses import dataclass
from typing import Any

from baja_studio.modules.feeds.config import FeedsSettings
from baja_studio.modules.feeds.control_api import PathSnapshot
from baja_studio.modules.feeds.urls import obs_read_url, publish_urls

LIVE = "live"
WAITING = "waiting"
UNKNOWN = "unknown"


@dataclass
class _ByteSample:
    at: float
    inbound_bytes: int


class FeedTracker:
    """Turns path snapshots into card states; told the time on every call."""

    def __init__(self) -> None:
        """Nothing seen yet."""
        self._samples: dict[str, _ByteSample] = {}
        self._waiting_since: dict[str, str] = {}

    def feed_cards(
        self,
        settings: FeedsSettings,
        snapshots: list[PathSnapshot] | None,
        addresses: list[str],
        now_monotonic: float,
        now_iso: str,
        mediamtx_started: str | None,
    ) -> list[dict[str, Any]]:
        """One card per configured feed; `snapshots` None means MediaMTX did not answer."""
        by_name = {snapshot.name: snapshot for snapshot in snapshots or []}
        cards = []
        for feed in settings.feeds:
            snapshot = by_name.get(feed.name)
            card: dict[str, Any] = {
                "id": feed.id,
                "name": feed.name,
                "label": feed.label,
                "obs_source_name": feed.obs_source_name,
                "kind": feed.kind.value,
                "record": feed.record,
                "has_password": bool(feed.password),
                "publish_urls": publish_urls(feed, settings, addresses),
                "obs_url": obs_read_url(feed.name, settings),
            }
            card.update(self._live_part(feed.name, snapshot, snapshots is None, now_monotonic,
                                        now_iso, mediamtx_started))  # fmt: skip
            cards.append(card)
        return cards

    def other_live_paths(
        self, settings: FeedsSettings, snapshots: list[PathSnapshot], now_monotonic: float
    ) -> list[dict[str, Any]]:
        """Senders on names that are not feeds (only possible with "accept any name")."""
        known = {feed.name for feed in settings.feeds}
        return [
            {
                "name": snapshot.name,
                **self._live_part(snapshot.name, snapshot, False, now_monotonic, "", None),
            }
            for snapshot in snapshots
            if snapshot.online and snapshot.name not in known
        ]

    def _live_part(
        self,
        name: str,
        snapshot: PathSnapshot | None,
        unknown: bool,
        now_monotonic: float,
        now_iso: str,
        mediamtx_started: str | None,
    ) -> dict[str, Any]:
        if unknown:
            self._samples.pop(name, None)
            return _empty_live_part(UNKNOWN, None)
        if snapshot is None or not snapshot.online:
            self._samples.pop(name, None)
            since = self._waiting_since.setdefault(name, mediamtx_started or now_iso)
            return _empty_live_part(WAITING, since)
        self._waiting_since[name] = now_iso
        video = snapshot.video
        return {
            "status": LIVE,
            "since": snapshot.online_since,
            "protocol": snapshot.protocol,
            "video_codec": video.codec if video else None,
            "width": video.width if video else None,
            "height": video.height if video else None,
            "audio_codec": snapshot.audio_codec,
            "bitrate_kbps": self._bitrate_kbps(name, snapshot.inbound_bytes, now_monotonic),
            "readers": snapshot.readers,
        }

    def _bitrate_kbps(self, name: str, inbound_bytes: int, now: float) -> int | None:
        previous = self._samples.get(name)
        self._samples[name] = _ByteSample(now, inbound_bytes)
        if previous is None or inbound_bytes < previous.inbound_bytes:
            return None
        elapsed = now - previous.at
        if elapsed <= 0:
            return None
        return round((inbound_bytes - previous.inbound_bytes) * 8 / elapsed / 1000)


def _empty_live_part(status: str, since: str | None) -> dict[str, Any]:
    return {
        "status": status,
        "since": since,
        "protocol": None,
        "video_codec": None,
        "width": None,
        "height": None,
        "audio_codec": None,
        "bitrate_kbps": None,
        "readers": 0,
    }
