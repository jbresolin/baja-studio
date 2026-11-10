"""A small studio for the hosts module's tests: hosts plus made-up modules that declare cards.

Loaded into every test through `pytest_plugins` in conftest.py.

`CardModule` stands in for playout or audio: it declares whatever cards the
test hands it, so the tests are about collecting and laying out cards, not
about any real module's data.
"""

from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

import pytest
from fastapi.testclient import TestClient

from baja_studio.core.hosts_cards import HostsCard
from baja_studio.core.module import Module
from baja_studio.core.registry import ModuleRegistry
from baja_studio.core.services import StudioServices
from baja_studio.modules.hosts import HostsModule
from tests.conftest import AppFactory, FakeClock, module_worker_threads, step_fake_time

HOSTS_WORKERS = module_worker_threads("hosts")


class CardModule(Module):
    """A module whose cards are whatever `cards` holds when asked."""

    def __init__(self, name: str, cards: list[HostsCard]) -> None:
        super().__init__()
        self.name = name  # type: ignore[misc]
        self.title = name.title()  # type: ignore[misc]
        self.cards = cards

    def hosts_cards(self) -> list[HostsCard]:
        return list(self.cards)


def now_playing_card() -> HostsCard:
    """What playout declares first."""
    return HostsCard("now_playing", "Now playing", 100, "playout.hosts_view", "now_playing")


def next_up_card() -> HostsCard:
    """What playout declares second."""
    return HostsCard("next_up", "Next up", 90, "playout.hosts_view", "next_up")


@dataclass
class HostsStudio:
    services: StudioServices
    clock: FakeClock
    playout: CardModule

    @property
    def module(self) -> HostsModule:
        module = self.services.registry.get("hosts")
        assert isinstance(module, HostsModule)
        return module

    def state(self, path: str = "") -> Any:
        return self.services.state.get("hosts", path)

    def run(
        self, command: str, args: dict[str, Any] | None = None, source: str = "web:prod1"
    ) -> Any:
        return self.services.registry.dispatch_command("hosts", command, args or {}, source)

    def settings(self) -> Any:
        return self.services.settings.get("hosts")

    def card_ids(self) -> list[str]:
        return [card["id"] for card in self.state("layout")]

    def let_time_pass(self, seconds: float) -> None:
        """Move fake time with the `screens` worker ticking as it would."""
        step_fake_time(self.clock, seconds, HOSTS_WORKERS, step=0.5)


@pytest.fixture
def hosts_studio(make_app: AppFactory, fake_clock: FakeClock) -> Iterator[HostsStudio]:
    """Hosts plus a made-up "playout" declaring Now playing and Next up."""
    playout = CardModule("playout", [now_playing_card(), next_up_card()])

    def modules(registry: ModuleRegistry) -> list[Module]:
        # Hosts starts after the modules declaring cards, as in main.py, so its first
        # layout already holds playout's; started first, tests race the first tick.
        return [playout, HostsModule(registry)]

    app = make_app(modules)
    with TestClient(app):
        yield HostsStudio(app.state.services, fake_clock, playout)
