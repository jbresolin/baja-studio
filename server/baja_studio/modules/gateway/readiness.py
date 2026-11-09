"""The gateway's readiness checks: Caddy up, its ports, DNS, the certificate, the apps.

Each check asks afresh (Caddy's admin API, Windows' port list, a DNS lookup,
a TLS handshake, one GET per app) rather than trusting the 10-second watcher,
so a re-run right after a fix shows the fix. None of them changes anything.
"""

from collections.abc import Callable
from datetime import datetime, timedelta

from baja_studio.core.clock import Clock
from baja_studio.core.readiness import CheckOutcome, ReadinessCheck, failed, passed, warning
from baja_studio.modules.gateway.admin_api import CaddyAdminClient, CaddyUnreachableError
from baja_studio.modules.gateway.config import GatewaySettings, TlsMode
from baja_studio.modules.gateway.ports import GATEWAY_SERVICE_NAME
from baja_studio.modules.gateway.probes import TlsProbeError, check_dns, check_upstream
from baja_studio.modules.gateway.system import GatewaySystem

CERTIFICATE_MIN_DAYS_LEFT = 7
GATEWAY_SETTINGS_LINK = "/settings/gateway"
START_GATEWAY_FIX = (
    f'Start "{GATEWAY_SERVICE_NAME}" in the Windows Services app; if it stops again, '
    "open the home page: it names whatever holds port 80 or 443."
)


def gateway_checks(
    settings: GatewaySettings, system: GatewaySystem, clock: Clock
) -> list[ReadinessCheck]:
    """Checks for the settings as they are now: one per proxied app on top of the fixed ones."""
    checks = [
        ReadinessCheck(
            "caddy",
            "Gateway (Caddy) is running",
            lambda: _caddy_running(settings, system),
            fix=START_GATEWAY_FIX,
        ),
        ReadinessCheck(
            "ports",
            "Ports 80 and 443 are free for the gateway",
            lambda: _ports(settings, system),
            fix=START_GATEWAY_FIX,
        ),
        ReadinessCheck(
            "dns",
            f"{settings.hostname or 'The hostname'} points at this PC",
            lambda: _dns(settings, system),
            fix=(
                "On the hEX: /ip dns static add name=<hostname> address=<this PC's LAN address> "
                "(docs/install.md, Static DNS); and in Cloudflare the A record must point "
                "at this PC."
            ),
            link=GATEWAY_SETTINGS_LINK,
        ),
        ReadinessCheck(
            "tls",
            "HTTPS certificate",
            lambda: _tls(settings, system, clock),
            fix=(
                "Check the Cloudflare token in Settings → Gateway and that the PC is online "
                "(Caddy renews the certificate by itself when it can reach Let's Encrypt)."
            ),
            link=GATEWAY_SETTINGS_LINK,
        ),
    ]
    for app in settings.proxied_apps:
        name = app.label or app.path_prefix
        checks.append(
            ReadinessCheck(
                f"app:{app.path_prefix}",
                f"{name} answers",
                _app_answers(app.upstream, system),
                fix=(
                    f"Start {name} on this PC (it should answer on {app.upstream}), or correct "
                    "its address under Proxied apps in Settings → Gateway."
                ),
                link=GATEWAY_SETTINGS_LINK,
            )
        )
    return checks


def _caddy_running(settings: GatewaySettings, system: GatewaySystem) -> CheckOutcome:
    try:
        CaddyAdminClient(settings.admin_api, system.admin_transport).read_config()
    except CaddyUnreachableError as error:
        return failed(f"Caddy does not answer on {settings.admin_api}: {error}")
    return passed(f"answering on {settings.admin_api}")


def _ports(settings: GatewaySettings, system: GatewaySystem) -> CheckOutcome:
    ports = (80, 443) if settings.tls.mode == TlsMode.DNS01 else (80,)
    holders = system.port_holders(ports)
    if not holders:
        return passed(f"nothing but Caddy listens on {' and '.join(str(p) for p in ports)}")
    first = holders[0]
    detail = "; ".join(f"port {holder.port} in use by {holder.description}" for holder in holders)
    return failed(detail, first.fix)


def _dns(settings: GatewaySettings, system: GatewaySystem) -> CheckOutcome:
    if not settings.hostname:
        return warning(
            "not set: phones can only use the PC's address",
            "Pick the studio's name and enter it as the hostname in Settings → Gateway.",
        )
    result = check_dns(settings.hostname, system.resolve, system.list_addresses)
    if result.matches:
        return passed(result.message)
    if not result.resolved:
        # Without the WAN and without the hEX entry the name simply does not resolve.
        return warning(result.message)
    return failed(result.message)


def _tls(settings: GatewaySettings, system: GatewaySystem, clock: Clock) -> CheckOutcome:
    if settings.tls.mode != TlsMode.DNS01:
        return passed("HTTPS is off: the gateway serves plain HTTP")
    if not settings.tls.cloudflare_api_token:
        return failed(
            "no Cloudflare token, so no certificate",
            "Paste a Cloudflare API token in Settings → Gateway (docs/install.md, HTTPS).",
        )
    try:
        certificate = system.tls_probe(settings.hostname)
    except TlsProbeError as error:
        return failed(f"no valid certificate for {settings.hostname}: {error}")
    expires = datetime.fromisoformat(certificate.expires_at)
    left = expires - clock.now_utc()
    detail = f"valid until {expires.date().isoformat()} ({max(left.days, 0)} days left)"
    if left < timedelta(days=CERTIFICATE_MIN_DAYS_LEFT):
        return failed(detail)
    return passed(detail)


def _app_answers(upstream: str, system: GatewaySystem) -> Callable[[], CheckOutcome]:
    def check() -> CheckOutcome:
        result = check_upstream(upstream, system.upstream_transport)
        if result.reachable:
            return passed(f"HTTP {result.status_code} from {upstream}")
        return failed(f"no answer from {upstream}: {result.error}")

    return check
