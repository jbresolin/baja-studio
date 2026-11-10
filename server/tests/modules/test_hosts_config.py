"""Tests for the hosts module's settings (modules/hosts/config.py)."""

import pytest
from pydantic import ValidationError

from baja_studio.modules.hosts.config import HostsSettings, default_seats


def test_the_defaults_are_two_hosts_on_and_two_guests_off() -> None:
    settings = HostsSettings()

    assert [(seat.id, seat.enabled, seat.audio_channel) for seat in settings.seats] == [
        (1, True, "1"),
        (2, True, "2"),
        (3, False, "3"),
        (4, False, "4"),
    ]
    assert settings.layout == []
    assert settings.chime is False


def test_seats_must_be_exactly_one_to_four_and_come_back_in_order() -> None:
    # Given in reverse order, they are stored 1 to 4.
    seats = list(reversed(default_seats()))
    assert [seat.id for seat in HostsSettings(seats=seats).seats] == [1, 2, 3, 4]

    # Three seats are not enough.
    with pytest.raises(ValidationError, match="exactly the seats 1, 2, 3 and 4"):
        HostsSettings(seats=seats[:3])


def test_quick_replies_are_trimmed_and_must_be_short() -> None:
    assert HostsSettings(quick_replies=["  ok "]).quick_replies == ["ok"]
    with pytest.raises(ValidationError, match="quick reply 1 is longer than 40"):
        HostsSettings(quick_replies=["x" * 41])
