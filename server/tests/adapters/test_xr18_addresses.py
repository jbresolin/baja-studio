"""Tests for the XR18 address table (adapters/xr18_addresses.py).

Each quirk the 2026 tool learned the hard way has a test here, so a
"tidy-up" that breaks one fails loudly.
"""

import pytest

from baja_studio.adapters.xr18_addresses import (
    ParsedAddress,
    fader_address,
    mute_address,
    name_address,
    normalise_channel,
    normalise_point_bus,
    pan_address,
    parse_address,
    point_address,
    send_address,
)


@pytest.mark.parametrize(
    ("channel", "address"),
    [
        ("1", "/ch/01/mix/fader"),  # inputs use two digits
        ("16", "/ch/16/mix/fader"),
        ("aux", "/rtn/aux/mix/fader"),  # the stereo return is not /ch/17
        ("lr", "/lr/mix/fader"),
        ("bus2", "/bus/2/mix/fader"),  # buses use one digit on the X Air family
    ],
)
def test_each_kind_of_strip_has_its_own_fader_address(channel: str, address: str) -> None:
    assert fader_address(channel) == address


def test_mute_lives_at_mix_on() -> None:
    assert mute_address("7") == "/ch/07/mix/on"
    assert mute_address("lr") == "/lr/mix/on"


def test_sends_use_two_digit_bus_numbers_for_inputs_and_the_aux_return() -> None:
    assert send_address("5", "bus1") == "/ch/05/mix/01/level"
    assert send_address("aux", "bus6") == "/rtn/aux/mix/06/level"


def test_buses_and_the_main_mix_have_no_sends_or_pan() -> None:
    with pytest.raises(ValueError, match="no sends"):
        send_address("bus1", "bus2")
    with pytest.raises(ValueError, match="no sends"):
        pan_address("lr")


def test_a_send_point_is_the_fader_for_main_and_the_send_otherwise() -> None:
    assert point_address("5", "main") == "/ch/05/mix/fader"
    assert point_address("5", "bus3") == "/ch/05/mix/03/level"


def test_names_and_pans() -> None:
    assert name_address("bus1") == "/bus/1/config/name"
    assert pan_address("aux") == "/rtn/aux/mix/pan"


@pytest.mark.parametrize(
    ("text", "channel"),
    [
        ("1", "1"),
        ("01", "1"),
        ("16", "16"),
        ("17", "aux"),
        ("18", "aux"),
        ("AUX", "aux"),
        ("lr", "lr"),
        ("bus6", "bus6"),
    ],
)
def test_channel_names_from_the_2026_d200_urls_are_understood(text: str, channel: str) -> None:
    # The D200 profile calls channel 17 for PC AUDIO: that is the aux return.
    assert normalise_channel(text) == channel


@pytest.mark.parametrize("text", ["0", "19", "bus7", "main", ""])
def test_unknown_channels_are_refused_in_the_2026_wording(text: str) -> None:
    with pytest.raises(ValueError, match=f"Unknown channel: {text}"):
        normalise_channel(text)


def test_point_buses_accept_lr_as_the_2026_word_for_main() -> None:
    assert normalise_point_bus("lr") == "main"
    assert normalise_point_bus("bus4") == "bus4"
    with pytest.raises(ValueError):
        normalise_point_bus("bus9")


@pytest.mark.parametrize(
    ("address", "parsed"),
    [
        ("/ch/03/mix/fader", ParsedAddress("fader", "3")),
        ("/ch/03/mix/on", ParsedAddress("mute", "3")),
        ("/ch/03/config/name", ParsedAddress("name", "3")),
        ("/ch/03/mix/pan", ParsedAddress("pan", "3")),
        ("/ch/03/mix/04/level", ParsedAddress("send", "3", "bus4")),
        ("/rtn/aux/mix/fader", ParsedAddress("fader", "aux")),
        ("/rtn/aux/mix/02/level", ParsedAddress("send", "aux", "bus2")),
        ("/lr/mix/on", ParsedAddress("mute", "lr")),
        ("/bus/5/mix/fader", ParsedAddress("fader", "bus5")),
        # The 2026 tool's two-digit bus form is understood as well.
        ("/bus/05/mix/fader", ParsedAddress("fader", "bus5")),
    ],
)
def test_incoming_addresses_are_recognised(address: str, parsed: ParsedAddress) -> None:
    assert parse_address(address) == parsed


@pytest.mark.parametrize(
    "address",
    [
        "/ch/01/eq/1/g",  # EQ: not ours
        "/ch/01/mix/07/level",  # sends 7-10 go to the effects
        "/ch/17/mix/fader",  # there is no /ch/17
        "/bus/7/mix/fader",
        "/meters/1",
    ],
)
def test_addresses_we_do_not_use_are_ignored(address: str) -> None:
    assert parse_address(address) is None
