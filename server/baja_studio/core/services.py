"""The bundle of shared services the app creates at startup.

Routers reach it through `request.app.state.services`, so they never import
globals and tests can build an app with their own data directory and clock.
"""

from dataclasses import dataclass
from pathlib import Path

from pydantic import BaseModel, Field

from baja_studio.core.clock import Clock
from baja_studio.core.commandlog import DEFAULT_RETENTION_DAYS, CommandLog
from baja_studio.core.database import Database
from baja_studio.core.logging import LogHub
from baja_studio.core.registry import ModuleRegistry
from baja_studio.core.settings import SettingsStore
from baja_studio.core.state import StateStore
from baja_studio.core.supervisor import Supervisor

SYSTEM_SETTINGS_SECTION = "system"


class SystemSettings(BaseModel):
    """Settings for the service itself rather than any one module."""

    command_log_retention_days: int = Field(
        default=DEFAULT_RETENTION_DAYS,
        ge=1,
        le=365,
        description="How many days of command history to keep.",
    )


@dataclass
class StudioServices:
    """Every shared service, created once in `main.create_app`."""

    data_directory: Path
    clock: Clock
    database: Database
    settings: SettingsStore
    state: StateStore
    supervisor: Supervisor
    command_log: CommandLog
    log_hub: LogHub
    registry: ModuleRegistry
