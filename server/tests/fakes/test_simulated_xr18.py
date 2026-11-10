"""Tests for the simulated XR18 (fakes/simulated_xr18.py).

The fake must behave like the mixer where the studio depends on it, or
the audio tests prove nothing: queries answered, subscriptions that
expire after 10 s, changes pushed to subscribers, the flood hang.
"""

from baja_studio.adapters.osc import OscMessage, decode_packet, encode_message
from baja_studio.fakes.simulated_xr18 import SimulatedXr18


def send(
    mixer: SimulatedXr18, message: OscMessage, now: float = 0.0, client: str = "studio"
) -> list[tuple[str, OscMessage]]:
    """Send one message; return what comes back, decoded, with its recipient."""
    return [
        (to, decode_packet(datagram)[0])
        for to, datagram in mixer.handle(client, encode_message(message), now)
    ]


def test_a_query_is_answered_with_the_current_value() -> None:
    mixer = SimulatedXr18()

    assert send(mixer, OscMessage("/ch/01/mix/fader")) == [
        ("studio", OscMessage("/ch/01/mix/fader", (0.75,)))
    ]


def test_a_fresh_mixer_has_inputs_on_lr_and_sends_on_and_pre_fader() -> None:
    # What the hosts' audio card reads: LR assign, a send's on switch, its tap.
    mixer = SimulatedXr18()

    assert send(mixer, OscMessage("/ch/01/mix/lr")) == [
        ("studio", OscMessage("/ch/01/mix/lr", (1,)))
    ]
    assert send(mixer, OscMessage("/ch/01/mix/05/on")) == [
        ("studio", OscMessage("/ch/01/mix/05/on", (1,)))
    ]
    assert send(mixer, OscMessage("/rtn/aux/mix/02/tap")) == [
        ("studio", OscMessage("/rtn/aux/mix/02/tap", (3,)))
    ]


def test_xinfo_says_who_the_mixer_is() -> None:
    reply = send(SimulatedXr18(name="XR18-TEST"), OscMessage("/xinfo"))

    assert reply == [
        ("studio", OscMessage("/xinfo", ("192.168.1.10", "XR18-TEST", "XR18", "1.22")))
    ]


def test_changes_are_pushed_to_subscribers_including_the_sender() -> None:
    mixer = SimulatedXr18()
    send(mixer, OscMessage("/xremote"), client="studio")
    send(mixer, OscMessage("/xremote"), client="tablet")

    pushed = send(mixer, OscMessage("/ch/02/mix/on", (0,)), now=1.0)

    assert sorted(to for to, _ in pushed) == ["studio", "tablet"]
    assert mixer.values["/ch/02/mix/on"] == 0


def test_echo_to_sender_can_be_turned_off() -> None:
    mixer = SimulatedXr18(echo_to_sender=False)
    send(mixer, OscMessage("/xremote"))

    assert send(mixer, OscMessage("/ch/02/mix/on", (0,))) == []


def test_a_subscription_lapses_after_ten_seconds() -> None:
    mixer = SimulatedXr18()
    send(mixer, OscMessage("/xremote"), now=0.0)

    assert mixer.external_change("/ch/01/mix/fader", 0.5, now=9.9)
    assert mixer.external_change("/ch/01/mix/fader", 0.4, now=10.0) == []


def test_loading_a_snapshot_pushes_what_changed() -> None:
    mixer = SimulatedXr18()
    send(mixer, OscMessage("/xremote"))
    mixer.snapshots[3] = {**mixer.values, "/ch/05/mix/fader": 0.25}

    pushed = send(mixer, OscMessage("/-snap/load", (3,)))

    assert pushed == [("studio", OscMessage("/ch/05/mix/fader", (0.25,)))]


def test_more_than_113_packets_in_half_a_second_hang_it_until_recovered() -> None:
    mixer = SimulatedXr18()
    for packet in range(114):
        send(mixer, OscMessage("/ch/01/mix/fader"), now=packet * 0.004)

    assert mixer.hung
    assert send(mixer, OscMessage("/xinfo"), now=1.0) == []
    mixer.recover()
    assert send(mixer, OscMessage("/xinfo"), now=1.0) != []


def test_a_dropping_mixer_answers_nothing_and_counts_queries() -> None:
    mixer = SimulatedXr18()
    send(mixer, OscMessage("/ch/03/mix/04/level"))
    mixer.drop_packets = True

    assert send(mixer, OscMessage("/xinfo")) == []
    assert mixer.queries_received("/ch/03") == ["/ch/03/mix/04/level"]
