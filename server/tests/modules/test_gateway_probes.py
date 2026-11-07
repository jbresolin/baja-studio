"""Tests for the gateway's checks (modules/gateway/probes.py).

The network is replaced: an in-process transport for HTTP, plain functions
for DNS. The real TLS handshake is not run here (it needs Caddy on port
443); what it returns is parsed by `certificate_info_from_peer`, tested below
with the exact dictionary Python's `ssl` module produces.
"""

import httpx2

from baja_studio.modules.gateway.probes import (
    certificate_info_from_peer,
    check_dns,
    check_upstream,
)


def answering(status: int) -> httpx2.MockTransport:
    return httpx2.MockTransport(lambda request: httpx2.Response(status))


def refusing() -> httpx2.MockTransport:
    def refuse(request: httpx2.Request) -> httpx2.Response:
        raise httpx2.ConnectError("connection refused", request=request)

    return httpx2.MockTransport(refuse)


def test_an_app_that_answers_is_reachable_whatever_the_status() -> None:
    # A 404 or 401 still proves the app is there; only silence is a problem.
    check = check_upstream("127.0.0.1:5000", answering(404))

    assert check.reachable
    assert check.status_code == 404


def test_an_app_that_refuses_the_connection_is_unreachable() -> None:
    check = check_upstream("127.0.0.1:5000", refusing())

    assert not check.reachable
    assert "connection refused" in check.error


def test_a_certificate_is_read_from_what_ssl_returns() -> None:
    peer = {
        "subject": ((("commonName", "live.bajasaebrasil.net"),),),
        "issuer": (
            (("countryName", "US"),),
            (("organizationName", "Let's Encrypt"),),
            (("commonName", "R11"),),
        ),
        "notAfter": "Feb  4 12:00:00 2027 GMT",
    }

    info = certificate_info_from_peer(peer)

    assert info.subject == "live.bajasaebrasil.net"
    assert info.issuer == "Let's Encrypt"
    assert info.expires_at == "2027-02-04T12:00:00+00:00"


def test_dns_pointing_at_this_pc_matches() -> None:
    check = check_dns(
        "live.example.net", lambda name: ["192.168.10.20"], lambda: ["192.168.10.20", "10.1.1.1"]
    )

    assert check.matches
    assert check.message == "live.example.net points at this PC"


def test_dns_pointing_elsewhere_says_where() -> None:
    check = check_dns("live.example.net", lambda name: ["192.168.10.99"], lambda: ["192.168.10.20"])

    assert not check.matches
    assert check.message == "live.example.net points at 192.168.10.99, but this PC is 192.168.10.20"


def test_a_name_that_does_not_resolve_says_to_add_the_records() -> None:
    check = check_dns("live.example.net", lambda name: [], lambda: ["192.168.10.20"])

    assert not check.matches
    assert "does not resolve" in check.message
    assert "hEX" in check.message
