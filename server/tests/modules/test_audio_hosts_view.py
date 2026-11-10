"""Tests for `audio.hosts_view`, the hosts screen's audio card data (modules/audio/hosts_view.py).

Through the running audio module and the simulated XR18. A fresh fake has
every input unmuted, at unity, assigned to LR, with every send off, on and
taken pre-fader. Changes "made in Mixing Station" (`change_on_mixer`) reach
the studio the way the real desk's pushes do, so these tests also show the
card never relies on which button was pressed last.
"""

from typing import Any

import pytest

from baja_studio.adapters.xr18_addresses import (
    fader_address,
    lr_assign_address,
    mute_address,
    send_address,
)
from baja_studio.adapters.xr18_levels import offset_level
from tests.modules.audio_studio import AudioStudio


def map_seat_1_to_channel_1(audio: AudioStudio) -> None:
    """Production maps seat 1's microphone to HOST 1 (channel 1) on the audio setup page."""
    audio.configure(
        seats=[
            {"seat_id": 1, "channel": "1", "iem_bus": "bus1"},
            {"seat_id": 2, "iem_bus": "bus2"},
            {"seat_id": 3, "iem_bus": "bus3"},
            {"seat_id": 4, "iem_bus": "bus4"},
        ]
    )


def seat(audio: AudioStudio, seat_id: int = 1) -> dict[str, Any]:
    view: dict[str, Any] = audio.state("hosts_view")
    seats: dict[str, Any] = view["seats"]
    return dict(seats[str(seat_id)])


def ready(audio: AudioStudio) -> None:
    """Mixer connected, seat 1 mapped, and its microphone known to be live."""
    audio.connect_mixer()
    map_seat_1_to_channel_1(audio)
    audio.soon(lambda: seat(audio)["state"] == "live")


def test_an_unmapped_seat_says_not_mapped(audio: AudioStudio) -> None:
    # By default no seat has a microphone channel: production has to map them.
    audio.connect_mixer()

    assert seat(audio)["state"] == "not_mapped"
    assert seat(audio)["iem_label"] == "IEM HOST 1"


def test_an_unmuted_microphone_on_the_main_mix_is_live(audio: AudioStudio) -> None:
    ready(audio)

    assert seat(audio)["channel_label"] == "HOST 1"
    assert audio.state("hosts_view.mixer_online") is True


def test_off_the_main_mix_but_open_to_production_is_to_production(audio: AudioStudio) -> None:
    ready(audio)

    # At the desk: HOST 1 taken off LR, its (pre-fader) send to bus 5 opened.
    audio.change_on_mixer(lr_assign_address("1"), 0)
    audio.change_on_mixer(send_address("1", "bus5"), 0.75)

    audio.soon(lambda: seat(audio)["state"] == "to_production")


def test_a_muted_microphone_is_muted(audio: AudioStudio) -> None:
    ready(audio)

    audio.change_on_mixer(mute_address("1"), 0)

    audio.soon(lambda: seat(audio)["state"] == "muted")


# --- "Host 1 → Prod" (ADR 0013 decision 8) -------------------------------------------

OTHER_SEATS_BUSES = ("bus2", "bus3", "bus4")


def send(audio: AudioStudio, channel: str, bus: str) -> float:
    value = audio.mixer_value(send_address(channel, bus))
    assert isinstance(value, float)
    return value


def host_1_on_air(audio: AudioStudio) -> None:
    """Seat 1 live, HOST 1 at unity and copied into every bus it is linked to.

    Host 2 hears HOST 1 2 dB louder than the main mix (an offset), so the
    tests can tell "back to main + offset" from "back to main".
    """
    ready(audio)
    audio.command("set_fader", channel="1", level=0.75)
    audio.command("set_seat_offset", source="web:hosts2", seat_id=2, channel="1", value_db=2.0)
    audio.settle()


def press_talk_to_production(audio: AudioStudio) -> None:
    audio.command("run_macro", macro="host1-talk-prod", source="device:d200")
    audio.settle()


def test_talk_to_production_takes_the_host_off_air_and_reads_to_production(
    audio: AudioStudio,
) -> None:
    # The macro turns HOST 1's LR assign off (the fader is not touched), and
    # closes HOST 1 in the other seats' in-ears. HOST 1 still hears
    # themselves (bus 1), and production (bus 5) and the camera operator
    # (bus 6) still hear them at the fader level: they stay linked.
    host_1_on_air(audio)

    press_talk_to_production(audio)

    audio.soon(lambda: seat(audio)["state"] == "to_production")
    assert audio.mixer_value(lr_assign_address("1")) == 0
    assert audio.mixer_value(fader_address("1")) == 0.75
    assert (send(audio, "1", "bus1"), send(audio, "1", "bus5"), send(audio, "1", "bus6")) == (
        0.75,
        0.75,
        0.75,
    )
    assert [send(audio, "1", bus) for bus in OTHER_SEATS_BUSES] == [0.0, 0.0, 0.0]
    assert audio.state("macros")["host1-talk-prod"]["active"] is True


def test_a_fader_move_while_talking_to_production_never_reaches_air_or_the_other_seats(
    audio: AudioStudio,
) -> None:
    # During the macro someone moves HOST 1's fader (the M-VAVE, the desk).
    # In 2026 that put the host back on air; now the fader only changes what
    # is still linked: the host's own in-ears and buses 5-6.
    host_1_on_air(audio)
    press_talk_to_production(audio)
    audio.pass_time(0.5)  # past write protection on the fader

    audio.change_on_mixer(fader_address("1"), 0.5)

    audio.soon(lambda: send(audio, "1", "bus5") == 0.5)
    assert (send(audio, "1", "bus1"), send(audio, "1", "bus6")) == (0.5, 0.5)
    assert audio.mixer_value(lr_assign_address("1")) == 0
    assert [send(audio, "1", bus) for bus in OTHER_SEATS_BUSES] == [0.0, 0.0, 0.0]
    assert seat(audio)["state"] == "to_production"


def test_releasing_talk_to_production_puts_every_send_back_at_main_plus_offset(
    audio: AudioStudio,
) -> None:
    # Pressed, the fader moved to 0.5 meanwhile, pressed again: HOST 1 is on
    # air again and every send follows the fader, with host 2's +2 dB kept.
    host_1_on_air(audio)
    press_talk_to_production(audio)
    audio.pass_time(0.5)
    audio.change_on_mixer(fader_address("1"), 0.5)
    audio.soon(lambda: send(audio, "1", "bus5") == 0.5)

    press_talk_to_production(audio)

    audio.soon(lambda: seat(audio)["state"] == "live")
    assert audio.mixer_value(lr_assign_address("1")) == 1
    assert send(audio, "1", "bus2") == pytest.approx(offset_level(0.5, 2.0))
    assert (send(audio, "1", "bus3"), send(audio, "1", "bus4")) == (0.5, 0.5)
    assert {bus: audio.state("links")["1"][bus] for bus in OTHER_SEATS_BUSES} == dict.fromkeys(
        OTHER_SEATS_BUSES, "linked"
    )
    assert audio.state("macros")["host1-talk-prod"]["active"] is False
    # Bus 5 follows the fader again.
    audio.pass_time(0.5)
    audio.change_on_mixer(fader_address("1"), 0.25)
    audio.soon(lambda: send(audio, "1", "bus5") == 0.25)


def test_production_to_you_follows_a_prod_mic_into_the_seats_in_ears(audio: AudioStudio) -> None:
    ready(audio)
    assert seat(audio)["production_to_you"] is False

    # PROD 1 (channel 5, a production microphone) opened into bus 1, seat 1's in-ears.
    audio.change_on_mixer(send_address("5", "bus1"), 0.75)
    audio.soon(lambda: seat(audio)["production_to_you"] is True)
    # Seat 2's in-ears (bus 2) do not hear it.
    assert seat(audio, 2)["production_to_you"] is False

    audio.change_on_mixer(send_address("5", "bus1"), 0.0)
    audio.soon(lambda: seat(audio)["production_to_you"] is False)


def test_a_mixer_that_stops_answering_shows_offline_and_no_stale_states(audio: AudioStudio) -> None:
    ready(audio)

    # The cable is pulled: after 5 s without an answer the mixer is offline.
    audio.mixer.drop_packets = True
    audio.soon(lambda: audio.state("hosts_view.mixer_online") is False, within_seconds=10)

    view = audio.state("hosts_view")
    assert [entry["state"] for entry in view["seats"].values()] == [None] * 4
    assert all(row["send_level"] is None for row in view["mixes"]["1"])


def test_a_seats_mix_lists_the_link_matrix_rows_with_offsets_for_linked_sources(
    audio: AudioStudio,
) -> None:
    audio.connect_mixer()

    rows = {row["channel"]: row for row in audio.state("hosts_view.mixes")["1"]}

    # SHOTGUN (9) and PC AUDIO are linked to bus 1 by the default seed (the
    # in-ears are main plus offsets): they follow main, offset 0 dB.
    assert (rows["9"]["link"], rows["9"]["offset_db"]) == ("linked", 0.0)
    assert rows["9"]["main_db"] == "0.0 dB"
    assert (rows["aux"]["link"], rows["aux"]["offset_db"]) == ("linked", 0.0)
    assert rows["aux"]["label"] == "PC AUDIO"
    # A hidden input linked to nothing is not offered.
    assert "12" not in rows

    # Unlinked on the link matrix, PC AUDIO stays in the mix as independent.
    audio.command("set_link", channel="aux", bus="bus1", state="unlinked")
    rows = {row["channel"]: row for row in audio.state("hosts_view.mixes")["1"]}
    assert (rows["aux"]["link"], rows["aux"]["offset_db"]) == ("unlinked", None)


def test_talkback_lights_while_a_macro_talking_to_the_camera_op_is_on(audio: AudioStudio) -> None:
    audio.connect_mixer()
    assert audio.state("hosts_view.talkback") == {"active": False, "label": "CAMERA OP"}

    # "Prod 1 → Cam" opens PROD 1's send to bus 6, the talkback bus.
    audio.command("run_macro", macro="macro-1774582749617")
    audio.settle()

    assert audio.state("hosts_view.talkback.active") is True


def test_the_view_carries_the_overlay_settings(audio: AudioStudio) -> None:
    audio.configure(hosts_can_adjust_mix=False, personal_mix_step_db=2.0)

    view = audio.state("hosts_view")

    assert (view["can_adjust"], view["step_db"]) == (False, 2.0)
