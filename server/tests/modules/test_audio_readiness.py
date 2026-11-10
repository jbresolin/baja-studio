"""Tests for the audio module's readiness checks (modules/audio/readiness.py).

Audio runs with its fakes (tests/modules/audio_studio.py): a simulated XR18 on
the in-process network and a fake M-VAVE behind an in-process MIDI child.
The checks send nothing to the mixer; they read what the workers publish.
"""

from baja_studio.adapters.xr18_addresses import lr_assign_address, send_tap_address
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


def map_host_1(audio: AudioStudio) -> None:
    """Seat 1's microphone is HOST 1 (channel 1); the other seats are not mapped."""
    audio.configure(
        seats=[
            {"seat_id": 1, "channel": "1", "iem_bus": "bus1"},
            {"seat_id": 2, "iem_bus": "bus2"},
            {"seat_id": 3, "iem_bus": "bus3"},
            {"seat_id": 4, "iem_bus": "bus4"},
        ]
    )


def writes_to_the_mixer(audio: AudioStudio) -> list[object]:
    """Every value the studio has set on the mixer (messages with an argument)."""
    return [message for _, message in audio.mixer.received if message.arguments]


def connect_and_read_taps(audio: AudioStudio) -> None:
    """Connect, and wait until the tap of every send in the matrix has been read.

    The last answers of the initial read can still be on their way when
    `connect_mixer` returns (seen on Windows CI).
    """
    audio.connect_mixer()
    controller = audio.module.controller
    pairs = [
        (channel, bus) for channel, buses in controller.links.matrix().items() for bus in buses
    ]
    audio.soon(lambda: all(controller.cache.send_taps.get(pair) is not None for pair in pairs))


def test_linked_sends_taken_before_the_fader_pass(audio: AudioStudio) -> None:
    # A fresh fake takes every send pre-fader; the default matrix has 37
    # linked sends (paused ones, like PROD 1-2 on buses 5-6, are not copied).
    connect_and_read_taps(audio)

    result = run_module_checks(audio.services, "audio", audio.clock)["linked_taps"]

    assert result.status == CheckStatus.OK
    assert result.detail == "37 linked sends, all before the fader"


def test_a_linked_send_after_the_fader_warns_and_is_only_read(audio: AudioStudio) -> None:
    # Someone set HOST 1's send to host 2's in-ears post-fader at the desk.
    # The studio copies the fader into that send, so the fader would cut it
    # twice. The check warns with the fix and writes nothing: the tap is
    # the desk's configuration.
    connect_and_read_taps(audio)
    audio.change_on_mixer(send_tap_address("1", "bus2"), 4)
    audio.soon(lambda: audio.module.controller.cache.send_taps.get(("1", "bus2")) == 4)
    writes_before = writes_to_the_mixer(audio)

    result = run_module_checks(audio.services, "audio", audio.clock)["linked_taps"]

    assert result.status == CheckStatus.WARN
    assert result.detail == "after the fader, so cut twice: HOST 1 → IEM HOST 2"
    assert "pre-fader" in result.fix
    assert writes_to_the_mixer(audio) == writes_before


def test_a_paused_or_unlinked_send_after_the_fader_is_not_a_problem(audio: AudioStudio) -> None:
    # The studio does not copy the fader into it, so nothing is cut twice.
    connect_and_read_taps(audio)
    audio.command("set_link", channel="1", bus="bus2", state="unlinked")
    audio.change_on_mixer(send_tap_address("1", "bus2"), 4)
    audio.soon(lambda: audio.module.controller.cache.send_taps.get(("1", "bus2")) == 4)

    result = run_module_checks(audio.services, "audio", audio.clock)["linked_taps"]

    assert result.status == CheckStatus.OK


def test_taps_and_lr_assigns_are_not_checked_while_the_mixer_is_away(audio: AudioStudio) -> None:
    # One cause, one red line: the mixer check already fails.
    map_host_1(audio)

    checks = run_module_checks(audio.services, "audio", audio.clock)

    for check_id in ("linked_taps", "lr_assign"):
        assert checks[check_id].status == CheckStatus.WARN
        assert checks[check_id].detail == "not checked: the XR18 is not answering"


def test_a_seat_microphones_lr_assign_read_from_the_mixer_passes(audio: AudioStudio) -> None:
    audio.connect_mixer()
    map_host_1(audio)
    audio.soon(lambda: audio.module.controller.cache.lr_assigns.get("1") is True)

    result = run_module_checks(audio.services, "audio", audio.clock)["lr_assign"]

    assert result.status == CheckStatus.OK
    assert result.detail == "LR assign read for 1 seat microphones"


def test_a_mixer_that_never_reports_lr_assign_warns_instead_of_failing_silently(
    audio: AudioStudio,
) -> None:
    # "Talk to production" depends on /ch/NN/mix/lr. A mixer that does not
    # answer it may not obey it either: say so before the show, do not let
    # the macro do nothing on air.
    del audio.mixer.values[lr_assign_address("1")]
    audio.connect_mixer()
    map_host_1(audio)

    result = run_module_checks(audio.services, "audio", audio.clock)["lr_assign"]

    assert result.status == CheckStatus.WARN
    assert result.detail == "the XR18 has not reported the LR assign of HOST 1"
    assert_fix_given(result)


def test_every_check_is_declared(audio: AudioStudio) -> None:
    assert set(results(audio)) == {
        "mixer",
        "mvave",
        "d200",
        "linked_taps",
        "lr_assign",
        "links",
    }
