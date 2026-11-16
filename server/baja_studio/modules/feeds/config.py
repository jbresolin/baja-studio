"""Settings of the feeds module: the feeds MediaMTX accepts, and the ports it listens on.

A feed is one remote picture (the drone, a trackside laptop's OBS) that a
sender pushes to MediaMTX under a path name, and OBS reads back from it.
The list of feeds is edited on the Feeds page (`managed_elsewhere`); the
ports and the bench switches go through the generic settings form.

Every value here ends up in `mediamtx.yml`, and MediaMTX *exits* when it
reloads a config it cannot load or whose port is taken (measured with
v1.21.3, ADR 0022). So the model is strict: names and passwords use a
small alphabet that is also safe inside RTMP and SRT URLs, and ports
cannot collide with each other or with the studio's own.
"""

import re
import secrets
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from baja_studio.core.settings import ModuleSettings

# Path names MediaMTX accepts, narrowed to what is easy to type on a drone controller.
FEED_NAME_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]{0,31}$")
# MediaMTX gives these names a meaning of their own.
RESERVED_FEED_NAMES = frozenset({"all", "all_others"})
# A publish password travels inside a URL (RTMP `?pass=`, SRT `streamid=...:pass`):
# no ':', '&', '?', '#', '%' or spaces, which would end the field early.
PASSWORD_PATTERN = re.compile(r"^[A-Za-z0-9._~-]{4,64}$")

DEFAULT_RTMP_PORT = 1935
DEFAULT_SRT_PORT = 8890
DEFAULT_RTSP_PORT = 8554
DEFAULT_API_PORT = 9997
# WebRTC, for OBS Browser Sources (the lowest delay, ADR 0022 addendum): the page
# and its signalling on 127.0.0.1 (TCP), the media on UDP.
DEFAULT_WEBRTC_PORT = 8889
DEFAULT_WEBRTC_MEDIA_PORT = 8189
DEFAULT_SRT_LATENCY_MS = 200
# Ports other programs of the streaming PC already use: the studio, Caddy (80/443 and its
# admin API), OBS's WebSocket. A feed port on one of them would stop one of the two.
STUDIO_PORTS = {80: "the gateway", 443: "the gateway", 2019: "Caddy's admin API"}
STUDIO_PORTS |= {8888: "the studio", 4455: "OBS's WebSocket"}


class FeedKind(StrEnum):
    """What sends the feed; decides which sender URL the page shows first."""

    DRONE = "drone"
    LAPTOP = "laptop"
    OTHER = "other"


def new_feed_id() -> str:
    """A stable id for a new feed, so renaming it keeps its password (settings `id` rule)."""
    return f"feed-{secrets.token_hex(4)}"


class Feed(BaseModel):
    """One remote picture: a MediaMTX path, a label for OBS, an optional password."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(default_factory=new_feed_id, description="Stable id; never shown.")
    name: str = Field(
        description="The path senders publish to, e.g. drone or laptop1 (a-z, 0-9, '-', '_')."
    )
    label: str = Field(
        default="",
        max_length=60,
        description="Name of the OBS source made by Add to OBS (empty: the feed's name).",
    )
    kind: FeedKind = Field(default=FeedKind.OTHER, description="drone, laptop or other.")
    record: bool = Field(
        default=False, description="MediaMTX also records this feed to disk (fMP4 segments)."
    )
    password: str = Field(
        default="",
        description="Optional publish password; senders without it are refused.",
        json_schema_extra={"secret": True},
    )

    @field_validator("name")
    @classmethod
    def _check_name(cls, value: str) -> str:
        value = value.strip()
        if not FEED_NAME_PATTERN.match(value):
            raise ValueError(
                "use 1 to 32 lowercase letters, digits, '-' or '_', starting with a letter or digit"
            )
        if value in RESERVED_FEED_NAMES:
            raise ValueError(f"{value!r} has a special meaning in MediaMTX; pick another name")
        return value

    @field_validator("label")
    @classmethod
    def _check_label(cls, value: str) -> str:
        return value.strip()

    @field_validator("password")
    @classmethod
    def _check_password(cls, value: str) -> str:
        if value and not PASSWORD_PATTERN.match(value):
            raise ValueError(
                "use 4 to 64 letters, digits, '.', '_', '~' or '-' "
                "(other characters would break the sender's URL)"
            )
        return value

    @property
    def obs_source_name(self) -> str:
        """The name Add to OBS gives the media source."""
        return self.label or self.name


class FeedsSettings(ModuleSettings):
    """Everything `mediamtx.yml` is written from. Nobody edits that file by hand."""

    feeds: list[Feed] = Field(
        default_factory=list,
        description="Edited on the Feeds page.",
        json_schema_extra={"managed_elsewhere": True},
    )
    rtmp_port: int = Field(
        default=DEFAULT_RTMP_PORT, ge=1, le=65535, description="RTMP (TCP), for the drone."
    )
    srt_port: int = Field(
        default=DEFAULT_SRT_PORT,
        ge=1,
        le=65535,
        description=(
            "SRT (UDP), for trackside laptops. The installer opens 8890 in the firewall; "
            "another port must be opened by hand."
        ),
    )
    srt_latency_ms: int = Field(
        default=DEFAULT_SRT_LATENCY_MS,
        ge=20,
        le=8000,
        description=(
            "SRT latency put in the laptops' URL: how long lost packets may be resent. "
            "About 4x the round trip; raise it over a radio link."
        ),
    )
    rtsp_port: int = Field(
        default=DEFAULT_RTSP_PORT, ge=1, le=65535, description="RTSP (TCP): OBS reads here."
    )
    rtsp_on_lan: bool = Field(
        default=False,
        description=(
            "Let other PCs read the feeds over RTSP. Off: only this PC (OBS) can read them. "
            "On: also allow TCP on the RTSP port in the Windows firewall by hand."
        ),
    )
    webrtc_port: int = Field(
        default=DEFAULT_WEBRTC_PORT,
        ge=1,
        le=65535,
        description=(
            "WebRTC page and signalling (TCP), on 127.0.0.1 only: OBS Browser Sources read "
            "feeds here with the least delay."
        ),
    )
    webrtc_media_port: int = Field(
        default=DEFAULT_WEBRTC_MEDIA_PORT,
        ge=1,
        le=65535,
        description=(
            "WebRTC media (UDP), from this PC's own browser sources. No firewall rule: "
            "other PCs cannot reach it."
        ),
    )
    api_port: int = Field(
        default=DEFAULT_API_PORT,
        ge=1,
        le=65535,
        description="MediaMTX's control API, on 127.0.0.1 only; the studio reads it.",
    )
    accept_any_name: bool = Field(
        default=False,
        description=(
            "Bench only: accept a sender on any path name, not just the feeds listed. "
            "Cannot be used together with feed passwords."
        ),
    )
    recordings_folder: str = Field(
        default="",
        description="Where recorded feeds go (empty: feeds-recordings in the data folder).",
    )
    lan_address: str = Field(
        default="",
        description=("The address senders use to reach this PC (empty: every LAN address it has)."),
    )

    @field_validator("recordings_folder", "lan_address")
    @classmethod
    def _strip(cls, value: str) -> str:
        return value.strip()

    @field_validator("lan_address")
    @classmethod
    def _check_lan_address(cls, value: str) -> str:
        if value and not re.match(r"^[A-Za-z0-9.-]{1,253}$", value):
            raise ValueError("must be an IPv4 address or a host name, e.g. 192.168.10.20")
        return value

    @field_validator("recordings_folder")
    @classmethod
    def _check_recordings_folder(cls, value: str) -> str:
        # The folder goes into mediamtx.yml inside double quotes.
        if '"' in value or "\n" in value or "%" in value:
            raise ValueError("a folder name cannot contain '\"', '%' or a line break")
        return value

    @model_validator(mode="after")
    def _check_feeds_and_ports(self) -> "FeedsSettings":
        names = [feed.name for feed in self.feeds]
        duplicates = sorted({name for name in names if names.count(name) > 1})
        if duplicates:
            raise ValueError(f"two feeds have the same name: {', '.join(duplicates)}")
        ids = [feed.id for feed in self.feeds]
        if len(set(ids)) != len(ids):
            raise ValueError("two feeds have the same id")
        sources = [feed.obs_source_name for feed in self.feeds]
        same_label = sorted({source for source in sources if sources.count(source) > 1})
        if same_label:
            raise ValueError(f"two feeds would make OBS sources named {', '.join(same_label)}")
        if self.accept_any_name and any(feed.password for feed in self.feeds):
            raise ValueError(
                "'accept any name' lets anyone publish on every name, passwords included: "
                "turn it off to use feed passwords"
            )
        ports = {
            "rtmp_port": self.rtmp_port,
            "srt_port": self.srt_port,
            "rtsp_port": self.rtsp_port,
            "api_port": self.api_port,
            "webrtc_port": self.webrtc_port,
            "webrtc_media_port": self.webrtc_media_port,
        }
        for field_name, port in ports.items():
            if port in STUDIO_PORTS:
                raise ValueError(f"{field_name} {port} is used by {STUDIO_PORTS[port]}")
        values = list(ports.values())
        if len(set(values)) != len(values):
            raise ValueError("the RTMP, SRT, RTSP, API and WebRTC ports must all be different")
        return self

    def feed_named(self, name: str) -> Feed | None:
        """The feed publishing on path `name`, if there is one."""
        return next((feed for feed in self.feeds if feed.name == name), None)
