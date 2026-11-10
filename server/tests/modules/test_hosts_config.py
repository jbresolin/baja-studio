"""Tests for the hosts module's settings (modules/hosts/config.py)."""

import pytest
from pydantic import ValidationError

from baja_studio.modules.hosts.config import HostsSettings, default_seats


def test_the_defaults_are_two_hosts_on_and_two_guests_off() -> None:
    settings = HostsSettings()

    assert [(seat.id, seat.enabled) for seat in settings.seats] == [
        (1, True),
        (2, True),
        (3, False),
        (4, False),
    ]
    assert settings.layout == []
    # Hosts wear in-ears, so their screen is silent by default; production is not.
    assert settings.chime is False
    assert settings.production_chime is True
    assert settings.chat_size == "small"
    assert settings.host_phrases == ["preciso de 1 min", "problema no áudio", "repete?", "pronto"]


def test_a_seat_saved_by_wp9_with_its_microphone_still_loads_without_it() -> None:
    # WP9 kept the seat's microphone channel in the hosts settings; it moved
    # to the audio settings (WP9b). A database or an exported file from WP9
    # still has it: it must load (dropping the key), not fall back to defaults.
    saved = HostsSettings().model_dump()
    saved["seats"][0] = {**saved["seats"][0], "screen_name": "Ana", "audio_channel": "1"}

    settings = HostsSettings.model_validate(saved)

    assert settings.seats[0].screen_name == "Ana"
    assert "audio_channel" not in settings.seats[0].model_dump()
    # The layout changed, so files exported before WP9b are flagged on import.
    assert HostsSettings.settings_version == 2


def test_the_chat_size_is_small_medium_or_large() -> None:
    assert HostsSettings(chat_size="large").chat_size == "large"
    with pytest.raises(ValidationError):
        HostsSettings.model_validate({"chat_size": "huge"})


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
