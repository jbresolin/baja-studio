"""Tests for what every agent server shares (agents/server.py): the token rule, profiles, the hub.

The fake agent and the camera node both serve the agent contract with these
pieces; the conformance suite checks them end to end, these pin the rules.
"""

import asyncio
import threading

import pytest

from baja_studio.agents.contract import ErrorCode
from baja_studio.agents.server import (
    MAX_RESUME_AFTER_MS,
    CameraCommandError,
    StateHub,
    checked_profile,
    token_matches,
    token_refusal,
)

TOKEN = "-".join(("shared", "test", "token"))


def test_without_a_token_every_request_goes_through() -> None:
    assert token_refusal("/agent/health", None, "") is None


def test_with_a_token_a_request_without_it_is_a_401_except_agent_info() -> None:
    refused = token_refusal("/agent/health", "wrong", TOKEN)

    assert refused is not None and refused.status_code == 401
    # The subnet scan reads /agent/info without knowing any token (contract §2.2).
    assert token_refusal("/agent/info", None, TOKEN) is None
    assert token_refusal("/agent/health", TOKEN, TOKEN) is None


def test_the_refusal_never_repeats_what_was_sent() -> None:
    refused = token_refusal("/cameras/cam1/stop", "a-guess", TOKEN)

    assert refused is not None
    assert b"a-guess" not in refused.body
    assert TOKEN.encode() not in refused.body


def test_tokens_are_compared_whole() -> None:
    assert token_matches(TOKEN, TOKEN)
    assert not token_matches(TOKEN[:-1], TOKEN)
    assert not token_matches(None, TOKEN)


def test_a_profile_keeps_its_keys_and_checks_resume_after_ms() -> None:
    assert checked_profile({"resume_after_ms": 0, "zone": "finish"}) == {
        "resume_after_ms": 0,
        "zone": "finish",
    }
    for bad in (-1, MAX_RESUME_AFTER_MS + 1, 1.5, True):
        with pytest.raises(CameraCommandError) as refused:
            checked_profile({"resume_after_ms": bad})
        assert refused.value.code == ErrorCode.BAD_REQUEST


def test_a_message_broadcast_from_another_thread_reaches_every_socket() -> None:
    # The node's push worker is a plain thread; the WebSockets live on the web
    # server's event loop. Here a test loop plays the server.
    hub = StateHub()

    async def server() -> list[object]:
        hub.loop = asyncio.get_running_loop()
        first, second = hub.add_subscriber(), hub.add_subscriber()
        sender = threading.Thread(target=hub.broadcast_from_thread, args=({"type": "state"},))
        sender.start()
        received: list[object] = [await asyncio.wait_for(q.get(), 5) for q in (first, second)]
        sender.join()
        return received

    assert asyncio.run(server()) == [{"type": "state"}, {"type": "state"}]


def test_before_any_socket_a_broadcast_from_a_thread_goes_nowhere_quietly() -> None:
    hub = StateHub()

    hub.broadcast_from_thread({"type": "state"})  # no loop yet: nothing to do, no error
    hub.close_all_from_thread()

    assert hub.subscriber_count == 0
