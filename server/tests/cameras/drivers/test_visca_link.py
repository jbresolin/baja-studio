"""Tests for the VISCA/IP driver's logic (cameras/drivers/visca_link.py).

`ViscaLink` is told the time on every call, so these tests need no clock and
no threads: they say "at t = 0.25 s, what goes out?" and hand what went out
to the fake camera, whose answers go back to the link. That is exactly what
the driver's worker does in a loop.

Most tests use the well-behaved fake (it echoes our sequence numbers and
answers at once). The ones about the bench PTZ use the fake's measured mode
(`measured_camera()`), which answers the way the real camera was measured
answering on 2026-11-15: its own sequence numbers, late and batched replies.
"""

import pytest

from baja_studio.agents.contract import Status
from baja_studio.cameras.driver import Velocity
from baja_studio.cameras.drivers import visca_packets as packets
from baja_studio.cameras.drivers.visca_link import (
    LIVENESS_POLL_SECONDS,
    STOP_SENDS,
    Outgoing,
    Purpose,
    ViscaLink,
)
from baja_studio.cameras.drivers.visca_replies import (
    MISSED_FOR_DEGRADED,
    MISSED_FOR_OFFLINE,
    REPLY_TIMEOUT_SECONDS,
)
from baja_studio.fakes.fake_visca_camera import FakeViscaCamera
from baja_studio.fakes.fake_visca_measured import MeasuredReplies

RESEND = 0.1


def exchange(link: ViscaLink, camera: FakeViscaCamera | None, now: float) -> list[Outgoing]:
    """One turn of the worker's loop at time `now`; `camera=None` is an unplugged cable."""
    outgoing = link.due(now)
    for packet in outgoing:
        if camera is not None:
            for answer in camera.handle(packet.datagram, now):
                link.received(answer, now)
    link.expire_replies(now)
    return outgoing


def run(
    link: ViscaLink, camera: FakeViscaCamera | None, start: float, end: float, step: float = 0.05
) -> list[Outgoing]:
    """Turn the loop every `step` seconds from `start` up to and including `end`."""
    sent: list[Outgoing] = []
    for tick in range(round((end - start) / step) + 1):
        sent += exchange(link, camera, round(start + tick * step, 6))
    return sent


def payloads(outgoing: list[Outgoing], purpose: Purpose | None = None) -> list[bytes]:
    """The VISCA payloads (without the 8-byte header), optionally of one purpose."""
    return [
        packet.datagram[packets.IP_HEADER_LENGTH :]
        for packet in outgoing
        if purpose is None or packet.purpose == purpose
    ]


def pan_tilt_stops(outgoing: list[Outgoing]) -> int:
    """How many stops went out (each is a pan-tilt stop plus a zoom stop)."""
    return payloads(outgoing, Purpose.STOP).count(packets.pan_tilt_stop())


def health_of(link: ViscaLink) -> Status:
    """The link's health, read through a function so each check reads it afresh."""
    return link.health


def connected_link(camera: FakeViscaCamera) -> ViscaLink:
    """A link that has already heard from the camera once (the first poll at t=0)."""
    link = ViscaLink(RESEND, now=0.0)
    exchange(link, camera, 0.0)
    assert health_of(link) == Status.OK
    return link


def measured_camera() -> FakeViscaCamera:
    """A fake answering like the bench PTZ (see fakes/fake_visca_measured.py)."""
    return FakeViscaCamera(measured=MeasuredReplies())


# A reply carrying a sequence number we never sent, as the bench PTZ sends them.
FOREIGN_ACK = packets.wrap(bytes.fromhex("90 48 FF"), packets.PAYLOAD_REPLY, 43191)


# --- startup and motion ---------------------------------------------------------------


def test_a_new_link_is_offline_until_the_camera_first_answers() -> None:
    link = ViscaLink(RESEND, now=0.0)
    assert health_of(link) == Status.OFFLINE

    exchange(link, FakeViscaCamera(), 0.0)

    assert health_of(link) == Status.OK


def test_nothing_but_one_position_inquiry_is_sent_at_startup() -> None:
    # In particular no stop: a blanket stop on connect is forbidden (brief §2).
    outgoing = ViscaLink(RESEND, now=0.0).due(0.0)

    assert [packet.purpose for packet in outgoing] == [Purpose.PAN_TILT_INQUIRY]


def test_a_move_sends_the_pan_tilt_and_zoom_packets_at_once() -> None:
    camera = FakeViscaCamera()
    link = connected_link(camera)

    link.move(Velocity(pan=1.0, zoom=0.6), ttl=0.3, now=0.01)
    sent = payloads(exchange(link, camera, 0.01), Purpose.MOTION)

    assert sent == [packets.pan_tilt_for(Velocity(pan=1.0)), packets.zoom_tele(5)]
    assert camera.moving


def test_a_held_velocity_is_resent_every_resend_interval_and_not_in_between() -> None:
    camera = FakeViscaCamera()
    link = connected_link(camera)
    link.move(Velocity(tilt=0.5), ttl=1.0, now=0.0)
    exchange(link, camera, 0.0)

    # 50 ms later: nothing new to say. 100 ms after the first send: say it again.
    assert payloads(exchange(link, camera, 0.05), Purpose.MOTION) == []
    assert len(payloads(exchange(link, camera, 0.10), Purpose.MOTION)) == 2


def test_a_changed_velocity_is_sent_straight_away_without_waiting_for_the_resend() -> None:
    camera = FakeViscaCamera()
    link = connected_link(camera)
    link.move(Velocity(pan=0.2), ttl=1.0, now=0.0)
    exchange(link, camera, 0.0)

    link.move(Velocity(pan=0.9), ttl=1.0, now=0.02)
    sent = payloads(exchange(link, camera, 0.02), Purpose.MOTION)

    assert sent[0] == packets.pan_tilt_for(Velocity(pan=0.9))


def test_when_the_ttl_runs_out_the_link_sends_a_full_stop() -> None:
    # The camera has no dead-man switch of its own: the driver is it.
    camera = FakeViscaCamera()
    link = connected_link(camera)
    link.move(Velocity(pan=1.0, zoom=1.0), ttl=0.3, now=0.0)
    exchange(link, camera, 0.0)

    sent = payloads(exchange(link, camera, 0.3), Purpose.STOP)

    assert sent == [packets.pan_tilt_stop(), packets.zoom_stop()]
    assert not link.moving
    assert not camera.moving


def test_preset_commands_go_out_on_the_next_turn() -> None:
    camera = FakeViscaCamera()
    link = connected_link(camera)

    link.store_preset(4)
    link.recall_preset(4)

    assert payloads(exchange(link, camera, 0.01), Purpose.PRESET) == [
        packets.preset_store(4),
        packets.preset_recall(4),
    ]


# --- stops: a few sends, never endless ---------------------------------------------------


def test_a_stop_is_pan_tilt_stop_and_zoom_stop_together() -> None:
    camera = FakeViscaCamera()
    link = connected_link(camera)

    link.stop(now=0.05)

    assert payloads(exchange(link, camera, 0.05), Purpose.STOP) == [
        packets.pan_tilt_stop(),
        packets.zoom_stop(),
    ]


def test_a_stop_an_echoing_camera_answers_is_not_sent_again() -> None:
    # The well-behaved fake answers the stop with its sequence number: done.
    camera = FakeViscaCamera()
    link = connected_link(camera)

    link.stop(now=0.1)
    sent = run(link, camera, 0.1, 2.0)

    assert payloads(sent, Purpose.STOP) == [packets.pan_tilt_stop(), packets.zoom_stop()]
    assert link.settled


def test_a_stop_nobody_answers_is_sent_three_times_and_then_no_more() -> None:
    # The bug on the bench: a camera that never "confirms" a stop got it again
    # every 100 ms forever, and its video froze. Now: three sends, then done.
    camera = FakeViscaCamera()
    link = connected_link(camera)
    camera.dropping_replies = True  # it hears everything but answers nothing

    link.stop(now=0.1)
    stop_times = []
    for tick in range(200):  # ten seconds, in 50 ms turns of the loop
        now = round(0.1 + tick * 0.05, 6)
        if payloads(exchange(link, camera, now), Purpose.STOP):
            stop_times.append(now)

    # Three sends of the pair, `resend_interval` (100 ms) apart, and nothing after.
    assert STOP_SENDS == 3
    assert stop_times == [0.1, 0.2, 0.3]


def test_a_stop_to_the_measured_camera_is_sent_exactly_three_times() -> None:
    # The bench PTZ never echoes our numbers, so a stop can never be matched to
    # an answer: it goes out three times whatever comes back, and that is all.
    camera = measured_camera()
    link = ViscaLink(RESEND, now=0.0)
    run(link, camera, 0.0, 5.0)  # past its 3 s of silence at startup
    assert health_of(link) == Status.OK

    link.move(Velocity(pan=0.5), ttl=60.0, now=5.0)
    run(link, camera, 5.0, 6.0)
    link.stop(now=6.0)
    sent = run(link, camera, 6.0, 30.0)

    assert pan_tilt_stops(sent) == 3
    assert not camera.moving
    assert link.settled


def test_a_stop_sent_while_the_camera_is_silent_lands_when_it_is_heard_again() -> None:
    # The stick is released while the cable is out: the three sends are lost.
    # When the camera talks again, the stop goes out once more, so the camera
    # does not keep panning to its end stop.
    camera = FakeViscaCamera()
    link = connected_link(camera)
    link.move(Velocity(pan=1.0), ttl=5.0, now=0.0)
    exchange(link, camera, 0.0)

    link.stop(now=0.1)
    lost = run(link, None, 0.1, 3.0)  # cable out
    assert pan_tilt_stops(lost) == 3
    assert camera.moving  # it never heard the stop
    assert not link.settled  # the stop is owed

    run(link, camera, 3.05, 5.0)  # cable back: the next poll is answered

    assert not camera.moving
    assert link.settled


def test_an_echoing_camera_answering_only_a_poll_has_not_confirmed_the_stop() -> None:
    # The cable is back 0.1 s after the last of three lost stop sends, and the
    # camera answers the next poll. That answer is to the poll, not the stop,
    # and an echoing camera tells them apart: the stop is still owed and sent.
    camera = FakeViscaCamera()
    link = connected_link(camera)
    link.move(Velocity(pan=1.0), ttl=60.0, now=0.0)
    exchange(link, camera, 0.0)

    link.stop(now=0.8)
    run(link, None, 0.8, 1.0)  # stop sends at 0.8, 0.9 and 1.0 are lost
    run(link, camera, 1.05, 3.0)  # cable back; polls are answered

    assert not camera.moving


def test_an_owed_stop_is_sent_again_only_once() -> None:
    # Still no endless resend: the second round of three is the last, even if
    # it is lost too.
    camera = FakeViscaCamera()
    link = connected_link(camera)
    link.stop(now=0.1)
    run(link, None, 0.1, 3.0)  # the first three sends are lost

    link.received(FOREIGN_ACK, 3.0)  # the camera is heard: the stop is owed
    second_round = run(link, None, 3.0, 6.0)  # and lost again
    after = run(link, camera, 6.05, 20.0)  # the camera is back for good

    assert pan_tilt_stops(second_round) == 3
    assert pan_tilt_stops(after) == 0


def test_a_new_move_cancels_an_owed_stop() -> None:
    # The operator moved again before the camera came back: their new move wins.
    camera = FakeViscaCamera()
    link = connected_link(camera)
    link.stop(now=0.1)
    run(link, None, 0.1, 3.0)

    link.move(Velocity(tilt=0.4), ttl=60.0, now=3.0)
    sent = run(link, camera, 3.0, 5.0)

    assert payloads(sent, Purpose.STOP) == []
    assert camera.moving


# --- polling: one inquiry at a time --------------------------------------------------------


def test_position_is_polled_once_a_second_alternating_pan_tilt_and_zoom() -> None:
    camera = FakeViscaCamera()
    link = ViscaLink(RESEND, now=0.0)  # default position_poll_seconds: 1.0

    sent = run(link, camera, 0.0, 3.95)

    assert [packet.purpose for packet in sent] == [
        Purpose.PAN_TILT_INQUIRY,  # t = 0
        Purpose.ZOOM_INQUIRY,  # t = 1
        Purpose.PAN_TILT_INQUIRY,  # t = 2
        Purpose.ZOOM_INQUIRY,  # t = 3
    ]


def test_the_poll_interval_is_read_live() -> None:
    camera = FakeViscaCamera()
    link = ViscaLink(RESEND, now=0.0)
    link.position_poll_seconds = 2.0  # what the driver does on a settings change

    sent = run(link, camera, 0.0, 5.95)

    assert len(sent) == 3  # t = 0, 2 and 4


def test_a_poll_of_zero_sends_only_a_liveness_inquiry_every_three_seconds() -> None:
    camera = FakeViscaCamera()
    link = ViscaLink(RESEND, now=0.0, position_poll_seconds=0.0)

    sent = run(link, camera, 0.0, 8.95)

    assert LIVENESS_POLL_SECONDS == 3.0
    assert [packet.purpose for packet in sent] == [Purpose.PAN_TILT_INQUIRY] * 3  # t = 0, 3, 6
    assert health_of(link) == Status.OK


def test_only_one_inquiry_is_in_flight_until_something_comes_back_or_half_a_second() -> None:
    # Polling every 0.25 s, but nothing comes back: each inquiry waits its
    # 0.5 s before the next goes out, so the camera gets two a second, not four.
    camera = FakeViscaCamera()
    link = ViscaLink(RESEND, now=0.0, position_poll_seconds=0.25)
    camera.dropping_replies = True

    sent = run(link, camera, 0.0, 1.95)

    assert len(sent) == 4  # t = 0, 0.5, 1.0, 1.5


# --- health ----------------------------------------------------------------------------


def test_an_echoing_camera_is_degraded_after_three_missed_replies_and_offline_after_ten() -> None:
    camera = FakeViscaCamera()
    link = connected_link(camera)
    camera.dropping_replies = True
    # Hold a velocity so packets keep going out every 100 ms; each goes unanswered.
    link.move(Velocity(pan=0.3), ttl=60.0, now=0.0)

    now, seen = 0.0, []
    while link.health != Status.OFFLINE and now < 5.0:
        now = round(now + RESEND, 3)
        exchange(link, camera, now)
        seen.append(link.health)

    assert Status.DEGRADED in seen
    assert health_of(link) == Status.OFFLINE
    assert "replies missed" in link.health_reason
    assert MISSED_FOR_DEGRADED == 3 and MISSED_FOR_OFFLINE == 10


def test_a_reply_after_half_a_second_is_too_late_to_count() -> None:
    camera = FakeViscaCamera()
    link = connected_link(camera)  # polled at t=0; the next poll is due at 1.0
    camera.dropping_replies = True

    # Three packets in a row go unanswered for half a second each. Between the
    # first and the second, the first one's answer turns up late: it must not
    # count as an answer (and so must not reset the count of misses).
    link.store_preset(1)
    (first,) = exchange(link, camera, 0.1)
    first_reply = packets.parse_reply(first.datagram)
    assert first_reply is not None
    late_answer = packets.wrap(
        bytes.fromhex("90 41 FF"), packets.PAYLOAD_REPLY, first_reply.sequence
    )
    link.expire_replies(0.1 + REPLY_TIMEOUT_SECONDS)
    link.received(late_answer, 0.61)
    link.store_preset(2)
    link.store_preset(3)
    link.due(0.62)  # sends both presets
    link.expire_replies(0.62 + REPLY_TIMEOUT_SECONDS)

    assert health_of(link) == Status.DEGRADED


def test_one_answer_after_misses_makes_the_link_ok_again() -> None:
    camera = FakeViscaCamera()
    link = connected_link(camera)
    camera.dropping_replies = True
    run(link, camera, 0.05, 12.0)  # a dozen once-a-second polls go unanswered
    assert health_of(link) == Status.OFFLINE

    camera.dropping_replies = False
    run(link, camera, 12.05, 13.0)

    assert health_of(link) == Status.OK


def test_the_measured_camera_stays_ok_for_minutes_despite_never_echoing() -> None:
    # The bench bug: replies never matched our numbers, so every packet was a
    # "miss" and the status flapped ok / degraded / offline. Now any message
    # shows the camera is alive, and its late batches do not flap it.
    camera = measured_camera()
    link = ViscaLink(RESEND, now=0.0)

    run(link, camera, 0.0, 5.0)
    seen = {link.health}
    for second in range(5, 300):
        run(link, camera, second + 0.05, second + 1.0)
        seen.add(link.health)

    assert link.replies.sequence_echo is False
    assert seen == {Status.OK}


def test_the_measured_camera_unplugged_is_degraded_then_offline() -> None:
    camera = measured_camera()
    link = ViscaLink(RESEND, now=0.0)
    run(link, camera, 0.0, 10.0)
    assert health_of(link) == Status.OK

    # Cable out after t = 10: nothing comes back. The first unanswered poll
    # goes out at t = 11; 7 s later the camera is degraded, 12 s later offline.
    run(link, None, 10.05, 17.0)
    assert health_of(link) == Status.OK  # a few silent seconds are normal for it
    run(link, None, 17.05, 18.5)
    assert health_of(link) == Status.DEGRADED
    run(link, None, 18.55, 23.5)
    assert health_of(link) == Status.OFFLINE
    assert "nothing heard" in link.health_reason


def test_the_socket_is_reopened_every_few_seconds_while_offline() -> None:
    link = ViscaLink(RESEND, now=0.0)  # never answered: offline

    assert not link.should_reopen(1.0)
    assert link.should_reopen(5.0)
    assert not link.should_reopen(6.0)  # not again straight away
    assert link.should_reopen(10.0)


# --- position ----------------------------------------------------------------------------


def test_position_answers_are_read_into_degrees_and_zoom_zero_to_one() -> None:
    camera = FakeViscaCamera()
    camera.pan.position, camera.tilt.position = 400, -200
    camera.zoom.position = packets.ZOOM_POSITION_TELE / 2
    link = ViscaLink(RESEND, now=0.0)

    run(link, camera, 0.0, 1.0)  # the pan/tilt poll at 0, the zoom poll at 1

    position = link.position()
    assert position is not None
    assert position.pan == 400 * packets.DEGREES_PER_PAN_TILT_STEP
    assert position.tilt == -200 * packets.DEGREES_PER_PAN_TILT_STEP
    assert position.zoom == 0.5


def test_the_measured_camera_s_occasional_position_answers_are_read() -> None:
    # Only about one inquiry in six gets its data, under the camera's own
    # sequence number: the link takes it by its length.
    camera = measured_camera()
    camera.pan.position, camera.tilt.position = 0x01A5, -1  # the bench's numbers
    link = ViscaLink(RESEND, now=0.0)

    run(link, camera, 0.0, 120.0)

    position = link.position()
    assert position is not None
    assert position.pan == 0x01A5 * packets.DEGREES_PER_PAN_TILT_STEP
    assert position.tilt == -1 * packets.DEGREES_PER_PAN_TILT_STEP


def test_with_a_poll_of_zero_no_position_is_published() -> None:
    # The bench PTZ gives no usable position: with 0, even a data answer to a
    # liveness inquiry is not shown as the camera's position.
    camera = FakeViscaCamera()
    link = ViscaLink(RESEND, now=0.0, position_poll_seconds=0.0)

    run(link, camera, 0.0, 4.0)

    assert link.position() is None
    assert health_of(link) == Status.OK


def test_the_worker_may_sleep_until_the_next_resend_but_never_past_a_reply_deadline() -> None:
    camera = FakeViscaCamera()
    link = connected_link(camera)
    link.move(Velocity(pan=0.5), ttl=1.0, now=0.0)
    exchange(link, camera, 0.0)

    # Next resend is due at 0.1 s; nothing else is earlier.
    assert link.seconds_until_next(0.03) == pytest.approx(0.07)
