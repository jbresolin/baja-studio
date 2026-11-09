"""Writes playout's live state, the tree every browser sees under `playout`.

Layout (brief §4 plus what the page needs):

- `obs`: connection (`not_configured`, `connecting`, `connected`, `offline`,
  `auth_failed`), message, current_scene, address, scenes (OBS's scene list)
- `program`: clip on air, slot, started_at, position_s, duration_s, remaining_s
- `preview`: the cued clip and its slot
- `queue`: entries in play order (entry_id, clip_id, added_by, added_at,
  playlist_id, playlist, title, duration_s, missing); `queue_total_s` their
  known durations summed, `queue_unknown` how many have no duration
- `library`: every clip keyed by id (so one clip changing is one small patch);
  `library_summary`: counts per group, missing, unplayed, unmatched
- `playlists`: every playlist (id, name, shuffle, on_air, clip_ids)
- `return_target`: the "Return to" control and its warning (`return_target.py`)
- `tools`: whether ffprobe/ffmpeg were found, and the banner text if not
- `cue_problem`: why the last cue failed (cleared by the next good one)
- `notice`: the latest problem to show as a toast ({id, message}), e.g. an
  on-air action that failed
- `hosts_view`: now/next for the hosts screen (`hosts_view.py`)
- `sync_status`, `folders` (scan errors), `auto_play_next`,
  `take_confirmation`, `export`
"""

import itertools
from collections.abc import Callable
from typing import Any

from baja_studio.adapters.obs_requests import MediaStatus
from baja_studio.modules.playout.hosts_view import build_hosts_view
from baja_studio.playout.library import LibraryStore
from baja_studio.playout.playlists import PlaylistStore
from baja_studio.playout.queue_store import QueueStore
from baja_studio.playout.switcher import ProgramSwitcher

UpdateState = Callable[[str, Any], None]


def initial_playout_state() -> dict[str, Any]:
    """Playout's state before anything has happened."""
    return {
        "obs": {"connection": "not_configured", "message": "", "current_scene": None, "scenes": []},
        "program": None,
        "preview": None,
        "queue": [],
        "queue_total_s": 0.0,
        "queue_unknown": 0,
        "library": {},
        "library_summary": {
            "total": 0,
            "missing": 0,
            "unplayed": 0,
            "unmatched": 0,
            "per_group": {},
        },
        "playlists": [],
        "return_target": None,
        "tools": {"ffprobe": None, "ffmpeg": None, "problem": None},
        "cue_problem": None,
        "notice": None,
        "hosts_view": build_hosts_view(None, None, []),
        "sync_status": {"state": "disabled", "last_success_at": None, "error": None},
        "folders": {"errors": {}},
        "auto_play_next": False,
        "take_confirmation": None,
        "export": {"stream_start_marker": None},
    }


class PlayoutPublisher:
    """Turns library, queue and switcher contents into state updates."""

    def __init__(
        self,
        update_state: UpdateState,
        library: LibraryStore,
        queue: QueueStore,
        playlists: PlaylistStore,
    ) -> None:
        """`update_state` is the module context's `update_state`."""
        self._update = update_state
        self._library = library
        self._queue = queue
        self._playlists = playlists
        self.auto_play_next = False
        self._notice_ids = itertools.count(1)
        # What the hosts view is built from, kept as last published.
        self._program: dict[str, Any] | None = None
        self._cued_clip: dict[str, Any] | None = None
        self._queue_entries: list[dict[str, Any]] = []
        self._hosts_view: dict[str, Any] | None = None

    def publish_everything(self, switcher: ProgramSwitcher) -> None:
        """Publish the whole subtree from the stores (at start and after restore)."""
        self._update("library", {str(clip["id"]): clip for clip in self._library.all()})
        self._update("library_summary", self._library.summary())
        self.publish_playlists()
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
        """The queue with each entry's title, duration and playlist name."""
        entries = []
        for entry in self._queue.entries():
            clip = self._library.get(entry["clip_id"]) or {}
            entries.append(
                {
                    **entry,
                    "playlist": self._playlists.name_of(entry["playlist_id"]),
                    "title": clip.get("title", "(deleted clip)"),
                    "duration_s": clip.get("duration_s"),
                    "missing": clip.get("missing", True),
                }
            )
        self._update("queue", entries)
        self._update("queue_total_s", sum(entry["duration_s"] or 0.0 for entry in entries))
        self._update("queue_unknown", sum(1 for entry in entries if entry["duration_s"] is None))
        self._queue_entries = entries
        self.publish_hosts_view()

    def publish_playlists(self) -> None:
        """Every playlist."""
        self._update("playlists", self._playlists.all())

    def publish_program(self, switcher: ProgramSwitcher) -> None:
        """What is on air (position unknown until the next poll)."""
        program = switcher.program
        if program is None:
            self._program = None
            self._update("program", None)
        else:
            duration = program.clip.get("duration_s")
            self._program = {
                "clip": program.clip,
                "slot": program.slot,
                "started_at": program.started_at,
                "position_s": 0.0,
                "duration_s": duration,
                "remaining_s": duration,
            }
            self._update("program", dict(self._program))
        self.publish_hosts_view()

    def publish_position(self, status: MediaStatus) -> None:
        """Position from the 5 Hz poll of the source on air."""
        position = status.cursor_s or 0.0
        duration = status.duration_s
        remaining = max(0.0, duration - position) if duration is not None else None
        self._update("program.position_s", position)
        self._update("program.duration_s", duration)
        self._update("program.remaining_s", remaining)
        if self._program is not None:
            self._program.update(position_s=position, duration_s=duration, remaining_s=remaining)
        self.publish_hosts_view()

    def publish_preview(self, switcher: ProgramSwitcher) -> None:
        """The cued clip."""
        cued = switcher.cued
        self._cued_clip = cued.clip if cued else None
        self._update("preview", {"clip": cued.clip, "slot": cued.slot} if cued else None)
        self.publish_hosts_view()

    def publish_hosts_view(self) -> None:
        """The hosts screen's now/next; sent only when it changed (about 1 Hz while playing)."""
        view = build_hosts_view(self._program, self._cued_clip, self._queue_entries)
        if view != self._hosts_view:
            self._hosts_view = view
            self._update("hosts_view", view)

    def publish_obs(self, **fields: Any) -> None:
        """Some of: connection, message, current_scene, address, scenes."""
        for name, value in fields.items():
            self._update(f"obs.{name}", value)

    def publish_return_target(self, view: dict[str, Any]) -> None:
        """The "Return to" control (see `return_target.py`)."""
        self._update("return_target", view)

    def publish_tools(self, tools: dict[str, Any]) -> None:
        """Which of ffprobe/ffmpeg were found, and the banner text when one is missing."""
        self._update("tools", tools)

    def publish_cue_problem(self, problem: str | None) -> None:
        """Why the last cue failed, or None after a good one."""
        self._update("cue_problem", problem)

    def publish_notice(self, message: str) -> None:
        """A problem for every page to show once as a toast (a new id each time)."""
        self._update("notice", {"id": next(self._notice_ids), "message": message})

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
