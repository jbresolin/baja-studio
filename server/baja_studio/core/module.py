"""The `Module` base class every studio tool is built from, and its context.

A module is a self-contained feature (cameras, playout, audio...). It declares
its settings model, initial state, commands, workers and optional extra HTTP
routes; the registry wires it to the shared services through a `ModuleContext`.
"""

import logging
from collections.abc import MutableMapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, ClassVar

from fastapi import APIRouter
from pydantic import BaseModel

from baja_studio.core.childproc import ChildProcessHost, EventCallback
from baja_studio.core.clock import Clock
from baja_studio.core.commandlog import CommandLog
from baja_studio.core.database import Database
from baja_studio.core.hosts_cards import HostsCard
from baja_studio.core.readiness import ReadinessCheck
from baja_studio.core.settings import ModuleSettings, SettingsStore
from baja_studio.core.state import StateStore
from baja_studio.core.supervisor import Supervisor, WorkerSpec


class CommandError(Exception):
    """A command was refused (unknown name, bad arguments, wrong moment).

    The message is shown to the operator, so it should say what to do.
    """


class ModuleLogger(logging.LoggerAdapter[logging.Logger]):
    """A logger that tags every record with the module name (merging other extras)."""

    def process(
        self, msg: Any, kwargs: MutableMapping[str, Any]
    ) -> tuple[Any, MutableMapping[str, Any]]:
        """Called by `logging` for each call; adds `module` to the record's extras."""
        kwargs["extra"] = {**(self.extra or {}), **kwargs.get("extra", {})}
        return msg, kwargs


@dataclass
class ModuleContext:
    """Everything a module may use from the shared base, handed to `Module.start`."""

    name: str
    settings: SettingsStore
    state: StateStore
    command_log: CommandLog
    supervisor: Supervisor
    clock: Clock
    logger: ModuleLogger
    # The shared SQLite file, for modules that keep their own tables (playout).
    database: Database
    # The service's data folder, for caches that must not live in synced folders.
    data_directory: Path

    def current_settings(self) -> BaseModel:
        """This module's settings as they are right now."""
        return self.settings.get(self.name)

    def update_state(self, path: str, value: Any) -> None:
        """Set a value in this module's state subtree; browsers see it within ~30 ms."""
        self.state.update(self.name, path, value)

    def start_worker(self, spec: WorkerSpec) -> None:
        """Start one more supervised worker while the module runs (a device just added)."""
        self.supervisor.start_worker(self.name, spec)

    def stop_worker(self, worker_name: str) -> None:
        """Stop one of this module's workers and wait for it (a device removed or re-addressed)."""
        self.supervisor.stop_worker(self.name, worker_name)

    def new_child_process(
        self, python_module: str, on_event: EventCallback | None = None
    ) -> ChildProcessHost:
        """Create (but not start) a child-process host owned by this module."""
        return ChildProcessHost(python_module, self.clock, self.name, on_event)


class Module:
    """Base class for studio modules; subclasses override what they need."""

    name: ClassVar[str]
    title: ClassVar[str]
    SettingsModel: ClassVar[type[ModuleSettings]] = ModuleSettings
    # The home module must stay on, or there would be no page to turn it back on from.
    can_be_disabled: ClassVar[bool] = True

    def __init__(self) -> None:
        """Modules are created in `main.py`; the context arrives later in `start`."""
        self._context: ModuleContext | None = None

    @property
    def context(self) -> ModuleContext:
        """The context given to `start`; only valid while the module runs."""
        if self._context is None:
            raise RuntimeError(f"module {self.name!r} has not been started")
        return self._context

    def initial_state(self) -> dict[str, Any]:
        """The module's state subtree before anything has happened."""
        return {}

    def start(self, context: ModuleContext) -> None:
        """Called when the service starts or the module is enabled; before workers start."""
        self._context = context

    def stop(self) -> None:
        """Called when the service stops or the module is disabled; after workers stopped."""

    def handle_command(self, command: str, args: dict[str, Any], source: str) -> Any:
        """Carry out `command`; return a JSON-serialisable result or raise `CommandError`.

        Called on a worker thread from the thread pool, never on the event loop,
        so it may block briefly (e.g. waiting for a child process to answer).
        """
        raise CommandError(f"module {self.name!r} has no command {command!r}")

    def wants_logged(
        self, command: str, args: dict[str, Any], source: str, result: Any, failed: bool
    ) -> bool:
        """Whether the command log keeps this command; called after every one.

        Every command is kept, unless a module that receives a steady stream of
        repeats (a browser holding a control) says otherwise. `result` is None
        when `failed`.
        """
        return True

    def workers(self) -> list[WorkerSpec]:
        """Long-running loops the supervisor should keep alive for this module."""
        return []

    def readiness_checks(self) -> list[ReadinessCheck]:
        """The pre-event checks this module owns; asked for at every readiness run.

        Only called while the module runs. Checks look and never change
        anything (see `core/readiness.py`); a module with none returns nothing.
        """
        return []

    def hosts_cards(self) -> list[HostsCard]:
        """The cards this module adds to the hosts screen (`core/hosts_cards.py`).

        Only called while the module runs; asked for again every second or
        so, so cards may follow the module's settings. Most modules have none.
        """
        return []

    def routes(self) -> APIRouter | None:
        """Optional extra HTTP routes, mounted under `/api/<name>`."""
        return None

    def root_routes(self) -> APIRouter | None:
        """Optional HTTP routes mounted at the service root, outside `/api`.

        Only for contracts fixed by a device that cannot be changed, such as
        the Ulanzi D200 profile's `/action/...` URLs (audio). Paths must not
        start with `/api` or `/ws`; they win over the frontend's pages.
        """
        return None
