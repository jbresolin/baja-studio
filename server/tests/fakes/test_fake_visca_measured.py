"""Tests for the fake VISCA camera's measured mode (fakes/fake_visca_measured.py).

The measured mode must answer the way the bench PTZ was measured answering
on 2026-11-15 (probe: 267 packets sent, 355 datagrams back). Each test
checks one measured behaviour, so a change that makes the fake "nicer" than
the real camera shows up here.
"""

from baja_studio.cameras.drivers import visca_packets as packets
from baja_studio.fakes.fake_visca_camera import (
    FakeViscaCamera,
    InProcessDatagramTransport,
    camera_for,
    parse_arguments,
)
from baja_studio.fakes.fake_visca_measured import MeasuredBehaviour, MeasuredReplies


def inquiry(sequence: int) -> bytes:
    """A pan/tilt position inquiry with our sequence number `sequence`."""
    return packets.wrap(packets.pan_tilt_position_inquiry(), packets.PAYLOAD_INQUIRY, sequence)


def command(payload: bytes, sequence: int) -> bytes:
    return packets.wrap(payload, packets.PAYLOAD_COMMAND, sequence)


def measured_camera(
    first_reply_delay: float = 3.0, data_answer_probability: float = 0.1
) -> FakeViscaCamera:
    """The fake in measured mode; `first_reply_delay` 0 skips its silent start,
    `data_answer_probability` 1.0 makes every inquiry earn its data pair."""
    behaviour = MeasuredBehaviour(
        first_reply_delay=first_reply_delay, data_answer_probability=data_answer_probability
    )
    return FakeViscaCamera(measured=MeasuredReplies(behaviour))


def every_message(datagrams: list[bytes]) -> list[packets.Reply]:
    return [reply for datagram in datagrams for reply in packets.parse_replies(datagram)]


def poll_once_a_second(camera: FakeViscaCamera, seconds: int) -> list[bytes]:
    """Send one inquiry a second (our numbers 1, 2, 3...); return every datagram back."""
    datagrams = []
    for second in range(seconds):
        datagrams += camera.handle(inquiry(sequence=second + 1), float(second))
    return datagrams


def test_nothing_comes_back_for_the_first_three_seconds() -> None:
    camera = measured_camera()

    assert poll_once_a_second(camera, 3) == []  # inquiries at t = 0, 1, 2
    assert camera.handle(inquiry(sequence=4), 3.0) != []  # at t = 3 the queue starts to flow


def test_a_packets_answers_come_only_with_a_later_packet() -> None:
    # The camera never speaks unless spoken to, and what it says answers
    # earlier packets: the answers to this one wait in its queue.
    camera = measured_camera(first_reply_delay=0.0)
    measured = camera.measured
    assert measured is not None

    first = camera.handle(inquiry(sequence=1), 0.0)
    assert first == [] and measured.queued >= 2  # ACK + completion (+ maybe data) wait

    second = camera.handle(inquiry(sequence=2), 1.0)
    assert len(every_message(second)) >= 2  # the first packet's answers


def test_replies_carry_the_cameras_own_counter_never_our_number() -> None:
    camera = measured_camera()

    replies = every_message(poll_once_a_second(camera, 60))

    ours = set(range(1, 61))
    assert replies and not any(reply.sequence in ours for reply in replies)
    # ~43000 and rising about 40 a second, like the probe's 43191 -> 47559 in 101 s.
    assert replies[0].sequence >= 43000
    assert replies[-1].sequence - replies[0].sequence > 40 * 50
    sequences = [reply.sequence for reply in replies]
    assert sequences == sorted(set(sequences))  # never repeats, never goes back


def test_several_messages_come_in_one_datagram_but_never_more_than_four() -> None:
    camera = measured_camera(first_reply_delay=0.0)
    for sequence in range(1, 5):  # four inquiries, answers piling up
        camera.handle(inquiry(sequence), 0.0)

    datagrams = camera.handle(inquiry(sequence=5), 1.0)

    counts = [len(packets.parse_replies(datagram)) for datagram in datagrams]
    assert max(counts) == 4
    assert sum(counts) >= 8  # at least ACK + completion for each of the four


def test_an_inquiry_gets_ack_and_completion_and_its_data_only_now_and_then() -> None:
    camera = measured_camera()

    replies = every_message(poll_once_a_second(camera, 300))

    payloads = [reply.payload for reply in replies]
    pan_tilt = [payload for payload in payloads if len(payload) == 11]
    # The bench PTZ's ACK and completion use socket 8: 90 48 FF, 90 58 FF.
    assert payloads.count(bytes.fromhex("90 48 FF")) >= 290
    assert payloads.count(bytes.fromhex("90 58 FF")) >= 290
    # 18 pan/tilt answers for 259 inquiries (round 1), 5 for 20 (round 2).
    assert 0.04 < len(pan_tilt) / 300 < 0.2


def test_data_comes_in_pairs_pan_tilt_then_a_constant_7_byte_answer() -> None:
    camera = measured_camera()
    camera.pan.position = 0x01A5

    payloads = [reply.payload for reply in every_message(poll_once_a_second(camera, 300))]

    data = [payload for payload in payloads if payload[:2] == bytes.fromhex("90 50")]
    assert data and len(data) % 2 == 0
    for first, second in zip(data[::2], data[1::2], strict=True):
        assert packets.parse_pan_tilt_position(first) == (0x01A5, 0)
        assert second == bytes.fromhex("90 50 00 00 00 00 FF")


def test_a_zoom_inquiry_gets_the_same_pair_as_a_pan_tilt_one() -> None:
    camera = measured_camera(first_reply_delay=0.0)
    zoom = packets.wrap(packets.zoom_position_inquiry(), packets.PAYLOAD_INQUIRY, 1)

    datagrams = []
    for second in range(200):
        datagrams += camera.handle(zoom, float(second))

    lengths = {len(reply.payload) for reply in every_message(datagrams)}
    assert 11 in lengths  # a pan/tilt answer, though only zoom was asked


def test_the_data_pair_comes_later_than_the_ack_and_completion() -> None:
    # It usually arrived in the batch answering a later request.
    camera = measured_camera(first_reply_delay=0.0, data_answer_probability=1.0)

    camera.handle(inquiry(sequence=1), 0.0)
    soon = every_message(camera.handle(inquiry(sequence=2), 0.1))
    later = every_message(camera.handle(inquiry(sequence=3), 1.0))

    assert [len(reply.payload) for reply in soon] == [3, 3]  # ACK + completion of the first
    assert 11 in [len(reply.payload) for reply in later]  # its data, with a later request


def test_no_data_while_moving() -> None:
    # A 3 s pan at medium speed got only ACKs and completions.
    camera = measured_camera(data_answer_probability=1.0)
    camera.handle(command(packets.pan_tilt_drive(8, 1, packets.PAN_RIGHT, packets.TILT_STOP), 1), 0)

    replies = every_message(poll_once_a_second(camera, 30))

    assert replies and all(len(reply.payload) == 3 for reply in replies)


def test_a_command_is_carried_out_at_once_though_its_answers_wait() -> None:
    camera = measured_camera()

    camera.handle(command(packets.pan_tilt_drive(1, 1, packets.PAN_RIGHT, packets.TILT_STOP), 1), 0)
    camera.advance_to(2.0)

    assert camera.pan.position > 0  # it moved (the probe's slow pan moved 5 units)


def test_through_the_in_process_transport_nothing_arrives_while_nothing_is_sent() -> None:
    camera = measured_camera()
    transport = InProcessDatagramTransport(camera, lambda: 5.0)

    assert transport.receive() is None


def test_the_measured_flag_on_the_command_line_makes_a_measured_camera() -> None:
    plain = camera_for(parse_arguments([]))
    measured = camera_for(parse_arguments(["--measured", "--data-answer-probability", "0.5"]))

    assert plain.measured is None  # the default stays well-behaved for other tests
    assert measured.measured is not None
    assert measured.measured.behaviour.data_answer_probability == 0.5
