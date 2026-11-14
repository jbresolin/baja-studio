"""Tests for the obs-websocket client (adapters/obs_websocket.py).

Almost every test talks to the fake OBS (`fakes/fake_obs.py`) through the
in-process network (`fakes/fake_obs_in_process.py`): the client believes it
has a WebSocket, but messages travel over a queue in this process, so no
port is opened. The fake clock decides when a timeout has run out.
"""

import queue
import threading
from collections.abc import Callable, Iterator
from typing import Any

import pytest

from baja_studio.adapters.obs_protocol import EventSubscription, OpCode, decode, encode
from baja_studio.adapters.obs_transport import (
    ObsAddress,
    ObsAuthenticationError,
    ObsConnectionClosedError,
    ObsNotConnectedError,
    ObsRequestFailedError,
    ObsRequestTimeoutError,
    ObsTransport,
    ObsUnreachableError,
)
from baja_studio.adapters.obs_websocket import ObsWebsocketClient, ReconnectBackoff
from baja_studio.fakes.fake_obs import FakeObs
from baja_studio.fakes.fake_obs_in_process import InProcessObsNetwork
from tests.conftest import FakeClock, eventually

HOST, PORT = "obs-pc", 4455
ADDRESS = ObsAddress(HOST, PORT)


@pytest.fixture
def network() -> InProcessObsNetwork:
    """An empty in-process network: no OBS anywhere until a test plugs one in."""
    return InProcessObsNetwork()


@pytest.fixture
def fake_obs(fake_clock: FakeClock, network: InProcessObsNetwork) -> FakeObs:
    """A fake OBS with the playout scenes, answering at obs-pc:4455, no password."""
    fake = FakeObs.with_playout_scenes(fake_clock)
    network.plug(HOST, PORT, fake)
    return fake


@pytest.fixture
def make_client(
    fake_clock: FakeClock, network: InProcessObsNetwork
) -> Iterator[Callable[..., ObsWebsocketClient]]:
    """Builds clients on the in-process network and closes them all after the test."""
    clients: list[ObsWebsocketClient] = []

    def build(**options: Any) -> ObsWebsocketClient:
        client = ObsWebsocketClient(fake_clock, network.connect, **options)
        clients.append(client)
        return client

    yield build
    for client in clients:
        client.close()


@pytest.fixture
def client(fake_obs: FakeObs, make_client: Callable[..., ObsWebsocketClient]) -> ObsWebsocketClient:
    """A client already connected to the fake OBS."""
    connected = make_client()
    connected.connect(ADDRESS, "")
    return connected


class SilentTransport:
    """A stand-in OBS that greets and identifies, then never answers a request.

    With `send_hello=False` it does not even say Hello, so the handshake hangs.
    """

    def __init__(self, send_hello: bool = True) -> None:
        self._incoming: queue.Queue[str | None] = queue.Queue()
        if send_hello:
            self._incoming.put(encode(OpCode.HELLO, {"rpcVersion": 1}))

    def send(self, text: str) -> None:
        op, _ = decode(text)
        if op == OpCode.IDENTIFY:
            self._incoming.put(encode(OpCode.IDENTIFIED, {"negotiatedRpcVersion": 1}))

    def receive(self) -> str:
        item = self._incoming.get()
        if item is None:
            self._incoming.put(None)
            raise ObsConnectionClosedError(1000, "closed")
        return item

    def close(self) -> None:
        self._incoming.put(None)


def run_in_thread(function: Callable[[], Any]) -> tuple[threading.Thread, dict[str, Any]]:
    """Run `function` on its own thread; the outcome lands in the returned dict."""
    outcome: dict[str, Any] = {}

    def target() -> None:
        try:
            outcome["result"] = function()
        except Exception as error:
            outcome["error"] = error

    thread = threading.Thread(target=target)
    thread.start()
    return thread, outcome


def test_connecting_without_a_password_identifies_with_obs(client: ObsWebsocketClient) -> None:
    # The fixture connected; the client should say it is connected and requests should work.
    assert client.is_connected
    assert client.get_version()["rpcVersion"] == 1


def test_connecting_with_the_right_password_identifies(
    fake_clock: FakeClock,
    network: InProcessObsNetwork,
    make_client: Callable[..., ObsWebsocketClient],
) -> None:
    # OBS with a password sends a challenge in its Hello; the client answers it.
    network.plug(HOST, PORT, FakeObs.with_playout_scenes(fake_clock, password="hunter2"))
    client = make_client()

    client.connect(ADDRESS, "hunter2")

    assert client.is_connected


def test_a_wrong_password_is_an_authentication_error(
    fake_clock: FakeClock,
    network: InProcessObsNetwork,
    make_client: Callable[..., ObsWebsocketClient],
) -> None:
    # OBS closes the socket with code 4009; the client must say "password", not "unreachable".
    network.plug(HOST, PORT, FakeObs.with_playout_scenes(fake_clock, password="hunter2"))
    client = make_client()

    with pytest.raises(ObsAuthenticationError, match="refused the password"):
        client.connect(ADDRESS, "wrong")
    assert not client.is_connected


def test_a_missing_password_is_an_authentication_error(
    fake_clock: FakeClock,
    network: InProcessObsNetwork,
    make_client: Callable[..., ObsWebsocketClient],
) -> None:
    # OBS wants a password and none is configured: say so before even trying.
    network.plug(HOST, PORT, FakeObs.with_playout_scenes(fake_clock, password="hunter2"))
    client = make_client()

    with pytest.raises(ObsAuthenticationError, match="asks for a password"):
        client.connect(ADDRESS, "")


def test_no_obs_at_the_address_is_unreachable(
    network: InProcessObsNetwork, make_client: Callable[..., ObsWebsocketClient]
) -> None:
    # Nothing is plugged in at obs-pc:4455, like OBS not running.
    client = make_client()

    with pytest.raises(ObsUnreachableError):
        client.connect(ADDRESS, "")


def test_a_handshake_that_never_finishes_gives_up_after_the_connect_timeout(
    fake_clock: FakeClock,
) -> None:
    # Something accepts the connection but never says Hello. connect() waits on the
    # fake clock; once three fake seconds pass it must give up as "unreachable".
    client = ObsWebsocketClient(fake_clock, lambda address, timeout: SilentTransport(False))
    thread, outcome = run_in_thread(lambda: client.connect(ADDRESS, ""))
    eventually(lambda: fake_clock.sleeping_count == 1, "connect never started waiting")

    fake_clock.advance(3.0)
    thread.join(5.0)

    assert isinstance(outcome.get("error"), ObsUnreachableError)
    client.close()


def test_a_request_returns_obs_response_data(client: ObsWebsocketClient) -> None:
    assert client.get_current_program_scene() == "Main"


def test_a_refused_request_raises_with_obs_status(client: ObsWebsocketClient) -> None:
    # The fake answers code 600 (resource not found) for a scene that does not exist.
    with pytest.raises(ObsRequestFailedError) as caught:
        client.set_current_program_scene("No such scene")

    assert caught.value.code == 600


def test_concurrent_requests_each_get_their_own_answer(
    client: ObsWebsocketClient, fake_obs: FakeObs
) -> None:
    # Two threads ask about two different sources many times at once. Responses
    # are matched by requestId, so each thread must only ever see its own source's file.
    client.set_input_settings("baja_program_a", {"local_file": "a.mp4"})
    client.set_input_settings("baja_program_b", {"local_file": "b.mp4"})

    def ask_many(source: str) -> set[str]:
        return {str(client.get_input_settings(source).get("local_file")) for _ in range(50)}

    thread_a, outcome_a = run_in_thread(lambda: ask_many("baja_program_a"))
    thread_b, outcome_b = run_in_thread(lambda: ask_many("baja_program_b"))
    thread_a.join(5.0)
    thread_b.join(5.0)

    assert outcome_a == {"result": {"a.mp4"}}
    assert outcome_b == {"result": {"b.mp4"}}


def test_a_request_obs_never_answers_times_out_on_the_fake_clock(fake_clock: FakeClock) -> None:
    # The silent OBS identifies us but ignores requests. The request waits two
    # fake seconds (the default request timeout) and then raises a timeout.
    client = ObsWebsocketClient(fake_clock, lambda address, timeout: SilentTransport())
    client.connect(ADDRESS, "")
    thread, outcome = run_in_thread(client.get_version)
    eventually(lambda: fake_clock.sleeping_count == 1, "the request never started waiting")

    fake_clock.advance(1.9)
    assert thread.is_alive(), "gave up before the two seconds were over"
    fake_clock.advance(0.1)
    thread.join(5.0)

    assert isinstance(outcome.get("error"), ObsRequestTimeoutError)
    client.close()


def test_requests_in_flight_counts_requests_still_waiting_for_obs(fake_clock: FakeClock) -> None:
    # Tests on a fake clock move time only when nothing waits for OBS (a
    # request waits on that same clock and would time out). So the client
    # says how many requests are sent and not yet answered.
    client = ObsWebsocketClient(fake_clock, lambda address, timeout: SilentTransport())
    client.connect(ADDRESS, "")
    assert client.requests_in_flight == 0

    thread, _ = run_in_thread(client.get_version)
    eventually(lambda: client.requests_in_flight == 1, "the request never went out")

    # Timed out (OBS never answers), it is no longer in flight.
    fake_clock.advance(2.0)
    thread.join(5.0)
    assert client.requests_in_flight == 0
    client.close()


def test_events_reach_every_listener(client: ObsWebsocketClient, fake_obs: FakeObs) -> None:
    # Two listeners are registered; the fake changes the program scene, which
    # OBS announces with a CurrentProgramSceneChanged event.
    first: list[tuple[str, dict[str, Any]]] = []
    second: list[tuple[str, dict[str, Any]]] = []
    client.add_event_listener(lambda kind, data: first.append((kind, data)))
    client.add_event_listener(lambda kind, data: second.append((kind, data)))

    fake_obs.change_program_scene("Playback A")

    expected = ("CurrentProgramSceneChanged", {"sceneName": "Playback A"})
    eventually(lambda: expected in first and expected in second, "an event did not arrive")


def test_a_failing_listener_does_not_stop_the_others(
    client: ObsWebsocketClient, fake_obs: FakeObs
) -> None:
    # A buggy listener raises; the next one must still be called.
    received: list[str] = []

    def broken(kind: str, data: dict[str, Any]) -> None:
        raise RuntimeError("listener bug")

    client.add_event_listener(broken)
    client.add_event_listener(lambda kind, data: received.append(kind))

    fake_obs.change_program_scene("Playback B")

    eventually(lambda: "CurrentProgramSceneChanged" in received)


def test_a_listener_cannot_make_requests(client: ObsWebsocketClient, fake_obs: FakeObs) -> None:
    # A request from the reader thread would wait for the reader itself; it is refused.
    errors: list[Exception] = []

    def greedy(kind: str, data: dict[str, Any]) -> None:
        try:
            client.get_version()
        except RuntimeError as error:
            errors.append(error)

    client.add_event_listener(greedy)
    fake_obs.change_program_scene("Playback A")

    eventually(lambda: len(errors) == 1, "the listener's request was not refused")


def test_only_subscribed_event_categories_arrive(
    fake_obs: FakeObs, make_client: Callable[..., ObsWebsocketClient]
) -> None:
    # This client subscribes to media events only, so a scene change must not reach
    # it, while a media start (loading a file) does.
    client = make_client(event_subscriptions=EventSubscription.MEDIA_INPUTS)
    client.connect(ADDRESS, "")
    received: list[str] = []
    client.add_event_listener(lambda kind, data: received.append(kind))

    fake_obs.change_program_scene("Playback A")
    client.set_input_settings("baja_program_a", {"local_file": "clip.mp4"})
    fake_obs.tick()  # the file finished opening: OBS reports the start

    eventually(lambda: "MediaInputPlaybackStarted" in received)
    assert "CurrentProgramSceneChanged" not in received


def test_an_expected_event_is_signalled_only_while_expected_and_only_if_it_matches(
    client: ObsWebsocketClient, fake_obs: FakeObs
) -> None:
    # Playout waits for "this source started" this way: it enters
    # `expecting_event` before the request that causes it.
    with client.expecting_event("MediaInputPlaybackStarted", inputName="baja_program_b") as seen:
        # A start of the *other* source does not count.
        client.set_input_settings("baja_program_a", {"local_file": "a.mp4"})
        fake_obs.tick()
        client.get_version()  # a round trip, so the event has surely been read
        assert not seen.arrived.is_set()

        client.set_input_settings("baja_program_b", {"local_file": "b.mp4"})
        fake_obs.tick()
        eventually(seen.arrived.is_set, "the expected start was not signalled")

    # Once the block is left, nothing is listening any more (no leak).
    client.set_input_settings("baja_program_b", {"local_file": "c.mp4"})
    fake_obs.tick()
    client.get_version()
    assert client._expected_events == []


def test_an_event_expected_after_a_request_ignores_events_from_before_its_response(
    client: ObsWebsocketClient, fake_obs: FakeObs
) -> None:
    # A source was loaded earlier and its "started" is still on its way when
    # playout loads it again. Only a start read after the new load's
    # response may count, or playout would seek a file that has no frame.
    client.set_input_settings("baja_program_a", {"local_file": "old.mp4"})
    with client.expecting_event(
        "MediaInputPlaybackStarted", after_next_request=True, inputName="baja_program_a"
    ) as started:
        # The earlier open finishes and its event is sent: not armed yet.
        fake_obs.tick()
        client.get_version()  # a round trip, so the event has surely been read
        assert started.request_number is not None  # GetVersion armed it...
        assert not started.arrived.is_set()  # ...after the stale event was read

    with client.expecting_event(
        "MediaInputPlaybackStarted", after_next_request=True, inputName="baja_program_a"
    ) as started:
        client.set_input_settings("baja_program_a", {"local_file": "new.mp4"})
        fake_obs.tick()  # the new open finishes: this start counts
        eventually(started.arrived.is_set, "the start after the request was not signalled")


def test_request_numbers_increase_with_every_request(
    client: ObsWebsocketClient, fake_obs: FakeObs
) -> None:
    numbers: list[int] = []
    for _ in range(3):
        with client.expecting_event("X", after_next_request=True) as expected:
            client.get_version()
        assert expected.request_number is not None
        numbers.append(expected.request_number)

    assert numbers == sorted(numbers)
    assert len(set(numbers)) == 3


def test_the_disconnect_listener_hears_obs_going_away(
    client: ObsWebsocketClient, network: InProcessObsNetwork
) -> None:
    # Unplugging the fake is OBS quitting: every connection closes.
    reasons: list[str] = []
    client.add_disconnect_listener(reasons.append)

    network.unplug(HOST, PORT)

    eventually(lambda: len(reasons) == 1, "the disconnect was not reported")
    assert not client.is_connected


def test_closing_the_client_is_not_reported_as_a_disconnect(client: ObsWebsocketClient) -> None:
    # Hanging up ourselves is not news; the listener stays quiet.
    reasons: list[str] = []
    client.add_disconnect_listener(reasons.append)

    client.close()

    assert reasons == []
    assert not client.is_connected


def test_requests_after_obs_went_away_say_not_connected(
    client: ObsWebsocketClient, network: InProcessObsNetwork
) -> None:
    network.unplug(HOST, PORT)
    eventually(lambda: not client.is_connected)

    with pytest.raises(ObsNotConnectedError):
        client.get_version()


def test_requests_before_connecting_say_not_connected(
    make_client: Callable[..., ObsWebsocketClient],
) -> None:
    with pytest.raises(ObsNotConnectedError):
        make_client().get_version()


def test_the_client_connects_again_after_obs_restarts(
    fake_clock: FakeClock, client: ObsWebsocketClient, network: InProcessObsNetwork
) -> None:
    # OBS quits, then a fresh OBS starts at the same address. A new connect works
    # and talks to the new instance (whose program scene we changed to tell them apart).
    network.unplug(HOST, PORT)
    eventually(lambda: not client.is_connected)
    restarted = FakeObs.with_playout_scenes(fake_clock, other_scenes=("Intervalo",))
    network.plug(HOST, PORT, restarted)

    client.connect(ADDRESS, "")

    assert client.get_current_program_scene() == "Intervalo"


def test_connecting_again_replaces_the_previous_connection(
    client: ObsWebsocketClient, fake_obs: FakeObs
) -> None:
    # connect() on a connected client closes the old connection first, so the
    # fake ends up with exactly one session, not two.
    client.connect(ADDRESS, "")

    assert client.is_connected
    assert fake_obs.session_count == 1


def test_the_reconnect_backoff_grows_to_four_seconds_and_stays_there() -> None:
    backoff = ReconnectBackoff()

    delays = [backoff.next_delay() for _ in range(6)]

    assert delays == [0.5, 1.0, 2.0, 4.0, 4.0, 4.0]


def test_the_reconnect_backoff_starts_over_after_a_reset() -> None:
    backoff = ReconnectBackoff()
    backoff.next_delay()
    backoff.next_delay()

    backoff.reset()

    assert backoff.next_delay() == 0.5


def test_a_silent_transport_satisfies_the_transport_protocol() -> None:
    # Keeps the stub honest for mypy: it must be usable wherever a transport is.
    transport: ObsTransport = SilentTransport()
    transport.close()
