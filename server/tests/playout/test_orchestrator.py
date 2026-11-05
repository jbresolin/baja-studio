"""Tests for the orchestrator manifest client (playout/orchestrator.py).

The client talks to the fake orchestrator through an httpx2 "mock
transport": requests go straight to the fake's handler, with no network.
"""

from collections.abc import Callable, Iterator
from datetime import UTC, datetime
from typing import Any

import httpx2
import pytest

from baja_studio.fakes.fake_orchestrator import FakeOrchestrator
from baja_studio.playout.orchestrator import OrchestratorClient, OrchestratorError

BASE_URL = "http://orchestrator.test:8080"


@pytest.fixture
def make_client() -> Iterator[Callable[..., OrchestratorClient]]:
    """Build clients and close them all after the test (warnings are errors)."""
    clients: list[OrchestratorClient] = []

    def build(token: str, transport: httpx2.BaseTransport) -> OrchestratorClient:
        client = OrchestratorClient(BASE_URL, token, transport)
        clients.append(client)
        return client

    yield build
    for client in clients:
        client.close()


def answering(body: Any, status: int = 200) -> httpx2.MockTransport:
    """A transport whose server always answers `body` (JSON or raw text)."""

    def handle(request: httpx2.Request) -> httpx2.Response:
        if isinstance(body, str):
            return httpx2.Response(status, text=body)
        return httpx2.Response(status, json=body)

    return httpx2.MockTransport(handle)


def test_the_manifest_is_fetched_with_the_studio_bearer_token(
    make_client: Callable[..., OrchestratorClient],
) -> None:
    # The fake has three sample deliveries and only accepts "studio-token".
    fake = FakeOrchestrator.with_samples(token="studio-token")
    client = make_client("studio-token", fake.transport())

    manifest = client.fetch_manifest()

    # All three come back, newest first, with the pipeline's relative paths.
    assert [clip.relative_path for clip in manifest.clips] == [
        "aceleracao/13/2.mp4",
        "suspensao/7/1.mp4",
        "enduro/42/3.mp4",
    ]
    assert manifest.clips[2].team_name == "Equipe Cerrado"
    assert fake.requests == ["GET /manifest"]


def test_since_asks_only_for_newer_deliveries(
    make_client: Callable[..., OrchestratorClient],
) -> None:
    # Samples are delivered at 12:00, 12:01 and 12:02; ask for after 12:00:30.
    fake = FakeOrchestrator.with_samples()
    client = make_client("", fake.transport())

    manifest = client.fetch_manifest(since=datetime(2026, 11, 1, 12, 0, 30, tzinfo=UTC))

    assert [clip.car_number for clip in manifest.clips] == [13, 7]


def test_a_refused_token_says_to_check_the_studio_token(
    make_client: Callable[..., OrchestratorClient],
) -> None:
    fake = FakeOrchestrator.with_samples(token="studio-token")
    client = make_client("wrong", fake.transport())

    with pytest.raises(OrchestratorError, match="refused our token"):
        client.fetch_manifest()


def test_an_unreachable_orchestrator_is_an_orchestrator_error(
    make_client: Callable[..., OrchestratorClient],
) -> None:
    # The fake refuses connections, like a VPS out of reach.
    fake = FakeOrchestrator.with_samples()
    fake.available = False
    client = make_client("", fake.transport())

    with pytest.raises(OrchestratorError, match="cannot reach the orchestrator"):
        client.fetch_manifest()


def test_another_error_status_is_reported_with_its_code(
    make_client: Callable[..., OrchestratorClient],
) -> None:
    client = make_client("", answering({"error": "boom"}, status=500))

    with pytest.raises(OrchestratorError, match="answered 500"):
        client.fetch_manifest()


def test_a_body_that_is_not_a_manifest_is_an_orchestrator_error(
    make_client: Callable[..., OrchestratorClient],
) -> None:
    # A proxy error page instead of JSON.
    client = make_client("", answering("<html>bad gateway</html>"))

    with pytest.raises(OrchestratorError, match="unexpected manifest"):
        client.fetch_manifest()


def test_fields_a_newer_pipeline_adds_are_ignored(
    make_client: Callable[..., OrchestratorClient],
) -> None:
    # A future pipeline release adds "frame_rate" to entries and "version" to
    # the response; the studio must keep syncing.
    entry = FakeOrchestrator.with_samples().manifest(None, None)["clips"][0]
    body = {
        "clips": [{**entry, "frame_rate": 59.94}],
        "generated_at": "2026-11-01T12:00:00Z",
        "version": 2,
    }
    client = make_client("", answering(body))

    manifest = client.fetch_manifest()

    assert manifest.clips[0].car_number == 13
    assert manifest.clips[0].as_manifest_clip().relative_path == "aceleracao/13/2.mp4"
