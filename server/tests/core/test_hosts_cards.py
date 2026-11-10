"""Tests for the hosts-screen card registry and layout (core/hosts_cards.py).

Modules declare cards; `merge_declared_cards` keeps one per id and
`resolve_layout` puts them in the order the hosts screen shows them. These
tests use plain cards, no modules.
"""

from baja_studio.core.hosts_cards import (
    PINNED_BOTTOM,
    HostsCard,
    LayoutChoice,
    merge_declared_cards,
    resolve_layout,
)


def card(card_id: str, priority: int, **extra: object) -> HostsCard:
    return HostsCard(card_id, card_id.title(), priority, f"x.{card_id}", card_id, **extra)  # type: ignore[arg-type]


def ids(layout: list[dict[str, object]]) -> list[object]:
    return [entry["id"] for entry in layout]


def test_without_choices_cards_are_ordered_by_priority_highest_first() -> None:
    cards = [("audio", card("audio", 50)), ("playout", card("now_playing", 100))]

    layout = resolve_layout(cards, [])

    assert ids(layout) == ["now_playing", "audio"]
    assert all(entry["visible"] for entry in layout)
    # Each entry says which module declared it and how to draw it.
    assert layout[0]["module"] == "playout"
    assert layout[0]["state_path"] == "x.now_playing"
    assert layout[0]["component"] == "now_playing"


def test_productions_order_and_visibility_come_first_and_new_cards_follow() -> None:
    cards = [
        ("playout", card("now_playing", 100)),
        ("audio", card("audio", 50)),
        ("hosts", card("clock", 60)),
    ]
    # Production put audio first and hid the clock; it never placed now_playing.
    choices = [LayoutChoice("audio", True), LayoutChoice("clock", False)]

    layout = resolve_layout(cards, choices)

    assert ids(layout) == ["audio", "clock", "now_playing"]
    assert [entry["visible"] for entry in layout] == [True, False, True]


def test_choices_for_cards_nobody_declares_are_ignored() -> None:
    # The audio module is off, so its card is not declared, but its choice is kept in settings.
    layout = resolve_layout([("playout", card("now_playing", 100))], [LayoutChoice("audio", True)])

    assert ids(layout) == ["now_playing"]


def test_a_pinned_card_is_always_last_and_always_visible() -> None:
    cards = [
        ("hosts", card("messages", 0, pinned=PINNED_BOTTOM)),
        ("playout", card("now_playing", 100)),
    ]
    # Even when production tries to put it first and hide it.
    layout = resolve_layout(cards, [LayoutChoice("messages", False)])

    assert ids(layout) == ["now_playing", "messages"]
    assert layout[-1]["visible"] is True


def test_placeholders_are_hidden_until_production_shows_them() -> None:
    cards = [("hosts", card("standings", 10, placeholder=True))]

    assert resolve_layout(cards, [])[0]["visible"] is False
    assert resolve_layout(cards, [LayoutChoice("standings", True)])[0]["visible"] is True


def test_a_real_card_replaces_a_placeholder_with_the_same_id() -> None:
    # The hosts module holds the "standings" id; later a Baja app module declares it for real.
    declared = [
        ("hosts", card("standings", 10, placeholder=True)),
        ("baja_app", card("standings", 40)),
    ]

    merged = merge_declared_cards(declared)

    assert len(merged) == 1
    module, standings = merged[0]
    assert module == "baja_app"
    assert standings.placeholder is False


def test_between_two_real_cards_with_one_id_the_first_declared_wins() -> None:
    merged = merge_declared_cards([("a", card("clock", 60)), ("b", card("clock", 70))])

    assert [module for module, _ in merged] == ["a"]
