"""HTTP route for camera previews, mounted under `/api/cameras` (ADR 0020).

- `GET /preview/{camera}?width=320`: a JPEG still of the camera's OBS source
  (`cameras/previews.py`), with `X-Preview-Shows` (`window` or `full`) and,
  for crop cameras, `X-Crop-Window` (x,y,w,h). 404 when the camera has no
  source, 503 with the reason ("OBS offline: ...") when OBS cannot answer.

Images go over HTTP, not through the shared state: the state goes to every
open page, a preview only to the page that shows it, at its own pace.
"""

from collections.abc import Callable

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import Response

from baja_studio.cameras.previews import CameraPreviews, PreviewUnavailableError

DEFAULT_WIDTH = 320


def camera_routes(previews: Callable[[], CameraPreviews | None]) -> APIRouter:
    """The router; `previews` returns the running module's preview service (None when stopped)."""
    router = APIRouter()

    @router.get("/preview/{camera_id}")
    def camera_preview(
        camera_id: str, width: int = Query(DEFAULT_WIDTH, ge=64, le=1920)
    ) -> Response:
        service = previews()
        if service is None:
            raise HTTPException(503, "the cameras module is not running")
        try:
            preview = service.preview(camera_id, width)
        except PreviewUnavailableError as unavailable:
            raise HTTPException(unavailable.status_code, unavailable.detail) from None
        return Response(
            preview.image.content, media_type=preview.image.media_type, headers=preview.headers
        )

    return router
