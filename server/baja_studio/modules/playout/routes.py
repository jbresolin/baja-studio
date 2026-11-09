"""HTTP routes for clip files, mounted under `/api/playout`.

- `GET /clips/{id}/media`: the video file, with range requests, so the
  preview card's `<video>` can scrub it without touching OBS.
- `GET /clips/{id}/thumbnail`: the cached JPEG still.

- `GET /browse?path=`: subfolders of a folder on this PC (drives when
  empty), for the folders form's "browse" button. Folder names only.

Only files the library knows are served, never an arbitrary path.
"""

from collections.abc import Callable
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from baja_studio.playout.browse import browse
from baja_studio.playout.library import LibraryStore


def playout_routes(library: Callable[[], LibraryStore | None]) -> APIRouter:
    """The router; `library` returns the running module's library (None when stopped)."""
    router = APIRouter()

    @router.get("/browse")
    def browse_folders(path: str = "") -> dict[str, Any]:
        return browse(path)

    def running_library() -> LibraryStore:
        store = library()
        if store is None:
            raise HTTPException(503, "playout is not running")
        return store

    @router.get("/clips/{clip_id}/media")
    def clip_media(clip_id: int) -> FileResponse:
        clip = running_library().get(clip_id)
        if clip is None or clip["missing"] or not Path(clip["file_path"]).is_file():
            raise HTTPException(404, "no such clip file")
        return FileResponse(clip["file_path"])

    @router.get("/clips/{clip_id}/thumbnail")
    def clip_thumbnail(clip_id: int) -> FileResponse:
        path = running_library().thumbnail_path(clip_id)
        if path is None or not Path(path).is_file():
            raise HTTPException(404, "no thumbnail for this clip")
        return FileResponse(path, media_type="image/jpeg")

    return router
