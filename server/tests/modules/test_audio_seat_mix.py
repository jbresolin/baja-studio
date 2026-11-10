"""Tests for the seats' personal in-ear mixes (modules/audio/seat_mix.py).

Through the running audio module and the simulated XR18. Bus 1 is seat 1's
in-ear bus (the default seats). The SHOTGUN (channel 9) and PROD 1
(channel 5) are linked to bus 1 by the 2026 seed, so their sends to bus 1
follow their main faders; a seat's "offset" is how many dB above or below
main it hears them. Faders start at unity (0 dB, level 0.75).
"""

import pytest

from baja_studio.adapters.xr18_addresses import fader_address, send_address
from baja_studio.adapters.xr18_levels import level_to_db, offset_level
from baja_studio.core.module import CommandError
from tests.modules.audio_studio import AudioStudio


def send_db(audio: AudioStudio, channel: str, bus: str) -> float:
    """The send's level on the mixer, in dB."""
    value = audio.mixer_value(send_address(channel, bus))
    assert isinstance(value, float)
    return level_to_db(value)


def overlay(audio: AudioStudio, channel: str, **amount: float) -> dict[str, object]:
    """Seat 1's hosts screen taps + or - (or sets a value) on its overlay."""
    result: dict[str, object] = audio.command(
        "set_seat_offset", source="web:hosts1", seat_id=1, channel=channel, **amount
    )
    audio.settle()
    return result


def test_a_linked_send_on_a_seats_bus_is_main_plus_the_offset(audio: AudioStudio) -> None:
    audio.connect_mixer()

    result = overlay(audio, "9", delta_db=2.0)

    assert send_db(audio, "9", "bus1") == pytest.approx(2.0)
    assert result["linked"] is True and result["offset_db"] == 2.0
    # The link stays: the send keeps following its fader.
    assert audio.state("links")["9"]["bus1"] == "linked"
    assert audio.state("offsets") == {"9:bus1": 2.0}


def test_moving_the_main_fader_carries_the_offset_even_from_mixing_station(
    audio: AudioStudio,
) -> None:
    audio.connect_mixer()
    overlay(audio, "9", delta_db=2.0)

    # From the audio page...
    audio.command("set_fader", channel="9", level=0.5)
    audio.settle()
    assert audio.mixer_value(send_address("9", "bus1")) == pytest.approx(offset_level(0.5, 2.0))

    # ...and from the desk or Mixing Station.
    audio.change_on_mixer(fader_address("9"), 0.25)
    audio.soon(
        lambda: audio.mixer_value(send_address("9", "bus1")) == pytest.approx(offset_level(0.25, 2))
    )


def test_offsets_stop_at_plus_10_and_minus_30_db(audio: AudioStudio) -> None:
    audio.connect_mixer()

    overlay(audio, "9", value_db=10.0)
    overlay(audio, "9", delta_db=5.0)
    assert audio.state("offsets") == {"9:bus1": 10.0}
    # Unity + 10 dB is the top of the fader law.
    assert audio.mixer_value(send_address("9", "bus1")) == 1.0

    overlay(audio, "9", value_db=-30.0)
    overlay(audio, "9", delta_db=-5.0)
    assert audio.state("offsets") == {"9:bus1": -30.0}


def test_reset_brings_every_source_back_to_main(audio: AudioStudio) -> None:
    audio.connect_mixer()
    overlay(audio, "9", delta_db=3.0)
    overlay(audio, "5", delta_db=-4.0)

    result = audio.command("reset_seat_offsets", source="web:hosts1", seat_id=1)
    audio.settle()

    assert "9" in result["reset"] and "5" in result["reset"]
    assert audio.state("offsets") == {}
    assert audio.mixer_value(send_address("9", "bus1")) == 0.75


def test_a_pad_and_the_overlay_move_the_same_offset(audio: AudioStudio) -> None:
    audio.connect_mixer()

    # Host 1's pad "vol up" (a D200 nudge-aux), then the overlay's +.
    audio.command("nudge", source="device:d200", channel="9", bus="bus1", delta_db=2.0)
    overlay(audio, "9", delta_db=1.0)

    assert audio.state("offsets") == {"9:bus1": 3.0}
    assert send_db(audio, "9", "bus1") == pytest.approx(3.0)


def test_a_pad_leaves_a_note_for_the_screens_toast_and_the_overlay_does_not(
    audio: AudioStudio,
) -> None:
    audio.connect_mixer()
    assert audio.state("hosts_view.pad_change") is None

    audio.command("nudge", source="device:d200", channel="9", bus="bus1", delta_db=2.0)
    first = audio.state("hosts_view.pad_change")
    audio.command("nudge", source="device:d200", channel="9", bus="bus1", delta_db=1.0)
    second = audio.state("hosts_view.pad_change")

    assert first == {"seq": 1, "seat_id": 1, "channel": "9", "label": "SHOTGUN", "offset_db": 2.0}
    assert (second["seq"], second["offset_db"]) == (2, 3.0)
    # The host using the overlay already sees the change: no toast for it.
    overlay(audio, "9", delta_db=1.0)
    assert audio.state("hosts_view.pad_change") == second


def test_setting_a_seat_send_to_a_level_keeps_the_link_as_an_offset(audio: AudioStudio) -> None:
    audio.connect_mixer()

    audio.command("set_aux", channel="9", bus="bus1", db=-3.0)
    audio.settle()

    assert audio.state("links")["9"]["bus1"] == "linked"
    assert audio.state("offsets") == {"9:bus1": -3.0}


def test_a_seat_send_cannot_be_set_to_a_level_while_its_fader_is_off(audio: AudioStudio) -> None:
    audio.connect_mixer()
    audio.command("set_fader", channel="9", level=0.0)

    # There is no "main" to be above or below; + and - still work.
    with pytest.raises(CommandError, match="follows its main fader, which is off"):
        audio.command("set_aux", channel="9", bus="bus1", db=-3.0)


def test_a_bus_that_is_no_seats_in_ears_still_pauses_the_link(audio: AudioStudio) -> None:
    # Bus 6 is the camera operator's: WP4's rule, a direct change pauses.
    audio.connect_mixer()

    audio.command("nudge", source="device:d200", channel="1", bus="bus6", delta_db=2.0)

    assert audio.state("links")["1"]["bus6"] == "paused"
    assert audio.state("offsets") == {}


def test_a_send_closed_by_a_macro_keeps_its_offset_for_when_it_reopens(audio: AudioStudio) -> None:
    audio.connect_mixer()
    overlay(audio, "5", value_db=2.0)

    # PROD 1's route into host 1's ears is closed (button or macro).
    assert audio.command("toggle_send", channel="5", bus="bus1")["open"] is False
    audio.settle()
    # Turned up meanwhile on the overlay: kept, but the closed send stays closed.
    overlay(audio, "5", delta_db=1.0)
    assert audio.mixer_value(send_address("5", "bus1")) == 0.0

    assert audio.command("toggle_send", channel="5", bus="bus1")["open"] is True
    audio.settle()

    assert send_db(audio, "5", "bus1") == pytest.approx(3.0)


def test_relinking_puts_the_send_at_main_plus_its_offset(audio: AudioStudio) -> None:
    audio.connect_mixer()
    overlay(audio, "9", value_db=2.0)
    audio.command("set_link", channel="9", bus="bus1", state="paused")
    audio.command("set_fader", channel="9", level=0.5)

    audio.command("set_link", channel="9", bus="bus1", state="linked")
    audio.settle()

    assert audio.mixer_value(send_address("9", "bus1")) == pytest.approx(offset_level(0.5, 2.0))


def test_holding_plus_stays_within_the_mixers_packet_budget(audio: AudioStudio) -> None:
    # A host holds + on the overlay: the screen repeats it. Every write goes
    # through the one 100 packets/s budget (latest value per address wins),
    # so the XR18 never sees the 2026 flood that hung it.
    audio.connect_mixer()
    sent_before = audio.network.sent_count
    started = audio.clock.monotonic()

    for _ in range(60):
        audio.command("set_seat_offset", source="web:hosts1", seat_id=1, channel="9", delta_db=0.1)
    audio.settle()
    audio.pass_time(1.0)

    elapsed = audio.clock.monotonic() - started
    assert audio.mixer.hung is False
    # A burst of 20, then 100 a second, at most.
    assert audio.network.sent_count - sent_before <= 20 + 100 * elapsed
    assert send_db(audio, "9", "bus1") == pytest.approx(6.0, abs=0.05)
