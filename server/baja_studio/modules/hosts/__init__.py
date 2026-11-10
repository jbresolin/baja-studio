"""The hosts module: the hosts screen's cards and the production → hosts chat (LIVESTACK-4, WP9).

The hosts screen (`/hosts`) is a read-mostly display for the monitor in
front of the hosts, built from cards that modules declare
(`Module.hosts_cards()`, `core/hosts_cards.py`); this module collects them
from every running module and publishes the layout production chose. It
declares the clock, the messages card and placeholders for later cards
(standings, team facts, schedule) itself.

The chat is a cue channel from production to four seats (no accounts, no
host-to-host messages): messages, reactions, quick replies, typed replies,
canned messages. Messages are kept in the database for the session.

State and commands: `desk.py` and `commands.py`. One worker, `screens`,
ticks once a second (silent screens go offline, cards follow their
modules). Readiness checks: `readiness.py`.
"""

from typing import Any

from pydantic import BaseModel

from baja_studio.core.hosts_cards import PINNED_BOTTOM, HostsCard, merge_declared_cards
from baja_studio.core.module import CommandError, Module, ModuleContext
from baja_studio.core.readiness import ReadinessCheck
from baja_studio.core.registry import ModuleRegistry
from baja_studio.core.supervisor import WorkerContext, WorkerSpec
from baja_studio.modules.hosts.commands import QUIET_COMMANDS, HostsCommands
from baja_studio.modules.hosts.config import HostsSettings
from baja_studio.modules.hosts.desk import HostsDesk, initial_hosts_state
from baja_studio.modules.hosts.messages import MessageStore, create_hosts_tables
from baja_studio.modules.hosts.readiness import hosts_checks

SCREENS_WORKER = "screens"
TICK_SECONDS = 1.0
# Cards no module provides yet; a module declaring the same id replaces them.
PLACEHOLDER_CARDS = (
    ("standings", "Standings"),
    ("team_facts", "Team facts"),
    ("schedule", "Schedule"),
)


class HostsModule(Module):
    """Collects the cards, runs the chat and tracks the hosts screens."""

    name = "hosts"
    title = "Hosts"
    SettingsModel = HostsSettings

    def __init__(self, registry: ModuleRegistry) -> None:
        """`registry` is where the cards come from (every running module)."""
        super().__init__()
        self._registry = registry
        self._desk: HostsDesk | None = None
        self._commands: HostsCommands | None = None
        self._subscribed = False
        # Modules whose cards could not be listed, so the tick logs each once, not every second.
        self._broken_card_modules: set[str] = set()

    @property
    def desk(self) -> HostsDesk:
        """The running desk (tests use it to look inside)."""
        if self._desk is None:
            raise CommandError("hosts is not running")
        return self._desk

    def initial_state(self) -> dict[str, Any]:
        """No seats, screens or messages yet."""
        return initial_hosts_state()

    def settings(self) -> HostsSettings:
        """Current settings, read fresh on every use so edits apply at once."""
        values = self.context.current_settings()
        assert isinstance(values, HostsSettings)
        return values

    def start(self, context: ModuleContext) -> None:
        """Create the table and the desk, publish everything; the worker starts next."""
        super().start(context)
        create_hosts_tables(context.database)
        store = MessageStore(context.database, context.clock)
        self._desk = HostsDesk(
            context.clock, self.settings, store, context.update_state, self._collect_cards
        )
        self._commands = HostsCommands(self._desk, context.settings, self.name)
        self._desk.publish_everything()
        if not self._subscribed:
            context.settings.subscribe(self.name, self._settings_changed)
            self._subscribed = True

    def stop(self) -> None:
        """The worker has stopped; drop the desk."""
        self._desk = None
        self._commands = None

    def workers(self) -> list[WorkerSpec]:
        """The once-a-second tick."""
        return [WorkerSpec(SCREENS_WORKER, self._run_ticks)]

    def handle_command(self, command: str, args: dict[str, Any], source: str) -> Any:
        """See `commands.py` for the list."""
        if self._commands is None:
            raise CommandError("hosts is not running")
        return self._commands.run(command, args, source)

    def wants_logged(
        self, command: str, args: dict[str, Any], source: str, result: Any, failed: bool
    ) -> bool:
        """Screens' repeats and delivery receipts stay out of the log, unless refused."""
        return failed or command not in QUIET_COMMANDS

    def hosts_cards(self) -> list[HostsCard]:
        """The clock, the messages card (pinned under the grid) and the placeholders."""
        return [
            HostsCard("clock", "Clock", 60, "playout.export", "clock"),
            HostsCard(
                "messages", "Messages", 0, "hosts.messages", "messages", pinned=PINNED_BOTTOM
            ),
            *(
                HostsCard(card_id, title, 10, None, "placeholder", placeholder=True)
                for card_id, title in PLACEHOLDER_CARDS
            ),
        ]

    def readiness_checks(self) -> list[ReadinessCheck]:
        """Seats set up, and a screen open for each (see `readiness.py`)."""
        return hosts_checks(self.settings(), self.desk.online_seats)

    def _collect_cards(self) -> list[tuple[str, HostsCard]]:
        """Every running module's cards; a module that cannot list them is left out and logged."""
        declared: list[tuple[str, HostsCard]] = []
        for module in self._registry.modules():
            if module.name != self.name and not self._registry.is_running(module.name):
                continue
            try:
                declared.extend((module.name, card) for card in module.hosts_cards())
            except Exception:
                if module.name not in self._broken_card_modules:
                    self._broken_card_modules.add(module.name)
                    self.context.logger.exception("%s could not list its hosts cards", module.name)
            else:
                self._broken_card_modules.discard(module.name)
        return merge_declared_cards(declared)

    def _run_ticks(self, worker: WorkerContext) -> None:
        while not worker.should_stop:
            self.desk.tick()
            worker.sleep(TICK_SECONDS)

    def _settings_changed(self, values: BaseModel) -> None:
        """Settings subscriber: seats, layout and texts apply at once."""
        if self._desk is not None:
            self._desk.settings_changed()
