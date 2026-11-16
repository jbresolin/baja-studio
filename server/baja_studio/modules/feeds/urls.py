"""The URLs a sender types in, and the one OBS reads.

Senders reach the studio PC over the LAN: the drone's controller app pushes
RTMP, a trackside laptop's OBS pushes SRT (MediaMTX's `streamid` syntax,
`publish:<path>[:<user>:<password>]`). A feed with a password shows the
place where it goes as `<password>`: the password itself never leaves the
settings (it is a secret), so the operator types it in the sender.

OBS reads every feed from the local MediaMTX over RTSP/TCP on 127.0.0.1
(ADR 0022: lossless on loopback, no jitter buffer to add delay).
"""

from typing import Any

from baja_studio.modules.feeds.config import Feed, FeedKind, FeedsSettings

PASSWORD_PLACEHOLDER = "<password>"
# MediaMTX's documented packet size for SRT from OBS and ffmpeg (7 MPEG-TS packets).
SRT_PACKET_SIZE = 1316
LOOPBACK = "127.0.0.1"


def rtmp_url(feed: Feed, settings: FeedsSettings, address: str) -> str:
    """rtmp://<address>:<port>/<name>, with `?user=<name>&pass=<password>` when it has one."""
    url = f"rtmp://{address}:{settings.rtmp_port}/{feed.name}"
    if feed.password:
        url += f"?user={feed.name}&pass={PASSWORD_PLACEHOLDER}"
    return url


def srt_url(feed: Feed, settings: FeedsSettings, address: str) -> str:
    """srt://<address>:<port>?streamid=publish:<name>...&latency=<microseconds>.

    OBS and ffmpeg take SRT's `latency` in microseconds (libsrt's URL syntax).
    """
    stream_id = f"publish:{feed.name}"
    if feed.password:
        stream_id += f":{feed.name}:{PASSWORD_PLACEHOLDER}"
    latency_us = settings.srt_latency_ms * 1000
    return (
        f"srt://{address}:{settings.srt_port}?streamid={stream_id}"
        f"&pkt_size={SRT_PACKET_SIZE}&latency={latency_us}"
    )


def publish_urls(feed: Feed, settings: FeedsSettings, addresses: list[str]) -> list[dict[str, Any]]:
    """Every URL a sender may use, the one for the feed's kind first.

    One per protocol and per address of this PC (a PC on two networks has
    two); `has_password` tells the page to explain the placeholder.
    """
    protocols = ["RTMP", "SRT"] if feed.kind != FeedKind.LAPTOP else ["SRT", "RTMP"]
    urls = []
    for protocol in protocols:
        for address in addresses:
            url = (
                rtmp_url(feed, settings, address)
                if protocol == "RTMP"
                else srt_url(feed, settings, address)
            )
            urls.append({"protocol": protocol, "address": address, "url": url})
    return urls


def obs_read_url(feed_name: str, settings: FeedsSettings) -> str:
    """What OBS's media source opens: the local MediaMTX over RTSP."""
    return f"rtsp://{LOOPBACK}:{settings.rtsp_port}/{feed_name}"


def obs_browser_url(feed_name: str, settings: FeedsSettings) -> str:
    """What OBS's browser source opens: MediaMTX's WebRTC player page for the feed.

    The page (MediaMTX's own) plays the feed muted and without controls, and
    reconnects by itself when the sender comes back.
    """
    return (
        f"http://{LOOPBACK}:{settings.webrtc_port}/{feed_name}/"
        "?controls=false&muted=true&autoplay=true"
    )


def sender_addresses(settings: FeedsSettings, lan_addresses: list[str]) -> list[str]:
    """The address(es) shown in sender URLs: the setting, or every LAN address found."""
    if settings.lan_address:
        return [settings.lan_address]
    return lan_addresses or ["<this PC's address>"]
