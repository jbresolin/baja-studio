"""Tests for the gateway's link to the outside world (modules/gateway/system.py).

The real `caddy.exe version` only exists on an installed PC (the release
workflow's smoke test reads it through the diagnostics bundle); here we check
that its absence is reported as "unknown" rather than as an error.
"""

from pathlib import Path

from baja_studio.core.system_services import InstallLayout
from baja_studio.modules.gateway.system import GatewaySystem, installed_caddy_version


def test_from_a_checkout_the_caddy_version_is_unknown() -> None:
    assert installed_caddy_version(None)() is None


def test_an_install_without_caddy_reports_an_unknown_version(tmp_path: Path) -> None:
    assert installed_caddy_version(InstallLayout(tmp_path))() is None


def test_the_default_system_uses_the_real_network_and_knows_no_version() -> None:
    system = GatewaySystem()

    assert system.admin_transport is None
    assert system.caddy_version() is None
