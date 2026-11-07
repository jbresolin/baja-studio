"""Tests for the gateway settings (modules/gateway/config.py).

Every value ends up in the Caddyfile, so the validators are what stops a
setting from injecting a directive. These tests try the kinds of input an
operator could type, and some no one should.
"""

from typing import Any

import pytest
from pydantic import ValidationError

from baja_studio.core.settings import mask_secrets
from baja_studio.modules.gateway.config import GatewaySettings, TlsMode


def app(**overrides: Any) -> dict[str, Any]:
    """A valid proxied app, with some fields changed."""
    return {"path_prefix": "/overlay", "upstream": "127.0.0.1:5000", **overrides}


def test_the_defaults_are_plain_http_to_the_studio_with_no_apps() -> None:
    settings = GatewaySettings()

    assert settings.tls.mode == TlsMode.OFF
    assert settings.studio_upstream == "127.0.0.1:8888"
    assert settings.admin_api == "127.0.0.1:2019"
    assert settings.proxied_apps == []


def test_the_cloudflare_token_is_masked_like_every_secret() -> None:
    settings = GatewaySettings.model_validate(
        {"tls": {"mode": "dns01", "cloudflare_api_token": "abc123"}}
    )

    masked = mask_secrets(GatewaySettings, settings.model_dump(mode="json"))

    assert masked["tls"]["cloudflare_api_token"] == "********"


def test_a_trailing_slash_on_a_path_prefix_is_dropped() -> None:
    settings = GatewaySettings.model_validate({"proxied_apps": [app(path_prefix="/overlay/")]})

    assert settings.proxied_apps[0].path_prefix == "/overlay"


@pytest.mark.parametrize(
    "path_prefix",
    [
        "overlay",  # no leading slash
        "/",  # the whole site is the studio's
        "/over lay",  # a space would split the Caddyfile token
        "/overlay}",  # a brace would close a block
        "/overlay\nrespond 200",  # a new line would add a directive
        "/api",  # the studio's API
        "/ws",  # the studio's WebSocket
        "/action",  # the D200's routes
        "/Audio",  # a studio page, whatever the case
    ],
)
def test_unsafe_or_reserved_path_prefixes_are_refused(path_prefix: str) -> None:
    with pytest.raises(ValidationError):
        GatewaySettings.model_validate({"proxied_apps": [app(path_prefix=path_prefix)]})


@pytest.mark.parametrize(
    "upstream",
    ["127.0.0.1", "http://127.0.0.1:5000", "127.0.0.1:0", "127.0.0.1:70000", "a b:80", "x:80 {"],
)
def test_upstreams_must_be_plain_host_and_port(upstream: str) -> None:
    with pytest.raises(ValidationError):
        GatewaySettings.model_validate({"proxied_apps": [app(upstream=upstream)]})


def test_two_apps_cannot_share_a_path() -> None:
    with pytest.raises(ValidationError, match="same path"):
        GatewaySettings.model_validate(
            {"proxied_apps": [app(), app(path_prefix="/Overlay", upstream="127.0.0.1:5001")]}
        )


@pytest.mark.parametrize("hostname", ["live", "live..example.net", "live.example.net {", "-a.net"])
def test_hostnames_must_be_dns_names(hostname: str) -> None:
    with pytest.raises(ValidationError):
        GatewaySettings.model_validate({"hostname": hostname})


def test_a_hostname_is_lower_cased_and_trimmed() -> None:
    settings = GatewaySettings.model_validate({"hostname": "  Live.Example.NET "})

    assert settings.hostname == "live.example.net"


def test_tls_through_dns_needs_a_hostname() -> None:
    with pytest.raises(ValidationError, match="needs a hostname"):
        GatewaySettings.model_validate({"hostname": "", "tls": {"mode": "dns01"}})


def test_plain_http_works_without_a_hostname() -> None:
    settings = GatewaySettings.model_validate({"hostname": ""})

    assert settings.hostname == ""


@pytest.mark.parametrize("admin_api", ["0.0.0.0:2019", "192.168.10.20:2019"])
def test_the_admin_api_must_stay_on_loopback(admin_api: str) -> None:
    # Anyone who reaches Caddy's admin API can reconfigure it: never the LAN.
    with pytest.raises(ValidationError, match=r"127\.0\.0\.1"):
        GatewaySettings.model_validate({"admin_api": admin_api})
