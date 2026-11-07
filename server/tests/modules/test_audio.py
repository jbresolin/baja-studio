"""Tests for the audio module running in the service (modules/audio/__init__.py).

The `audio` fixture (tests/modules/audio_studio.py) runs the service with
the audio module, a simulated XR18 on the in-process network, and a fake
M-VAVE. These tests follow what an operator does: point the module at the
mixer, move things on the page and on the mixer, and check what each side
sees.
"""

import pytest

from baja_studio.adapters.xr18_addresses import fader_address, mute_address, send_address
from baja_studio.core.module import CommandError
from baja_studio.core.supervisor import HealthState
from baja_studio.fakes.simulated_xr18 import SimulatedXr18
from tests.modules.audio_studio import AudioStudio


def test_without_a_mixer_address_audio_is_offline_and_says_what_to_do(audio: AudioStudio) -> None:
    # Fresh install: no XR18 address yet. The page must say so, not spin.
    audio.soon(lambda: audio.state("mixer.connection") == "not_configured")

    health = audio.services.supervisor.module_health("audio")
    assert health.state == HealthState.OFFLINE
    assert "address" in health.message


def test_connecting_reads_every_strip_and_reports_the_mixer_name(audio: AudioStudio) -> None:
    # The fake mixer starts with faders at unity (0.75) and nothing muted.
    audio.connect_mixer()

    assert audio.state("mixer.name") == "XR18-FAKE"
    assert audio.state("channels.1")["fader"] == 0.75
    assert audio.state("channels.1")["db"] == "0.0 dB"
    assert audio.state("channels.1")["muted"] is False
    assert audio.services.supervisor.module_health("audio").state == HealthState.OK


def test_a_fader_moved_on_the_page_reaches_the_mixer(audio: AudioStudio) -> None:
    audio.connect_mixer()

    audio.command("set_fader", channel="3", db=-10.0)
    audio.settle()

    # -10 dB is exactly 0.5 on the XR18's fader law.
    assert audio.mixer_value(fader_address("3")) == 0.5
    assert audio.state("channels.3")["db"] == "-10.0 dB"
    assert audio.state("last_source.3:main") == "web:alice"


def test_a_change_made_in_mixing_station_shows_on_the_page(audio: AudioStudio) -> None:
    audio.connect_mixer()

    audio.change_on_mixer(mute_address("2"), 0)

    # The push crosses the reader thread, so wait for it rather than assume.
    audio.soon(lambda: audio.state("channels.2")["muted"] is True)


def test_a_main_fader_change_is_mirrored_to_its_linked_sends(audio: AudioStudio) -> None:
    # The 2026 seed links HOST 1 (channel 1) to every bus. Moving its fader
    # must move its sends to the IEMs too.
    audio.connect_mixer()

    audio.command("set_fader", channel="1", level=0.6)
    audio.settle()

    # The mixer keeps 32-bit floats, hence approx.
    assert audio.mixer_value(send_address("1", "bus1")) == pytest.approx(0.6)
    assert audio.mixer_value(send_address("1", "bus6")) == pytest.approx(0.6)


def test_the_subscription_is_renewed_every_eight_seconds_for_as_long_as_it_runs(
    audio: AudioStudio,
) -> None:
    # The XR18 forgets a subscriber after 10 s. Over 30 s of fake time the
    # mixer must hear /xremote at the start and then every 8 s.
    audio.connect_mixer()
    start = audio.clock.monotonic()

    audio.pass_time(30.0)

    renewals = [m for _, m in audio.mixer.received if m.address == "/xremote"]
    assert len(renewals) >= 4
    assert audio.mixer.subscribed_clients(audio.clock.monotonic())
    assert audio.clock.monotonic() - start == pytest.approx(30.0)


def test_our_own_echo_does_not_fight_a_value_just_set(audio: AudioStudio) -> None:
    # We set channel 4 to 0.5. Before the mixer has applied it, it pushes the
    # old value (0.75) back. Within 300 ms that echo must be ignored, or the
    # page would flick back to the old level.
    audio.connect_mixer()
    audio.command("set_fader", channel="4", level=0.5)
    audio.settle()

    audio.change_on_mixer(fader_address("4"), 0.75)
    audio.pass_time(0.1)

    assert audio.state("channels.4")["fader"] == 0.5


def test_after_the_protection_window_the_mixer_is_the_truth_again(audio: AudioStudio) -> None:
    audio.connect_mixer()
    audio.command("set_fader", channel="4", level=0.5)
    audio.settle()  # sent: the 300 ms window starts now
    audio.pass_time(0.5)

    audio.change_on_mixer(fader_address("4"), 0.25)

    audio.soon(lambda: audio.state("channels.4")["fader"] == 0.25)


def test_connecting_never_polls_every_aux_send(audio: AudioStudio) -> None:
    # In 2026 asking for all 16 x 6 sends hung the mixer. On connect only the
    # sends something depends on are read (links, macros, M-VAVE buttons).
    audio.connect_mixer()
    audio.pass_time(3.0)

    send_queries = [q for q in audio.mixer.queries_received() if q.endswith("/level")]
    assert 0 < len(send_queries) < 16 * 6
    assert len(send_queries) == len(set(send_queries))  # none asked twice
    # Channel 12 is not linked, visible or mapped anywhere: none of its sends.
    assert not [q for q in send_queries if q.startswith("/ch/12/")]
    assert not audio.mixer.hung


def test_half_an_hour_with_the_page_open_keeps_a_steady_low_packet_rate(audio: AudioStudio) -> None:
    # Bench step 7 in miniature: after the initial read the studio sends
    # only keep-alives and /xinfo, about one packet a second.
    audio.connect_mixer()
    audio.pass_time(5.0)
    before = audio.network.sent_count

    audio.pass_time(60.0)

    per_second = (audio.network.sent_count - before) / 60.0
    assert per_second < 1.0
    assert audio.state("mixer.packets_per_s")["sent"] <= 2
    assert not audio.mixer.hung


def test_a_silent_mixer_is_offline_after_five_seconds_and_back_when_it_answers(
    audio: AudioStudio,
) -> None:
    audio.connect_mixer()

    audio.mixer.drop_packets = True
    audio.soon(lambda: audio.state("mixer.connection") == "offline", within_seconds=8.0)
    assert audio.services.supervisor.module_health("audio").state == HealthState.OFFLINE

    audio.mixer.drop_packets = False
    audio.soon(lambda: audio.state("mixer.connection") == "ok", within_seconds=4.0)


def test_commands_are_refused_with_advice_while_the_mixer_is_offline(audio: AudioStudio) -> None:
    with pytest.raises(CommandError, match="XR18 is offline"):
        audio.command("set_fader", channel="1", level=0.5)


def test_a_new_mixer_address_reconnects_and_forgets_the_old_values(audio: AudioStudio) -> None:
    audio.connect_mixer()
    second = audio.network.add_mixer("192.168.1.11", 10024, SimulatedXr18(name="XR18-SPARE"))
    second.values[fader_address("1")] = 0.25

    audio.configure(mixer={"host": "192.168.1.11", "port": 10024})

    audio.soon(lambda: audio.state("mixer.name") == "XR18-SPARE")
    audio.soon(lambda: audio.state("channels.1")["fader"] == 0.25)
    assert len(audio.network.open_transports()) == 1


def test_turning_the_mixer_off_closes_the_connection(audio: AudioStudio) -> None:
    audio.connect_mixer()

    audio.configure(mixer={"host": "192.168.1.10", "port": 10024, "enabled": False})

    audio.soon(lambda: audio.state("mixer.connection") == "disabled")
    assert audio.network.open_transports() == []


def test_stopping_the_service_closes_the_mixer_socket(audio: AudioStudio) -> None:
    audio.connect_mixer()

    audio.services.registry.stop_module("audio")

    assert audio.network.open_transports() == []


def test_without_the_m_vave_the_module_is_degraded_not_offline(audio: AudioStudio) -> None:
    # The mixer is what matters; a missing control surface is a lesser problem.
    audio.connect_mixer()
    audio.midi_backend.plugged = False
    audio.midi_hosts[-1].child.check_port()

    audio.soon(lambda: audio.state("midi.status") == "offline")
    health = audio.services.supervisor.module_health("audio")
    assert health.state == HealthState.DEGRADED
    assert "M-VAVE" in health.message
