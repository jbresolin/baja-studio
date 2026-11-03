"""Tests for the Pelco-D parser and frame meanings (cameras/inputs/pelco_d.py).

A Pelco-D frame is 7 bytes: FF, address, cmd1, cmd2, data1, data2, checksum.
The fixtures in `SNIFFED_FRAMES` are the real controller's frames, rebuilt
from the flags mapped with ptz_test.py in March 2026.
"""

import pytest

from baja_studio.cameras.inputs import pelco_d
from baja_studio.cameras.inputs.pelco_d import (
    CallButton,
    Frame,
    FrameParser,
    Motion,
    RecallPreset,
    StopAll,
    Unknown,
    interpret,
)
from baja_studio.fakes.fake_serial import SNIFFED_FRAMES


def parse_one(data: bytes) -> Frame:
    frames = FrameParser().feed(data)
    assert len(frames) == 1
    return frames[0]


# --- the byte parser -----------------------------------------------------------------------


def test_the_checksum_is_the_sum_of_bytes_one_to_five_modulo_256() -> None:
    # Address 1, cmd2 0x02 (pan right), pan speed 0x3F: 1 + 0 + 2 + 0x3F + 0 = 0x42.
    assert pelco_d.checksum(bytes.fromhex("FF 01 00 02 3F 00 00")) == 0x42
    assert SNIFFED_FRAMES["pan_right_full"] == bytes.fromhex("FF 01 00 02 3F 00 42")


def test_a_clean_frame_is_parsed_into_its_fields() -> None:
    frame = parse_one(SNIFFED_FRAMES["pan_right_full"])

    assert frame == Frame(address=1, command1=0, command2=0x02, data1=0x3F, data2=0)


def test_frames_split_across_reads_are_put_back_together() -> None:
    # The serial port hands over whatever arrived: here, 3 bytes and then 4.
    parser = FrameParser()
    whole = SNIFFED_FRAMES["tilt_up_full"]

    assert parser.feed(whole[:3]) == []
    assert parser.feed(whole[3:]) == [Frame(1, 0, pelco_d.TILT_UP, 0, 0x3F)]


def test_several_frames_in_one_read_all_come_out_in_order() -> None:
    frames = FrameParser().feed(SNIFFED_FRAMES["zoom_in"] + SNIFFED_FRAMES["stop"])

    assert [frame.command2 for frame in frames] == [pelco_d.ZOOM_IN, pelco_d.STOP]


def test_garbage_before_a_frame_is_skipped_until_the_sync_byte() -> None:
    # Line noise when the adapter is plugged in, for example.
    parser = FrameParser()

    frames = parser.feed(bytes.fromhex("00 13 37 42") + SNIFFED_FRAMES["stop"])

    assert frames == [Frame(1, 0, 0, 0, 0)]
    assert parser.skipped_bytes == 4


def test_a_frame_with_a_wrong_checksum_is_rejected() -> None:
    corrupted = bytearray(SNIFFED_FRAMES["pan_left_full"])
    corrupted[6] ^= 0x01
    parser = FrameParser()

    assert parser.feed(bytes(corrupted)) == []
    assert parser.rejected_frames == 1


def test_after_a_false_sync_byte_the_parser_finds_the_real_frame_inside() -> None:
    # A stray 0xFF just before a real frame: the 7 bytes starting at the stray
    # byte fail the checksum, and the parser must look again one byte later,
    # not throw all 7 bytes away (which would lose the real frame).
    parser = FrameParser()

    frames = parser.feed(b"\xff" + SNIFFED_FRAMES["zoom_out"])

    assert frames == [Frame(1, 0, pelco_d.ZOOM_OUT, 0, 0)]
    assert parser.rejected_frames == 1


# --- what frames mean ----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("fixture", "pan", "tilt", "zoom"),
    [
        ("tilt_up_full", 0.0, 1.0, 0),
        ("tilt_down_full", 0.0, -1.0, 0),
        ("pan_right_full", 1.0, 0.0, 0),
        ("pan_left_full", -1.0, 0.0, 0),
        ("up_left_full", -1.0, 1.0, 0),
        ("up_right_full", 1.0, 1.0, 0),
        ("down_left_full", -1.0, -1.0, 0),
        ("down_right_full", 1.0, -1.0, 0),
        ("zoom_in", 0.0, 0.0, 1),
        ("zoom_out", 0.0, 0.0, -1),
    ],
)
def test_every_direction_flag_and_diagonal_gives_the_right_signs(
    fixture: str, pan: float, tilt: float, zoom: int
) -> None:
    # Right and up are positive, as everywhere in the studio.
    action = interpret(parse_one(SNIFFED_FRAMES[fixture]))

    assert action == Motion(address=1, pan=pan, tilt=tilt, zoom_direction=zoom)


def test_speed_bytes_from_0_to_0x3f_become_0_to_1() -> None:
    action = interpret(parse_one(SNIFFED_FRAMES["pan_right_half"]))

    assert isinstance(action, Motion)
    assert action.pan == pytest.approx(0x20 / 0x3F)  # about 0.51


def test_turbo_speed_bytes_above_0x3f_count_as_full_speed() -> None:
    action = interpret(Frame(1, 0, pelco_d.PAN_LEFT, 0x40, 0))

    assert action == Motion(1, -1.0, 0.0, 0)


def test_cmd2_zero_is_a_stop() -> None:
    assert interpret(parse_one(SNIFFED_FRAMES["stop"])) == StopAll(address=1)


def test_direction_flags_with_zero_speed_mean_the_stick_is_at_rest() -> None:
    # Pan-right flag but speed byte 0: nothing would move, so it is a stop.
    assert interpret(Frame(1, 0, pelco_d.PAN_RIGHT, 0, 0)) == StopAll(1)


def test_preset_recall_is_one_based_on_the_controller_and_zero_based_for_the_camera() -> None:
    # The controller's preset button "1" is VISCA preset 0.
    action = interpret(parse_one(SNIFFED_FRAMES["recall_preset_1"]))

    assert action == RecallPreset(address=1, n=0)


def test_preset_zero_from_the_controller_is_not_a_preset() -> None:
    assert isinstance(interpret(Frame(1, 0, pelco_d.PRESET_RECALL, 0, 0)), Unknown)


def test_the_call_button_arrives_from_address_three_with_its_number() -> None:
    assert interpret(parse_one(SNIFFED_FRAMES["call_2"])) == CallButton(n=2)


def test_cmd2_seven_from_another_address_is_not_the_call_button() -> None:
    # Bit 0 set means "a command, not motion"; 0x07 elsewhere is unknown.
    assert isinstance(interpret(Frame(1, 0, pelco_d.CALL_BUTTON, 0, 2)), Unknown)


def test_the_address_byte_is_kept_so_frames_can_be_routed() -> None:
    frame = Frame(address=4, command1=0, command2=pelco_d.TILT_UP, data1=0, data2=0x3F)

    assert interpret(frame) == Motion(address=4, pan=0.0, tilt=1.0, zoom_direction=0)
