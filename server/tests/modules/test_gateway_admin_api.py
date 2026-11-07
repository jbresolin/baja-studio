"""Tests for the Caddy admin API client (modules/gateway/admin_api.py).

The client talks to the fake Caddy (baja_studio/fakes/fake_caddy.py) through
its in-process transport: no port is opened.
"""

import httpx2
import pytest

from baja_studio.fakes.fake_caddy import FakeCaddy
from baja_studio.modules.gateway.admin_api import (
    CaddyAdminClient,
    CaddyRejectedConfigError,
    CaddyUnreachableError,
)

CADDYFILE = ":80 {\n\trespond ok\n}\n"


@pytest.fixture
def caddy() -> FakeCaddy:
    return FakeCaddy()


@pytest.fixture
def client(caddy: FakeCaddy) -> CaddyAdminClient:
    return CaddyAdminClient("127.0.0.1:2019", caddy.transport())


def test_a_caddyfile_is_loaded_as_text(caddy: FakeCaddy, client: CaddyAdminClient) -> None:
    client.load_caddyfile(CADDYFILE)

    assert caddy.loaded == [CADDYFILE]


def test_a_refused_config_raises_with_caddys_explanation(
    caddy: FakeCaddy, client: CaddyAdminClient
) -> None:
    caddy.refuse_next_loads("unrecognized directive: reverse_prox")

    with pytest.raises(CaddyRejectedConfigError, match="unrecognized directive: reverse_prox"):
        client.load_caddyfile(CADDYFILE)
    # Caddy keeps the old config: nothing was loaded.
    assert caddy.loaded == []


def test_reading_the_config_tells_that_caddy_is_up(client: CaddyAdminClient) -> None:
    assert client.read_config() == {"fake": True, "caddyfile_lines": 0}


def test_a_stopped_caddy_is_reported_as_unreachable(
    caddy: FakeCaddy, client: CaddyAdminClient
) -> None:
    caddy.stop()

    with pytest.raises(CaddyUnreachableError, match=r"127\.0\.0\.1:2019"):
        client.read_config()
    with pytest.raises(CaddyUnreachableError):
        client.load_caddyfile(CADDYFILE)


def test_an_error_page_without_json_is_reported_as_text() -> None:
    # A proxy or an old Caddy may answer an error page instead of {"error": ...}.
    transport = httpx2.MockTransport(lambda request: httpx2.Response(500, text="Internal oops"))
    client = CaddyAdminClient("127.0.0.1:2019", transport)

    with pytest.raises(CaddyRejectedConfigError, match="Internal oops"):
        client.load_caddyfile(CADDYFILE)
