"""Tests for the fake VISCA camera (fakes/fake_visca_camera.py).

The fake stands in for the SMTAV in every other test, so it must answer the
way the real camera does: ACK then completion for a command, one answer for
an inquiry, each with the sequence number of the packet it answers.
"""

from baja_studio.cameras.drivers import visca_packets as packets
from baja_studio.fakes.fake_visca_camera import FakeViscaCamera, InProcessDatagramTransport


def command(payload: bytes, sequence: int = 5) -> bytes:
    return packets.wrap(payload, packets.PAYLOAD_COMMAND, sequence)


def inquiry(payload: bytes, sequence: int = 6) -> bytes:
    return packets.wrap(payload, packets.PAYLOAD_INQUIRY, sequence)


def kinds(answers: list[bytes]) -> list[packets.ReplyKind]:
    parsed = [packets.parse_reply(answer) for answer in answers]
    return [reply.kind for reply in parsed if reply is not None]


def test_a_command_is_answered_with_ack_then_completion_carrying_its_sequence_number() -> None:
    answers = FakeViscaCamera().handle(command(packets.zoom_stop(), sequence=42), now=0.0)

    assert kinds(answers) == [packets.ReplyKind.ACK, packets.ReplyKind.COMPLETION]
    replies = [packets.parse_reply(answer) for answer in answers]
    assert [reply.sequence for reply in replies if reply is not None] == [42, 42]


def test_pan_and_tilt_are_integrated_from_the_speed_step_and_direction() -> None:
    camera = FakeViscaCamera()
    camera.handle(command(packets.pan_tilt_drive(18, 17, packets.PAN_LEFT, packets.TILT_UP)), 0.0)

    camera.advance_to(1.0)

    assert camera.pan.position < 0  # left is negative
    assert camera.tilt.position > 0  # up is positive


def test_the_stop_packet_stops_both_axes() -> None:
    camera = FakeViscaCamera()
    camera.handle(command(packets.pan_tilt_drive(5, 5, packets.PAN_RIGHT, packets.TILT_DOWN)), 0.0)

    camera.handle(command(packets.pan_tilt_stop()), 0.5)

    assert camera.pan.rate == camera.tilt.rate == 0.0


def test_zoom_moves_between_wide_and_tele_and_stops_at_the_ends() -> None:
    camera = FakeViscaCamera()
    camera.handle(command(packets.zoom_tele(7)), 0.0)

    camera.advance_to(100.0)

    assert camera.zoom.position == packets.ZOOM_POSITION_TELE


def test_inquiries_answer_the_current_position_in_the_cameras_format() -> None:
    camera = FakeViscaCamera()
    camera.pan.position, camera.tilt.position = 291, -10

    (answer,) = camera.handle(inquiry(packets.pan_tilt_position_inquiry()), 0.0)
    reply = packets.parse_reply(answer)

    assert reply is not None and reply.kind == packets.ReplyKind.INQUIRY_ANSWER
    assert packets.parse_pan_tilt_position(reply.payload) == (291, -10)


def test_a_stored_preset_is_recalled() -> None:
    camera = FakeViscaCamera()
    camera.pan.position = 500
    camera.handle(command(packets.preset_store(3)), 0.0)
    camera.pan.position = 0

    camera.handle(command(packets.preset_recall(3)), 0.1)

    assert camera.pan.position == 500


def test_an_unknown_packet_gets_a_syntax_error() -> None:
    answers = FakeViscaCamera().handle(command(bytes.fromhex("81 01 7E 01 FF")), 0.0)

    assert kinds(answers) == [packets.ReplyKind.ERROR]


def test_dropping_replies_still_carries_out_the_command() -> None:
    # Like a cable that loses only the answers: the camera moves, we never hear.
    camera = FakeViscaCamera(dropping_replies=True)

    answers = camera.handle(command(packets.zoom_wide(3)), 0.0)

    assert answers == []
    assert camera.zoom.rate < 0


def test_the_in_process_transport_queues_answers_until_read_and_unplugging_loses_them() -> None:
    camera = FakeViscaCamera()
    transport = InProcessDatagramTransport(camera, lambda: 0.0)

    transport.send(command(packets.zoom_stop()))
    assert transport.receive() is not None
    assert transport.receive() is not None
    assert transport.receive() is None

    transport.unplugged = True
    transport.send(command(packets.zoom_tele(2)))
    assert camera.zoom.rate == 0.0  # never arrived
