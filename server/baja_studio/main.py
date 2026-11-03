"""Builds the FastAPI app: shared services, the module list, routes, static frontend.

To add a module in a later work package, create it under `modules/` and add
it to `build_module_list` below. Nothing else needs to change.
"""

import asyncio
import logging
import os
import sys
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, HTMLResponse, Response
from pydantic import BaseModel

from baja_studio.core.api import router as core_router
from baja_studio.core.clock import Clock, SystemClock
from baja_studio.core.commandlog import CommandLog
from baja_studio.core.database import Database
from baja_studio.core.logging import LogHub, configure_logging
from baja_studio.core.module import Module
from baja_studio.core.registry import ModuleRegistry
from baja_studio.core.services import SYSTEM_SETTINGS_SECTION, StudioServices, SystemSettings
from baja_studio.core.settings import SettingsStore
from baja_studio.core.state import DEFAULT_COALESCE_SECONDS, StateStore
from baja_studio.core.supervisor import Supervisor
from baja_studio.modules.agents import AgentsModule
from baja_studio.modules.cameras import CamerasModule
from baja_studio.modules.echo import EchoModule
from baja_studio.modules.heartbeat import HeartbeatModule
from baja_studio.modules.home import HomeModule

logger = logging.getLogger(__name__)

DEFAULT_PORT = 8888
DATA_DIRECTORY_ENVIRONMENT_VARIABLE = "BAJA_STUDIO_DATA"
FRONTEND_DIRECTORY_ENVIRONMENT_VARIABLE = "BAJA_STUDIO_FRONTEND"
DATABASE_FILE_NAME = "baja-studio.sqlite3"
LOG_DIRECTORY_NAME = "logs"

ModuleListBuilder = Callable[[ModuleRegistry], list[Module]]


def build_module_list(registry: ModuleRegistry) -> list[Module]:
    """The modules this service runs, in the order they appear on the home page."""
    agents = AgentsModule()
    return [
        HomeModule(registry),
        CamerasModule(agents),
        agents,
        HeartbeatModule(),
        EchoModule(),
    ]


def default_data_directory() -> Path:
    """Where the SQLite file and logs live, unless `BAJA_STUDIO_DATA` says otherwise.

    Kept outside any synced folder (Dropbox caused conflicted copies in 2026).
    """
    override = os.environ.get(DATA_DIRECTORY_ENVIRONMENT_VARIABLE)
    if override:
        return Path(override)
    if sys.platform == "win32":
        return Path(os.environ.get("PROGRAMDATA", r"C:\ProgramData")) / "BajaStudio"
    return Path.home() / ".local" / "share" / "baja-studio"


def default_frontend_directory() -> Path:
    """The built frontend: `frontend/dist` next to `server/` unless overridden."""
    override = os.environ.get(FRONTEND_DIRECTORY_ENVIRONMENT_VARIABLE)
    if override:
        return Path(override)
    return Path(__file__).resolve().parents[2] / "frontend" / "dist"


def create_app(
    data_directory: Path | None = None,
    clock: Clock | None = None,
    frontend_directory: Path | None = None,
    coalesce_seconds: float = DEFAULT_COALESCE_SECONDS,
    log_to_file: bool = True,
    module_list_builder: ModuleListBuilder = build_module_list,
) -> FastAPI:
    """Create the app and its services. Modules start when the app starts serving.

    Tests pass a temporary `data_directory`, a fake `clock`, and sometimes
    their own `module_list_builder`.
    """
    data_directory = data_directory or default_data_directory()
    clock = clock or SystemClock()
    frontend_directory = frontend_directory or default_frontend_directory()

    log_hub = LogHub()
    configure_logging(data_directory / LOG_DIRECTORY_NAME if log_to_file else None, log_hub)

    database = Database(data_directory / DATABASE_FILE_NAME)
    settings = SettingsStore(database, clock)
    system_settings = settings.register(SYSTEM_SETTINGS_SECTION, SystemSettings)
    assert isinstance(system_settings, SystemSettings)
    command_log = CommandLog(database, clock, system_settings.command_log_retention_days)
    settings.subscribe(
        SYSTEM_SETTINGS_SECTION, lambda values: _apply_system_settings(command_log, values)
    )
    state = StateStore(coalesce_seconds)
    supervisor = Supervisor(clock)
    registry = ModuleRegistry(settings, state, command_log, supervisor, clock)
    services = StudioServices(
        data_directory, clock, database, settings, state, supervisor, command_log, log_hub, registry
    )

    modules = module_list_builder(registry)
    for module in modules:
        registry.register(module)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        state.attach_loop(asyncio.get_running_loop())
        logger.info("starting with data directory %s", data_directory)
        command_log.prune()
        registry.start_all()
        try:
            yield
        finally:
            logger.info("shutting down")
            registry.stop_all()
            supervisor.stop_all()
            database.close()

    app = FastAPI(title="Baja Studio", lifespan=lifespan)
    app.state.services = services
    app.include_router(core_router)
    for module in modules:
        module_router = module.routes()
        if module_router is not None:
            app.include_router(module_router, prefix=f"/api/{module.name}")
    _serve_frontend(app, frontend_directory)
    return app


def _apply_system_settings(command_log: CommandLog, values: BaseModel) -> None:
    """Settings subscriber for the "system" section."""
    assert isinstance(values, SystemSettings)
    command_log.set_retention_days(values.command_log_retention_days)


FRONTEND_NOT_BUILT_PAGE = """<!doctype html>
<html><body style="background:#111;color:#ddd;font-family:sans-serif;padding:2rem">
<h1>Baja Studio is running</h1>
<p>The web interface has not been built yet. In the <code>frontend</code> folder run
<code>npm install</code> and then <code>npm run build</code>, and reload this page.</p>
<p>The API is up: try <a style="color:#8cf" href="/api/health">/api/health</a>.</p>
</body></html>"""


def _serve_frontend(app: FastAPI, frontend_directory: Path) -> None:
    """Serve built files; any other non-API path gets index.html (client-side routes)."""

    @app.get("/{requested_path:path}", include_in_schema=False)
    def serve_frontend_file(requested_path: str) -> Response:
        if requested_path.startswith(("api/", "ws")):
            raise HTTPException(404)
        index_file = frontend_directory / "index.html"
        if not index_file.is_file():
            return HTMLResponse(FRONTEND_NOT_BUILT_PAGE)
        candidate = (frontend_directory / requested_path).resolve()
        # Refuse paths like ../../secret that would escape the frontend folder.
        if candidate.is_file() and candidate.is_relative_to(frontend_directory.resolve()):
            return FileResponse(candidate)
        return FileResponse(index_file)
