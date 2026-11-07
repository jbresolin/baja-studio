"""Tests for applying the gateway config (modules/gateway/controller.py).

The controller decides between reloading Caddy through its admin API and
restarting the gateway service with a new environment (the token). Caddy is
the in-process fake; NSSM is `FakeServiceControl` (tests/modules/gateway_studio.py).
"""

import logging
from pathlib import Path
from typing import Any

import pytest

from baja_studio.core.system_services import GATEWAY_SERVICE, ServiceControlError
from baja_studio.modules.gateway.admin_api import CaddyAdminClient
from baja_studio.modules.gateway.caddyfile import caddyfile_path
from baja_studio.modules.gateway.config import GatewaySettings
from baja_studio.modules.gateway.controller import (
    GatewayApplyError,
    GatewayController,
    desired_gateway_environment,
)
from tests.conftest import FakeClock
from tests.modules.gateway_studio import FakeServiceControl, GatewayFakes

TOKEN = "cf-token-0123456789abcdef"


def settings_with(**values: Any) -> GatewaySettings:
    return GatewaySettings.model_validate(values)


TLS_ON = {"tls": {"mode": "dns01", "cloudflare_api_token": TOKEN}}


@pytest.fixture
def controller(
    gateway_fakes: GatewayFakes, tmp_path: Path, fake_clock: FakeClock
) -> GatewayController:
    return GatewayController(
        tmp_path,
        gateway_fakes.services,
        lambda address: CaddyAdminClient(address, gateway_fakes.caddy.transport()),
        fake_clock,
        logging.getLogger("test"),
    )


def written(tmp_path: Path) -> str | None:
    path = caddyfile_path(tmp_path)
    return path.read_text(encoding="utf-8") if path.is_file() else None


def test_a_change_without_a_new_token_is_loaded_then_written(
    controller: GatewayController, gateway_fakes: GatewayFakes, tmp_path: Path
) -> None:
    outcome = controller.apply(settings_with(proxied_apps=[]))

    assert outcome.loaded and not outcome.restarted
    # What Caddy runs and what it will start from next time are the same file.
    assert gateway_fakes.caddy.loaded == [written(tmp_path)]
    assert gateway_fakes.services.restarts == []


def test_a_config_caddy_refuses_is_not_written(
    controller: GatewayController, gateway_fakes: GatewayFakes, tmp_path: Path
) -> None:
    # The file is what Caddy starts from after a reboot: never a config it refused.
    gateway_fakes.caddy.refuse_next_loads("unknown directive")

    with pytest.raises(GatewayApplyError, match=r"Caddy refused the config: .*unknown directive"):
        controller.apply(GatewaySettings())

    assert written(tmp_path) is None


def test_with_caddy_down_the_file_is_written_for_its_next_start(
    controller: GatewayController, gateway_fakes: GatewayFakes, tmp_path: Path
) -> None:
    gateway_fakes.caddy.stop()

    outcome = controller.apply(GatewaySettings())

    assert not outcome.loaded
    assert "not answering" in outcome.message
    assert written(tmp_path) is not None


def test_a_new_token_restarts_the_gateway_with_it_in_the_environment(
    controller: GatewayController, gateway_fakes: GatewayFakes, tmp_path: Path
) -> None:
    outcome = controller.apply(settings_with(**TLS_ON))

    assert outcome.restarted
    assert gateway_fakes.services.environments[GATEWAY_SERVICE] == {"CLOUDFLARE_API_TOKEN": TOKEN}
    assert gateway_fakes.services.restarts == [GATEWAY_SERVICE]
    # Caddy reads the new file as it starts; no /load (it would lack the token).
    assert gateway_fakes.caddy.loaded == []
    assert TOKEN not in (written(tmp_path) or "")


def test_the_same_token_again_reloads_without_a_restart(
    controller: GatewayController, gateway_fakes: GatewayFakes
) -> None:
    controller.apply(settings_with(**TLS_ON))

    controller.apply(settings_with(**TLS_ON, proxied_apps=[]))

    assert gateway_fakes.services.restarts == [GATEWAY_SERVICE]
    assert len(gateway_fakes.caddy.loaded) == 1


def test_switching_tls_off_removes_the_token_from_the_environment(
    controller: GatewayController, gateway_fakes: GatewayFakes
) -> None:
    controller.apply(settings_with(**TLS_ON))

    controller.apply(settings_with(tls={"mode": "off", "cloudflare_api_token": TOKEN}))

    assert gateway_fakes.services.environments[GATEWAY_SERVICE] == {}
    assert gateway_fakes.services.restarts == [GATEWAY_SERVICE, GATEWAY_SERVICE]


def test_from_a_checkout_there_is_no_service_and_the_config_is_just_loaded(
    gateway_fakes: GatewayFakes, tmp_path: Path, fake_clock: FakeClock
) -> None:
    services = FakeServiceControl(installed=False)
    controller = GatewayController(
        tmp_path,
        services,
        lambda address: CaddyAdminClient(address, gateway_fakes.caddy.transport()),
        fake_clock,
        logging.getLogger("test"),
    )

    outcome = controller.apply(settings_with(**TLS_ON))

    assert outcome.loaded and not outcome.restarted
    assert services.restarts == []


def test_nssm_failing_is_reported_as_an_apply_error(
    controller: GatewayController, gateway_fakes: GatewayFakes
) -> None:
    def broken_restart(service: str) -> None:
        raise ServiceControlError("nssm restart BajaStudioGateway failed (exit 5): Access denied")

    gateway_fakes.services.restart = broken_restart  # type: ignore[method-assign]

    with pytest.raises(GatewayApplyError, match="Access denied"):
        controller.apply(settings_with(**TLS_ON))


def test_the_environment_holds_the_token_only_when_tls_uses_it() -> None:
    assert desired_gateway_environment(settings_with(**TLS_ON)) == {"CLOUDFLARE_API_TOKEN": TOKEN}
    assert desired_gateway_environment(settings_with(tls={"mode": "dns01"})) == {}
    assert (
        desired_gateway_environment(settings_with(tls={"mode": "off", "cloudflare_api_token": "x"}))
        == {}
    )
