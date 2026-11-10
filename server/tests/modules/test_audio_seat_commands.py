"""Tests for the hosts screen's personal-mix commands (modules/audio/seat_commands.py).

A hosts screen is a tablet on the desk: the server never trusts it to say
which bus it changes. Every refusal here is a `CommandError` worded for the
host or production. Seat 1's in-ear bus is bus 1 (the default seats).
"""

from typing import Any

import pytest

from baja_studio.adapters.xr18_addresses import send_address
from baja_studio.adapters.xr18_levels import level_to_db
from baja_studio.core.module import CommandError
from tests.modules.audio_studio import AudioStudio


def seat_command(audio: AudioStudio, command: str, **args: Any) -> Any:
    return audio.command(command, source="web:hosts1", **args)


@pytest.mark.parametrize(
    ("args", "problem"),
    [
        ({"seat_id": 5, "channel": "9", "delta_db": 1}, "must be a seat"),
        ({"seat_id": True, "channel": "9", "delta_db": 1}, "must be a seat"),
        # Seat 1 may not reach into seat 2's in-ears (bus 2), whatever it says.
        ({"seat_id": 1, "bus": "bus2", "channel": "9", "delta_db": 1}, "only its own in-ears"),
        # Channel 12 is hidden and linked to nothing: not in the seat's mix.
        ({"seat_id": 1, "channel": "12", "delta_db": 1}, "not in IEM HOST 1's personal mix"),
        ({"seat_id": 1, "channel": "9"}, "either delta_db or value_db"),
        ({"seat_id": 1, "channel": "9", "delta_db": 1, "value_db": 2}, "either delta_db"),
        ({"seat_id": 1, "channel": "9", "delta_db": "up"}, "must be a number of dB"),
        ({"seat_id": 1, "channel": "9", "delta_db": 20}, "one step must be between"),
        ({"seat_id": 1, "channel": "9", "value_db": 12}, "an offset must be between -30 and 10"),
        ({"seat_id": 1, "channel": "aux", "value_db": 11}, "a send must be between -90 and 10"),
    ],
)
def test_set_seat_offset_refuses_what_is_not_this_seats_to_change(
    audio: AudioStudio, args: dict[str, Any], problem: str
) -> None:
    audio.connect_mixer()

    with pytest.raises(CommandError, match=problem):
        seat_command(audio, "set_seat_offset", **args)
    assert audio.state("offsets") == {}


def test_naming_the_seats_own_bus_is_accepted(audio: AudioStudio) -> None:
    audio.connect_mixer()

    result = seat_command(
        audio, "set_seat_offset", seat_id=1, bus="bus1", channel="9", delta_db=1.0
    )

    assert (result["bus"], result["offset_db"]) == ("bus1", 1.0)


def test_with_hosts_can_adjust_mix_off_the_overlay_is_view_only(audio: AudioStudio) -> None:
    audio.connect_mixer()
    audio.configure(hosts_can_adjust_mix=False)

    with pytest.raises(CommandError, match="view-only"):
        seat_command(audio, "set_seat_offset", seat_id=1, channel="9", delta_db=1.0)
    with pytest.raises(CommandError, match="view-only"):
        seat_command(audio, "reset_seat_offsets", seat_id=1)


def test_nothing_can_be_changed_while_the_mixer_is_offline(audio: AudioStudio) -> None:
    # Never connected: the mixer is not there.
    with pytest.raises(CommandError, match="XR18 is offline"):
        seat_command(audio, "set_seat_offset", seat_id=1, channel="9", delta_db=1.0)


def test_an_independent_source_has_its_send_changed_directly(audio: AudioStudio) -> None:
    # PC AUDIO is in the seat's mix (shown on the audio page) but not linked
    # to bus 1: + and - change its send itself, and there is no offset.
    audio.connect_mixer()
    audio.change_on_mixer(send_address("aux", "bus1"), 0.75)
    audio.soon(lambda: audio.state("channels.aux")["sends"]["bus1"] == 0.75)

    result = seat_command(audio, "set_seat_offset", seat_id=1, channel="aux", delta_db=-6.0)
    audio.settle()

    assert (result["linked"], result["offset_db"]) == (False, None)
    assert level_to_db(float(result["level"])) == pytest.approx(-6.0)
    assert audio.state("offsets") == {}
    assert audio.state("links").get("aux", {}).get("bus1") is None

    # A value sets the send to that many dB.
    seat_command(audio, "set_seat_offset", seat_id=1, channel="aux", value_db=-10.0)
    audio.settle()
    value = audio.mixer_value(send_address("aux", "bus1"))
    assert isinstance(value, float)
    assert level_to_db(value) == pytest.approx(-10.0)
