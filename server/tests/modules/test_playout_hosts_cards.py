"""Tests for playout's cards on the hosts screen (modules/playout/hosts_cards.py)."""

from baja_studio.modules.playout.hosts_cards import playout_hosts_cards


def test_now_playing_comes_before_next_up_and_both_read_the_hosts_view() -> None:
    cards = playout_hosts_cards()

    assert [card.id for card in cards] == ["now_playing", "next_up"]
    assert cards[0].priority > cards[1].priority
    assert {card.state_path for card in cards} == {"playout.hosts_view"}
