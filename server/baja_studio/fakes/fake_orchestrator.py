"""A fake pipeline orchestrator serving the clip manifest (`GET /manifest`).

Same shape as baja-video-pipeline's `ManifestResponse`: newest first, only
the latest delivery per (prova, car, passagem), `since` and `prova` filters,
bearer-token auth. There is no write-back endpoint because the real API has
none yet (ADR 0005).

Run it for the bench:

    python -m baja_studio.fakes.fake_orchestrator --port 8000 --token studio-token
    python -m baja_studio.fakes.fake_orchestrator --from-folder D:\\Clips

`--from-folder` announces every file laid out as `<prova>/<car>/<passagem>.mp4`
under that folder, so dropped files get metadata. Tests reach the fake
in-process with `transport()` (an httpx2 mock transport); no port opened.
"""

import argparse
import hashlib
import threading
import unicodedata
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx2
import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response

DEFAULT_PORT = 8000
SAMPLE_START = datetime(2026, 11, 1, 12, 0, tzinfo=UTC)


@dataclass
class FakeDelivery:
    """One delivered clip as the orchestrator records it."""

    job_id: str
    prova: str
    car_number: int
    team_name: str | None
    passagem: int
    size_bytes: int
    delivered_at: datetime

    @property
    def relative_path(self) -> str:
        """Built like the pipeline's `shared.layout.clip_relative_path`."""
        return f"{prova_slug(self.prova)}/{self.car_number}/{self.passagem}.mp4"

    def as_json(self) -> dict[str, Any]:
        """The `ManifestEntry` JSON."""
        return {
            "job_id": self.job_id,
            "prova": self.prova,
            "car_number": self.car_number,
            "team_name": self.team_name,
            "passagem": self.passagem,
            "relative_path": self.relative_path,
            "sha256": hashlib.sha256(self.job_id.encode()).hexdigest(),
            "size_bytes": self.size_bytes,
            "delivered_at": self.delivered_at.isoformat().replace("+00:00", "Z"),
        }


def prova_slug(prova: str) -> str:
    """Lowercase ASCII, accents removed, other characters → '-' (pipeline rule)."""
    ascii_text = unicodedata.normalize("NFKD", prova).encode("ascii", "ignore").decode()
    slug = "".join(char if char.isalnum() else "-" for char in ascii_text.lower())
    return "-".join(part for part in slug.split("-") if part)


class FakeOrchestrator:
    """The fake's deliveries and its request handling."""

    def __init__(self, token: str = "", deliveries: list[FakeDelivery] | None = None) -> None:
        """An orchestrator that has delivered `deliveries` (none by default)."""
        self.token = token
        self.available = True
        self._lock = threading.Lock()
        self._deliveries: list[FakeDelivery] = list(deliveries or [])
        self.requests: list[str] = []

    @classmethod
    def with_samples(cls, token: str = "") -> "FakeOrchestrator":
        """Three delivered clips, for the bench and for tests."""
        samples = [
            ("Enduro", 42, "Equipe Cerrado", 3),
            ("Suspensão", 7, "Mangue Baja", 1),
            ("Aceleração", 13, None, 2),
        ]
        fake = cls(token)
        for index, (prova, car, team, passagem) in enumerate(samples):
            fake.deliver(prova, car, team, passagem, SAMPLE_START + timedelta(minutes=index))
        return fake

    def deliver(
        self,
        prova: str,
        car_number: int,
        team_name: str | None,
        passagem: int,
        delivered_at: datetime,
        size_bytes: int = 1_000_000,
    ) -> FakeDelivery:
        """Record a delivery (a reprocess of the same pass replaces the older one)."""
        with self._lock:
            delivery = FakeDelivery(
                f"job{len(self._deliveries) + 1:04d}",
                prova,
                car_number,
                team_name,
                passagem,
                size_bytes,
                delivered_at,
            )
            self._deliveries.append(delivery)
        return delivery

    def manifest(self, since: datetime | None, prova: str | None) -> dict[str, Any]:
        """The `ManifestResponse` body."""
        with self._lock:
            latest: dict[tuple[str, int, int], FakeDelivery] = {}
            for delivery in sorted(self._deliveries, key=lambda item: item.delivered_at):
                latest[(prova_slug(delivery.prova), delivery.car_number, delivery.passagem)] = (
                    delivery
                )
        clips = sorted(latest.values(), key=lambda item: item.delivered_at, reverse=True)
        if since is not None:
            clips = [clip for clip in clips if clip.delivered_at > since]
        if prova:
            wanted = prova_slug(prova)
            clips = [clip for clip in clips if prova_slug(clip.prova) == wanted]
        return {
            "clips": [clip.as_json() for clip in clips],
            "generated_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        }

    def answer(
        self, method: str, path: str, query: dict[str, str], authorization: str | None
    ) -> tuple[int, dict[str, Any]]:
        """Handle one request: (status, JSON body)."""
        self.requests.append(f"{method} {path}")
        if path == "/health":
            return 200, {"status": "ok"}
        if method != "GET" or path != "/manifest":
            return 404, {"error": {"code": "not_found", "message": path}}
        if self.token and authorization != f"Bearer {self.token}":
            return 401, {"error": {"code": "unauthenticated", "message": "bad token"}}
        since = datetime.fromisoformat(query["since"]) if query.get("since") else None
        return 200, self.manifest(since, query.get("prova"))

    def transport(self) -> httpx2.MockTransport:
        """An httpx2 transport that hands requests to this fake (no network)."""

        def handle(request: httpx2.Request) -> httpx2.Response:
            if not self.available:
                raise httpx2.ConnectError("connection refused", request=request)
            status, body = self.answer(
                request.method,
                request.url.path,
                dict(request.url.params),
                request.headers.get("authorization"),
            )
            return httpx2.Response(status, json=body)

        return httpx2.MockTransport(handle)


def create_fake_orchestrator_app(fake: FakeOrchestrator) -> FastAPI:
    """A FastAPI app serving `fake` over HTTP."""
    app = FastAPI(title="Fake orchestrator")

    @app.get("/{path:path}")
    def any_get(path: str, request: Request) -> Response:
        status, body = fake.answer(
            "GET", f"/{path}", dict(request.query_params), request.headers.get("authorization")
        )
        return JSONResponse(body, status_code=status)

    return app


def deliveries_from_folder(folder: Path) -> list[FakeDelivery]:
    """A delivery for every `<prova>/<car>/<passagem>.mp4` file under `folder`."""
    deliveries = []
    for index, path in enumerate(sorted(folder.glob("*/*/*.mp4"))):
        prova, car, passagem = path.parent.parent.name, path.parent.name, path.stem
        if car.isdigit() and passagem.isdigit():
            deliveries.append(
                FakeDelivery(
                    f"folder{index + 1:04d}",
                    prova.capitalize(),
                    int(car),
                    None,
                    int(passagem),
                    path.stat().st_size,
                    SAMPLE_START + timedelta(minutes=index),
                )
            )
    return deliveries


def main(argv: list[str] | None = None) -> None:
    """Command line entry point: serve a fake orchestrator."""
    parser = argparse.ArgumentParser(description="Serve a fake pipeline orchestrator manifest.")
    parser.add_argument("--host", default="127.0.0.1", help="address to listen on")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help="port to listen on")
    parser.add_argument("--token", default="", help="require this bearer token")
    parser.add_argument(
        "--from-folder", type=Path, default=None, help="announce the clips laid out in this folder"
    )
    options = parser.parse_args(argv)
    if options.from_folder is not None:
        fake = FakeOrchestrator(options.token, deliveries_from_folder(options.from_folder))
    else:
        fake = FakeOrchestrator.with_samples(options.token)
    uvicorn.run(create_fake_orchestrator_app(fake), host=options.host, port=options.port)


if __name__ == "__main__":
    main()
