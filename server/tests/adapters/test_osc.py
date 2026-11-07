"""Tests for the OSC codec (adapters/osc.py), byte for byte.

OSC packs everything in 4-byte blocks: a string is followed by 1-4 zero
bytes, numbers are 4 bytes big-endian. The expected bytes below are
written out by hand so a reader can check them against the OSC 1.0 spec.
"""

import struct

import pytest

from baja_studio.adapters.osc import OscDecodeError, OscMessage, decode_packet, encode_message


def test_a_query_is_the_address_and_an_empty_type_tag() -> None:
    # "/xinfo" is 6 characters, padded with 2 zeros to 8; "," is padded to 4.
    assert encode_message(OscMessage("/xinfo")) == b"/xinfo\x00\x00,\x00\x00\x00"


def test_an_address_of_exactly_four_characters_still_gets_four_zero_bytes() -> None:
    # A string must end with at least one zero, so 4 characters take 8 bytes.
    assert encode_message(OscMessage("/abc")) == b"/abc\x00\x00\x00\x00,\x00\x00\x00"


def test_a_fader_level_is_a_big_endian_float32() -> None:
    datagram = encode_message(OscMessage("/ch/01/mix/fader", (0.75,)))

    assert datagram == b"/ch/01/mix/fader\x00\x00\x00\x00,f\x00\x00" + struct.pack(">f", 0.75)
    assert datagram[-4:] == b"\x3f\x40\x00\x00"


def test_a_mute_is_an_int32() -> None:
    datagram = encode_message(OscMessage("/ch/16/mix/on", (0,)))

    assert datagram == b"/ch/16/mix/on\x00\x00\x00,i\x00\x00\x00\x00\x00\x00"


@pytest.mark.parametrize(
    ("address", "expected_address_bytes"),
    [
        # Each address form the studio sends, with its padding.
        ("/rtn/aux/mix/fader", b"/rtn/aux/mix/fader\x00\x00"),
        ("/lr/mix/on", b"/lr/mix/on\x00\x00"),
        ("/bus/3/mix/fader", b"/bus/3/mix/fader\x00\x00\x00\x00"),
        ("/ch/05/mix/02/level", b"/ch/05/mix/02/level\x00"),
        ("/rtn/aux/mix/06/level", b"/rtn/aux/mix/06/level\x00\x00\x00"),
        ("/-snap/load", b"/-snap/load\x00"),
    ],
)
def test_every_address_form_is_padded_to_four_bytes(
    address: str, expected_address_bytes: bytes
) -> None:
    datagram = encode_message(OscMessage(address))

    assert datagram == expected_address_bytes + b",\x00\x00\x00"
    assert len(expected_address_bytes) % 4 == 0


def test_xinfo_reply_with_four_strings_decodes() -> None:
    # What the XR18 answers to /xinfo: IP, network name, model, firmware.
    datagram = (
        b"/xinfo\x00\x00,ssss\x00\x00\x00"
        b"192.168.1.10\x00\x00\x00\x00XR18-5E-91-3A\x00\x00\x00XR18\x00\x00\x00\x001.22\x00\x00\x00\x00"
    )

    assert decode_packet(datagram) == [
        OscMessage("/xinfo", ("192.168.1.10", "XR18-5E-91-3A", "XR18", "1.22"))
    ]


@pytest.mark.parametrize(
    "message",
    [
        OscMessage("/ch/01/mix/fader", (0.5,)),
        OscMessage("/ch/01/mix/on", (1,)),
        OscMessage("/ch/01/config/name", ("HOST 1",)),
        OscMessage("/meters/1", (b"\x01\x02\x03",)),
        OscMessage("/xinfo", ("a", "bb", "ccc", "dddd")),
        OscMessage("/xremote"),
    ],
)
def test_whatever_is_encoded_decodes_back_to_the_same_message(message: OscMessage) -> None:
    assert decode_packet(encode_message(message)) == [message]


def test_a_bundle_is_unpacked_into_its_messages() -> None:
    first = encode_message(OscMessage("/ch/01/mix/on", (0,)))
    second = encode_message(OscMessage("/ch/02/mix/on", (1,)))
    bundle = (
        b"#bundle\x00"
        + b"\x00" * 7
        + b"\x01"
        + struct.pack(">i", len(first))
        + first
        + struct.pack(">i", len(second))
        + second
    )

    assert decode_packet(bundle) == [
        OscMessage("/ch/01/mix/on", (0,)),
        OscMessage("/ch/02/mix/on", (1,)),
    ]


@pytest.mark.parametrize(
    "datagram",
    [
        b"no slash\x00\x00\x00\x00",
        b"/ch/01/mix/fader",  # no terminator
        b"/ch/01/mix/fader\x00\x00\x00\x00,f\x00\x00\x3f",  # float cut short
        b"/x\x00\x00,q\x00\x00",  # unknown type tag
    ],
)
def test_malformed_packets_are_refused(datagram: bytes) -> None:
    with pytest.raises(OscDecodeError):
        decode_packet(datagram)


def test_a_bool_is_refused_because_the_xr18_wants_ints() -> None:
    with pytest.raises(TypeError, match="0 or 1"):
        encode_message(OscMessage("/ch/01/mix/on", (True,)))
