"""Tests for the VISCA-over-IP packets (cameras/drivers/visca_packets.py).

Every expected byte string below is what ptz_test.py sent to the real SMTAV
in March 2026. Written out in hex so a reader can compare them with the
VISCA documentation or a packet capture. "81" is "to camera 1", "FF" ends
every packet.
"""

import pytest

from baja_studio.cameras.driver import Velocity
from baja_studio.cameras.drivers import visca_packets as packets


def hex_bytes(text: str) -> bytes:
    """ "81 01 06" -> b"\\x81\\x01\\x06", so expectations read like a packet capture."""
    return bytes.fromhex(text)


# --- the builders, byte for byte ------------------------------------------------------


@pytest.mark.parametrize(
    ("packet", "expected"),
    [
        # Pan-tilt stop: speeds 00 00, both directions 03 ("stop").
        (packets.pan_tilt_stop(), "81 01 06 01 00 00 03 03 FF"),
        # Drive: pan speed 0x0A, tilt speed 0x05, pan right (02), tilt up (01).
        (
            packets.pan_tilt_drive(10, 5, packets.PAN_RIGHT, packets.TILT_UP),
            "81 01 06 01 0A 05 02 01 FF",
        ),
        # Zoom in at speed 5 is 0x20 | 5; zoom out at speed 5 is 0x30 | 5.
        (packets.zoom_tele(5), "81 01 04 07 25 FF"),
        (packets.zoom_wide(5), "81 01 04 07 35 FF"),
        (packets.zoom_stop(), "81 01 04 07 00 FF"),
        # Presets: 01 = store ("set"), 02 = recall, then the number.
        (packets.preset_store(3), "81 01 04 3F 01 03 FF"),
        (packets.preset_recall(3), "81 01 04 3F 02 03 FF"),
        # Inquiries ("09" instead of "01").
        (packets.pan_tilt_position_inquiry(), "81 09 06 12 FF"),
        (packets.zoom_position_inquiry(), "81 09 04 47 FF"),
    ],
)
def test_each_builder_makes_exactly_the_bytes_ptz_test_sent(packet: bytes, expected: str) -> None:
    assert packet == hex_bytes(expected)


def test_pan_and_tilt_speeds_are_clamped_to_the_visca_spec_ranges() -> None:
    # Pan goes 0x01-0x18 and tilt 0x01-0x17. Asking for more, or for 0, is clamped.
    too_fast = packets.pan_tilt_drive(99, 99, packets.PAN_LEFT, packets.TILT_DOWN)
    too_slow = packets.pan_tilt_drive(0, 0, packets.PAN_LEFT, packets.TILT_DOWN)

    assert too_fast == hex_bytes("81 01 06 01 18 17 01 02 FF")
    assert too_slow == hex_bytes("81 01 06 01 01 01 01 02 FF")


def test_zoom_speed_is_clamped_to_two_through_seven() -> None:
    assert packets.zoom_tele(9) == hex_bytes("81 01 04 07 27 FF")
    assert packets.zoom_wide(0) == hex_bytes("81 01 04 07 32 FF")


def test_preset_numbers_are_masked_to_seven_bits_as_ptz_test_did() -> None:
    assert packets.preset_recall(0x85) == hex_bytes("81 01 04 3F 02 05 FF")


# --- from normalised velocity to packets ---------------------------------------------------


def test_full_speed_right_and_up_is_the_default_top_speed_step_on_both_axes() -> None:
    packet = packets.pan_tilt_for(Velocity(pan=1.0, tilt=1.0))

    # The settings' defaults: pan 0x18, tilt 0x14.
    assert packet == hex_bytes("81 01 06 01 18 14 02 01 FF")


def test_full_speed_is_the_cameras_own_top_step_when_one_is_set() -> None:
    # ptz_test.py's 18 / 17 decimal, as a camera's limits might set them.
    packet = packets.pan_tilt_for(Velocity(pan=1.0, tilt=1.0), pan_max_step=18, tilt_max_step=17)

    assert packet == hex_bytes("81 01 06 01 12 11 02 01 FF")


def test_a_top_step_beyond_the_spec_is_held_to_the_spec() -> None:
    packet = packets.pan_tilt_for(Velocity(pan=1.0, tilt=1.0), pan_max_step=40, tilt_max_step=40)

    assert packet[4:6] == bytes([0x18, 0x17])


def test_negative_pan_is_left_and_negative_tilt_is_down() -> None:
    packet = packets.pan_tilt_for(Velocity(pan=-1.0, tilt=-1.0))

    assert packet[6:8] == bytes([packets.PAN_LEFT, packets.TILT_DOWN])


def test_a_velocity_with_only_pan_leaves_tilt_stopped() -> None:
    packet = packets.pan_tilt_for(Velocity(pan=0.5))

    # Half speed: 1 + round(0.5 * 23) = 13 (0x0D); tilt direction 03 (stop).
    assert packet == hex_bytes("81 01 06 01 0D 01 02 03 FF")


def test_no_pan_and_no_tilt_is_the_stop_packet() -> None:
    assert packets.pan_tilt_for(Velocity(zoom=0.5)) == packets.pan_tilt_stop()


def test_the_default_joystick_zoom_velocity_gives_ptz_tests_fixed_zoom_speed_five() -> None:
    # ptz_test.py always zoomed at speed 5. The controller's zoom buttons give 0.6.
    assert packets.zoom_for(Velocity(zoom=0.6)) == packets.zoom_tele(5)
    assert packets.zoom_for(Velocity(zoom=-0.6)) == packets.zoom_wide(5)
    assert packets.zoom_for(Velocity()) == packets.zoom_stop()


# --- the VISCA/IP header -----------------------------------------------------------------


def test_the_header_holds_type_length_and_sequence_number() -> None:
    wrapped = packets.wrap(packets.zoom_stop(), packets.PAYLOAD_COMMAND, sequence=0x01020304)

    # 01 00 = command, 00 06 = six payload bytes, 01 02 03 04 = sequence number.
    assert wrapped == hex_bytes("01 00 00 06 01 02 03 04 81 01 04 07 00 FF")


def test_inquiries_use_payload_type_0110() -> None:
    wrapped = packets.wrap(packets.zoom_position_inquiry(), packets.PAYLOAD_INQUIRY, 7)

    assert wrapped[:2] == hex_bytes("01 10")


def test_sequence_numbers_start_at_one_count_up_and_wrap_at_32_bits() -> None:
    counter = packets.SequenceCounter()
    assert [counter.take(), counter.take(), counter.take()] == [1, 2, 3]

    near_the_end = packets.SequenceCounter(first=0xFFFFFFFF)
    assert [near_the_end.take(), near_the_end.take()] == [0xFFFFFFFF, 0]


# --- replies ---------------------------------------------------------------------------------


def reply(payload_hex: str, sequence: int = 9) -> bytes:
    return packets.wrap(hex_bytes(payload_hex), packets.PAYLOAD_REPLY, sequence)


@pytest.mark.parametrize(
    ("payload", "kind"),
    [
        ("90 41 FF", packets.ReplyKind.ACK),
        ("90 51 FF", packets.ReplyKind.COMPLETION),
        ("90 50 00 01 02 03 FF", packets.ReplyKind.INQUIRY_ANSWER),
        ("90 60 02 FF", packets.ReplyKind.ERROR),
        ("A0 41 FF", packets.ReplyKind.UNKNOWN),  # not from camera 1
    ],
)
def test_replies_are_classified_by_their_second_byte(payload: str, kind: packets.ReplyKind) -> None:
    parsed = packets.parse_reply(reply(payload))

    assert parsed is not None
    assert parsed.kind == kind
    assert parsed.sequence == 9


def test_a_datagram_too_short_to_hold_a_header_and_reply_is_ignored() -> None:
    assert packets.parse_reply(hex_bytes("01 11 00 03 00 00")) is None


# --- several messages in one datagram (the bench PTZ, probe of 2026-11-15) ------------------

# A datagram exactly as the bench PTZ sent it: three messages, each with its own
# 8-byte header ("01 11" reply, its length, the camera's own sequence number):
# an ACK, a completion, and a pan/tilt answer (pan 0x01A5, tilt 0xFFFF).
BENCH_DATAGRAM = (
    "01 11 00 03 00 00 a8 e2 90 48 ff "
    "01 11 00 03 00 00 a8 e3 90 58 ff "
    "01 11 00 0b 00 00 a8 e4 90 50 00 01 0a 05 0f 0f 0f 0f ff"
)


def test_every_message_of_a_datagram_is_read_not_only_the_first() -> None:
    replies = packets.parse_replies(hex_bytes(BENCH_DATAGRAM))

    # Three messages, in order, each with its own sequence number.
    assert [reply.kind for reply in replies] == [
        packets.ReplyKind.ACK,
        packets.ReplyKind.COMPLETION,
        packets.ReplyKind.INQUIRY_ANSWER,
    ]
    assert [reply.sequence for reply in replies] == [0xA8E2, 0xA8E3, 0xA8E4]
    # The third one's payload is a whole pan/tilt answer: 11 bytes.
    assert packets.parse_pan_tilt_position(replies[2].payload) == (0x01A5, -1)


def test_a_datagram_with_one_message_gives_a_list_of_one() -> None:
    replies = packets.parse_replies(reply("90 41 FF", sequence=7))

    assert [(reply.kind, reply.sequence) for reply in replies] == [(packets.ReplyKind.ACK, 7)]


def test_a_message_cut_short_at_the_end_is_dropped_and_the_ones_before_it_kept() -> None:
    # The last message says it has 11 bytes of payload but only 4 arrived.
    cut = hex_bytes(BENCH_DATAGRAM)[:-7]

    replies = packets.parse_replies(cut)

    assert [reply.kind for reply in replies] == [
        packets.ReplyKind.ACK,
        packets.ReplyKind.COMPLETION,
    ]


def test_bytes_too_short_for_a_header_give_no_messages() -> None:
    assert packets.parse_replies(hex_bytes("01 11 00 03 00 00")) == []
    assert packets.parse_replies(b"") == []


def test_a_header_saying_zero_bytes_of_payload_stops_the_reading() -> None:
    # Nothing sensible can follow a message with no payload: stop there rather
    # than reading the rest of the datagram as headers.
    empty = hex_bytes("01 11 00 00 00 00 00 01")

    assert packets.parse_replies(empty + reply("90 41 FF")) == []


def test_a_pan_tilt_answer_is_read_as_signed_steps() -> None:
    # Pan 0x0123 = 291; tilt 0xFFF6 = -10 (16-bit two's complement).
    answer = hex_bytes("90 50 00 01 02 03 0F 0F 0F 06 FF")

    assert packets.parse_pan_tilt_position(answer) == (291, -10)


def test_0x8000_is_the_most_negative_position_not_a_positive_one() -> None:
    # ptz_test.py compared with "> 0x8000", which got this one value wrong.
    answer = hex_bytes("90 50 08 00 00 00 00 00 00 00 FF")

    assert packets.parse_pan_tilt_position(answer) == (-0x8000, 0)


def test_a_zoom_answer_is_read_as_an_unsigned_value() -> None:
    assert packets.parse_zoom_position(hex_bytes("90 50 04 00 00 00 FF")) == 0x4000


def test_answers_of_the_wrong_length_are_not_positions() -> None:
    # ptz_test.py checked a length shorter than the bytes it then read.
    assert packets.parse_pan_tilt_position(hex_bytes("90 50 00 01 02 03 FF")) is None
    assert packets.parse_zoom_position(hex_bytes("90 41 FF")) is None


def test_encode_nibbles_is_the_inverse_of_reading_them() -> None:
    assert packets.encode_nibbles(0x1A2B, 4) == hex_bytes("01 0A 02 0B")
