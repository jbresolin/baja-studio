"""Tests for the obs-websocket v5 wire format (adapters/obs_protocol.py).

These are pure functions: no sockets, no threads. They check that the
client and the fake build exactly the messages the protocol document
describes.
"""

import base64
import hashlib
import json

import pytest

from baja_studio.adapters.obs_protocol import (
    RPC_VERSION,
    EventSubscription,
    OpCode,
    authentication_string,
    decode,
    encode,
    identify_message,
    request_message,
)


def test_the_authentication_string_follows_the_documented_two_step_hash() -> None:
    # The protocol says: secret = base64(sha256(password + salt)), then
    # auth = base64(sha256(secret + challenge)). We compute it by hand here
    # and expect the helper to agree.
    secret = base64.b64encode(hashlib.sha256(b"supersecret" + b"lM1GncleQOaCu9lT").digest())
    expected = base64.b64encode(hashlib.sha256(secret + b"+IxH4CnCiqpX1rM9").digest()).decode()

    assert authentication_string("supersecret", "lM1GncleQOaCu9lT", "+IxH4CnCiqpX1rM9") == expected


def test_a_different_password_gives_a_different_authentication_string() -> None:
    # A wrong password must not happen to match the right one.
    right = authentication_string("right", "salt", "challenge")
    wrong = authentication_string("wrong", "salt", "challenge")

    assert right != wrong


def test_encode_wraps_the_data_with_its_op_code() -> None:
    text = encode(OpCode.REQUEST, {"requestType": "GetVersion"})

    assert json.loads(text) == {"op": 6, "d": {"requestType": "GetVersion"}}


def test_decode_splits_a_message_into_op_and_data() -> None:
    op, data = decode('{"op": 2, "d": {"negotiatedRpcVersion": 1}}')

    assert op == OpCode.IDENTIFIED
    assert data == {"negotiatedRpcVersion": 1}


@pytest.mark.parametrize(
    "text",
    ['["not", "an", "object"]', '{"op": 0}', '{"d": {}}', '{"op": "zero", "d": {}}'],
)
def test_decode_refuses_text_that_is_not_an_obs_message(text: str) -> None:
    # Missing "d", missing or non-integer "op": the client must notice, not guess.
    with pytest.raises(ValueError):
        decode(text)


def test_identify_without_authentication_has_no_authentication_field() -> None:
    # OBS without a password: the Identify must not carry an empty password.
    op, data = decode(identify_message(None, EventSubscription.ALL))

    assert op == OpCode.IDENTIFY
    assert data == {"rpcVersion": RPC_VERSION, "eventSubscriptions": EventSubscription.ALL}


def test_identify_with_authentication_carries_it() -> None:
    _, data = decode(identify_message("abc=", EventSubscription.SCENES))

    assert data["authentication"] == "abc="
    assert data["eventSubscriptions"] == EventSubscription.SCENES


def test_a_request_without_data_leaves_out_request_data() -> None:
    _, data = decode(request_message("GetVersion", "7", {}))

    assert data == {"requestType": "GetVersion", "requestId": "7"}


def test_a_request_with_data_includes_it() -> None:
    _, data = decode(request_message("SetCurrentProgramScene", "8", {"sceneName": "Main"}))

    assert data["requestData"] == {"sceneName": "Main"}


def test_all_event_subscriptions_cover_every_low_volume_category() -> None:
    # ALL is every category bit from General (1) to Ui (1024), nothing more.
    assert int(EventSubscription.ALL) == 2047
    assert EventSubscription.ALL & EventSubscription.MEDIA_INPUTS
