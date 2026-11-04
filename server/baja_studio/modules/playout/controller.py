"""Playout's policy: queue, cue, take, post-clip, replay, aired log and hooks.

Commands (thread pool) and the OBS worker (events, polling) both call in
here, always under `lock`, so the switcher's idea of what is on air never
changes halfway through an action. OBS requests are made with the lock held;
each has a 2 s timeout. Event listeners never take the lock: they hand the
event to the OBS worker, which calls `on_media_ended` / `on_scene_changed`.
"""

import logging
import threading
from collections.abc import Callable
from typing import Any

from baja_studio.adapters.obs_transport import ObsError
from baja_studio.core.clock import Clock
from baja_studio.modules.playout.publisher import PlayoutPublisher
from baja_studio.playout.config import PlayoutSettings
from baja_studio.playout.export import build_aired_export
from baja_studio.playout.hooks import WebhookNotifier, off_air_body, on_air_body
from baja_studio.playout.library import LibraryStore
from baja_studio.playout.queue_store import QueueStore
from baja_studio.playout.switcher import Cued, OnAir, ProgramSwitcher, SlotName, SwitchError

TAKE_CONFIRMATION_WINDOW_SECONDS = 3.0


class PlayoutController:
    """Everything playout does, in one place, behind one lock."""

    def __init__(
        self,
        clock: Clock,
        settings: Callable[[], PlayoutSettings],
        library: LibraryStore,
        queue: QueueStore,
        switcher: ProgramSwitcher,
        notifier: WebhookNotifier,
        publisher: PlayoutPublisher,
        logger: logging.LoggerAdapter[logging.Logger],
    ) -> None:
        """Wire the parts; `restore_session` then brings back what was saved."""
        self.lock = threading.RLock()
        self._clock = clock
        self._settings = settings
        self.library = library
        self.queue = queue
        self.switcher = switcher
        self._notifier = notifier
        self.publisher = publisher
        self._logger = logger
        self._take_armed_until: float | None = None

    # --- session --------------------------------------------------------------------------

    def restore_session(self) -> None:
        """Bring back program, cue and toggles saved before a restart."""
        with self.lock:
            saved_program = self.queue.session_value("program")
            if saved_program and (clip := self.library.get(saved_program["clip_id"])):
                self.switcher.program = OnAir(
                    clip,
                    _slot_name(saved_program["slot"]),
                    saved_program["started_at"],
                    saved_program["aired_id"],
                )
            saved_cue = self.queue.session_value("cued")
            if saved_cue and (clip := self.library.get(saved_cue["clip_id"])):
                self.switcher.cued = Cued(clip, _slot_name(saved_cue["slot"]))
            self.switcher.last_slot = _slot_name(self.queue.session_value("last_slot", "b"))
            self.publisher.auto_play_next = bool(self.queue.session_value("auto_play_next", False))
            self.publisher.publish_everything(self.switcher)

    def _save_session(self) -> None:
        program, cued = self.switcher.program, self.switcher.cued
        self.queue.set_session_value(
            "program",
            {
                "clip_id": program.clip["id"],
                "slot": program.slot,
                "started_at": program.started_at,
                "aired_id": program.aired_id,
            }
            if program
            else None,
        )
        self.queue.set_session_value(
            "cued", {"clip_id": cued.clip["id"], "slot": cued.slot} if cued else None
        )
        self.queue.set_session_value("last_slot", self.switcher.last_slot)
        self.publisher.publish_program(self.switcher)
        self.publisher.publish_preview(self.switcher)

    # --- queue ----------------------------------------------------------------------------

    def queue_add(self, clip_ids: list[int], added_by: str, at_front: bool = False) -> list[int]:
        """Queue clips (refusing missing files) and publish the queue."""
        with self.lock:
            for clip_id in clip_ids:
                self.library.require_queueable(clip_id)
            entry_ids = self.queue.add(clip_ids, added_by, at_front)
            self.publisher.publish_queue()
            return entry_ids

    def queue_changed(self) -> None:
        """Publish the queue after a remove, move or clear done through `queue`."""
        self.publisher.publish_queue()

    # --- switching ------------------------------------------------------------------------

    def cue_next(self) -> dict[str, Any]:
        """Load the head of the queue into the off-air source; returns the cued clip."""
        with self.lock:
            if self.switcher.cued is not None:
                raise SwitchError("a clip is already cued: take it or drop it first")
            entry = self.queue.pop_first()
            if entry is None:
                raise SwitchError("the queue is empty: add a clip first")
            try:
                clip = self.library.require_queueable(entry["clip_id"])
                self.switcher.cue(clip)
            except ObsError:
                self.queue.add([entry["clip_id"]], entry["added_by"], at_front=True)
                raise
            finally:
                self.publisher.publish_queue()
            self._save_session()
            return clip

    def take(self, source: str, cue_if_needed: bool = False) -> dict[str, Any]:
        """Put the cued clip on air (cueing the head of the queue first if asked).

        With take confirmation on, the first take only arms: a second one
        (from any client) within 3 s fires.
        """
        with self.lock:
            if self.switcher.cued is None and not (cue_if_needed and self.queue.entries()):
                raise SwitchError("nothing is cued: cue a clip first")
            if self._settings().operators.require_take_confirmation and not self._take_armed():
                self._arm_take(source)
                return {"status": "confirm", "window_s": TAKE_CONFIRMATION_WINDOW_SECONDS}
            self._disarm_take()
            if self.switcher.cued is None:
                self.cue_next()
            on_air, previous = self.switcher.take()
            if previous is not None:
                self._end_airing(previous)
            self._start_airing(on_air)
            self._save_session()
            return {"status": "on_air", "clip": on_air.clip}

    def skip(self) -> None:
        """End the clip on air now, as if it had finished: post-clip applies (return/hold/fade).

        The source is paused first, so "hold" freezes the frame and a fade
        fades from a still picture. Auto-play next does not apply: a skip
        means "get me out of this clip", not "play the next one".
        """
        with self.lock:
            program = self.switcher.program
            if program is None:
                raise SwitchError("nothing is on air to skip")
            self.switcher.pause_slot(program.slot)
            self.switcher.end_program()
            self._end_airing(program)
            self._apply_post_clip()
            self._save_session()

    def drop_next(self) -> dict[str, Any] | None:
        """Drop the next clip (the cued one, else the head of the queue) and cue the one after."""
        with self.lock:
            dropped = self.switcher.discard_cue()
            if dropped is None:
                entry = self.queue.pop_first()
                if entry is None:
                    raise SwitchError("nothing to drop: the queue is empty")
                self.publisher.publish_queue()
            self._save_session()
            if self.queue.entries():
                return self.cue_next()
            return None

    def return_to_scene(self) -> None:
        """Take the clip off air now and go to the return scene."""
        with self.lock:
            ended = self.switcher.end_program()
            if not self.switcher.go_to_return_scene(fade=False):
                if ended is not None:
                    self.switcher.program = ended
                raise SwitchError("no return scene is set in the playout settings")
            if ended is not None:
                self._end_airing(ended)
                self.switcher.stop_slot(ended.slot)
            self._save_session()

    def replay_last(self, source: str) -> dict[str, Any]:
        """Put the last aired clip at the front of the queue, and cue it if nothing is cued."""
        with self.lock:
            clip_id = self.queue.session_value("last_aired_clip_id")
            if clip_id is None:
                raise SwitchError("nothing has aired yet")
            self.queue_add([clip_id], source, at_front=True)
            if self.switcher.cued is None:
                return self.cue_next()
            return self.library.require_queueable(clip_id)

    def set_auto_play_next(self, enabled: bool) -> None:
        """Turn "auto-play next" on or off (off by default: hosts usually talk between passes)."""
        with self.lock:
            self.queue.set_session_value("auto_play_next", enabled)
            self.publisher.auto_play_next = enabled
            self.publisher.publish_auto_play_next()

    # --- OBS side (called by the OBS worker) ---------------------------------------------

    def on_connected(self, current_scene: str) -> None:
        """OBS (re)connected: force cuts into the playback scenes and re-read where we are."""
        with self.lock:
            try:
                self.switcher.prepare_scenes()
            except ObsError as error:
                self._logger.warning(
                    "could not set the cut override on the playback scenes: %s", error
                )
            program = self.switcher.program
            if program is not None and self.switcher.slot(program.slot).scene != current_scene:
                # OBS restarted or someone cut away while we were disconnected.
                self.switcher.end_program()
                self._end_airing(program)
            cued = self.switcher.cued
            if cued is not None:
                self.switcher.cued = None
                if self.switcher.slot(cued.slot).scene == current_scene:
                    self.queue.add([cued.clip["id"]], "resync", at_front=True)
                else:
                    self.switcher.cue(cued.clip)  # OBS may have restarted and lost it
            self.publisher.publish_queue()
            self._save_session()

    def on_media_ended(self, input_name: str) -> None:
        """A media source ended: if it was the clip on air, apply auto-play or post-clip."""
        with self.lock:
            program = self.switcher.program
            if program is None or self.switcher.slot(program.slot).source != input_name:
                return
            self.switcher.end_program()
            self._end_airing(program)
            if self.publisher.auto_play_next and (self.switcher.cued or self.queue.entries()):
                self._disarm_take()
                if self.switcher.cued is None:
                    self.cue_next()
                on_air, _ = self.switcher.take()
                self._start_airing(on_air)
            else:
                self._apply_post_clip()
            self._save_session()

    def on_scene_changed(self, scene_name: str) -> None:
        """Program changed in OBS; if someone cut away from the clip on air, it left the air."""
        with self.lock:
            self.publisher.publish_obs(current_scene=scene_name)
            if self.switcher.is_our_scene_change(scene_name):
                return
            program = self.switcher.program
            if program is not None and self.switcher.slot(program.slot).scene != scene_name:
                self.switcher.end_program()
                self._end_airing(program)
                self._save_session()

    def poll_program(self) -> None:
        """Read the position of the clip on air (5 Hz); an ended file counts as ended."""
        with self.lock:
            status = self.switcher.program_status()
            program = self.switcher.program
            if status is None or program is None:
                return
            if status.has_ended:
                self.on_media_ended(self.switcher.slot(program.slot).source)
                return
            self.publisher.publish_position(status)

    # --- export ---------------------------------------------------------------------------

    def now_iso(self) -> str:
        """The current UTC time as stored everywhere in playout."""
        return self._clock.now_utc().isoformat()

    def export_aired(self) -> dict[str, Any]:
        """The aired log with offsets from the stream-start marker (when set)."""
        marker = self._settings().export.stream_start_marker
        return build_aired_export(self.queue.aired(), marker)

    # --- internals ------------------------------------------------------------------------

    def _start_airing(self, on_air: OnAir) -> None:
        clip = on_air.clip
        on_air.aired_id = self.queue.start_airing(clip["id"], clip["title"], on_air.started_at)
        self.queue.set_session_value("last_aired_clip_id", clip["id"])
        self.library.record_played(clip["id"])
        self.publisher.publish_clips([clip["id"]])
        self._notifier.notify(
            self._settings().hooks.on_air_webhook_url, on_air_body(clip, on_air.started_at)
        )

    def _apply_post_clip(self) -> None:
        post_clip = self._settings().obs.post_clip
        if post_clip != "hold" and not self.switcher.go_to_return_scene(post_clip == "fade"):
            self._logger.warning("clip ended but no return scene is set; holding")

    def _end_airing(self, ended: OnAir) -> None:
        ended_at = self._clock.now_utc().isoformat()
        self.queue.end_airing(ended.aired_id, ended_at)
        self._notifier.notify(
            self._settings().hooks.on_air_webhook_url, off_air_body(ended.clip, ended_at)
        )

    def _take_armed(self) -> bool:
        armed_until = self._take_armed_until
        return armed_until is not None and self._clock.monotonic() <= armed_until

    def _arm_take(self, source: str) -> None:
        self._take_armed_until = self._clock.monotonic() + TAKE_CONFIRMATION_WINDOW_SECONDS
        self.publisher.publish_take_confirmation(
            {"requested_by": source, "window_s": TAKE_CONFIRMATION_WINDOW_SECONDS}
        )
        armed_at = self._take_armed_until
        self._clock.call_later(
            TAKE_CONFIRMATION_WINDOW_SECONDS, lambda: self._expire_take(armed_at)
        )

    def _expire_take(self, armed_until: float) -> None:
        with self.lock:
            if self._take_armed_until == armed_until:
                self._disarm_take()

    def _disarm_take(self) -> None:
        self._take_armed_until = None
        self.publisher.publish_take_confirmation(None)


def _slot_name(value: object) -> SlotName:
    return "a" if value == "a" else "b"
