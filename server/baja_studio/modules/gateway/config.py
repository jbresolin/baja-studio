"""Settings of the gateway module: the hostname, TLS, and the apps Caddy proxies.

Every value here ends up in the Caddyfile, so each is checked against a
strict pattern: a setting can never inject a directive. The Cloudflare token
is the exception, and it never reaches the Caddyfile at all (see caddyfile.py).
"""

import re
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from baja_studio.core.settings import ModuleSettings

# No default name: which one phones open is João's choice (ADR 0010); until then, the LAN address.
DEFAULT_HOSTNAME = ""
HOSTNAME_EXAMPLE = "live.bajasaebrasil.net"
DEFAULT_STUDIO_UPSTREAM = "127.0.0.1:8888"
DEFAULT_ADMIN_API = "127.0.0.1:2019"

# host:port, where host is a name or an IPv4 address (no scheme, no path).
UPSTREAM_PATTERN = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9.-]{0,251}[A-Za-z0-9])?:\d{1,5}$")
HOSTNAME_PATTERN = re.compile(
    r"^(?=.{1,253}$)(?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.)+[A-Za-z]{2,63}$"
)
# One or more path segments of plain characters: /overlay, /review/v2.
PATH_PREFIX_PATTERN = re.compile(r"^(?:/[A-Za-z0-9][A-Za-z0-9._-]*)+$")
# First path segments the studio itself answers; an app proxied there would hide them.
RESERVED_PATH_SEGMENTS = frozenset(
    {
        "api",
        "ws",
        "action",
        "assets",
        "modules",
        "settings",
        "logs",
        "cameras",
        "playout",
        "audio",
    }
)
LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost"})


class TlsMode(StrEnum):
    """How the gateway gets its certificate."""

    DNS01 = "dns01"
    OFF = "off"


class GatewayTls(BaseModel):
    """TLS: a Let's Encrypt certificate through Cloudflare DNS, or plain HTTP."""

    model_config = ConfigDict(extra="forbid")

    mode: TlsMode = Field(
        default=TlsMode.OFF,
        description=(
            "dns01: HTTPS on the hostname with a certificate obtained through Cloudflare DNS. "
            "off: plain HTTP on port 80."
        ),
    )
    cloudflare_api_token: str = Field(
        default="",
        description="A Cloudflare API token allowed to edit DNS on the hostname's zone.",
        json_schema_extra={"secret": True},
    )


class ProxiedApp(BaseModel):
    """Another app served under a path of the gateway, e.g. /overlay → 127.0.0.1:5000."""

    model_config = ConfigDict(extra="forbid")

    path_prefix: str = Field(description="e.g. /overlay; the prefix is removed before proxying.")
    upstream: str = Field(description="host:port of the app, e.g. 127.0.0.1:5000.")
    websocket: bool = Field(
        default=False, description="Keep its WebSockets open while the gateway reloads."
    )
    label: str = Field(default="", max_length=40, description="Shown on the home page.")

    @field_validator("path_prefix")
    @classmethod
    def _check_path_prefix(cls, value: str) -> str:
        value = value.rstrip("/")
        if not PATH_PREFIX_PATTERN.match(value):
            raise ValueError("must look like /name (letters, digits, '.', '_' or '-')")
        first_segment = value.split("/")[1].lower()
        if first_segment in RESERVED_PATH_SEGMENTS:
            raise ValueError(f"/{first_segment} belongs to the studio itself")
        return value

    @field_validator("upstream")
    @classmethod
    def _check_upstream(cls, value: str) -> str:
        return check_upstream(value)


class GatewaySettings(ModuleSettings):
    """Everything the Caddyfile is rendered from. No Caddyfile is ever edited by hand."""

    hostname: str = Field(
        default=DEFAULT_HOSTNAME,
        description=(
            "The name phones open; its DNS A record points at this PC's LAN address. "
            "Empty: phones use the PC's address."
        ),
        examples=[HOSTNAME_EXAMPLE],
    )
    tls: GatewayTls = Field(default_factory=GatewayTls)
    studio_upstream: str = Field(
        default=DEFAULT_STUDIO_UPSTREAM, description="Where this service listens (host:port)."
    )
    proxied_apps: list[ProxiedApp] = Field(
        default_factory=list, description="Other apps served under a path of the same host."
    )
    admin_api: str = Field(
        default=DEFAULT_ADMIN_API,
        description="Caddy's admin API; always on this PC (127.0.0.1), never on the LAN.",
    )

    @field_validator("hostname")
    @classmethod
    def _check_hostname(cls, value: str) -> str:
        value = value.strip().lower()
        if value and not HOSTNAME_PATTERN.match(value):
            raise ValueError("must be a DNS name such as live.example.net")
        return value

    @field_validator("studio_upstream")
    @classmethod
    def _check_studio_upstream(cls, value: str) -> str:
        return check_upstream(value)

    @field_validator("admin_api")
    @classmethod
    def _check_admin_api(cls, value: str) -> str:
        value = check_upstream(value)
        if value.rsplit(":", 1)[0] not in LOOPBACK_HOSTS:
            raise ValueError("Caddy's admin API must stay on 127.0.0.1")
        return value

    @model_validator(mode="after")
    def _check_tls_and_apps(self) -> "GatewaySettings":
        if self.tls.mode == TlsMode.DNS01 and not self.hostname:
            raise ValueError("TLS through DNS needs a hostname")
        prefixes = [app.path_prefix.lower() for app in self.proxied_apps]
        duplicates = sorted({prefix for prefix in prefixes if prefixes.count(prefix) > 1})
        if duplicates:
            raise ValueError(f"two apps use the same path: {', '.join(duplicates)}")
        return self


def check_upstream(value: str) -> str:
    """Validate a host:port; shared by the studio, the apps and the admin API."""
    value = value.strip()
    if not UPSTREAM_PATTERN.match(value) or not 0 < int(value.rsplit(":", 1)[1]) < 65536:
        raise ValueError("must be host:port, e.g. 127.0.0.1:5000")
    return value
