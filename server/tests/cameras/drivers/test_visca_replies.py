"""Tests for reading a VISCA/IP camera's replies (cameras/drivers/visca_replies.py).

`ReplyTracker` is told what was sent and what came back, with the time, and
says which kind of camera it is talking to (does it echo our sequence
numbers?), how the camera is doing, and where it is. No fake camera here:
each test builds the exact replies it needs, many of them copied from the
bench PTZ's probe of 2026-11-15.
"""

from baja_studio.agents.contract import Status
from baja_studio.cameras.drivers import visca_packets as packets
from baja_studio.cameras.drivers.visca_replies import (
    POSITION_MAX_AGE_SECONDS,
    REST_BEFORE_POSITION_SECONDS,
    SILENCE_FOR_DEGRADED_SECONDS,
    SILENCE_FOR_OFFLINE_SECONDS,
    Purpose,
    ReplyTracker,
)


def message(payload_hex: str, sequence: int, payload_type: int = packets.PAYLOAD_REPLY) -> bytes:
    """One VISCA/IP message: the 8-byte header, then the payload written in hex."""
    return packets.wrap(bytes.fromhex(payload_hex), payload_type, sequence)


def hear(tracker: ReplyTracker, now: float, *messages: bytes) -> set[Purpose]:
    """Hand the tracker one datagram holding `messages`, then let it judge at `now`."""
    answered = tracker.received(packets.parse_replies(b"".join(messages)), now)
    tracker.review(now)
    return answered


def status_of(tracker: ReplyTracker) -> Status:
    """The tracker's status, read through a function so each check reads it afresh."""
    return tracker.status


# The bench PTZ's own sequence numbers: ~43000 and rising, never ours.
CAMERA_COUNTER = 43191


# --- does the camera echo our numbers? ------------------------------------------------------


def test_a_reply_carrying_our_number_says_the_camera_echoes_and_answers_that_packet() -> None:
    tracker = ReplyTracker()
    tracker.sent(5, Purpose.STOP, now=0.0)

    answered = hear(tracker, 0.01, message("90 41 FF", sequence=5))

    assert tracker.sequence_echo is True
    assert answered == {Purpose.STOP}


def test_three_replies_in_a_row_with_numbers_we_never_sent_say_it_does_not_echo() -> None:
    tracker = ReplyTracker()
    tracker.sent(1, Purpose.PAN_TILT_INQUIRY, now=0.0)

    # Two strange numbers could be stray packets: not enough to decide.
    hear(tracker, 0.1, message("90 48 FF", CAMERA_COUNTER), message("90 58 FF", CAMERA_COUNTER + 1))
    assert tracker.sequence_echo is None

    # The third decides it, and nothing it says answers a packet of ours.
    answered = hear(tracker, 0.2, message("90 48 FF", CAMERA_COUNTER + 2))
    assert tracker.sequence_echo is False
    assert answered == set()


def test_the_completion_after_an_ack_is_not_a_strange_number() -> None:
    # An echoing camera answers each command twice with the same number. The
    # second answer finds nothing pending any more, but the number is ours.
    tracker = ReplyTracker()
    for sequence in (1, 2, 3):
        tracker.sent(sequence, Purpose.MOTION, now=0.0)
        hear(tracker, 0.01, message("90 41 FF", sequence), message("90 51 FF", sequence))

    assert tracker.sequence_echo is True


def test_the_latest_evidence_wins() -> None:
    # Decided "no echo" (e.g. late replies left over from before a restart),
    # then a reply matches a pending packet: it does echo after all.
    tracker = ReplyTracker()
    tracker.sent(1, Purpose.ZOOM_INQUIRY, now=0.0)
    hear(tracker, 0.1, *(message("90 41 FF", 900 + n) for n in range(3)))
    assert tracker.sequence_echo is False

    hear(tracker, 0.2, message("90 41 FF", sequence=1))

    assert tracker.sequence_echo is True


def test_a_control_message_shows_the_camera_is_there_but_says_nothing_about_echo() -> None:
    # The bench PTZ answered a sequence-reset control message with "02 01 ... 01".
    tracker = ReplyTracker()
    tracker.sent(1, Purpose.PAN_TILT_INQUIRY, now=0.0)

    hear(tracker, 0.0, message("01", sequence=1, payload_type=0x0201))

    assert tracker.sequence_echo is None
    assert status_of(tracker) == Status.OK


# --- health of a camera that does not echo: silence, not misses -----------------------


def not_echoing_tracker() -> ReplyTracker:
    """A tracker that has learnt (at t = 0) that its camera does not echo."""
    tracker = ReplyTracker()
    tracker.sent(1, Purpose.PAN_TILT_INQUIRY, now=0.0)
    hear(tracker, 0.0, *(message("90 48 FF", CAMERA_COUNTER + n) for n in range(3)))
    assert tracker.sequence_echo is False and status_of(tracker) == Status.OK
    return tracker


def test_until_its_kind_is_known_unanswered_packets_are_not_counted_as_misses() -> None:
    # One reply with a strange number: echo or not is still unknown. Five
    # packets then go unanswered for half a second each. Counting them as
    # misses would make the bench PTZ "degraded" at every start; only silence counts.
    tracker = ReplyTracker()
    tracker.sent(1, Purpose.PAN_TILT_INQUIRY, now=0.0)
    hear(tracker, 0.05, message("90 48 FF", CAMERA_COUNTER))
    for sequence in range(2, 7):
        tracker.sent(sequence, Purpose.MOTION, now=0.1)

    tracker.review(1.0)

    assert tracker.sequence_echo is None
    assert status_of(tracker) == Status.OK


def test_packets_without_any_reply_do_not_count_as_misses_for_a_camera_that_does_not_echo() -> None:
    # Once a second for five seconds we send and the camera stays quiet (the
    # bench PTZ did that for up to 6 s): with an echoing camera that would be
    # five misses and "degraded"; here it is just a quiet spell.
    tracker = not_echoing_tracker()
    for second in range(1, 6):
        tracker.sent(10 + second, Purpose.PAN_TILT_INQUIRY, now=float(second))
        tracker.review(second + 0.9)

    assert status_of(tracker) == Status.OK


def test_silence_while_sending_is_degraded_after_seven_seconds_and_offline_after_twelve() -> None:
    tracker = not_echoing_tracker()
    tracker.sent(2, Purpose.PAN_TILT_INQUIRY, now=1.0)  # the first unanswered packet

    tracker.review(1.0 + SILENCE_FOR_DEGRADED_SECONDS - 0.1)
    assert status_of(tracker) == Status.OK
    tracker.review(1.0 + SILENCE_FOR_DEGRADED_SECONDS)
    assert status_of(tracker) == Status.DEGRADED
    tracker.review(1.0 + SILENCE_FOR_OFFLINE_SECONDS)
    assert status_of(tracker) == Status.OFFLINE
    assert tracker.reason == "nothing heard from the camera for 12 s"
    assert (SILENCE_FOR_DEGRADED_SECONDS, SILENCE_FOR_OFFLINE_SECONDS) == (7.0, 12.0)


def test_any_message_at_all_makes_it_ok_again() -> None:
    tracker = not_echoing_tracker()
    tracker.sent(2, Purpose.PAN_TILT_INQUIRY, now=1.0)
    tracker.review(20.0)
    assert status_of(tracker) == Status.OFFLINE

    hear(tracker, 20.1, message("90 58 FF", CAMERA_COUNTER + 999))

    assert status_of(tracker) == Status.OK


def test_silence_while_we_send_nothing_is_not_a_problem() -> None:
    # The bench PTZ never speaks unless spoken to: quiet with nothing sent is fine.
    tracker = not_echoing_tracker()

    tracker.review(60.0)

    assert status_of(tracker) == Status.OK


def test_a_tracker_that_never_heard_anything_is_offline() -> None:
    tracker = ReplyTracker()
    tracker.sent(1, Purpose.PAN_TILT_INQUIRY, now=0.0)

    tracker.review(0.1)

    assert status_of(tracker) == Status.OFFLINE
    assert tracker.reason == "no answer from the camera yet"


# --- position: published only while it can be trusted ----------------------------------------

# Copied from the probes: the pan/tilt answer (pan 0x01A5, tilt 0xFFFF) and
# the 7-byte answer that followed it, asked for or not.
PAN_TILT_ANSWER = "90 50 00 01 0A 05 0F 0F 0F 0F FF"
SHORT_ANSWER = "90 50 02 00 00 00 FF"


def test_an_echoing_camera_s_answers_are_read_as_the_inquiry_they_answer() -> None:
    tracker = ReplyTracker()
    tracker.sent(1, Purpose.PAN_TILT_INQUIRY, now=0.0)
    tracker.sent(2, Purpose.ZOOM_INQUIRY, now=0.0)

    # Each answer carries the number of the inquiry it answers. The pan/tilt
    # one comes after its ACK, which already matched number 1: still read.
    hear(tracker, 0.1, message("90 41 FF", 1), message(PAN_TILT_ANSWER, 1))
    hear(tracker, 0.1, message(SHORT_ANSWER, 2))

    position = tracker.position()
    assert position is not None
    assert position.pan == 0x01A5 * packets.DEGREES_PER_PAN_TILT_STEP
    assert position.tilt == -1 * packets.DEGREES_PER_PAN_TILT_STEP
    assert position.zoom == 0x2000 / packets.ZOOM_POSITION_TELE


def test_from_a_camera_that_does_not_echo_an_11_byte_answer_is_pan_tilt() -> None:
    tracker = not_echoing_tracker()

    hear(tracker, 5.0, message(PAN_TILT_ANSWER, CAMERA_COUNTER + 10))

    position = tracker.position()
    assert position is not None
    assert position.pan == 0x01A5 * packets.DEGREES_PER_PAN_TILT_STEP


def test_a_7_byte_answer_is_zoom_only_while_a_zoom_inquiry_is_the_only_one_outstanding() -> None:
    # The bench PTZ sent 7-byte answers when no zoom inquiry had been asked.
    tracker = not_echoing_tracker()
    tracker.sent(10, Purpose.PAN_TILT_INQUIRY, now=5.0)
    hear(
        tracker,
        5.05,
        message(PAN_TILT_ANSWER, CAMERA_COUNTER + 10),
        message(SHORT_ANSWER, CAMERA_COUNTER + 11),
    )
    position = tracker.position()
    assert position is not None and position.zoom == 0.0  # not taken: zoom still unknown

    tracker.review(6.0)  # the pan/tilt inquiry is no longer outstanding
    tracker.sent(11, Purpose.ZOOM_INQUIRY, now=6.0)
    hear(tracker, 6.05, message(SHORT_ANSWER, CAMERA_COUNTER + 12))

    position = tracker.position()
    assert position is not None and position.zoom == 0x2000 / packets.ZOOM_POSITION_TELE


def test_motion_forgets_the_position_of_a_camera_that_does_not_echo() -> None:
    # Its answers cannot be dated: after a move the old position is not current,
    # and an answer arriving soon after may have been made before the move.
    tracker = not_echoing_tracker()
    hear(tracker, 5.0, message(PAN_TILT_ANSWER, CAMERA_COUNTER + 10))

    tracker.sent(20, Purpose.MOTION, now=6.0)
    assert tracker.position() is None

    hear(tracker, 8.0, message(PAN_TILT_ANSWER, CAMERA_COUNTER + 11))  # 2 s after: too soon
    assert tracker.position() is None

    hear(tracker, 9.0, message(PAN_TILT_ANSWER, CAMERA_COUNTER + 12))  # 3 s after: at rest
    assert tracker.position() is not None
    assert REST_BEFORE_POSITION_SECONDS == 3.0


def test_motion_does_not_forget_an_echoing_camera_s_position() -> None:
    # Its answers are matched to inquiries sent while moving: they stay current.
    tracker = ReplyTracker()
    tracker.sent(1, Purpose.PAN_TILT_INQUIRY, now=0.0)
    hear(tracker, 0.0, message(PAN_TILT_ANSWER, 1))

    tracker.sent(2, Purpose.MOTION, now=0.5)

    assert tracker.position() is not None


def test_a_position_not_refreshed_for_thirty_seconds_is_dropped_not_kept() -> None:
    # Never a stale position presented as current: after 30 s without a
    # pan/tilt answer the tracker says it does not know.
    tracker = not_echoing_tracker()
    hear(tracker, 1.0, message(PAN_TILT_ANSWER, CAMERA_COUNTER + 10))

    # Polls keep going and get only ACKs and completions, no data.
    for second in range(2, 40):
        tracker.sent(second, Purpose.PAN_TILT_INQUIRY, now=float(second))
        hear(tracker, second + 0.05, message("90 48 FF", CAMERA_COUNTER + 10 + second))
        if second == 30:
            assert tracker.position() is not None  # 29 s old: still published

    assert tracker.position() is None
    assert status_of(tracker) == Status.OK  # and the camera itself is fine
    assert POSITION_MAX_AGE_SECONDS == 30.0


def test_a_refusal_is_reported_with_what_it_refused_when_that_is_known() -> None:
    tracker = ReplyTracker()
    tracker.sent(4, Purpose.PRESET, now=0.0)

    hear(tracker, 0.0, message("90 60 02 FF", sequence=4))
    assert tracker.last_error == "camera refused a preset command"

    hear(tracker, 0.1, message("90 60 02 FF", sequence=CAMERA_COUNTER))
    assert tracker.last_error == "camera refused a command"
