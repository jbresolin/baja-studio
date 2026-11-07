"""Keeps the list of modules and starts, stops and routes commands to them.

`main.py` registers modules in a fixed list; from then on the registry owns
their lifecycle, including turning a module off and on when its `enabled`
setting changes.
"""

import logging
import threading
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from baja_studio import __version__
from baja_studio.core.clock import Clock
from baja_studio.core.commandlog import CommandLog
from baja_studio.core.database import Database
from baja_studio.core.module import CommandError, Module, ModuleContext, ModuleLogger
from baja_studio.core.settings import ModuleSettings, SettingsStore
from baja_studio.core.state import StateStore
from baja_studio.core.supervisor import HealthState, Supervisor

logger = logging.getLogger(__name__)


class UnknownModuleError(KeyError):
    """Raised when a command or lookup names a module that is not registered."""


class ModuleRegistry:
    """The set of registered modules and which of them are running."""

    def __init__(
        self,
        settings: SettingsStore,
        state: StateStore,
        command_log: CommandLog,
        supervisor: Supervisor,
        clock: Clock,
        database: Database,
        data_directory: Path,
    ) -> None:
        """Create an empty registry over the shared services."""
        self._database = database
        self._data_directory = data_directory
        self._settings = settings
        self._state = state
        self._command_log = command_log
        self._supervisor = supervisor
        self._clock = clock
        self._modules: dict[str, Module] = {}
        self._running: set[str] = set()
        self._lifecycle_lock = threading.RLock()

    def register(self, module: Module) -> None:
        """Add a module: load its settings, create its state and health entries."""
        if module.name in self._modules:
            raise ValueError(f"module {module.name!r} registered twice")
        self._modules[module.name] = module
        self._settings.register(module.name, module.SettingsModel)
        self._state.register_module(module.name, module.initial_state())
        self._supervisor.register_module(module.name)
        self._settings.subscribe(
            module.name, lambda values: self._apply_enabled_setting(module.name, values)
        )

    def modules(self) -> list[Module]:
        """All registered modules in registration order."""
        return list(self._modules.values())

    def get(self, name: str) -> Module:
        """The module called `name`, or `UnknownModuleError`."""
        if name not in self._modules:
            raise UnknownModuleError(name)
        return self._modules[name]

    def is_running(self, name: str) -> bool:
        """True if the module is started (not disabled or stopped)."""
        with self._lifecycle_lock:
            return name in self._running

    def health_report(self) -> dict[str, Any]:
        """Each module's health and the service version, as `/api/health` returns them.

        `status` is "ok" only if no running module is degraded or offline.
        """
        modules = []
        for module in self._modules.values():
            health = self._supervisor.module_health(module.name)
            modules.append(
                {
                    "name": module.name,
                    "title": module.title,
                    "state": health.state.value,
                    "message": health.message,
                }
            )
        problems = [m for m in modules if m["state"] in (HealthState.DEGRADED, HealthState.OFFLINE)]
        return {
            "status": "ok" if not problems else "degraded",
            "version": __version__,
            "modules": modules,
        }

    def start_all(self) -> None:
        """Start every module whose `enabled` setting is on; called at startup."""
        for module in self._modules.values():
            if self._is_enabled(module):
                self.start_module(module.name)

    def stop_all(self) -> None:
        """Stop running modules in reverse order; called at shutdown."""
        for module in reversed(list(self._modules.values())):
            self.stop_module(module.name)

    def start_module(self, name: str) -> None:
        """Start one module and then its workers. Does nothing if already running."""
        with self._lifecycle_lock:
            if name in self._running:
                return
            module = self.get(name)
            context = ModuleContext(
                name=name,
                settings=self._settings,
                state=self._state,
                command_log=self._command_log,
                supervisor=self._supervisor,
                clock=self._clock,
                logger=ModuleLogger(
                    logging.getLogger(f"baja_studio.modules.{name}"), {"studio_module": name}
                ),
                database=self._database,
                data_directory=self._data_directory,
            )
            try:
                module.start(context)
            except Exception:
                logger.exception("module failed to start", extra={"studio_module": name})
                return
            self._supervisor.start_module(name, module.workers())
            self._running.add(name)
            logger.info("module started", extra={"studio_module": name})

    def stop_module(self, name: str) -> None:
        """Stop one module's workers and then the module. Does nothing if not running."""
        with self._lifecycle_lock:
            if name not in self._running:
                return
            self._running.discard(name)
            self._supervisor.stop_module(name)
            try:
                self.get(name).stop()
            except Exception:
                logger.exception("module failed to stop cleanly", extra={"studio_module": name})
            logger.info("module stopped", extra={"studio_module": name})

    def dispatch_command(
        self, module_name: str, command: str, args: dict[str, Any], source: str
    ) -> Any:
        """Run a command on a module and record it in the command log (if the module wants).

        Raises `UnknownModuleError`, `CommandError` (also when the module is
        stopped), or whatever unexpected error the module raised.
        """
        module = self.get(module_name)
        try:
            if not self.is_running(module_name):
                raise CommandError(f"module {module_name!r} is stopped")
            result = module.handle_command(command, args, source)
        except Exception as error:
            if module.wants_logged(command, args, source, None, failed=True):
                self._command_log.append(module_name, source, command, args, f"error: {error}")
            raise
        if module.wants_logged(command, args, source, result, failed=False):
            self._command_log.append(module_name, source, command, args, "ok")
        return result

    def _is_enabled(self, module: Module) -> bool:
        if not module.can_be_disabled:
            return True
        values = self._settings.get(module.name)
        return not isinstance(values, ModuleSettings) or values.enabled

    def _apply_enabled_setting(self, name: str, values: BaseModel) -> None:
        """Settings subscriber: start or stop the module when `enabled` flips."""
        module = self.get(name)
        enabled = not isinstance(values, ModuleSettings) or values.enabled
        if not enabled and not module.can_be_disabled:
            logger.warning("this module cannot be disabled", extra={"studio_module": name})
            return
        if enabled and not self.is_running(name):
            self.start_module(name)
        elif not enabled and self.is_running(name):
            self.stop_module(name)
