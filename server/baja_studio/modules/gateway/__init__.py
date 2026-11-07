"""The gateway module: owns Caddy, the HTTPS front door on ports 80/443.

Caddy runs as its own Windows service (`BajaStudioGateway`); this module
renders its Caddyfile from the settings, applies it through the admin API,
and watches it: is Caddy up, is the certificate valid, does each proxied app
answer. It also publishes the apps for the home page's cards.

State (`state.gateway`):
- `caddy`: {running, version, admin_api, message}
- `tls`: {mode, hostname, issued, issuer, expires_at, last_error, checked_at}
- `studio`, `apps[]`: each upstream with {reachable, status_code, error}
- `public_base`: https://<hostname> with TLS on, else null (the page uses its own host)
- `config`: the last apply's outcome; `dns`: the last `test_dns` result

Commands: `apply`, `test_dns`, `test_tls`. Settings edits apply by themselves.
"""

import contextlib
import threading
from typing import Any

from pydantic import BaseModel

from baja_studio.core.module import CommandError, Module, ModuleContext
from baja_studio.core.supervisor import DeviceOfflineError, HealthState, WorkerContext, WorkerSpec
from baja_studio.core.system_services import NotInstalledServiceControl, ServiceControl
from baja_studio.modules.gateway.admin_api import CaddyAdminClient, CaddyUnreachableError
from baja_studio.modules.gateway.config import GatewaySettings, TlsMode
from baja_studio.modules.gateway.controller import GatewayApplyError, GatewayController
from baja_studio.modules.gateway.probes import TlsProbeError, check_dns, check_upstream
from baja_studio.modules.gateway.system import GatewaySystem, installed_caddy_version

__all__ = ["GatewayModule", "GatewaySystem", "installed_caddy_version"]

CADDY_WORKER = "caddy"
POLL_SECONDS = 10.0
# A valid certificate is re-checked once a minute; a missing one at every poll.
TLS_RECHECK_SECONDS = 60.0


class GatewayModule(Module):
    """Renders, applies and watches Caddy's config."""

    name = "gateway"
    title = "Gateway"
    SettingsModel = GatewaySettings

    def __init__(
        self,
        service_control: ServiceControl | None = None,
        system: GatewaySystem | None = None,
    ) -> None:
        """Defaults: no service to control, real network; `main.py` passes the installed ones."""
        super().__init__()
        self._service_control = service_control or NotInstalledServiceControl()
        self._system = system or GatewaySystem()
        self._controller: GatewayController | None = None
        self._apply_requested = threading.Event()
        self._tls_checked_at: float | None = None
        self._tls_valid = False
        self._config_error = ""
        self._subscribed = False

    def initial_state(self) -> dict[str, Any]:
        """Nothing known about Caddy until the worker first asks."""
        return {
            "caddy": {"running": False, "version": None, "admin_api": "", "message": ""},
            "tls": {
                "mode": TlsMode.OFF.value,
                "hostname": "",
                "issued": None,
                "issuer": "",
                "expires_at": None,
                "last_error": "",
                "checked_at": None,
            },
            "studio": {"upstream": "", "reachable": None, "status_code": None, "error": ""},
            "apps": [],
            "public_base": None,
            "config": None,
            "dns": None,
        }

    def start(self, context: ModuleContext) -> None:
        """Create the controller and apply the current settings as soon as the worker runs."""
        super().start(context)
        self._controller = GatewayController(
            context.data_directory,
            self._service_control,
            lambda address: CaddyAdminClient(address, self._system.admin_transport),
            context.clock,
            context.logger,
        )
        self._tls_checked_at = None
        self._apply_requested.set()
        self._publish_settings(self._settings())
        if not self._subscribed:
            self._subscribed = True
            context.settings.subscribe(self.name, self._settings_changed)

    def workers(self) -> list[WorkerSpec]:
        """One worker: apply when asked, then watch Caddy, TLS and the apps."""
        return [WorkerSpec(CADDY_WORKER, self._run_watcher, starts_offline=True)]

    def handle_command(self, command: str, args: dict[str, Any], source: str) -> Any:
        """`apply` (re-render and reload), `test_dns`, `test_tls`."""
        if command == "apply":
            outcome = self._apply(self._settings())
            self.context.supervisor.wake(self.name, CADDY_WORKER)
            return outcome
        if command == "test_dns":
            settings = self._settings()
            if not settings.hostname:
                raise CommandError("set a hostname in the gateway settings first")
            result = check_dns(
                settings.hostname, self._system.resolve, self._system.list_addresses
            ).as_state()
            self.context.update_state("dns", result)
            return result
        if command == "test_tls":
            return self._check_tls(self._settings())
        raise CommandError(
            f"gateway has no command {command!r}; try 'apply', 'test_dns' or 'test_tls'"
        )

    def _run_watcher(self, worker: WorkerContext) -> None:
        """Worker body: Caddy up? apply if asked; check apps and TLS; sleep 10 s."""
        self.context.update_state("caddy.version", self._system.caddy_version())
        while not worker.should_stop:
            settings = self._settings()
            if self._apply_requested.is_set():
                self._apply_requested.clear()
                # A refusal is already in state.config, and in the health below.
                with contextlib.suppress(CommandError):
                    self._apply(settings)
            try:
                CaddyAdminClient(settings.admin_api, self._system.admin_transport).read_config()
            except CaddyUnreachableError as error:
                self.context.update_state("caddy.running", False)
                self.context.update_state("caddy.message", str(error))
                raise DeviceOfflineError(
                    f"Caddy is not answering on {settings.admin_api}; "
                    "is the BajaStudioGateway service running?"
                ) from error
            self.context.update_state("caddy.running", True)
            self.context.update_state("caddy.message", "")
            problems = [self._config_error] if self._config_error else []
            problems.extend(self._check_upstreams(settings))
            if self._tls_check_due(settings):
                self._check_tls(settings)
            problems.extend(self._tls_problems(settings))
            # Online and its problems together, after the checks (a TLS probe can
            # take seconds), so the card never shows "ok" in between.
            worker.mark_online()
            worker.report_health(
                HealthState.DEGRADED if problems else HealthState.OK, "; ".join(problems)
            )
            worker.sleep(POLL_SECONDS)

    def _apply(self, settings: GatewaySettings) -> dict[str, Any]:
        """Apply through the controller; record the outcome (or the refusal) in state."""
        assert self._controller is not None
        try:
            outcome = self._controller.apply(settings).as_state()
        except GatewayApplyError as error:
            self._config_error = str(error)
            self.context.update_state(
                "config", {"applied_at": None, "loaded": False, "error": str(error)}
            )
            raise CommandError(str(error)) from error
        self._config_error = ""
        self.context.update_state("config", {**outcome, "error": ""})
        # A new config may mean a new certificate: check it at the next poll.
        self._tls_checked_at = None
        return outcome

    def _check_upstreams(self, settings: GatewaySettings) -> list[str]:
        """Ask the studio and every app directly; returns a problem per silent one."""
        problems = []
        studio = check_upstream(settings.studio_upstream, self._system.upstream_transport)
        self.context.update_state(
            "studio", {"upstream": settings.studio_upstream, **_check_state(studio)}
        )
        if not studio.reachable:
            problems.append(f"the studio is not answering on {settings.studio_upstream}")
        apps = []
        for app in settings.proxied_apps:
            check = check_upstream(app.upstream, self._system.upstream_transport)
            apps.append({**app.model_dump(mode="json"), **_check_state(check)})
            if not check.reachable:
                problems.append(f"{app.label or app.path_prefix} is not answering")
        self.context.update_state("apps", apps)
        return problems

    def _tls_check_due(self, settings: GatewaySettings) -> bool:
        if settings.tls.mode != TlsMode.DNS01:
            return False
        if self._tls_checked_at is None or not self._tls_valid:
            return True
        return self.context.clock.monotonic() - self._tls_checked_at >= TLS_RECHECK_SECONDS

    def _check_tls(self, settings: GatewaySettings) -> dict[str, Any]:
        """Probe the certificate Caddy serves for the hostname; publish and return it."""
        if settings.tls.mode != TlsMode.DNS01:
            raise CommandError("TLS is off in the gateway settings; there is nothing to test")
        now = self.context.clock.now_utc().isoformat()
        self._tls_checked_at = self.context.clock.monotonic()
        try:
            certificate = self._system.tls_probe(settings.hostname)
        except TlsProbeError as error:
            self._tls_valid = False
            result: dict[str, Any] = {
                "issued": False,
                "issuer": "",
                "expires_at": None,
                "last_error": str(error),
            }
        else:
            self._tls_valid = True
            result = {**certificate.as_state(), "issued": True, "last_error": ""}
            result.pop("subject")
        result["checked_at"] = now
        for key, value in result.items():
            self.context.update_state(f"tls.{key}", value)
        return result

    def _tls_problems(self, settings: GatewaySettings) -> list[str]:
        if settings.tls.mode != TlsMode.DNS01 or self._tls_valid:
            return []
        if not settings.tls.cloudflare_api_token:
            return ["no certificate: paste a Cloudflare API token in the gateway settings"]
        error = self.context.state.get(self.name, "tls.last_error") or "not issued yet"
        return [f"no valid certificate for {settings.hostname}: {error}"]

    def _settings(self) -> GatewaySettings:
        settings = self.context.current_settings()
        assert isinstance(settings, GatewaySettings)
        return settings

    def _publish_settings(self, settings: GatewaySettings) -> None:
        """The parts of the state that follow from the settings alone (never the token)."""
        self.context.update_state("caddy.admin_api", settings.admin_api)
        self.context.update_state("tls.mode", settings.tls.mode.value)
        self.context.update_state("tls.hostname", settings.hostname)
        dns01 = settings.tls.mode == TlsMode.DNS01
        self.context.update_state("public_base", f"https://{settings.hostname}" if dns01 else None)
        if not dns01:
            self._tls_valid = False
            for key in ("issued", "issuer", "expires_at", "checked_at"):
                self.context.update_state(f"tls.{key}", None if key != "issuer" else "")
            self.context.update_state("tls.last_error", "")

    def _settings_changed(self, values: BaseModel) -> None:
        """Settings subscriber: publish what changed and have the worker apply it."""
        assert isinstance(values, GatewaySettings)
        if self._context is None:
            return
        self._publish_settings(values)
        self._apply_requested.set()
        self.context.supervisor.wake(self.name, CADDY_WORKER)


def _check_state(check: Any) -> dict[str, Any]:
    return {"reachable": check.reachable, "status_code": check.status_code, "error": check.error}
