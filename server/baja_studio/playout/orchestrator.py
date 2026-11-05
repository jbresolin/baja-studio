"""Read-only client of the pipeline orchestrator's clip manifest.

Shape from baja-video-pipeline `docs/api.md` §7.1 and `openapi.yaml`
(`GET /manifest?since=&prova=` → `ManifestResponse`). Authenticated with the
studio's worker bearer token. Played state and notes stay local: the API has
no write-back yet (ADR 0005, left open).

Offline rule: 3 s to connect, 10 s to read; any failure is reported and the
last merged list stays in the library.
"""

from datetime import datetime

import httpx2
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from baja_studio.playout.library import ManifestClip

CONNECT_TIMEOUT_SECONDS = 3.0
READ_TIMEOUT_SECONDS = 10.0


class OrchestratorError(Exception):
    """The manifest could not be fetched; the message is shown in the sync status."""


class ManifestEntry(BaseModel):
    """One delivered clip, mirroring the pipeline's `ManifestEntry` model.

    Unknown fields are ignored rather than refused, so a pipeline release
    that adds a field does not stop the studio syncing.
    """

    model_config = ConfigDict(extra="ignore")

    job_id: str
    prova: str
    car_number: int = Field(ge=0, le=9999)
    team_name: str | None
    passagem: int = Field(ge=1, le=999)
    relative_path: str
    sha256: str
    size_bytes: int = Field(ge=0)
    delivered_at: datetime

    def as_manifest_clip(self) -> ManifestClip:
        """The library's view of this entry."""
        return ManifestClip(
            job_id=self.job_id,
            prova=self.prova,
            car_number=self.car_number,
            team_name=self.team_name,
            passagem=self.passagem,
            relative_path=self.relative_path,
            sha256=self.sha256,
            size_bytes=self.size_bytes,
            delivered_at=self.delivered_at.isoformat(),
        )


class ManifestResponse(BaseModel):
    """The whole answer: clips newest first, latest delivery per prova/car/passagem."""

    model_config = ConfigDict(extra="ignore")

    clips: list[ManifestEntry]
    generated_at: datetime


class OrchestratorClient:
    """Fetches the manifest; `transport` lets tests route requests to the fake."""

    def __init__(
        self,
        base_url: str,
        token: str,
        transport: httpx2.BaseTransport | None = None,
    ) -> None:
        """Remember where and how to ask; one HTTP client per sync worker run."""
        self._client = httpx2.Client(
            base_url=base_url,
            headers={"Authorization": f"Bearer {token}"} if token else {},
            timeout=httpx2.Timeout(READ_TIMEOUT_SECONDS, connect=CONNECT_TIMEOUT_SECONDS),
            transport=transport,
        )

    def fetch_manifest(self, since: datetime | None = None) -> ManifestResponse:
        """`GET /manifest`, only clips delivered after `since` when given."""
        params = {"since": since.isoformat()} if since is not None else {}
        try:
            response = self._client.get("/manifest", params=params)
        except httpx2.HTTPError as error:
            raise OrchestratorError(f"cannot reach the orchestrator: {error}") from error
        if response.status_code in (401, 403):
            raise OrchestratorError("the orchestrator refused our token (check the studio token)")
        if response.status_code != 200:
            raise OrchestratorError(f"the orchestrator answered {response.status_code}")
        try:
            return ManifestResponse.model_validate_json(response.content)
        except ValidationError as error:
            raise OrchestratorError(
                f"unexpected manifest from the orchestrator: {error}"
            ) from error

    def close(self) -> None:
        """Close the HTTP client's connections."""
        self._client.close()
