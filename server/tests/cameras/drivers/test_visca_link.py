"""Tests for the VISCA/IP driver's logic (cameras/drivers/visca_link.py).

`ViscaLink` is told the time on every call, so these tests need no clock and
no threads: they say "at t = 0.25 s, what goes out?" and hand what went out
to the fake camera, whose answers go back to the link. That is exactly what
the driver's worker does in a loop.
"""

import pytest

from baja_studio.agents.contract import Status
from baja_studio.cameras.driver import Velocity
from baja_studio.cameras.drivers import visca_packets as packets
from baja_studio.cameras.drivers.visca_link import (
    MISSED_FOR_DEGRADED,
    MISSED_FOR_OFFLINE,
    REPLY_TIMEOUT_SECONDS,
    Outgoing,
    Purpose,
    ViscaLink,
)
from baja_studio.fakes.fake_visca_camera import FakeViscaCamera

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


def payloads(outgoing: list[Outgoing], purpose: Purpose | None = None) -> list[bytes]:
    """The VISCA payloads (without the 8-byte header), optionally of one purpose."""
    return [
        packet.datagram[packets.IP_HEADER_LENGTH :]
        for packet in outgoing
        if purpose is None or packet.purpose == purpose
    ]


def health_of(link: ViscaLink) -> Status:
    """The link's health, read through a function so each check reads it afresh."""
    return link.health


def connected_link(camera: FakeViscaCamera) -> ViscaLink:
    """A link that has already heard from the camera once (the first poll at t=0)."""
    link = ViscaLink(RESEND, now=0.0)
    exchange(link, camera, 0.0)
    assert link.health == Status.OK
    return link


def test_a_new_link_is_offline_until_the_camera_first_answers() -> None:
    link = ViscaLink(RESEND, now=0.0)
    assert health_of(link) == Status.OFFLINE

    exchange(link, FakeViscaCamera(), 0.0)

    assert health_of(link) == Status.OK


def test_nothing_but_position_inquiries_is_sent_at_startup() -> None:
    # In particular no stop: a blanket stop on connect is forbidden (brief §2).
    outgoing = ViscaLink(RESEND, now=0.0).due(0.0)

    assert [packet.purpose for packet in outgoing] == [
        Purpose.PAN_TILT_INQUIRY,
        Purpose.ZOOM_INQUIRY,
    ]


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


def test_a_stop_is_pan_tilt_stop_and_zoom_stop_together() -> None:
    camera = FakeViscaCamera()
    link = connected_link(camera)

    link.stop(now=0.05)

    assert payloads(exchange(link, camera, 0.05), Purpose.STOP) == [
        packets.pan_tilt_stop(),
        packets.zoom_stop(),
    ]


def test_a_stop_the_camera_did_not_answer_is_repeated_until_it_does() -> None:
    # The cable is out when the operator lets go of the stick; the stop must
    # still land when it is plugged back in, or the camera keeps panning.
    camera = FakeViscaCamera()
    link = connected_link(camera)
    link.move(Velocity(pan=1.0), ttl=5.0, now=0.0)
    exchange(link, camera, 0.0)

    link.stop(now=0.1)
    assert payloads(exchange(link, None, 0.1), Purpose.STOP)  # lost
    assert payloads(exchange(link, None, 0.2), Purpose.STOP)  # lost again
    assert camera.moving  # still panning: it never heard a stop

    assert payloads(exchange(link, camera, 0.3), Purpose.STOP)  # cable back
    assert not camera.moving
    assert payloads(exchange(link, camera, 0.4), Purpose.STOP) == []  # confirmed: no more


def test_preset_commands_go_out_on_the_next_turn() -> None:
    camera = FakeViscaCamera()
    link = connected_link(camera)

    link.store_preset(4)
    link.recall_preset(4)

    assert payloads(exchange(link, camera, 0.01), Purpose.PRESET) == [
        packets.preset_store(4),
        packets.preset_recall(4),
    ]


def test_three_missed_replies_in_a_row_are_degraded_and_ten_are_offline() -> None:
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
    assert link.health == Status.OFFLINE
    assert "replies missed" in link.health_reason
    assert MISSED_FOR_DEGRADED == 3 and MISSED_FOR_OFFLINE == 10


def test_a_reply_after_half_a_second_is_too_late_to_count() -> None:
    camera = FakeViscaCamera()
    link = connected_link(camera)  # polled at t=0; the next poll is due at 0.5
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
    link.due(0.62)  # sends both presets and the 0.5 s poll (two inquiries)
    link.expire_replies(0.62 + REPLY_TIMEOUT_SECONDS)

    assert link.health == Status.DEGRADED


def test_one_answer_after_misses_makes_the_link_ok_again() -> None:
    camera = FakeViscaCamera()
    link = connected_link(camera)
    camera.dropping_replies = True
    for step in range(1, 13):
        exchange(link, camera, step * 0.5)  # the twice-a-second poll goes unanswered
    assert health_of(link) == Status.OFFLINE

    camera.dropping_replies = False
    exchange(link, camera, 7.0)

    assert health_of(link) == Status.OK


def test_the_socket_is_reopened_every_few_seconds_while_offline() -> None:
    link = ViscaLink(RESEND, now=0.0)  # never answered: offline

    assert not link.should_reopen(1.0)
    assert link.should_reopen(5.0)
    assert not link.should_reopen(6.0)  # not again straight away
    assert link.should_reopen(10.0)


def test_position_answers_are_read_into_degrees_and_zoom_zero_to_one() -> None:
    camera = FakeViscaCamera()
    camera.pan.position, camera.tilt.position = 400, -200
    camera.zoom.position = packets.ZOOM_POSITION_TELE / 2
    link = ViscaLink(RESEND, now=0.0)

    exchange(link, camera, 0.0)

    position = link.position()
    assert position is not None
    assert position.pan == 400 * packets.DEGREES_PER_PAN_TILT_STEP
    assert position.tilt == -200 * packets.DEGREES_PER_PAN_TILT_STEP
    assert position.zoom == 0.5


def test_the_worker_may_sleep_until_the_next_resend_but_never_past_a_reply_deadline() -> None:
    camera = FakeViscaCamera()
    link = connected_link(camera)
    link.move(Velocity(pan=0.5), ttl=1.0, now=0.0)
    exchange(link, camera, 0.0)

    # Next resend is due at 0.1 s; nothing else is earlier.
    assert link.seconds_until_next(0.03) == pytest.approx(0.07)
