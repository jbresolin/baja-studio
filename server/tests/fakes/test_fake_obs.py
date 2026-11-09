"""Tests for the fake OBS server's protocol core (fakes/fake_obs.py).

We talk to `FakeObs` the way a transport would: open a session with two
callbacks (one collects the messages it sends us, one records a close) and
hand it raw JSON. That checks the fake on its own, without the client.
"""

from dataclasses import dataclass, field
from typing import Any

import pytest

from baja_studio.adapters.obs_protocol import (
    EventSubscription,
    OpCode,
    RequestStatus,
    authentication_string,
    decode,
    identify_message,
    request_message,
)
from baja_studio.fakes.fake_obs import FIXED_CHALLENGE, FIXED_SALT, FakeObs, FakeObsSession
from tests.conftest import FakeClock


@dataclass
class Peer:
    """One fake client: what the fake sent it, and whether it was hung up on."""

    received: list[tuple[int, dict[str, Any]]] = field(default_factory=list)
    closed: tuple[int, str] | None = None
    session: FakeObsSession | None = None

    def deliver(self, text: str) -> None:
        self.received.append(decode(text))

    def close(self, code: int, reason: str) -> None:
        self.closed = (code, reason)

    def events(self) -> list[str]:
        return [str(data["eventType"]) for op, data in self.received if op == OpCode.EVENT]

    def last_response(self) -> dict[str, Any]:
        responses = [data for op, data in self.received if op == OpCode.REQUEST_RESPONSE]
        return responses[-1]


def connect(fake: FakeObs, subscriptions: int = EventSubscription.ALL) -> Peer:
    """Open a session and identify (with the right password if the fake has one)."""
    peer = Peer()
    peer.session = fake.open_session(peer.deliver, peer.close)
    auth = (
        authentication_string(fake.password, FIXED_SALT, FIXED_CHALLENGE) if fake.password else None
    )
    fake.receive(peer.session, identify_message(auth, subscriptions))
    return peer


def ask(fake: FakeObs, peer: Peer, request_type: str, **data: Any) -> dict[str, Any]:
    """Send one request and return the fake's response message."""
    assert peer.session is not None
    fake.receive(peer.session, request_message(request_type, "1", data))
    return peer.last_response()


@pytest.fixture
def fake(fake_clock: FakeClock) -> FakeObs:
    """The playout scenes; files play for 10 s."""
    return FakeObs.with_playout_scenes(fake_clock)


def test_a_new_session_is_greeted_with_hello(fake: FakeObs) -> None:
    peer = Peer()

    fake.open_session(peer.deliver, peer.close)

    op, data = peer.received[0]
    assert op == OpCode.HELLO
    assert "authentication" not in data  # no password set


def test_hello_asks_for_authentication_when_a_password_is_set(fake_clock: FakeClock) -> None:
    peer = Peer()

    FakeObs(fake_clock, password="pw").open_session(peer.deliver, peer.close)

    assert peer.received[0][1]["authentication"] == {
        "challenge": FIXED_CHALLENGE,
        "salt": FIXED_SALT,
    }


def test_identify_is_answered_with_identified(fake: FakeObs) -> None:
    peer = connect(fake)

    assert peer.received[-1][0] == OpCode.IDENTIFIED
    assert peer.session is not None and peer.session.identified


def test_a_wrong_password_closes_with_code_4009(fake_clock: FakeClock) -> None:
    # 4009 is the code real OBS uses; the client relies on it to say "password".
    fake = FakeObs(fake_clock, password="pw")
    peer = Peer()
    session = fake.open_session(peer.deliver, peer.close)

    fake.receive(session, identify_message("wrong", EventSubscription.ALL))

    assert peer.closed is not None and peer.closed[0] == 4009
    assert fake.session_count == 0


def test_requests_before_identifying_are_ignored(fake: FakeObs) -> None:
    peer = Peer()
    session = fake.open_session(peer.deliver, peer.close)

    fake.receive(session, request_message("GetVersion", "1", {}))

    assert [op for op, _ in peer.received] == [OpCode.HELLO]


def test_an_unknown_request_answers_status_204(fake: FakeObs) -> None:
    response = ask(fake, connect(fake), "MakeCoffee")

    assert response["requestStatus"]["result"] is False
    assert response["requestStatus"]["code"] == RequestStatus.UNKNOWN_REQUEST_TYPE


def test_a_missing_scene_answers_status_600(fake: FakeObs) -> None:
    response = ask(fake, connect(fake), "SetCurrentProgramScene", sceneName="Nope")

    assert response["requestStatus"]["code"] == RequestStatus.RESOURCE_NOT_FOUND


def test_a_successful_request_is_logged_with_its_data(fake: FakeObs) -> None:
    # Tests of playout read this log to check, say, that a take is one scene change.
    ask(fake, connect(fake), "SetCurrentProgramScene", sceneName="Playback A")

    assert [record.data for record in fake.requests_of("SetCurrentProgramScene")] == [
        {"sceneName": "Playback A"}
    ]
    assert fake.program_history == ["Playback A"]


def test_a_refused_request_is_not_logged(fake: FakeObs) -> None:
    ask(fake, connect(fake), "SetCurrentProgramScene", sceneName="Nope")

    assert fake.requests_of("SetCurrentProgramScene") == []


def test_events_go_only_to_subscribed_sessions(fake: FakeObs) -> None:
    # One client wants scene events, the other only media events.
    scenes_only = connect(fake, EventSubscription.SCENES)
    media_only = connect(fake, EventSubscription.MEDIA_INPUTS)

    fake.change_program_scene("Playback B")

    assert scenes_only.events() == ["CurrentProgramSceneChanged"]
    assert media_only.events() == []


def test_a_loaded_file_opens_and_starts_on_the_next_tick_like_real_obs(fake: FakeObs) -> None:
    # Real OBS opens a new file in the background and reports the start as an
    # event; until then the source is "opening".
    peer = connect(fake)

    ask(
        fake,
        peer,
        "SetInputSettings",
        inputName="baja_program_a",
        inputSettings={"local_file": "x.mp4"},
    )
    assert fake.media_of("baja_program_a").state == "OBS_MEDIA_STATE_OPENING"
    assert peer.events() == []

    fake.tick()

    assert fake.media_of("baja_program_a").state == "OBS_MEDIA_STATE_PLAYING"
    assert peer.events() == ["MediaInputPlaybackStarted"]


def test_seeking_a_source_that_has_not_started_is_refused_with_obs_words(fake: FakeObs) -> None:
    # The bench's "media must be playing or paused" (2026-11-08), reproduced.
    peer = connect(fake)
    fake.stuck_files.add("x.mp4")  # it stays "opening"
    ask(
        fake,
        peer,
        "SetInputSettings",
        inputName="baja_program_a",
        inputSettings={"local_file": "x.mp4"},
    )

    status = ask(fake, peer, "SetMediaInputCursor", inputName="baja_program_a", mediaCursor=0)[
        "requestStatus"
    ]

    assert status["result"] is False
    assert status["code"] == RequestStatus.INVALID_RESOURCE_STATE
    assert "must be playing or paused" in status["comment"]


def test_setting_the_same_file_again_does_not_restart_it(fake: FakeObs) -> None:
    peer = connect(fake)
    settings = {"local_file": "x.mp4"}
    ask(fake, peer, "SetInputSettings", inputName="baja_program_a", inputSettings=settings)
    fake.tick()

    ask(fake, peer, "SetInputSettings", inputName="baja_program_a", inputSettings=settings)
    fake.tick()

    assert peer.events() == ["MediaInputPlaybackStarted"]


def test_tick_sends_playback_ended_once_when_a_file_finishes(
    fake: FakeObs, fake_clock: FakeClock
) -> None:
    # The file plays 10 s. Ticking at 9 s says nothing; at 10 s one event; later, none.
    peer = connect(fake)
    ask(
        fake,
        peer,
        "SetInputSettings",
        inputName="baja_program_a",
        inputSettings={"local_file": "x.mp4"},
    )
    fake.tick()  # opened: it plays from now

    fake_clock.advance(9.0)
    fake.tick()
    assert "MediaInputPlaybackEnded" not in peer.events()
    fake_clock.advance(1.0)
    fake.tick()
    fake_clock.advance(5.0)
    fake.tick()

    assert peer.events().count("MediaInputPlaybackEnded") == 1


def test_durations_come_from_the_injected_function(fake_clock: FakeClock) -> None:
    fake = FakeObs.with_playout_scenes(fake_clock, duration_for=lambda file: 42.0)
    peer = connect(fake)
    ask(
        fake,
        peer,
        "SetInputSettings",
        inputName="baja_program_a",
        inputSettings={"local_file": "x.mp4"},
    )

    response = ask(fake, peer, "GetMediaInputStatus", inputName="baja_program_a")

    assert response["responseData"]["mediaDuration"] == 42000


def test_disconnect_all_closes_every_session(fake: FakeObs) -> None:
    first, second = connect(fake), connect(fake)

    fake.disconnect_all()

    assert first.closed is not None and second.closed is not None
    assert fake.session_count == 0


def test_a_closed_session_gets_no_more_events(fake: FakeObs) -> None:
    peer = connect(fake)
    assert peer.session is not None

    fake.close_session(peer.session)
    fake.change_program_scene("Playback A")

    assert peer.events() == []


def test_with_playout_scenes_puts_the_first_other_scene_on_program(fake: FakeObs) -> None:
    assert fake.program_scene == "Main"
    assert set(fake.scenes) == {"Main", "Playback A", "Playback B"}
    assert [item.source_name for item in fake.scenes["Playback A"]] == ["baja_program_a"]
