"""Tests for the URLs shown on the Feeds page (modules/feeds/urls.py).

These are what the operator types into the drone's app or the laptop's OBS,
and what OBS reads. A wrong character here means a feed that never arrives,
so each URL is checked whole.
"""

from baja_studio.modules.feeds.config import Feed, FeedKind, FeedsSettings
from baja_studio.modules.feeds.urls import (
    obs_read_url,
    publish_urls,
    rtmp_url,
    sender_addresses,
    srt_url,
)

SETTINGS = FeedsSettings()
LAN = "192.168.10.20"


def test_the_drone_pushes_rtmp_to_the_feed_name() -> None:
    assert rtmp_url(Feed(name="drone"), SETTINGS, LAN) == "rtmp://192.168.10.20:1935/drone"


def test_a_password_protected_rtmp_feed_shows_where_the_password_goes() -> None:
    # The password itself is a secret and never sent to the page: the
    # operator replaces <password> in the sender.
    feed = Feed(name="drone", password="secret1")
    assert rtmp_url(feed, SETTINGS, LAN) == (
        "rtmp://192.168.10.20:1935/drone?user=drone&pass=<password>"
    )


def test_a_laptop_pushes_srt_with_mediamtx_stream_id_and_latency_in_microseconds() -> None:
    # 200 ms of SRT latency is written 200000: OBS and ffmpeg read microseconds.
    assert srt_url(Feed(name="laptop1"), SETTINGS, LAN) == (
        "srt://192.168.10.20:8890?streamid=publish:laptop1&pkt_size=1316&latency=200000"
    )
    slow_radio = FeedsSettings(srt_latency_ms=800, srt_port=9000)
    assert srt_url(Feed(name="laptop1", password="laptop-password"), slow_radio, LAN) == (
        "srt://192.168.10.20:9000?streamid=publish:laptop1:laptop1:<password>"
        "&pkt_size=1316&latency=800000"
    )


def test_the_url_for_the_feeds_kind_comes_first_for_each_address() -> None:
    drone = publish_urls(Feed(name="drone", kind=FeedKind.DRONE), SETTINGS, [LAN, "10.0.0.5"])
    laptop = publish_urls(Feed(name="laptop1", kind=FeedKind.LAPTOP), SETTINGS, [LAN])
    assert [(item["protocol"], item["address"]) for item in drone] == [
        ("RTMP", LAN),
        ("RTMP", "10.0.0.5"),
        ("SRT", LAN),
        ("SRT", "10.0.0.5"),
    ]
    assert [item["protocol"] for item in laptop] == ["SRT", "RTMP"]


def test_obs_reads_the_local_mediamtx_over_rtsp() -> None:
    assert obs_read_url("drone", SETTINGS) == "rtsp://127.0.0.1:8554/drone"
    assert obs_read_url("drone", FeedsSettings(rtsp_port=8600)) == "rtsp://127.0.0.1:8600/drone"


def test_the_sender_address_setting_wins_over_the_addresses_found() -> None:
    assert sender_addresses(FeedsSettings(), [LAN, "10.0.0.5"]) == [LAN, "10.0.0.5"]
    assert sender_addresses(FeedsSettings(lan_address="studio.lan"), [LAN]) == ["studio.lan"]
    # No address found at all (no network yet): say so in the URL rather than show nothing.
    assert sender_addresses(FeedsSettings(), []) == ["<this PC's address>"]
