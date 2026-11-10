"""Hosts-screen cards: what a module declares, and how the screen's layout is worked out.

The hosts screen (`/hosts`, WP9) is built from cards. Every module may return
cards from `Module.hosts_cards()`, the way it returns readiness checks; the
hosts module collects them from every running module and publishes the
layout in `state.hosts.layout`. A card names the state it renders from
(`state_path`, e.g. `playout.hosts_view`) and the frontend component that
draws it (`component`, registered by name in `components/hosts/cards.tsx`),
so a new card is a change to the module that owns the data and nothing else.

A *placeholder* card holds an id for a card no module provides yet
(standings, team facts, schedule): it is hidden unless production shows it,
and a real card with the same id replaces it.
"""

from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

# The messages card is the chat; it is always shown, under the grid.
PINNED_BOTTOM = "bottom"


@dataclass(frozen=True)
class HostsCard:
    """One card a module declares.

    `id` is unique across the studio (`now_playing`); a higher `priority`
    comes first in the default layout. `options` are handed to the component
    as they are (JSON-ready), for what the component cannot read from state.
    """

    id: str
    title: str
    priority: int
    state_path: str | None
    component: str
    options: dict[str, Any] = field(default_factory=dict)
    placeholder: bool = False
    pinned: str | None = None


@dataclass(frozen=True)
class LayoutChoice:
    """Production's choice for one card: where it goes (list order) and whether it shows."""

    id: str
    visible: bool


def merge_declared_cards(declared: Iterable[tuple[str, HostsCard]]) -> list[tuple[str, HostsCard]]:
    """One card per id from `(module, card)` pairs: a real card beats a placeholder.

    Between two real cards with the same id the first declared wins (the
    hosts module logs the other).
    """
    chosen: dict[str, tuple[str, HostsCard]] = {}
    for module, card in declared:
        existing = chosen.get(card.id)
        if existing is None or (existing[1].placeholder and not card.placeholder):
            chosen[card.id] = (module, card)
    return list(chosen.values())


def resolve_layout(
    cards: list[tuple[str, HostsCard]], choices: list[LayoutChoice]
) -> list[dict[str, Any]]:
    """The cards in display order with their visibility, as `state.hosts.layout`.

    Cards production placed come first, in its order and with its
    visibility; cards it never placed follow by priority (highest first),
    visible unless they are placeholders. Choices for cards nobody declares
    any more are ignored. Pinned cards (the messages card) are always
    visible and always last.
    """
    by_id = {card.id: (module, card) for module, card in cards}
    ordered: list[tuple[str, HostsCard, bool]] = []
    placed: set[str] = set()
    for choice in choices:
        if choice.id in by_id and choice.id not in placed:
            module, card = by_id[choice.id]
            ordered.append((module, card, choice.visible))
            placed.add(choice.id)
    unplaced = sorted(
        (pair for pair in cards if pair[1].id not in placed),
        key=lambda pair: (-pair[1].priority, pair[1].id),
    )
    ordered.extend((module, card, not card.placeholder) for module, card in unplaced)
    floating = [entry for entry in ordered if entry[1].pinned is None]
    pinned = [(module, card, True) for module, card, _ in ordered if card.pinned is not None]
    return [_card_view(module, card, visible) for module, card, visible in floating + pinned]


def _card_view(module: str, card: HostsCard, visible: bool) -> dict[str, Any]:
    return {
        "id": card.id,
        "module": module,
        "title": card.title,
        "priority": card.priority,
        "state_path": card.state_path,
        "component": card.component,
        "options": dict(card.options),
        "placeholder": card.placeholder,
        "pinned": card.pinned,
        "visible": visible,
    }
