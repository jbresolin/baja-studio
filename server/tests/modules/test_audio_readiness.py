"""Tests for the audio module's readiness checks (modules/audio/readiness.py).

Audio runs with its fakes (tests/modules/audio_studio.py): a simulated XR18 on
the in-process network and a fake M-VAVE behind an in-process MIDI child.
The checks send nothing to the mixer; they read what the workers publish.
"""

from baja_studio.audio.defaults import channels as default_channels
from baja_studio.core.readiness import CheckStatus
from tests.modules.audio_studio import MIXER_HOST, AudioStudio
from tests.modules.readiness_runner import assert_fix_given, run_module_checks


def results(audio: AudioStudio) -> dict[str, object]:
    return {
        check_id: result.status
        for check_id, result in run_module_checks(audio.services, "audio", audio.clock).items()
    }


def test_a_connected_mixer_passes_and_says_which_one(audio: AudioStudio) -> None:
    audio.connect_mixer()

    result = run_module_checks(audio.services, "audio", audio.clock)["mixer"]

    assert result.status == CheckStatus.OK
    assert result.detail.endswith(f"at {MIXER_HOST}")


def test_no_mixer_address_fails_and_says_where_to_enter_it(audio: AudioStudio) -> None:
    result = run_module_checks(audio.services, "audio", audio.clock)["mixer"]

    assert result.status == CheckStatus.FAIL
    assert result.detail == "no mixer address is set"
    assert result.fix == "Enter the XR18's address in Audio setup → Devices."


def test_a_silent_mixer_fails_with_the_cable_fix(audio: AudioStudio) -> None:
    # Bench §7 step 2: the XR18's network cable is pulled.
    audio.connect_mixer()
    audio.mixer.drop_packets = True
    audio.soon(lambda: audio.state("mixer.connection") == "offline", within_seconds=8.0)

    result = run_module_checks(audio.services, "audio", audio.clock)["mixer"]

    assert result.status == CheckStatus.FAIL
    assert result.fix.startswith("Check the XR18 is on and its network cable is in")
    assert MIXER_HOST in result.fix


def test_an_open_m_vave_passes(audio: AudioStudio) -> None:
    audio.soon(lambda: audio.state("midi.status") == "ok")

    result = run_module_checks(audio.services, "audio", audio.clock)["mvave"]

    assert result.status == CheckStatus.OK
    assert result.detail.startswith("open: ")


def test_an_unplugged_m_vave_only_warns(audio: AudioStudio) -> None:
    # It may be left out on purpose; the page and the D200 still work.
    audio.soon(lambda: audio.state("midi.status") == "ok")
    audio.midi_backend.plugged = False
    audio.midi_hosts[-1].child.check_port()
    audio.soon(lambda: audio.state("midi.status") == "offline")

    result = run_module_checks(audio.services, "audio", audio.clock)["mvave"]

    assert result.status == CheckStatus.WARN
    assert_fix_given(result)


def test_m_vave_control_turned_off_passes(audio: AudioStudio) -> None:
    audio.configure(midi={"enabled": False})

    result = run_module_checks(audio.services, "audio", audio.clock)["mvave"]

    assert (result.status, result.detail) == (CheckStatus.OK, "M-VAVE control is off")


def test_the_d200_endpoint_self_test_passes_and_tells_the_last_call(audio: AudioStudio) -> None:
    never = run_module_checks(audio.services, "audio", audio.clock)["d200"]
    # The D200 polls a button's state, as it does every second.
    audio.http.get("/action/state/1")
    after_a_call = run_module_checks(audio.services, "audio", audio.clock)["d200"]

    assert never.status == CheckStatus.OK
    assert never.detail.endswith("last D200 call: not since the studio started")
    assert after_a_call.detail.endswith("last D200 call: 12:00:00 UTC")


def test_the_d200_self_test_is_not_a_d200_call(audio: AudioStudio) -> None:
    run_module_checks(audio.services, "audio", audio.clock)

    assert audio.state("d200.last_seen") is None


def test_links_on_hidden_channels_warn_and_name_them(audio: AudioStudio) -> None:
    # The 2026 link matrix links 7 and 8, which the default layout hides.
    result = run_module_checks(audio.services, "audio", audio.clock)["links"]

    assert result.status == CheckStatus.WARN
    assert result.detail == "links on hidden channels: 7, 8"
    assert_fix_given(result)


def test_links_all_on_visible_channels_pass(audio: AudioStudio) -> None:
    shown = {channel: {**config, "visible": True} for channel, config in default_channels().items()}
    audio.configure(channels=shown)

    result = run_module_checks(audio.services, "audio", audio.clock)["links"]

    assert result.status == CheckStatus.OK
    assert result.detail.endswith("links, all on visible channels")


def test_every_check_is_declared(audio: AudioStudio) -> None:
    assert set(results(audio)) == {"mixer", "mvave", "d200", "links"}
