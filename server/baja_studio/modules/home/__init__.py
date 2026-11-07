"""The home module: the landing page's list of modules and their health.

It also builds the diagnostics bundle (`diagnostics_bundle`), served for
download at `GET /api/home/diagnostics/<file name>`, publishes the service
version the page shows, exports and imports the settings of every module
(`settings_transfer.py`), and owns the PC's own readiness checks (`checks.py`).
"""

import threading
from typing import Any

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from baja_studio import __version__
from baja_studio.core.diagnostics import (
    BUNDLE_NAME_PATTERN,
    DiagnosticsSources,
    build_bundle,
    diagnostics_directory,
)
from baja_studio.core.logging import LOG_DIRECTORY_NAME
from baja_studio.core.module import CommandError, Module, ModuleContext
from baja_studio.core.readiness import ReadinessCheck
from baja_studio.core.registry import ModuleRegistry
from baja_studio.core.supervisor import Health
from baja_studio.core.system_probes import SystemProbes
from baja_studio.core.system_services import NotInstalledServiceControl, ServiceControl
from baja_studio.modules.gateway.caddyfile import caddyfile_path
from baja_studio.modules.home.checks import home_checks
from baja_studio.modules.home.settings_transfer import (
    COMMANDS as SETTINGS_TRANSFER_COMMANDS,
)
from baja_studio.modules.home.settings_transfer import (
    SettingsTransfer,
    SettingsUploads,
    settings_upload_routes,
)

DIAGNOSTICS_URL_PREFIX = "/api/home/diagnostics/"


class HomeModule(Module):
    """Publishes every module's title and health into `state.home.modules`.

    The home page reads that subtree, so health badges update live without
    the page polling `/api/health`.
    """

    name = "home"
    title = "Home"
    can_be_disabled = False

    def __init__(
        self,
        registry: ModuleRegistry,
        service_control: ServiceControl | None = None,
        probes: SystemProbes | None = None,
    ) -> None:
        """`registry` lists the modules; `service_control` reports the services in bundles;
        `probes` look at the PC for the readiness checks (tests pass fakes)."""
        super().__init__()
        self._registry = registry
        self._service_control = service_control or NotInstalledServiceControl()
        self._probes = probes or SystemProbes()
        self._listening = threading.Event()
        self._uploads: SettingsUploads | None = None

    def initial_state(self) -> dict[str, Any]:
        """No modules known until `start`; the version is known at once."""
        return {"modules": {}, "version": __version__}

    def start(self, context: ModuleContext) -> None:
        """Publish the current health of every module, then follow changes."""
        super().start(context)
        if self._uploads is None:
            self._uploads = SettingsUploads(context.clock)
        for module in self._registry.modules():
            self._publish(module.name, context.supervisor.module_health(module.name))
        # Home cannot be disabled, but guard anyway so a restart never doubles the listener.
        if not self._listening.is_set():
            self._listening.set()
            context.supervisor.add_health_listener(self._publish)

    def handle_command(self, command: str, args: dict[str, Any], source: str) -> Any:
        """`diagnostics_bundle` (build a zip, return where to download it), and
        `export_settings`, `import_settings_dryrun`, `import_settings_apply`."""
        if command in SETTINGS_TRANSFER_COMMANDS:
            assert self._uploads is not None
            transfer = SettingsTransfer(self.context.settings, self.context.clock, self._uploads)
            result = transfer.handle(command, args)
            if command == "import_settings_apply":
                self.context.logger.info(
                    "settings imported by %s: %s",
                    source,
                    ", ".join(f"{r['section']} {r['result']}" for r in result["results"]),
                )
            return result
        if command != "diagnostics_bundle":
            raise CommandError(
                f"home has no command {command!r}; try 'diagnostics_bundle' or "
                + ", ".join(repr(name) for name in SETTINGS_TRANSFER_COMMANDS)
            )
        path = build_bundle(self._diagnostics_sources())
        return {"file_name": path.name, "url": DIAGNOSTICS_URL_PREFIX + path.name}

    def readiness_checks(self) -> list[ReadinessCheck]:
        """The PC's own checks (see `checks.py`)."""
        return home_checks(self.context.data_directory, self._probes, self.context.clock)

    def routes(self) -> APIRouter:
        """`GET /diagnostics/<name>`: a bundle built earlier, as a download;
        `POST /settings-import`: a settings file to import (see `settings_transfer.py`)."""
        router = APIRouter()
        router.include_router(settings_upload_routes(self._upload_store))

        @router.get("/diagnostics/{file_name}")
        def download_bundle(file_name: str) -> FileResponse:
            # Only names this module writes: never a path into the rest of the data folder.
            path = diagnostics_directory(self.context.data_directory) / file_name
            if not BUNDLE_NAME_PATTERN.match(file_name) or not path.is_file():
                raise HTTPException(404, "no such diagnostics bundle")
            return FileResponse(path, media_type="application/zip", filename=file_name)

        return router

    def _upload_store(self) -> SettingsUploads:
        if self._uploads is None:
            raise HTTPException(503, "the studio is still starting")
        return self._uploads

    def _diagnostics_sources(self) -> DiagnosticsSources:
        context = self.context
        return DiagnosticsSources(
            data_directory=context.data_directory,
            log_directory=context.data_directory / LOG_DIRECTORY_NAME,
            caddyfile=caddyfile_path(context.data_directory),
            clock=context.clock,
            settings=context.settings,
            state=context.state,
            command_log=context.command_log,
            module_names=lambda: [module.name for module in self._registry.modules()],
            health=self._registry.health_report,
            service_control=self._service_control,
        )

    def _publish(self, module_name: str, health: Health) -> None:
        """Health listener: write one module's entry into the home state."""
        title = next(
            (module.title for module in self._registry.modules() if module.name == module_name),
            module_name,
        )
        self.context.update_state(
            f"modules.{module_name}",
            {"title": title, "state": health.state.value, "message": health.message},
        )
