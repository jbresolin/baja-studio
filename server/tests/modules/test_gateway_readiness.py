"""Tests for the gateway's readiness checks (modules/gateway/readiness.py).

The gateway module runs with the fakes of tests/modules/gateway_studio.py
standing in for Caddy, Windows' port list, DNS, TLS and the proxied apps.
Each test makes one of them misbehave and runs the gateway's checks.
"""

from collections.abc import Iterator
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from baja_studio.core.readiness import CheckStatus
from baja_studio.core.services import StudioServices
from baja_studio.modules.gateway import GatewayModule
from baja_studio.modules.gateway.ports import SYSTEM_PID, PortHolder
from baja_studio.modules.gateway.probes import CertificateInfo
from tests.conftest import AppFactory, FakeClock
from tests.modules.gateway_studio import LAN_ADDRESS, GatewayFakes
from tests.modules.readiness_runner import assert_fix_given, run_module_checks, statuses

HOSTNAME = "live.bajasaebrasil.net"
TLS_ON = {"tls": {"mode": "dns01", "cloudflare_api_token": "cf-token-0123456789"}}
OVERLAY = {"path_prefix": "/overlay", "upstream": "127.0.0.1:5000", "label": "BajaOvrLay"}


@pytest.fixture
def services(make_app: AppFactory, gateway_fakes: GatewayFakes) -> Iterator[StudioServices]:
    """An app with just the gateway module; its name is set and resolves to this PC."""
    gateway_fakes.dns_records[HOSTNAME] = [LAN_ADDRESS]
    app = make_app(lambda registry: [GatewayModule(gateway_fakes.services, gateway_fakes.system())])
    with TestClient(app):
        services: StudioServices = app.state.services
        services.settings.update("gateway", {"hostname": HOSTNAME})
        yield services


def test_no_hostname_yet_says_not_set(services: StudioServices, fake_clock: FakeClock) -> None:
    # A fresh install has no hostname until João picks the studio's name.
    services.settings.update("gateway", {"hostname": ""})

    result = run_module_checks(services, "gateway", fake_clock)["dns"]

    # A warning, not a failure: phones can still open the PC's address.
    assert result.status == CheckStatus.WARN
    assert result.detail.startswith("not set")
    assert_fix_given(result)


def certificate_expiring(fake_clock: FakeClock, days: int) -> CertificateInfo:
    expires = fake_clock.now_utc() + timedelta(days=days)
    return CertificateInfo("Let's Encrypt", HOSTNAME, expires.isoformat())


def test_a_healthy_gateway_passes_every_check(
    services: StudioServices, fake_clock: FakeClock
) -> None:
    results = run_module_checks(services, "gateway", fake_clock)

    assert statuses(results) == {
        "caddy": CheckStatus.OK,
        "ports": CheckStatus.OK,
        "dns": CheckStatus.OK,
        "tls": CheckStatus.OK,
    }
    assert results["dns"].detail == f"{HOSTNAME} points at this PC"
    assert results["tls"].detail == "HTTPS is off: the gateway serves plain HTTP"


def test_caddy_not_answering_fails(
    services: StudioServices, gateway_fakes: GatewayFakes, fake_clock: FakeClock
) -> None:
    gateway_fakes.caddy.stop()

    result = run_module_checks(services, "gateway", fake_clock)["caddy"]

    assert result.status == CheckStatus.FAIL
    assert result.detail.startswith("Caddy does not answer on 127.0.0.1:2019")
    assert_fix_given(result)


def test_another_program_on_port_80_fails_with_that_programs_fix(
    services: StudioServices, gateway_fakes: GatewayFakes, fake_clock: FakeClock
) -> None:
    iis = PortHolder(80, SYSTEM_PID, "System")
    gateway_fakes.port_holders = [iis]

    result = run_module_checks(services, "gateway", fake_clock)["ports"]

    assert result.status == CheckStatus.FAIL
    assert result.detail == "port 80 in use by Windows HTTP.sys (usually IIS)"
    assert result.fix == iis.fix
    # With TLS off only port 80 matters.
    assert gateway_fakes.ports_asked[-1] == (80,)


def test_with_tls_on_port_443_is_checked_too(
    services: StudioServices, gateway_fakes: GatewayFakes, fake_clock: FakeClock
) -> None:
    services.settings.update("gateway", TLS_ON)

    run_module_checks(services, "gateway", fake_clock)

    assert gateway_fakes.ports_asked[-1] == (80, 443)


def test_a_name_pointing_elsewhere_fails(
    services: StudioServices, gateway_fakes: GatewayFakes, fake_clock: FakeClock
) -> None:
    gateway_fakes.dns_records[HOSTNAME] = ["203.0.113.7"]

    result = run_module_checks(services, "gateway", fake_clock)["dns"]

    assert result.status == CheckStatus.FAIL
    assert result.detail == f"{HOSTNAME} points at 203.0.113.7, but this PC is {LAN_ADDRESS}"
    assert_fix_given(result)


def test_a_name_that_does_not_resolve_only_warns(
    services: StudioServices, gateway_fakes: GatewayFakes, fake_clock: FakeClock
) -> None:
    # WAN down and no static entry on the hEX yet: the page still works by address.
    gateway_fakes.dns_records.clear()

    result = run_module_checks(services, "gateway", fake_clock)["dns"]

    assert result.status == CheckStatus.WARN
    assert "does not resolve here" in result.detail
    assert_fix_given(result)


def test_a_certificate_with_weeks_left_passes(
    services: StudioServices, gateway_fakes: GatewayFakes, fake_clock: FakeClock
) -> None:
    services.settings.update("gateway", TLS_ON)
    gateway_fakes.certificates.certificate = certificate_expiring(fake_clock, 60)

    result = run_module_checks(services, "gateway", fake_clock)["tls"]

    assert result.status == CheckStatus.OK
    assert result.detail == "valid until 2026-11-30 (60 days left)"


def test_a_certificate_with_less_than_a_week_left_fails(
    services: StudioServices, gateway_fakes: GatewayFakes, fake_clock: FakeClock
) -> None:
    # Caddy renews 30 days ahead; a week left means renewal has been failing.
    services.settings.update("gateway", TLS_ON)
    gateway_fakes.certificates.certificate = certificate_expiring(fake_clock, 6)

    result = run_module_checks(services, "gateway", fake_clock)["tls"]

    assert result.status == CheckStatus.FAIL
    assert_fix_given(result)


def test_no_certificate_fails_with_the_handshake_error(
    services: StudioServices, gateway_fakes: GatewayFakes, fake_clock: FakeClock
) -> None:
    services.settings.update("gateway", TLS_ON)

    result = run_module_checks(services, "gateway", fake_clock)["tls"]

    assert result.status == CheckStatus.FAIL
    assert result.detail.startswith(f"no valid certificate for {HOSTNAME}: certificate verify")


def test_tls_on_without_a_token_says_to_paste_one(
    services: StudioServices, fake_clock: FakeClock
) -> None:
    services.settings.update("gateway", {"tls": {"mode": "dns01"}})

    result = run_module_checks(services, "gateway", fake_clock)["tls"]

    assert result.status == CheckStatus.FAIL
    assert result.fix.startswith("Paste a Cloudflare API token")


def test_each_proxied_app_gets_a_check_that_fails_while_it_is_silent(
    services: StudioServices, gateway_fakes: GatewayFakes, fake_clock: FakeClock
) -> None:
    services.settings.update("gateway", {"proxied_apps": [OVERLAY]})

    silent = run_module_checks(services, "gateway", fake_clock)["app:/overlay"]
    gateway_fakes.upstreams.answering["127.0.0.1:5000"] = 200
    answering = run_module_checks(services, "gateway", fake_clock)["app:/overlay"]

    assert silent.label == "BajaOvrLay answers"
    assert silent.status == CheckStatus.FAIL
    assert silent.fix.startswith("Start BajaOvrLay on this PC (it should answer on 127.0.0.1:5000)")
    assert answering.status == CheckStatus.OK
    assert answering.detail == "HTTP 200 from 127.0.0.1:5000"
