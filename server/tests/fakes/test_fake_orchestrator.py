"""Tests for the fake pipeline orchestrator (fakes/fake_orchestrator.py).

It must answer `GET /manifest` in the pipeline's shape: newest first, only
the latest delivery per (prova, car, passagem), with the `since` and `prova`
filters and bearer-token auth. Tests call it through its httpx2 mock
transport, so no port is opened.
"""

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx2
import pytest

from baja_studio.fakes.fake_orchestrator import (
    SAMPLE_START,
    FakeOrchestrator,
    deliveries_from_folder,
    prova_slug,
)


def http_client(fake: FakeOrchestrator, token: str = "tok") -> httpx2.Client:
    """A client whose requests go straight to `fake`."""
    return httpx2.Client(
        base_url="http://orchestrator",
        transport=fake.transport(),
        headers={"Authorization": f"Bearer {token}"},
    )


@pytest.fixture
def client() -> Iterator[httpx2.Client]:
    """A client of the sample orchestrator (three clips), token "tok"."""
    with http_client(FakeOrchestrator.with_samples("tok")) as connected:
        yield connected


def test_the_manifest_lists_clips_newest_first(client: httpx2.Client) -> None:
    clips = client.get("/manifest").json()["clips"]

    # The samples were delivered a minute apart: Enduro, Suspensão, Aceleração.
    assert [clip["prova"] for clip in clips] == ["Aceleração", "Suspensão", "Enduro"]


def test_each_clip_has_the_pipeline_fields_and_layout(client: httpx2.Client) -> None:
    clip = client.get("/manifest").json()["clips"][-1]

    assert clip["relative_path"] == "enduro/42/3.mp4"
    assert clip["team_name"] == "Equipe Cerrado"
    assert len(clip["sha256"]) == 64
    assert clip["delivered_at"].endswith("Z")


def test_a_reprocessed_pass_replaces_the_older_delivery() -> None:
    # The same prova/car/passagem delivered twice: only the newer one is listed.
    fake = FakeOrchestrator()
    fake.deliver("Enduro", 42, None, 3, SAMPLE_START, size_bytes=1)
    fake.deliver("Enduro", 42, None, 3, SAMPLE_START + timedelta(minutes=5), size_bytes=2)

    clips = fake.manifest(None, None)["clips"]

    assert [clip["size_bytes"] for clip in clips] == [2]


def test_since_returns_only_later_deliveries(client: httpx2.Client) -> None:
    since = (SAMPLE_START + timedelta(seconds=30)).isoformat()

    clips = client.get("/manifest", params={"since": since}).json()["clips"]

    assert [clip["prova"] for clip in clips] == ["Aceleração", "Suspensão"]


def test_prova_filters_by_name_or_slug(client: httpx2.Client) -> None:
    by_slug = client.get("/manifest", params={"prova": "suspensao"}).json()["clips"]
    by_name = client.get("/manifest", params={"prova": "Suspensão"}).json()["clips"]

    assert [clip["car_number"] for clip in by_slug] == [7]
    assert by_name == by_slug


def test_a_wrong_token_is_refused_with_401() -> None:
    with http_client(FakeOrchestrator.with_samples("tok"), token="nope") as client:
        assert client.get("/manifest").status_code == 401


def test_without_a_configured_token_any_caller_is_accepted() -> None:
    with http_client(FakeOrchestrator.with_samples(""), token="whatever") as client:
        assert client.get("/manifest").status_code == 200


def test_unknown_paths_answer_404(client: httpx2.Client) -> None:
    assert client.get("/jobs/1").status_code == 404


def test_an_unavailable_orchestrator_refuses_connections() -> None:
    # The studio must treat this like the VPS being unreachable.
    fake = FakeOrchestrator.with_samples("tok")
    fake.available = False

    with http_client(fake) as client, pytest.raises(httpx2.ConnectError):
        client.get("/manifest")


def test_requests_are_recorded(client: httpx2.Client) -> None:
    fake = FakeOrchestrator.with_samples("tok")
    with http_client(fake) as recorded:
        recorded.get("/manifest")

    assert fake.requests == ["GET /manifest"]


@pytest.mark.parametrize(
    ("prova", "slug"),
    [("Suspensão", "suspensao"), ("Aceleração", "aceleracao"), ("Lama & Água", "lama-agua")],
)
def test_the_prova_slug_removes_accents_and_joins_words_with_dashes(prova: str, slug: str) -> None:
    assert prova_slug(prova) == slug


def test_deliveries_are_made_for_files_laid_out_as_prova_car_passagem(tmp_path: Path) -> None:
    # Two files follow <prova>/<car>/<passagem>.mp4; one does not (car is not a number).
    for relative in ("enduro/42/3.mp4", "suspensao/7/1.mp4", "notes/abc/1.mp4"):
        file = tmp_path / relative
        file.parent.mkdir(parents=True)
        file.write_bytes(b"12345")

    deliveries = deliveries_from_folder(tmp_path)

    assert sorted(delivery.relative_path for delivery in deliveries) == [
        "enduro/42/3.mp4",
        "suspensao/7/1.mp4",
    ]
    assert all(delivery.size_bytes == 5 for delivery in deliveries)


def test_generated_at_is_a_utc_time(client: httpx2.Client) -> None:
    generated_at = client.get("/manifest").json()["generated_at"]

    assert datetime.fromisoformat(generated_at).tzinfo == UTC
