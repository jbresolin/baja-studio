"""Tests for the XR18 timing rules (adapters/xr18_link.py).

`Xr18Link` is told the time on every call, so these tests just pass
numbers: "at 0 s", "at 8 s"... and look at which packets it wants sent.
Packets are decoded back to addresses to keep the checks readable.
"""

from baja_studio.adapters.osc import OscMessage, decode_packet, encode_message
from baja_studio.adapters.xr18_link import (
    MAX_QUEUED_QUERIES,
    PACKET_BURST,
    PacketRates,
    Xr18Link,
)


def addresses(datagrams: list[bytes]) -> list[str]:
    """The address of each datagram, in order."""
    return [decode_packet(datagram)[0].address for datagram in datagrams]


def xinfo_reply() -> bytes:
    return encode_message(OscMessage("/xinfo", ("192.168.1.10", "XR18-AB", "XR18", "1.22")))


def online_link() -> Xr18Link:
    """A link that has sent its first keep-alive and heard /xinfo answered at time 0."""
    link = Xr18Link(now=0.0)
    link.due_packets(0.0)
    link.receive(xinfo_reply(), 0.0)
    return link


def test_the_first_call_subscribes_and_asks_who_is_there() -> None:
    link = Xr18Link(now=0.0)

    assert addresses(link.due_packets(0.0)) == ["/xremote", "/xinfo"]
    # Nothing more is due straight away.
    assert link.due_packets(0.0) == []


def test_xremote_is_renewed_every_eight_seconds_before_the_mixer_drops_us_at_ten() -> None:
    link = Xr18Link(now=0.0)
    link.due_packets(0.0)

    sent_at = [t / 10 for t in range(1, 200) if "/xremote" in addresses(link.due_packets(t / 10))]

    assert sent_at == [8.0, 16.0]


def test_the_link_sleeps_only_until_the_next_thing_is_due() -> None:
    link = Xr18Link(now=0.0)
    link.due_packets(0.0)

    # /xinfo is due every 2 s, /xremote every 8 s: the next is /xinfo at 2 s.
    assert link.next_wake(0.5) == 1.5


def test_the_mixer_is_online_once_xinfo_is_answered_and_offline_after_five_silent_seconds() -> None:
    link = Xr18Link(now=0.0)
    link.due_packets(0.0)
    assert not link.is_online(0.0)

    link.receive(xinfo_reply(), 0.1)

    assert link.is_online(0.1)
    assert link.info is not None and link.info.name == "XR18-AB"
    assert link.is_online(5.0)
    assert not link.is_online(5.1)


def test_a_write_is_sent_at_once_and_its_echo_is_ignored_for_300_ms() -> None:
    # The mixer echoes what we set. A late echo carrying an older value
    # (the mixer had not applied ours yet) must not overwrite what we set.
    link = online_link()
    link.write("/ch/01/mix/fader", 0.6)

    assert addresses(link.due_packets(1.0)) == ["/ch/01/mix/fader"]

    # 0.375 is exact in the 32-bit floats OSC carries, so it compares equal.
    stale_echo = encode_message(OscMessage("/ch/01/mix/fader", (0.375,)))
    assert link.receive(stale_echo, 1.1) == []
    assert link.receive(stale_echo, 1.29) == []
    # After the window the mixer is the truth again (Mixing Station moved it).
    assert link.receive(stale_echo, 1.31) == [OscMessage("/ch/01/mix/fader", (0.375,))]


def test_a_write_still_waiting_to_be_sent_is_protected_too() -> None:
    link = online_link()
    link.write("/ch/02/mix/on", 0)

    pushed = encode_message(OscMessage("/ch/02/mix/on", (1,)))
    assert link.receive(pushed, 0.5) == []


def test_other_addresses_are_never_held_back() -> None:
    link = online_link()
    link.write("/ch/01/mix/fader", 0.6)
    link.due_packets(1.0)

    other = encode_message(OscMessage("/ch/02/mix/fader", (0.25,)))
    assert link.receive(other, 1.0) == [OscMessage("/ch/02/mix/fader", (0.25,))]


def test_a_fader_swept_faster_than_the_budget_sends_only_its_latest_value() -> None:
    link = online_link()
    for step in range(1, 51):
        link.write("/ch/01/mix/fader", step / 64)  # exact in float32

    sent = [decode_packet(datagram)[0] for datagram in link.due_packets(0.0)]

    assert sent == [OscMessage("/ch/01/mix/fader", (50 / 64,))]


def test_never_more_than_the_budget_however_much_is_queued() -> None:
    # In 2026 the mixer hung under ~113 packets per 500 ms. Queue a flood of
    # queries and count what goes out over two seconds in 10 ms ticks.
    link = online_link()
    link.query([f"/ch/{n:02d}/mix/{bus:02d}/level" for n in range(1, 17) for bus in range(1, 7)])

    per_half_second: dict[int, int] = {}
    for tick in range(1, 201):
        now = tick / 100
        per_half_second[int(now * 2)] = per_half_second.get(int(now * 2), 0) + len(
            link.due_packets(now)
        )

    assert max(per_half_second.values()) <= 50 + PACKET_BURST
    assert sum(per_half_second.values()) >= 96  # and everything did go out


def test_writes_go_before_queries() -> None:
    link = online_link()
    link.query(["/ch/03/mix/fader"])
    link.write("/ch/01/mix/fader", 0.5)

    assert addresses(link.due_packets(0.0)) == ["/ch/01/mix/fader", "/ch/03/mix/fader"]


def test_the_same_query_is_not_queued_twice_and_a_written_address_needs_no_query() -> None:
    link = online_link()
    link.write("/ch/01/mix/fader", 0.5)
    link.query(["/ch/02/mix/fader", "/ch/02/mix/fader", "/ch/01/mix/fader"])

    assert link.pending_counts() == (1, 1)


def test_queries_beyond_the_queue_limit_are_dropped_and_counted() -> None:
    link = online_link()

    dropped = link.query([f"/q/{n}" for n in range(MAX_QUEUED_QUERIES + 5)])

    assert dropped == 5


def test_xinfo_replies_are_not_passed_on_and_garbage_is_ignored() -> None:
    link = Xr18Link(now=0.0)

    assert link.receive(xinfo_reply(), 0.0) == []
    assert link.receive(b"not osc", 0.0) == []


def test_packet_rates_cover_the_last_whole_second() -> None:
    link = Xr18Link(now=0.0)
    link.due_packets(0.0)  # 2 sent
    link.receive(xinfo_reply(), 0.5)  # 1 received

    assert link.rates(0.9) == PacketRates(0, 0)  # the first second is not over
    assert link.rates(1.0) == PacketRates(sent=2, received=1)
    assert link.rates(3.5) == PacketRates(0, 0)  # a quiet second in between


def test_once_the_mixer_is_silent_the_link_does_not_ask_to_be_woken_at_once_forever() -> None:
    # The "offline at" deadline lies in the past once it has passed; using it
    # would make the worker spin without sleeping.
    link = online_link()
    link.due_packets(6.0)

    assert not link.is_online(6.0)
    assert link.next_wake(6.0) > 0.0
