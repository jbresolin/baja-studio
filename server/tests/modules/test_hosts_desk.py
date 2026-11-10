"""Tests for the hosts desk and the state it publishes (modules/hosts/desk.py).

What the pages read: `hosts.messages`, `hosts.seats`, `hosts.layout`,
`hosts.pushed_card`, `hosts.config`. Driven through commands, as the pages
drive it.
"""

from baja_studio.modules.hosts.desk import production_sender
from tests.modules.hosts_studio import HostsStudio


def test_production_sender_names_the_page_that_sent() -> None:
    assert production_sender("web:ab12") == "production:ab12"
    # From another program through POST /api/commands.
    assert production_sender("api:stream-deck") == "production:api:stream-deck"


def test_on_start_the_seats_and_texts_from_the_settings_are_published(
    hosts_studio: HostsStudio,
) -> None:
    seats = hosts_studio.state("seats")
    assert [(seat["id"], seat["screen_name"], seat["enabled"]) for seat in seats] == [
        (1, "Host 1", True),
        (2, "Host 2", True),
        (3, "Guest 1", False),
        (4, "Guest 2", False),
    ]
    assert not any(seat["online"] for seat in seats)
    config = hosts_studio.state("config")
    assert config["canned_messages"][:2] == ["30 s", "wrap up"]
    assert config["quick_replies"] == ["ok", "need 1 min", "repeat?"]
    assert config["reactions"] == ["👍", "👎", "✋", "❓"]
    assert config["chime"] is False


def test_the_message_lifecycle_is_visible_in_state_at_every_step(
    hosts_studio: HostsStudio,
) -> None:
    # Production sends "30 s" to seat 2.
    message_id = hosts_studio.run("send_message", {"to": 2, "text": "30 s"})["id"]
    [message] = hosts_studio.state("messages")
    assert message["shown"] == [] and message["reactions"] == []

    # The hosts screen showing seat 2 puts it up: production sees "delivered".
    hosts_studio.run("mark_shown", {"message_id": message_id, "seat_ids": [2]}, "web:monitor")
    [message] = hosts_studio.state("messages")
    assert [entry["seat_id"] for entry in message["shown"]] == [2]

    # The host reacts: production sees 👍 from seat 2.
    hosts_studio.run("react", {"message_id": message_id, "seat_id": 2, "reaction": "👍"})
    [message] = hosts_studio.state("messages")
    assert [(r["seat_id"], r["reaction"]) for r in message["reactions"]] == [(2, "👍")]


def test_state_carries_only_the_last_fifty_messages(hosts_studio: HostsStudio) -> None:
    for number in range(55):
        hosts_studio.run("send_message", {"to": "all", "text": f"m{number}"})

    messages = hosts_studio.state("messages")

    assert len(messages) == 50
    assert (messages[0]["text"], messages[-1]["text"]) == ("m5", "m54")


def test_a_pushed_card_is_published_and_cleared_when_its_module_goes(
    hosts_studio: HostsStudio,
) -> None:
    hosts_studio.run("push_card", {"card_id": "next_up"})
    assert hosts_studio.state("pushed_card")["id"] == "next_up"

    # Playout is switched off: its cards leave and the push has nothing to show.
    hosts_studio.services.registry.stop_module("playout")
    hosts_studio.let_time_pass(1.0)

    assert hosts_studio.state("pushed_card") is None


def test_a_seat_counts_every_screen_showing_it(hosts_studio: HostsStudio) -> None:
    hosts_studio.run("register_screen", {"seats": [1, 2]}, source="web:monitor")
    hosts_studio.run("register_screen", {"seats": [2]}, source="web:tablet")

    seats = {seat["id"]: seat for seat in hosts_studio.state("seats")}
    assert (seats[1]["screens"], seats[2]["screens"], seats[3]["screens"]) == (1, 2, 0)
    assert hosts_studio.state("screens") == 2


def test_a_settings_edit_on_the_settings_page_reaches_the_published_seats(
    hosts_studio: HostsStudio,
) -> None:
    seats = [seat.model_dump() for seat in hosts_studio.settings().seats]
    seats[0]["screen_name"] = "Ana"
    hosts_studio.services.settings.update("hosts", {"seats": seats, "chime": True})

    assert hosts_studio.state("seats")[0]["screen_name"] == "Ana"
    assert hosts_studio.state("config")["chime"] is True
