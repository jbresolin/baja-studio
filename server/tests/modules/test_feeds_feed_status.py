"""Tests for the feed cards (modules/feeds/feed_status.py).

`FeedTracker` turns MediaMTX's path list into what each card on the Feeds
page shows. It is told the time on every call, so these tests move time by
passing numbers: no clock, no threads.
"""

from typing import Any

from baja_studio.modules.feeds.config import Feed, FeedKind, FeedsSettings
from baja_studio.modules.feeds.control_api import PathSnapshot, VideoTrack
from baja_studio.modules.feeds.feed_status import FeedTracker

SETTINGS = FeedsSettings(
    feeds=[Feed(id="feed-drone", name="drone", label="Drone", kind=FeedKind.DRONE)]
)
STARTED = "2026-11-21T07:00:00-03:00"
ADDRESSES = ["192.168.10.20"]


def snapshot(online: bool = True, inbound_bytes: int = 0, name: str = "drone") -> PathSnapshot:
    return PathSnapshot(
        name=name,
        conf_name=name,
        online=online,
        online_since="2026-11-21T08:00:00-03:00" if online else None,
        protocol="RTMP" if online else None,
        video=VideoTrack("H264", 1920, 1080) if online else None,
        audio_codec="MPEG-4 Audio" if online else None,
        readers=1 if online else 0,
        inbound_bytes=inbound_bytes,
    )


def cards(
    tracker: FeedTracker, snapshots: list[PathSnapshot] | None, now: float, iso: str = "now"
) -> dict[str, Any]:
    return tracker.feed_cards(SETTINGS, snapshots, ADDRESSES, now, iso, STARTED)[0]


def test_a_feed_never_live_since_mediamtx_started_is_waiting_since_then() -> None:
    card = cards(FeedTracker(), [snapshot(online=False)], now=0.0)
    assert card["status"] == "waiting"
    assert card["since"] == STARTED
    assert card["bitrate_kbps"] is None
    # The card always carries what the page needs to show and copy.
    assert card["obs_source_name"] == "Drone"
    assert card["obs_url"] == "rtsp://127.0.0.1:8554/drone"
    assert card["publish_urls"][0]["url"] == "rtmp://192.168.10.20:1935/drone"


def test_a_live_feed_shows_what_mediamtx_reports() -> None:
    card = cards(FeedTracker(), [snapshot()], now=0.0)
    assert card["status"] == "live"
    assert card["since"] == "2026-11-21T08:00:00-03:00"
    assert (card["protocol"], card["width"], card["height"]) == ("RTMP", 1920, 1080)
    assert card["video_codec"] == "H264"
    assert card["audio_codec"] == "MPEG-4 Audio"
    assert card["readers"] == 1


def test_the_bitrate_is_the_growth_of_the_byte_count_between_two_polls() -> None:
    tracker = FeedTracker()
    # One sample is not a rate yet.
    assert cards(tracker, [snapshot(inbound_bytes=0)], now=10.0)["bitrate_kbps"] is None
    # 750,000 bytes in 2 seconds = 6,000,000 bits / 2 s = 3000 kbit/s.
    assert cards(tracker, [snapshot(inbound_bytes=750_000)], now=12.0)["bitrate_kbps"] == 3000


def test_a_byte_count_that_goes_down_starts_the_measure_again() -> None:
    # The drone reconnected: MediaMTX counts the new stream from zero. A
    # negative rate would be nonsense, so the card shows "unknown" once.
    tracker = FeedTracker()
    cards(tracker, [snapshot(inbound_bytes=5_000_000)], now=0.0)
    assert cards(tracker, [snapshot(inbound_bytes=1000)], now=2.0)["bitrate_kbps"] is None
    assert cards(tracker, [snapshot(inbound_bytes=251_000)], now=4.0)["bitrate_kbps"] == 1000


def test_a_feed_that_stops_is_waiting_since_the_last_poll_it_was_live() -> None:
    tracker = FeedTracker()
    cards(tracker, [snapshot()], now=0.0, iso="08:10")
    cards(tracker, [snapshot()], now=2.0, iso="08:12")
    card = cards(tracker, [snapshot(online=False)], now=4.0, iso="08:14")
    assert card["status"] == "waiting"
    assert card["since"] == "08:12"
    # It stays "since 08:12" while it stays away.
    assert cards(tracker, [snapshot(online=False)], now=6.0, iso="08:16")["since"] == "08:12"


def test_when_mediamtx_does_not_answer_every_card_is_unknown() -> None:
    card = cards(FeedTracker(), None, now=0.0)
    assert card["status"] == "unknown"
    assert card["since"] is None


def test_senders_on_names_that_are_not_feeds_are_listed_apart() -> None:
    # Only possible with "accept any name" (the bench).
    others = FeedTracker().other_live_paths(
        SETTINGS, [snapshot(), snapshot(name="test"), snapshot(online=False, name="old")], 0.0
    )
    assert [item["name"] for item in others] == ["test"]
    assert others[0]["status"] == "live"
