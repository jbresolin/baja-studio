"""Tests for the hosts module's commands (modules/hosts/commands.py).

Commands go through the registry as a page would send them, with a source
(`web:prod1` is production, `web:monitor` the hosts screen). Refusals must
be `CommandError`s an operator can act on.
"""

import pytest

from baja_studio.core.module import CommandError
from tests.modules.hosts_studio import HostsStudio


def test_send_message_addresses_a_seat_with_productions_name_and_the_default_expiry(
    hosts_studio: HostsStudio,
) -> None:
    message = hosts_studio.run("send_message", {"to": 2, "text": "  30 s  "})

    assert message["from"] == "production:prod1"
    assert message["to"] == 2
    # Blank space around the text is trimmed; kind and expiry come from the defaults.
    assert (message["text"], message["kind"], message["expires_s"]) == ("30 s", "info", 60)


def test_send_message_to_all_with_a_kind_and_no_expiry(hosts_studio: HostsStudio) -> None:
    message = hosts_studio.run(
        "send_message", {"to": "all", "text": "go to break", "kind": "urgent", "expires_s": 0}
    )

    assert (message["to"], message["kind"], message["expires_s"]) == ("all", "urgent", None)


@pytest.mark.parametrize(
    ("args", "problem"),
    [
        ({"to": 5, "text": "hi"}, "'to' must be a seat"),
        ({"to": True, "text": "hi"}, "'to' must be a seat"),
        ({"to": 3, "text": "hi"}, "seat is switched off"),
        ({"to": 1, "text": "   "}, "'text' must be some text"),
        ({"to": 1, "text": "x" * 201}, "longer than 200 characters"),
        ({"to": 1, "text": "hi", "kind": "shout"}, "info, question or urgent"),
        ({"to": 1, "text": "hi", "expires_s": -1}, "whole number of seconds"),
    ],
)
def test_send_message_refuses_what_cannot_be_delivered(
    hosts_studio: HostsStudio, args: dict[str, object], problem: str
) -> None:
    # Seats 3 and 4 are off by default (the guests), so seat 3 cannot be addressed.
    with pytest.raises(CommandError, match=problem):
        hosts_studio.run("send_message", args)


def test_react_takes_the_four_reactions_and_the_configured_quick_replies(
    hosts_studio: HostsStudio,
) -> None:
    message_id = hosts_studio.run("send_message", {"to": 1, "text": "ready?"})["id"]

    hosts_studio.run("react", {"message_id": message_id, "seat_id": 1, "reaction": "✋"})
    hosts_studio.run("react", {"message_id": message_id, "seat_id": 1, "reaction": "need 1 min"})

    with pytest.raises(CommandError, match="'reaction' must be one of"):
        hosts_studio.run("react", {"message_id": message_id, "seat_id": 1, "reaction": "lol"})


def test_reply_and_dismiss_reach_the_message(hosts_studio: HostsStudio) -> None:
    message_id = hosts_studio.run("send_message", {"to": 1, "text": "how long?"})["id"]

    hosts_studio.run("reply", {"message_id": message_id, "seat_id": 1, "text": "2 minutes"})
    message = hosts_studio.run("dismiss", {"message_id": message_id})

    assert message["reply_text"] == "2 minutes"
    assert message["dismissed_at"] is not None


def test_a_message_that_does_not_exist_is_refused_in_operator_words(
    hosts_studio: HostsStudio,
) -> None:
    with pytest.raises(CommandError, match="no longer there"):
        hosts_studio.run("dismiss", {"message_id": 99})


def test_push_card_accepts_a_card_in_the_layout_and_null_clears_it(
    hosts_studio: HostsStudio,
) -> None:
    pushed = hosts_studio.run("push_card", {"card_id": "next_up"})["pushed_card"]
    assert (pushed["id"], pushed["by"]) == ("next_up", "web:prod1")

    assert hosts_studio.run("push_card", {"card_id": None}) == {"pushed_card": None}

    with pytest.raises(CommandError, match="no such card"):
        hosts_studio.run("push_card", {"card_id": "weather"})
    with pytest.raises(CommandError, match="no such card"):
        hosts_studio.run("push_card", {"card_id": ["next_up"]})


def test_set_card_layout_saves_the_order_and_publishes_it(hosts_studio: HostsStudio) -> None:
    hosts_studio.run(
        "set_card_layout",
        {"cards": [{"id": "clock", "visible": True}, {"id": "now_playing", "visible": False}]},
    )

    assert hosts_studio.card_ids()[:3] == ["clock", "now_playing", "next_up"]
    # It is a setting, so it survives a restart and travels in the settings export.
    assert [entry.id for entry in hosts_studio.settings().layout] == ["clock", "now_playing"]


def test_set_card_layout_refuses_a_malformed_list_with_the_settings_words(
    hosts_studio: HostsStudio,
) -> None:
    with pytest.raises(CommandError, match=r"layout\.0\.visible"):
        hosts_studio.run("set_card_layout", {"cards": [{"id": "clock", "visible": "maybe"}]})


def test_set_seat_name_and_set_seat_change_one_seat(hosts_studio: HostsStudio) -> None:
    hosts_studio.run("set_seat_name", {"seat_id": 1, "screen_name": "Ana"})
    hosts_studio.run("set_seat", {"seat_id": 3, "enabled": True, "color": "#00ff00"})

    seats = {seat["id"]: seat for seat in hosts_studio.state("seats")}
    assert seats[1]["screen_name"] == "Ana"
    assert (seats[3]["enabled"], seats[3]["color"]) == (True, "#00ff00")
    # Seat 3 can now be addressed.
    hosts_studio.run("send_message", {"to": 3, "text": "welcome"})


def test_set_seat_refuses_a_colour_that_is_not_hex(hosts_studio: HostsStudio) -> None:
    with pytest.raises(CommandError, match=r"seats\.1\.color"):
        hosts_studio.run("set_seat", {"seat_id": 2, "color": "orange"})


def test_set_texts_changes_canned_messages_and_quick_replies(hosts_studio: HostsStudio) -> None:
    hosts_studio.run(
        "set_texts", {"canned_messages": ["10 s", "stretch"], "quick_replies": ["sim", "não"]}
    )

    config = hosts_studio.state("config")
    assert config["canned_messages"] == ["10 s", "stretch"]
    assert config["quick_replies"] == ["sim", "não"]
    # A host may now answer with one of the new quick replies.
    message_id = hosts_studio.run("send_message", {"to": 1, "text": "ok?"})["id"]
    hosts_studio.run("react", {"message_id": message_id, "seat_id": 1, "reaction": "sim"})


def test_set_texts_refuses_an_empty_canned_message(hosts_studio: HostsStudio) -> None:
    with pytest.raises(CommandError, match="canned message 2 is empty"):
        hosts_studio.run("set_texts", {"canned_messages": ["30 s", " "]})


def test_clear_messages_starts_a_new_session_and_export_returns_the_whole_session(
    hosts_studio: HostsStudio,
) -> None:
    hosts_studio.run("send_message", {"to": 1, "text": "one"})
    hosts_studio.run("send_message", {"to": "all", "text": "two"})

    exported = hosts_studio.run("export_messages")
    assert [m["text"] for m in exported["messages"]] == ["one", "two"]

    assert hosts_studio.run("clear_messages") == {"cleared": 2}
    assert hosts_studio.state("messages") == []


def test_an_unknown_command_is_refused(hosts_studio: HostsStudio) -> None:
    with pytest.raises(CommandError, match="hosts has no command 'shout'"):
        hosts_studio.run("shout")
