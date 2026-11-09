"""Tests for the gateway module (modules/gateway), running inside the service.

Each test starts an app with only the gateway module, on the fake clock, with
the fakes from tests/modules/gateway_studio.py standing in for Caddy, NSSM,
the proxied apps, DNS and TLS. The module's worker polls every ten seconds;
tests move fake time with `step_fake_time` to make a poll happen.
"""

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from baja_studio.core.module import CommandError
from baja_studio.core.services import StudioServices
from baja_studio.core.supervisor import HealthState, WorkerSpec
from baja_studio.core.system_services import GATEWAY_SERVICE
from baja_studio.modules.gateway import GatewayModule
from baja_studio.modules.gateway.caddyfile import caddyfile_path
from baja_studio.modules.gateway.ports import PortHolder
from baja_studio.modules.gateway.probes import CertificateInfo
from tests.conftest import (
    AppFactory,
    FakeClock,
    eventually,
    module_worker_threads,
    step_fake_time,
    step_fake_time_until,
)
from tests.modules.gateway_studio import FAKE_CADDY_VERSION, LAN_ADDRESS, GatewayFakes

TOKEN = "cf-token-0123456789abcdef"
OVERLAY = {"path_prefix": "/overlay", "upstream": "127.0.0.1:5000", "label": "BajaOvrLay"}
TLS_ON = {
    "hostname": "live.bajasaebrasil.net",
    "tls": {"mode": "dns01", "cloudflare_api_token": TOKEN},
}
CERTIFICATE = CertificateInfo(
    "Let's Encrypt", "live.bajasaebrasil.net", "2027-02-04T12:00:00+00:00"
)
POLL = 10.0


@pytest.fixture
def services(make_app: AppFactory, gateway_fakes: GatewayFakes) -> Iterator[StudioServices]:
    """An app with just the gateway module, its outside world faked."""
    app = make_app(lambda registry: [GatewayModule(gateway_fakes.services, gateway_fakes.system())])
    services: StudioServices = app.state.services
    with TestClient(app):
        eventually(lambda: health(services)[0] != HealthState.OFFLINE)
        yield services


def health(services: StudioServices) -> tuple[HealthState, str]:
    current = services.supervisor.module_health("gateway")
    return current.state, current.message


def state(services: StudioServices, path: str = "") -> Any:
    return services.state.get("gateway", path)


def command(services: StudioServices, name: str) -> Any:
    return services.registry.dispatch_command("gateway", name, {}, "web:test")


def poll(fake_clock: FakeClock) -> None:
    """Let one poll interval pass, the worker asleep before and after."""
    step_fake_time(fake_clock, POLL, module_worker_threads("gateway"), step=POLL)


def test_on_start_the_config_is_applied_and_caddy_is_watched(
    services: StudioServices, gateway_fakes: GatewayFakes
) -> None:
    assert len(gateway_fakes.caddy.loaded) == 1
    assert state(services, "caddy") == {
        "running": True,
        "version": FAKE_CADDY_VERSION,
        "admin_api": "127.0.0.1:2019",
        "message": "",
        "port_conflicts": [],
    }
    assert state(services, "config.loaded") is True
    assert state(services, "studio.reachable") is True
    assert health(services) == (HealthState.OK, "")


def test_a_settings_change_is_applied_without_a_restart(
    services: StudioServices, gateway_fakes: GatewayFakes, fake_clock: FakeClock
) -> None:
    services.settings.update("gateway", {"proxied_apps": [OVERLAY]})

    eventually(lambda: len(gateway_fakes.caddy.loaded) == 2)
    assert "handle_path /overlay/*" in gateway_fakes.caddy.loaded[-1]


def test_a_silent_app_degrades_the_gateway_until_it_answers(
    services: StudioServices, gateway_fakes: GatewayFakes, fake_clock: FakeClock
) -> None:
    # The overlay app is configured but nothing listens on its port yet.
    services.settings.update("gateway", {"proxied_apps": [OVERLAY]})
    eventually(lambda: health(services) == (HealthState.DEGRADED, "BajaOvrLay is not answering"))
    assert state(services, "apps")[0]["reachable"] is False

    # It starts; the next poll sees it.
    gateway_fakes.upstreams.answering["127.0.0.1:5000"] = 200
    poll(fake_clock)

    assert health(services) == (HealthState.OK, "")
    assert state(services, "apps")[0] == {
        **OVERLAY,
        "websocket": False,
        "reachable": True,
        "status_code": 200,
        "error": "",
    }


def test_caddy_going_away_shows_the_gateway_offline_and_back(
    services: StudioServices, gateway_fakes: GatewayFakes, fake_clock: FakeClock
) -> None:
    gateway_fakes.caddy.stop()
    poll(fake_clock)

    assert health(services)[0] == HealthState.OFFLINE
    assert "is the BajaStudioGateway service running?" in health(services)[1]
    assert state(services, "caddy.running") is False

    # The supervisor runs the worker again after a second; Caddy is back by then.
    gateway_fakes.caddy.start()
    step_fake_time_until(
        fake_clock,
        lambda: health(services) == (HealthState.OK, ""),
        within_seconds=2.0,
        workers=module_worker_threads("gateway"),
    )
    assert state(services, "caddy.running") is True


def test_a_program_holding_port_80_is_named_with_the_fix(
    services: StudioServices, gateway_fakes: GatewayFakes, fake_clock: FakeClock
) -> None:
    # IIS starts with Windows, takes port 80 through HTTP.sys, and Caddy exits.
    gateway_fakes.port_holders = [PortHolder(80, 4, "System")]
    gateway_fakes.caddy.stop()
    poll(fake_clock)

    assert health(services) == (
        HealthState.OFFLINE,
        "port 80 in use by Windows HTTP.sys (usually IIS): Caddy cannot start",
    )
    [conflict] = state(services, "caddy.port_conflicts")
    assert conflict["holder"] == "Windows HTTP.sys (usually IIS)"
    assert '"World Wide Web Publishing Service"' in conflict["fix"]
    assert gateway_fakes.ports_asked[-1] == (80,)

    # Someone stops IIS and starts the gateway: the conflict is gone from the page.
    gateway_fakes.port_holders = []
    gateway_fakes.caddy.start()
    step_fake_time_until(
        fake_clock,
        lambda: health(services) == (HealthState.OK, ""),
        within_seconds=2.0,
        workers=module_worker_threads("gateway"),
    )
    assert state(services, "caddy.port_conflicts") == []


def test_with_tls_on_port_443_is_checked_too(
    services: StudioServices, gateway_fakes: GatewayFakes, fake_clock: FakeClock
) -> None:
    services.settings.update("gateway", TLS_ON)
    eventually(lambda: gateway_fakes.services.restarts == [GATEWAY_SERVICE])
    eventually(lambda: state(services, "tls.issued") is False)
    gateway_fakes.port_holders = [PortHolder(443, 5120, "vmware-hostd.exe", ("VMwareHostd",))]
    gateway_fakes.caddy.stop()
    poll(fake_clock)

    assert gateway_fakes.ports_asked[-1] == (80, 443)
    assert health(services)[1] == (
        "port 443 in use by vmware-hostd.exe (service VMwareHostd): Caddy cannot start"
    )


def test_caddy_down_with_its_ports_free_asks_about_the_service(
    services: StudioServices, gateway_fakes: GatewayFakes, fake_clock: FakeClock
) -> None:
    gateway_fakes.caddy.stop()
    poll(fake_clock)

    assert "is the BajaStudioGateway service running?" in health(services)[1]
    assert state(services, "caddy.port_conflicts") == []


def test_turning_tls_on_restarts_caddy_with_the_token_and_checks_the_certificate(
    services: StudioServices, gateway_fakes: GatewayFakes, fake_clock: FakeClock
) -> None:
    services.settings.update("gateway", TLS_ON)

    # The token went to the service's environment, and Caddy was restarted to read it.
    eventually(lambda: gateway_fakes.services.restarts == [GATEWAY_SERVICE])
    assert gateway_fakes.services.environments[GATEWAY_SERVICE] == {"CLOUDFLARE_API_TOKEN": TOKEN}
    # No certificate yet (Let's Encrypt takes a moment): degraded, with the reason.
    eventually(lambda: state(services, "tls.issued") is False)
    assert health(services)[0] == HealthState.DEGRADED
    assert "no valid certificate for live.bajasaebrasil.net" in health(services)[1]
    assert state(services, "public_base") == "https://live.bajasaebrasil.net"

    # The certificate arrives; the next poll checks again, since it was missing.
    gateway_fakes.certificates.certificate = CERTIFICATE
    poll(fake_clock)

    assert health(services) == (HealthState.OK, "")
    tls = state(services, "tls")
    assert (tls["issued"], tls["issuer"], tls["expires_at"]) == (
        True,
        "Let's Encrypt",
        "2027-02-04T12:00:00+00:00",
    )


def test_a_valid_certificate_is_rechecked_once_a_minute_not_every_poll(
    services: StudioServices, gateway_fakes: GatewayFakes, fake_clock: FakeClock
) -> None:
    gateway_fakes.certificates.certificate = CERTIFICATE
    services.settings.update("gateway", TLS_ON)
    eventually(lambda: state(services, "tls.issued") is True)
    probes_so_far = len(gateway_fakes.certificates.probed)

    for _ in range(5):
        poll(fake_clock)
    assert len(gateway_fakes.certificates.probed) == probes_so_far

    poll(fake_clock)  # the sixth poll: a minute since the last check
    assert len(gateway_fakes.certificates.probed) == probes_so_far + 1


def test_tls_on_without_a_token_says_to_paste_one(
    services: StudioServices, gateway_fakes: GatewayFakes
) -> None:
    services.settings.update("gateway", {**TLS_ON, "tls": {"mode": "dns01"}})

    eventually(lambda: "paste a Cloudflare API token" in health(services)[1])


def test_a_config_caddy_refuses_shows_in_the_health_and_the_state(
    services: StudioServices, gateway_fakes: GatewayFakes, fake_clock: FakeClock
) -> None:
    gateway_fakes.caddy.refuse_next_loads("unknown directive 'reverse_prox'")

    with pytest.raises(CommandError, match="Caddy refused the config"):
        command(services, "apply")

    assert "reverse_prox" in state(services, "config.error")
    poll(fake_clock)
    assert health(services)[0] == HealthState.DEGRADED
    assert "Caddy refused the config" in health(services)[1]


def test_the_apply_command_reports_what_it_did(services: StudioServices) -> None:
    outcome = command(services, "apply")

    assert outcome["loaded"] is True
    assert outcome["restarted"] is False
    assert outcome["caddyfile"].endswith("Caddyfile")


def test_test_dns_compares_the_name_with_this_pcs_addresses(
    services: StudioServices, gateway_fakes: GatewayFakes
) -> None:
    services.settings.update("gateway", {"hostname": "live.bajasaebrasil.net"})
    gateway_fakes.dns_records["live.bajasaebrasil.net"] = [LAN_ADDRESS]

    result = command(services, "test_dns")

    assert result["matches"] is True
    assert state(services, "dns") == result


def test_test_tls_with_tls_off_explains_there_is_nothing_to_test(
    services: StudioServices,
) -> None:
    with pytest.raises(CommandError, match="TLS is off"):
        command(services, "test_tls")


def test_the_token_never_appears_in_the_state(
    services: StudioServices, gateway_fakes: GatewayFakes, fake_clock: FakeClock
) -> None:
    gateway_fakes.certificates.certificate = CERTIFICATE
    services.settings.update("gateway", {**TLS_ON, "proxied_apps": [OVERLAY]})
    eventually(lambda: state(services, "tls.issued") is True)
    command(services, "test_tls")

    assert TOKEN not in json.dumps(services.state.snapshot())


class GatewayWithoutWatcher(GatewayModule):
    """The gateway module with its worker left out: only `start()` can write the Caddyfile."""

    def workers(self) -> list[WorkerSpec]:
        return []


def test_the_caddyfile_is_written_before_the_service_answers(
    make_app: AppFactory, gateway_fakes: GatewayFakes, data_directory: Path
) -> None:
    # A fresh install: no Caddyfile yet, and Caddy not running (the installer
    # starts it only once the studio answers /api/health).
    gateway_fakes.caddy.stop()
    app = make_app(
        lambda registry: [GatewayWithoutWatcher(gateway_fakes.services, gateway_fakes.system())]
    )

    # Entering the client runs the startup, as the real service does before it
    # listens; without the watcher, the file can only come from `start()`.
    with TestClient(app) as client:
        assert client.get("/api/health").status_code == 200
        written = caddyfile_path(data_directory).read_text(encoding="utf-8")

    # It is the config for the current settings: the studio behind port 80.
    assert "reverse_proxy 127.0.0.1:8888" in written


def test_an_existing_caddyfile_is_left_for_the_watcher_to_replace(
    make_app: AppFactory, gateway_fakes: GatewayFakes, data_directory: Path
) -> None:
    # An upgrade: the Caddyfile Caddy last accepted is already there.
    path = caddyfile_path(data_directory)
    path.parent.mkdir(parents=True)
    path.write_text("# the config Caddy accepted\n", encoding="utf-8")
    app = make_app(
        lambda registry: [GatewayWithoutWatcher(gateway_fakes.services, gateway_fakes.system())]
    )

    with TestClient(app):
        # `start()` does not touch it: only an apply Caddy accepted may change it.
        assert path.read_text(encoding="utf-8") == "# the config Caddy accepted\n"
