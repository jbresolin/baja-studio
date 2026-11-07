"""Applies the gateway settings to Caddy: render, write, then reload or restart.

The Cloudflare token reaches Caddy only through its process environment.
NSSM holds that environment (`AppEnvironmentExtra` of `BajaStudioGateway`,
which belongs to this module alone) and passes it on when Caddy starts. So:

- the token changed (or TLS was switched on or off): write the Caddyfile,
  set the environment, restart the gateway service; Caddy starts with both;
- anything else: `POST /load` first, so Caddy checks the config, and write
  the file only once Caddy accepted it, so a config Caddy refuses never
  becomes the file it starts from;
- Caddy not answering: write the file; Caddy uses it when it starts.
"""

import logging
import threading
from collections.abc import Callable
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from baja_studio.core.clock import Clock
from baja_studio.core.system_services import (
    GATEWAY_SERVICE,
    NOT_INSTALLED,
    ServiceControl,
    ServiceControlError,
)
from baja_studio.modules.gateway.admin_api import (
    CaddyAdminClient,
    CaddyRejectedConfigError,
    CaddyUnreachableError,
)
from baja_studio.modules.gateway.caddyfile import (
    TOKEN_ENVIRONMENT_VARIABLE,
    caddyfile_path,
    certificate_storage,
    render_caddyfile,
    write_caddyfile,
)
from baja_studio.modules.gateway.config import GatewaySettings, TlsMode


class GatewayApplyError(Exception):
    """Caddy refused the config, or the gateway service could not be changed."""


@dataclass(frozen=True)
class ApplyOutcome:
    """What `apply` did, for the command's answer and the state tree."""

    applied_at: str
    caddyfile: str
    loaded: bool
    restarted: bool
    message: str

    def as_state(self) -> dict[str, Any]:
        """JSON-ready."""
        return asdict(self)


def desired_gateway_environment(settings: GatewaySettings) -> dict[str, str]:
    """The variables Caddy must be started with: the token, when TLS uses it."""
    token = settings.tls.cloudflare_api_token.strip()
    if settings.tls.mode == TlsMode.DNS01 and token:
        return {TOKEN_ENVIRONMENT_VARIABLE: token}
    return {}


class GatewayController:
    """Owns the Caddyfile and the gateway service's environment; one apply at a time."""

    def __init__(
        self,
        data_directory: Path,
        service_control: ServiceControl,
        admin_client: Callable[[str], CaddyAdminClient],
        clock: Clock,
        logger: logging.LoggerAdapter[logging.Logger] | logging.Logger,
    ) -> None:
        """`admin_client(address)` makes a client for the configured admin address."""
        self._data_directory = data_directory
        self._service_control = service_control
        self._admin_client = admin_client
        self._clock = clock
        self._logger = logger
        self._lock = threading.Lock()

    def apply(self, settings: GatewaySettings) -> ApplyOutcome:
        """Make Caddy run `settings`; raises `GatewayApplyError` if that cannot be done."""
        with self._lock:
            text = render_caddyfile(settings, certificate_storage(self._data_directory))
            path = caddyfile_path(self._data_directory)
            if self._environment_needs_change(settings):
                return self._restart_with(settings, text, path)
            try:
                self._admin_client(settings.admin_api).load_caddyfile(text)
            except CaddyRejectedConfigError as refused:
                raise GatewayApplyError(f"Caddy refused the config: {refused}") from refused
            except CaddyUnreachableError:
                write_caddyfile(path, text)
                return self._outcome(
                    path, False, False, "Caddy is not answering; it will use this config on start"
                )
            write_caddyfile(path, text)
            return self._outcome(path, True, False, "config loaded")

    def _environment_needs_change(self, settings: GatewaySettings) -> bool:
        """True when the service's variables differ from what the settings need.

        Run from a checkout there is no service: a token then cannot reach
        Caddy, which the outcome of the load (or the TLS check) shows.
        """
        if self._service_control.status(GATEWAY_SERVICE) == NOT_INSTALLED:
            return False
        try:
            current = self._service_control.environment(GATEWAY_SERVICE)
        except ServiceControlError as error:
            raise GatewayApplyError(str(error)) from error
        return current != desired_gateway_environment(settings)

    def _restart_with(self, settings: GatewaySettings, text: str, path: Path) -> ApplyOutcome:
        write_caddyfile(path, text)
        try:
            self._service_control.set_environment(
                GATEWAY_SERVICE, desired_gateway_environment(settings)
            )
            self._service_control.restart(GATEWAY_SERVICE)
        except ServiceControlError as error:
            raise GatewayApplyError(str(error)) from error
        # Never the values: the token is one of them.
        self._logger.info("gateway service restarted with new environment")
        return self._outcome(path, True, True, "gateway restarted with the new token")

    def _outcome(self, path: Path, loaded: bool, restarted: bool, message: str) -> ApplyOutcome:
        return ApplyOutcome(
            applied_at=self._clock.now_utc().isoformat(),
            caddyfile=str(path),
            loaded=loaded,
            restarted=restarted,
            message=message,
        )
