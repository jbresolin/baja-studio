"""Tests for `audio.hosts_view`, the hosts screen's audio card data (modules/audio/hosts_view.py).

Through the running audio module and the simulated XR18. A fresh fake has
every input unmuted, at unity, assigned to LR, with every send off, on and
taken pre-fader. Changes "made in Mixing Station" (`change_on_mixer`) reach
the studio the way the real desk's pushes do, so these tests also show the
card never relies on which button was pressed last.
"""

from typing import Any

from baja_studio.adapters.xr18_addresses import (
    fader_address,
    lr_assign_address,
    mute_address,
    send_address,
)
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


def test_the_default_talk_to_production_macro_leaves_the_host_muted_not_to_production(
    audio: AudioStudio,
) -> None:
    # REPORTED FINDING (WP9b, not special-cased): "Host 1 → Prod" closes
    # HOST 1's main fader (store_levels on 1:main). The link matrix links
    # HOST 1 to every bus, bus 5 included, so mirroring copies that silence
    # into the send to production too. By the state rules the host is then
    # heard nowhere: MUTED, not TO PRODUCTION. The macro (or the links)
    # must change for the card to show TO PRODUCTION; this test pins what
    # the rules say about the macro as it is today.
    ready(audio)
    audio.change_on_mixer(send_address("1", "bus5"), 0.75)
    audio.soon(lambda: audio.state("channels.1")["sends"]["bus5"] == 0.75)

    audio.command("run_macro", macro="host1-talk-prod")
    audio.settle()

    assert audio.mixer_value(fader_address("1")) == 0.0
    assert audio.mixer_value(send_address("1", "bus5")) == 0.0
    audio.soon(lambda: seat(audio)["state"] == "muted")


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

    # SHOTGUN (9) is linked to bus 1 by the 2026 seed: it follows main, offset 0 dB.
    assert (rows["9"]["link"], rows["9"]["offset_db"]) == ("linked", 0.0)
    assert rows["9"]["main_db"] == "0.0 dB"
    # PC AUDIO is shown on the audio page but not linked to bus 1: independent.
    assert (rows["aux"]["link"], rows["aux"]["offset_db"]) == ("unlinked", None)
    assert rows["aux"]["label"] == "PC AUDIO"
    # A hidden input linked to nothing is not offered.
    assert "12" not in rows


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
