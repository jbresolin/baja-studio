"""Tests for the hosts module's wiring (modules/hosts/__init__.py).

The module collects cards from every running module, ticks once a second
(screens time out, cards follow their modules) and keeps the screens'
repeats out of the command log. The studio is hosts plus a made-up
"playout" declaring two cards (`hosts_studio.py`).
"""

from baja_studio.core.hosts_cards import HostsCard
from tests.conftest import eventually
from tests.modules.hosts_studio import HostsStudio


def test_the_layout_holds_other_modules_cards_and_the_hosts_modules_own(
    hosts_studio: HostsStudio,
) -> None:
    # Playout's two cards by priority, the clock, the three placeholders, and messages last.
    assert hosts_studio.card_ids() == [
        "now_playing",
        "next_up",
        "clock",
        "schedule",
        "standings",
        "team_facts",
        "messages",
    ]
    by_id = {card["id"]: card for card in hosts_studio.state("layout")}
    assert by_id["now_playing"]["module"] == "playout"
    assert by_id["standings"]["placeholder"] is True
    assert by_id["standings"]["visible"] is False
    assert by_id["messages"]["pinned"] == "bottom"


def test_cards_of_a_module_that_is_switched_off_leave_the_layout(
    hosts_studio: HostsStudio,
) -> None:
    hosts_studio.services.registry.stop_module("playout")

    # The next tick (within a second) notices.
    hosts_studio.let_time_pass(1.0)

    assert "now_playing" not in hosts_studio.card_ids()


def test_a_card_a_module_starts_declaring_shows_up_within_a_second(
    hosts_studio: HostsStudio,
) -> None:
    hosts_studio.playout.cards.append(HostsCard("replay", "Replay", 95, None, "replay"))

    hosts_studio.let_time_pass(1.0)

    assert hosts_studio.card_ids()[:3] == ["now_playing", "replay", "next_up"]


def test_a_module_that_cannot_list_its_cards_is_left_out_and_the_rest_still_show(
    hosts_studio: HostsStudio,
) -> None:
    class Broken(list[HostsCard]):
        def __iter__(self):  # type: ignore[no-untyped-def]
            raise RuntimeError("bad card")

    hosts_studio.playout.cards = Broken()

    hosts_studio.let_time_pass(1.0)

    assert "now_playing" not in hosts_studio.card_ids()
    assert "clock" in hosts_studio.card_ids()


def test_screens_heartbeats_and_delivery_receipts_stay_out_of_the_command_log(
    hosts_studio: HostsStudio,
) -> None:
    message = hosts_studio.run("send_message", {"to": 1, "text": "30 s"})
    hosts_studio.run("register_screen", {"seats": [1]}, source="web:monitor")
    hosts_studio.run("mark_shown", {"message_id": message["id"], "seat_ids": [1]}, "web:monitor")
    hosts_studio.run("react", {"message_id": message["id"], "seat_id": 1, "reaction": "👍"})

    logged = [entry["command"] for entry in hosts_studio.services.command_log.tail("hosts")]

    assert sorted(logged) == ["react", "send_message"]


def test_a_seat_goes_offline_when_its_screen_stops_talking(hosts_studio: HostsStudio) -> None:
    hosts_studio.run("register_screen", {"seats": [1, 2]}, source="web:monitor")
    assert [seat["online"] for seat in hosts_studio.state("seats")] == [True, True, False, False]
    assert hosts_studio.state("screens") == 1

    # The monitor's network goes; after the 6 s timeout the tick takes the seats offline.
    hosts_studio.let_time_pass(7.0)

    eventually(lambda: not any(seat["online"] for seat in hosts_studio.state("seats")))
    assert hosts_studio.state("screens") == 0
