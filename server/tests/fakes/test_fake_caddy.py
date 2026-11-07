"""Tests for the fake Caddy admin API (fakes/fake_caddy.py).

The fake is what the gateway tests trust to behave like Caddy, so its own
rules are pinned here: well-formed configs are kept, broken ones refused,
and a stopped Caddy refuses connections. Requests go through its in-process
transport.
"""

import httpx2
import pytest

from baja_studio.fakes.fake_caddy import FakeCaddy, check_caddyfile


def post_caddyfile(caddy: FakeCaddy, text: str) -> httpx2.Response:
    with httpx2.Client(transport=caddy.transport()) as client:
        return client.post(
            "http://127.0.0.1:2019/load",
            content=text.encode(),
            headers={"Content-Type": "text/caddyfile"},
        )


def test_a_well_formed_caddyfile_is_kept() -> None:
    caddy = FakeCaddy()

    response = post_caddyfile(caddy, ":80 {\n\trespond ok\n}\n")

    assert response.status_code == 200
    assert caddy.loaded == [":80 {\n\trespond ok\n}\n"]


def test_unbalanced_braces_are_refused_like_caddys_parser_would() -> None:
    caddy = FakeCaddy()

    response = post_caddyfile(caddy, ":80 {\n\trespond ok\n")

    assert response.status_code == 400
    assert "missing '}'" in response.json()["error"]
    assert caddy.loaded == []


def test_braces_in_comments_do_not_count() -> None:
    assert check_caddyfile("# not a block {\n:80 {\n}\n") is None
    assert check_caddyfile("}\n") == "Caddyfile:1: unexpected '}'"


def test_json_configs_are_refused_since_the_studio_only_sends_caddyfiles() -> None:
    with httpx2.Client(transport=FakeCaddy().transport()) as client:
        response = client.post("http://127.0.0.1:2019/load", json={"apps": {}})

    assert response.status_code == 400


def test_a_stopped_caddy_refuses_connections_until_started() -> None:
    caddy = FakeCaddy()
    caddy.stop()

    with httpx2.Client(transport=caddy.transport()) as client:
        with pytest.raises(httpx2.ConnectError):
            client.get("http://127.0.0.1:2019/config/")
        caddy.start()
        assert client.get("http://127.0.0.1:2019/config/").status_code == 200


def test_refusals_can_be_switched_on_and_off() -> None:
    caddy = FakeCaddy()

    caddy.refuse_next_loads("unknown directive")
    refused = post_caddyfile(caddy, ":80 {\n}\n")
    caddy.refuse_next_loads(None)
    accepted = post_caddyfile(caddy, ":80 {\n}\n")

    assert refused.json() == {"error": "adapting config: unknown directive"}
    assert accepted.status_code == 200
