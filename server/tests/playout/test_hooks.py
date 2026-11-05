"""Tests for the on-air webhook (playout/hooks.py).

The POSTs go to an httpx2 mock transport that records them, so no overlay
machine (or network) is needed.
"""

import json
import logging
from collections.abc import Iterator
from typing import Any

import httpx2
import pytest

from baja_studio.playout.hooks import (
    HOOK_CLIP_FIELDS,
    WebhookNotifier,
    off_air_body,
    on_air_body,
)
from tests.conftest import FakeClock

URL = "http://overlay.test:3000/playout"
CLIP: dict[str, Any] = {
    "id": 3,
    "title": "Enduro · carro 42",
    "source": "orchestrator",
    "prova": "Enduro",
    "car_number": 42,
    "team": "Cerrado",
    "passagem": 3,
    "show_pass": True,
    "duration_s": 12.5,
    "file_path": "D:/clips/enduro/42/3.mp4",
    "notes": "operator only",
}


class Overlay:
    """A pretend overlay server: records POSTs and answers with `status`."""

    def __init__(self) -> None:
        self.bodies: list[dict[str, Any]] = []
        self.status = 200
        self.refuse_connections = False

    def handle(self, request: httpx2.Request) -> httpx2.Response:
        if self.refuse_connections:
            raise httpx2.ConnectError("connection refused", request=request)
        self.bodies.append(json.loads(request.content))
        return httpx2.Response(self.status)


@pytest.fixture
def overlay() -> Overlay:
    return Overlay()


@pytest.fixture
def client(overlay: Overlay) -> Iterator[httpx2.Client]:
    """The HTTP client the hooks worker would use, routed to the pretend overlay."""
    with httpx2.Client(transport=httpx2.MockTransport(overlay.handle)) as http_client:
        yield http_client


def test_the_bodies_carry_only_the_overlay_fields_and_the_time() -> None:
    # The file path and operator notes stay inside the studio.
    started = on_air_body(CLIP, "2026-11-07T14:05:00+00:00")
    ended = off_air_body(CLIP, "2026-11-07T14:05:12+00:00")

    assert set(started) == {"clip", "started_at"}
    assert set(ended) == {"clip", "ended_at"}
    assert tuple(started["clip"]) == HOOK_CLIP_FIELDS
    assert started["clip"]["car_number"] == 42
    assert "file_path" not in started["clip"]


def test_queued_notifications_are_posted_in_order(
    fake_clock: FakeClock, overlay: Overlay, client: httpx2.Client
) -> None:
    # The notifier wakes its worker on every notify; count those nudges.
    wakes: list[bool] = []
    notifier = WebhookNotifier(fake_clock, logging.getLogger("test.hooks"))
    notifier.wake_worker = lambda: wakes.append(True)

    notifier.notify(URL, on_air_body(CLIP, "t1"))
    notifier.notify(URL, off_air_body(CLIP, "t2"))
    notifier.send_pending(client)

    assert [body.get("started_at") or body.get("ended_at") for body in overlay.bodies] == [
        "t1",
        "t2",
    ]
    assert len(wakes) == 2
    # Sent once only: the next round has nothing left.
    notifier.send_pending(client)
    assert len(overlay.bodies) == 2


def test_without_a_url_nothing_is_queued(
    fake_clock: FakeClock, overlay: Overlay, client: httpx2.Client
) -> None:
    notifier = WebhookNotifier(fake_clock, logging.getLogger("test.hooks"))

    notifier.notify(None, on_air_body(CLIP, "t1"))
    notifier.notify("", on_air_body(CLIP, "t1"))
    notifier.send_pending(client)

    assert overlay.bodies == []


def test_failures_are_logged_at_most_once_a_minute(
    fake_clock: FakeClock,
    overlay: Overlay,
    client: httpx2.Client,
    caplog: pytest.LogCaptureFixture,
) -> None:
    # The overlay machine is off: every POST fails.
    overlay.refuse_connections = True
    notifier = WebhookNotifier(fake_clock, logging.getLogger("test.hooks"))

    def fail_once() -> None:
        notifier.notify(URL, on_air_body(CLIP, "t"))
        notifier.send_pending(client)

    with caplog.at_level(logging.WARNING, logger="test.hooks"):
        # Three failures within a minute make one log line...
        fail_once()
        fake_clock.advance(30)
        fail_once()
        fail_once()
        assert len(caplog.records) == 1
        # ...and a minute after that line, the next failure is logged again.
        fake_clock.advance(30)
        fail_once()
    assert len(caplog.records) == 2
    assert "on-air webhook failed" in caplog.records[0].getMessage()


def test_an_error_status_counts_as_a_failure(
    fake_clock: FakeClock,
    overlay: Overlay,
    client: httpx2.Client,
    caplog: pytest.LogCaptureFixture,
) -> None:
    overlay.status = 500
    notifier = WebhookNotifier(fake_clock, logging.getLogger("test.hooks"))

    with caplog.at_level(logging.WARNING, logger="test.hooks"):
        notifier.notify(URL, on_air_body(CLIP, "t"))
        notifier.send_pending(client)

    assert "answered 500" in caplog.records[0].getMessage()
