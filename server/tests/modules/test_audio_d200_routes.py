"""Tests for the D200 HTTP contract (modules/audio/d200_routes.py).

The existing Ulanzi profile must keep working with only its host and port
changed, so the responses are compared byte for byte with what the 2026
server answered (d200_2026_responses.json, worked out from its server.js).
The D200 itself only reads `muted` / `active`, but other scripts may have
read more.
"""

import json
from pathlib import Path
from typing import Any

import pytest

from baja_studio.adapters.xr18_addresses import fader_address, mute_address
from baja_studio.adapters.xr18_levels import level_to_db
from baja_studio.modules.audio.d200_routes import express_json, js_number, parse_float, parse_int
from tests.modules.audio_studio import AudioStudio

RECORDED = json.loads((Path(__file__).parent / "d200_2026_responses.json").read_text("utf-8"))


def call(audio: AudioStudio, method: str, path: str) -> Any:
    response = audio.http.request(method, path)
    audio.settle()
    return response


def test_every_recorded_2026_response_is_reproduced_byte_for_byte(audio: AudioStudio) -> None:
    audio.connect_mixer()

    for case in RECORDED["cases"]:
        response = call(audio, case["method"], case["path"])

        where = f"{case['method']} {case['path']}"
        assert response.status_code == case["status"], where
        assert response.content.decode("utf-8") == case["body"], where
        assert response.headers["content-type"] == "application/json; charset=utf-8"


def test_a_mute_from_the_d200_reaches_the_mixer_and_the_log(audio: AudioStudio) -> None:
    audio.connect_mixer()

    call(audio, "GET", "/action/mute/3/on")

    assert audio.mixer_value(mute_address("3")) == 0
    assert ("device:d200", "set_mute", "ok") in audio.log()


def test_a_nudge_answers_with_the_new_level_in_the_2026_shape(audio: AudioStudio) -> None:
    # HOST 1 at unity, +3 dB. The keys and their order are 2026's; the level
    # follows the real XR18 law, so `fader` differs from 2026's 0.8913.
    audio.connect_mixer()

    body = call(audio, "GET", "/action/nudge/1/3").json()

    assert list(body) == ["ok", "channel", "fader", "db"]
    assert body["db"] == "3.0 dB"
    assert level_to_db(body["fader"]) == pytest.approx(3.0)
    assert audio.mixer_value(fader_address("1")) == pytest.approx(body["fader"])


def test_a_nudge_aux_answers_in_the_2026_shape_and_pauses_the_link(audio: AudioStudio) -> None:
    # Bus 6 is the camera operator's, not a seat's in-ears: WP4's rule holds.
    audio.connect_mixer()

    body = call(audio, "POST", "/action/nudge-aux/1/bus6/+3").json()

    assert list(body) == ["ok", "channel", "bus", "fader", "db"]
    assert body["bus"] == "bus6"
    assert audio.state("links")["1"]["bus6"] == "paused"


def test_a_hosts_pad_nudge_on_a_seat_bus_moves_the_offset_and_keeps_the_link(
    audio: AudioStudio,
) -> None:
    # Host 1's pad "vol up" for the SHOTGUN (channel 9, linked to bus1 by
    # the 2026 seed) in their in-ears (bus1 is seat 1's bus) is the same
    # nudge-aux action the D200 always called. It now moves the seat's
    # offset instead of pausing the link (WP9b §3), and answers in the same
    # 2026 shape with the send's new level.
    audio.connect_mixer()

    body = call(audio, "POST", "/action/nudge-aux/9/bus1/+2").json()

    assert list(body) == ["ok", "channel", "bus", "fader", "db"]
    assert body["db"] == "2.0 dB"  # the shotgun's fader is at unity: 0 dB + 2 dB
    assert audio.state("links")["9"]["bus1"] == "linked"
    assert audio.state("offsets") == {"9:bus1": 2.0}


def test_state_polls_are_not_commands_but_tell_the_page_a_d200_is_there(audio: AudioStudio) -> None:
    audio.connect_mixer()

    call(audio, "GET", "/action/state/1")

    assert audio.log() == []
    assert audio.state("d200.last_seen") is not None


def test_actions_are_refused_while_the_mixer_is_offline(audio: AudioStudio) -> None:
    response = call(audio, "GET", "/action/mute/1")

    assert response.status_code == 400
    assert "XR18 is offline" in response.json()["error"]


def test_actions_can_be_turned_off_in_the_setup(audio: AudioStudio) -> None:
    audio.connect_mixer()
    audio.configure(d200={"enabled": False})

    response = call(audio, "GET", "/action/mute/1")

    assert response.status_code == 503
    assert audio.mixer_value(mute_address("1")) == 1


def test_the_actions_are_at_the_root_not_under_api(audio: AudioStudio) -> None:
    assert audio.http.get("/api/audio/action/state/1").status_code == 404


@pytest.mark.parametrize(
    ("value", "text"),
    [
        (0.0, "0"),
        (1.0, "1"),
        (0.75, "0.75"),
        (-0.25, "-0.25"),
        (2.0833333333333333e-05, "0.000020833333333333333"),
        (1e-7, "1e-7"),
        (1.5e21, "1.5e+21"),
        (123.0, "123"),
        (7, "7"),
    ],
)
def test_numbers_are_written_as_javascript_writes_them(value: float, text: str) -> None:
    assert js_number(value) == text


def test_json_is_compact_keeps_key_order_and_unicode() -> None:
    assert express_json({"b": 1, "a": [True, None, "\u00e9"]}) == '{"b":1,"a":[true,null,"\u00e9"]}'


@pytest.mark.parametrize(
    ("text", "number"), [("3", 3.0), ("+3", 3.0), ("-1.5dB", -1.5), (".5", 0.5), ("abc", None)]
)
def test_parse_float_reads_like_javascripts(text: str, number: float | None) -> None:
    assert parse_float(text) == number


def test_parse_int_reads_like_javascripts() -> None:
    assert parse_int("12abc") == 12
    assert parse_int("x") is None
