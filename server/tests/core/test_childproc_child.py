"""Tests for the loop a child process runs (core/childproc_child.py).

These need no child process at all: `run_child` is given in-memory text
streams instead of the real stdin and stdout.
"""

import io
import json
from typing import Any

from baja_studio.core.childproc_child import ChildChannel, run_child


def test_run_child_answers_pings_and_passes_other_requests_to_the_handler() -> None:
    # Instead of a real pipe we give run_child two in-memory text streams:
    # one with two requests already in it, and an empty one to write replies to.
    requests = io.StringIO(
        '{"id": 1, "method": "ping"}\n{"id": 2, "method": "double", "params": {"n": 21}}\n'
    )
    replies = io.StringIO()

    def handler(method: str, params: dict[str, Any]) -> Any:
        assert method == "double"
        return params["n"] * 2

    run_child(handler, stdin=requests, stdout=replies)

    lines = [json.loads(line) for line in replies.getvalue().splitlines()]
    assert lines == [{"id": 1, "result": "pong"}, {"id": 2, "result": 42}]


def test_run_child_turns_handler_exceptions_into_error_replies() -> None:
    requests = io.StringIO('{"id": 5, "method": "explode"}\n')
    replies = io.StringIO()

    def handler(method: str, params: dict[str, Any]) -> Any:
        raise KeyError("missing")

    run_child(handler, stdin=requests, stdout=replies)

    reply = json.loads(replies.getvalue())
    assert reply["id"] == 5
    assert "KeyError" in reply["error"]


def test_run_child_skips_blank_and_non_json_lines() -> None:
    requests = io.StringIO('\nnot json\n{"id": 1, "method": "ping"}\n')
    replies = io.StringIO()

    run_child(lambda method, params: None, stdin=requests, stdout=replies)

    assert json.loads(replies.getvalue()) == {"id": 1, "result": "pong"}


def test_events_sent_through_the_channel_share_the_output_with_replies() -> None:
    # A MIDI child sends events (a fader moved) between replies. We give the
    # child one channel, send an event through it, then run the loop with a
    # request: both lines must reach the same output, each whole.
    replies = io.StringIO()
    channel = ChildChannel(replies)
    channel.send_event("midi", {"bytes": [0x90, 0x10, 0x7F]})

    run_child(
        lambda method, params: "ok",
        stdin=io.StringIO('{"id": 3, "method": "x"}\n'),
        channel=channel,
    )

    lines = [json.loads(line) for line in replies.getvalue().splitlines()]
    assert lines == [
        {"event": "midi", "data": {"bytes": [0x90, 0x10, 0x7F]}},
        {"id": 3, "result": "ok"},
    ]
