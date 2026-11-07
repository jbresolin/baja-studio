"""The home module's settings export and import commands, and the upload route.

The file itself never travels inside a command: commands are logged with
their arguments, and a hand-edited file may hold real secrets. So the page
first uploads the file (`POST /api/home/settings-import`, kept in memory for
an hour) and the commands name it by `upload_id`:

- `export_settings` → {file_name, document}; the page saves it as a file
- `import_settings_dryrun {upload_id}` → the per-module diff and a token
- `import_settings_apply {upload_id, token, apply_partial}` → per-module results
"""

import secrets
import threading
from collections import OrderedDict
from collections.abc import Callable
from typing import Any

from fastapi import APIRouter, HTTPException, Request

from baja_studio.core.clock import Clock
from baja_studio.core.module import CommandError
from baja_studio.core.settings import SettingsStore
from baja_studio.core.settings_export import export_document, export_file_name, this_hostname
from baja_studio.core.settings_import import (
    SettingsFileError,
    apply_import,
    plan_import,
)

MAX_UPLOAD_BYTES = 5 * 1024 * 1024
UPLOADS_KEPT = 5
UPLOAD_LIFETIME_SECONDS = 3600.0
COMMANDS = ("export_settings", "import_settings_dryrun", "import_settings_apply")


class SettingsUploads:
    """Uploaded settings files, by id, until used or an hour old (five at most)."""

    def __init__(self, clock: Clock) -> None:
        """Empty."""
        self._clock = clock
        self._lock = threading.Lock()
        self._files: OrderedDict[str, tuple[float, bytes]] = OrderedDict()

    def add(self, content: bytes) -> str:
        """Keep `content`; returns the id the commands name it by."""
        upload_id = secrets.token_hex(8)
        with self._lock:
            self._forget_old()
            self._files[upload_id] = (self._clock.monotonic(), content)
            while len(self._files) > UPLOADS_KEPT:
                self._files.popitem(last=False)
        return upload_id

    def get(self, upload_id: str) -> bytes:
        """The file; `CommandError` when it is unknown or too old."""
        with self._lock:
            self._forget_old()
            entry = self._files.get(upload_id)
        if entry is None:
            raise CommandError("that uploaded file is no longer here; choose the file again")
        return entry[1]

    def _forget_old(self) -> None:
        cutoff = self._clock.monotonic() - UPLOAD_LIFETIME_SECONDS
        for upload_id in [key for key, (added, _) in self._files.items() if added < cutoff]:
            del self._files[upload_id]


class SettingsTransfer:
    """Export, dry run and apply, over the shared settings store."""

    def __init__(self, settings: SettingsStore, clock: Clock, uploads: SettingsUploads) -> None:
        """`uploads` is shared with the upload route."""
        self._settings = settings
        self._clock = clock
        self._uploads = uploads

    def handle(self, command: str, args: dict[str, Any]) -> Any:
        """Run one of `COMMANDS`."""
        if command == "export_settings":
            hostname = this_hostname()
            return {
                "file_name": export_file_name(hostname, self._clock),
                "document": export_document(self._settings, self._clock, hostname),
            }
        content = self._uploads.get(str(args.get("upload_id", "")))
        try:
            plan = plan_import(content, self._settings)
            if command == "import_settings_dryrun":
                return plan.as_state()
            if args.get("token") != plan.token:
                raise CommandError(
                    "the settings changed since the dry run (or another file was chosen); "
                    "run the dry run again and check the changes"
                )
            results = apply_import(plan, self._settings, bool(args.get("apply_partial")))
        except SettingsFileError as refused:
            raise CommandError(str(refused)) from None
        to_enter = [path for section in plan.sections for path in section.secrets_to_enter]
        return {"results": results, "secrets_to_enter": to_enter}


def settings_upload_routes(uploads: Callable[[], SettingsUploads]) -> APIRouter:
    """`POST /settings-import`: the raw file as the body; answers {upload_id}.

    `uploads()` is the running module's store (it raises while the module starts).
    """
    router = APIRouter()

    @router.post("/settings-import")
    async def upload_settings_file(request: Request) -> dict[str, str]:
        content = await request.body()
        if len(content) > MAX_UPLOAD_BYTES:
            raise HTTPException(413, "a settings file is never this big; is it the right file?")
        if not content:
            raise HTTPException(400, "the file is empty")
        return {"upload_id": uploads().add(content)}

    return router
