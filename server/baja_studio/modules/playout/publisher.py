"""Writes playout's live state, the tree every browser sees under `playout`.

Layout (brief §4 plus what the page needs):

- `obs`: connection (`not_configured`, `connecting`, `connected`, `offline`,
  `auth_failed`), message, current_scene, address
- `program`: clip on air, slot, started_at, position_s, duration_s, remaining_s
- `preview`: the cued clip and its slot
- `queue`: entries in play order (entry_id, clip_id, added_by, added_at, title,
  duration_s); `queue_total_s` their summed duration
- `library`: every clip keyed by id (so one clip changing is one small patch);
  `library_summary`: counts per prova, missing, unplayed
- `sync_status`, `folders` (scan errors), `auto_play_next`,
  `take_confirmation`, `export`
"""

from collections.abc import Callable
from typing import Any

from baja_studio.adapters.obs_requests import MediaStatus
from baja_studio.playout.library import LibraryStore
from baja_studio.playout.queue_store import QueueStore
from baja_studio.playout.switcher import ProgramSwitcher

UpdateState = Callable[[str, Any], None]


def initial_playout_state() -> dict[str, Any]:
    """Playout's state before anything has happened."""
    return {
        "obs": {"connection": "not_configured", "message": "", "current_scene": None},
        "program": None,
        "preview": None,
        "queue": [],
        "queue_total_s": 0.0,
        "library": {},
        "library_summary": {"total": 0, "missing": 0, "unplayed": 0, "per_prova": {}},
        "sync_status": {"state": "disabled", "last_success_at": None, "error": None},
        "folders": {"errors": {}},
        "auto_play_next": False,
        "take_confirmation": None,
        "export": {"stream_start_marker": None},
    }


class PlayoutPublisher:
    """Turns library, queue and switcher contents into state updates."""

    def __init__(self, update_state: UpdateState, library: LibraryStore, queue: QueueStore) -> None:
        """`update_state` is the module context's `update_state`."""
        self._update = update_state
        self._library = library
        self._queue = queue
        self.auto_play_next = False

    def publish_everything(self, switcher: ProgramSwitcher) -> None:
        """Publish the whole subtree from the stores (at start and after restore)."""
        self._update("library", {str(clip["id"]): clip for clip in self._library.all()})
        self._update("library_summary", self._library.summary())
        self.publish_queue()
        self.publish_program(switcher)
        self.publish_preview(switcher)
        self.publish_auto_play_next()

    def publish_clips(self, clip_ids: list[int]) -> None:
        """Re-publish some clips (scan, probe, sync or an edit changed them) and the summary."""
        if not clip_ids:
            return
        for clip_id in clip_ids:
            clip = self._library.get(clip_id)
            if clip is not None:
                self._update(f"library.{clip_id}", clip)
        self._update("library_summary", self._library.summary())
        self.publish_queue()

    def publish_queue(self) -> None:
        """The queue with each entry's title and duration."""
        entries = []
        for entry in self._queue.entries():
            clip = self._library.get(entry["clip_id"]) or {}
            entries.append(
                {
                    **entry,
                    "title": clip.get("title", "(deleted clip)"),
                    "duration_s": clip.get("duration_s"),
                    "missing": clip.get("missing", True),
                }
            )
        self._update("queue", entries)
        self._update("queue_total_s", sum(entry["duration_s"] or 0.0 for entry in entries))

    def publish_program(self, switcher: ProgramSwitcher) -> None:
        """What is on air (position unknown until the next poll)."""
        program = switcher.program
        if program is None:
            self._update("program", None)
            return
        duration = program.clip.get("duration_s")
        self._update(
            "program",
            {
                "clip": program.clip,
                "slot": program.slot,
                "started_at": program.started_at,
                "position_s": 0.0,
                "duration_s": duration,
                "remaining_s": duration,
            },
        )

    def publish_position(self, status: MediaStatus) -> None:
        """Position from the 5 Hz poll of the source on air."""
        position = status.cursor_s or 0.0
        duration = status.duration_s
        self._update("program.position_s", position)
        self._update("program.duration_s", duration)
        self._update(
            "program.remaining_s", max(0.0, duration - position) if duration is not None else None
        )

    def publish_preview(self, switcher: ProgramSwitcher) -> None:
        """The cued clip."""
        cued = switcher.cued
        self._update("preview", {"clip": cued.clip, "slot": cued.slot} if cued else None)

    def publish_obs(self, **fields: Any) -> None:
        """Some of: connection, message, current_scene, address."""
        for name, value in fields.items():
            self._update(f"obs.{name}", value)

    def publish_sync(self, status: dict[str, Any]) -> None:
        """The orchestrator sync's state, last success and last error."""
        self._update("sync_status", status)

    def publish_folder_errors(self, errors: dict[str, str]) -> None:
        """Folders the scanner could not read."""
        self._update("folders", {"errors": errors})

    def publish_auto_play_next(self) -> None:
        """The auto-play-next toggle."""
        self._update("auto_play_next", self.auto_play_next)

    def publish_take_confirmation(self, pending: dict[str, Any] | None) -> None:
        """An armed take waiting for its second press, or None."""
        self._update("take_confirmation", pending)

    def publish_export(self, marker: str | None) -> None:
        """The stream-start marker."""
        self._update("export", {"stream_start_marker": marker})
