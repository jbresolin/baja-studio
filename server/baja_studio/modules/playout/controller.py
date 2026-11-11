"""Playout's policy: queue, cue, take, post-clip, return target, replay and playlists.

Commands (thread pool) and the OBS worker (events, polling) both call in
here, always under `lock`, so the switcher's idea of what is on air never
changes halfway through an action. OBS requests are made with the lock held;
each has a 2 s timeout (a cue may wait up to 3 s for its source to start).
Event listeners never take the lock: they hand the event to the OBS worker,
which calls `on_media_ended` / `on_scene_changed`. What happens around an
airing (log, webhook, on-air actions) is in `airing.py`; transport and slow
motion for the clip on air in `transport.py` (`controller.transport`).
"""

import logging
import threading
from collections.abc import Callable
from typing import Any

from baja_studio.adapters.obs_transport import ObsError
from baja_studio.core.clock import Clock
from baja_studio.modules.playout.airing import AiringBookkeeper
from baja_studio.modules.playout.publisher import PlayoutPublisher
from baja_studio.modules.playout.return_target import ReturnMode, ReturnTarget
from baja_studio.modules.playout.session import restore_switcher, save_switcher
from baja_studio.modules.playout.speed_change import SpeedChangeQueue
from baja_studio.modules.playout.take_confirmation import (
    TAKE_CONFIRMATION_WINDOW_SECONDS,
    TakeConfirmation,
)
from baja_studio.modules.playout.transport import ProgramTransport
from baja_studio.playout.config import PlayoutSettings
from baja_studio.playout.export import build_aired_export
from baja_studio.playout.library import LibraryError, LibraryStore
from baja_studio.playout.playlists import PlaylistStore
from baja_studio.playout.queue_store import QueueStore
from baja_studio.playout.switcher import Cued, CueError, OnAir, ProgramSwitcher, SwitchError


class PlayoutController:
    """Everything playout does, in one place, behind one lock."""

    def __init__(
        self,
        clock: Clock,
        settings: Callable[[], PlayoutSettings],
        library: LibraryStore,
        queue: QueueStore,
        playlists: PlaylistStore,
        switcher: ProgramSwitcher,
        airing: AiringBookkeeper,
        publisher: PlayoutPublisher,
        logger: logging.LoggerAdapter[logging.Logger],
    ) -> None:
        """Wire the parts; `restore_session` then brings back what was saved."""
        self.lock = threading.RLock()
        self._clock = clock
        self._settings = settings
        self.library = library
        self.queue = queue
        self.playlists = playlists
        self.switcher = switcher
        self._airing = airing
        self.publisher = publisher
        self._logger = logger
        self.return_target = ReturnTarget(queue, publisher)
        # Program scene as OBS last reported it (None until connected).
        self.current_scene: str | None = None
        # Why the last cue failed; the OBS worker reports it as `degraded`.
        self.cue_problem: str | None = None
        self._confirmation = TakeConfirmation(clock, publisher, self.lock)
        self.transport = ProgramTransport(
            switcher, settings, clock, logger, self._save_session, self._cue_lost
        )
        # Live speed changes, one at a time (they take the lock one change at a time).
        self.speed_changes = SpeedChangeQueue(
            self._change_program_speed, publisher.publish_speed_change
        )

    # --- session --------------------------------------------------------------------------

    def restore_session(self) -> None:
        """Bring back program, cue, toggles and the return target saved before a restart."""
        with self.lock:
            restore_switcher(self.switcher, self.queue, self.library, self.publisher)
            self.transport.next_cue_speed = self.queue.session_value("next_cue_speed", 100)
            self.publisher.publish_everything(self.switcher)
            self.publisher.publish_next_cue_speed(self.transport.next_cue_speed)
            self.return_target.publish()

    def _save_session(self) -> None:
        save_switcher(self.switcher, self.queue)
        self.queue.set_session_value("next_cue_speed", self.transport.next_cue_speed)
        self.publisher.publish_program(self.switcher)
        self.publisher.publish_preview(self.switcher)
        self.publisher.publish_next_cue_speed(self.transport.next_cue_speed)

    def _change_program_speed(self, speed_percent: int, source: str) -> None:
        with self.lock:
            self.transport.set_program_speed(speed_percent, source)

    def _cue_lost(self, cued: Cued, reason: str) -> None:
        """A clip that was cued could not be cued again: back to the head of the queue."""
        self.queue.add([cued.clip["id"]], "resync", True, cued.playlist_id)
        self.publisher.publish_queue()
        self._set_cue_problem(reason)

    # --- queue and playlists --------------------------------------------------------------

    def queue_add(
        self,
        clip_ids: list[int],
        added_by: str,
        at_front: bool = False,
        playlist_id: int | None = None,
    ) -> list[int]:
        """Queue clips (refusing missing files) and publish the queue."""
        with self.lock:
            for clip_id in clip_ids:
                self.library.require_queueable(clip_id)
            entry_ids = self.queue.add(clip_ids, added_by, at_front, playlist_id)
            self.publisher.publish_queue()
            return entry_ids

    def queue_changed(self) -> None:
        """Publish the queue after a remove, move or clear done through `queue`."""
        self.publisher.publish_queue()

    def queue_playlist(self, playlist_id: int, added_by: str) -> list[int]:
        """Queue a playlist's clips in one action (shuffled if it says so).

        Clips whose file is missing are left out rather than refusing the
        whole playlist; refused only when none can play.
        """
        with self.lock:
            clip_ids = [
                clip_id
                for clip_id in self.playlists.play_order(playlist_id)
                if (clip := self.library.get(clip_id)) is not None and clip["queueable"]
            ]
            if not clip_ids:
                name = self.playlists.get(playlist_id)["name"]
                raise SwitchError(f"no clip of playlist {name!r} has its file")
            return self.queue_add(clip_ids, added_by, playlist_id=playlist_id)

    def playlists_changed(self) -> None:
        """Publish playlists (and the queue, which shows their names) after an edit."""
        self.publisher.publish_playlists()
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
                self.switcher.cue(clip, entry["playlist_id"], self.transport.next_cue_speed)
            except (ObsError, SwitchError) as error:
                self.queue.add([entry["clip_id"]], entry["added_by"], True, entry["playlist_id"])
                if isinstance(error, CueError):
                    self._set_cue_problem(str(error))
                raise
            finally:
                self.publisher.publish_queue()
            self.transport.take_next_cue_speed()
            self._set_cue_problem(None)
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
            if (
                self._settings().operators.require_take_confirmation
                and not self._confirmation.is_armed()
            ):
                self._confirmation.arm(source)
                return {"status": "confirm", "window_s": TAKE_CONFIRMATION_WINDOW_SECONDS}
            self._confirmation.disarm()
            if self.switcher.cued is None:
                self.cue_next()
            on_air = self._take_cued()
            return {"status": "on_air", "clip": on_air.clip}

    def skip(self) -> None:
        """End the clip on air now, exactly as if it had finished.

        It takes the same path as a natural end: with auto-play next on (and
        something to play) the next clip goes to air, otherwise post-clip
        applies. The source is paused first, so "hold" freezes the frame
        and a fade fades from a still picture.
        """
        with self.lock:
            program = self.switcher.program
            if program is None:
                raise SwitchError("nothing is on air to skip")
            self.switcher.pause_slot(program.slot)
            self._program_finished(program)

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
        """Take the clip off air now and go to the return target."""
        with self.lock:
            scene = self.return_target.scene()
            if scene is None:
                raise SwitchError(self.return_target.warning() or "no return scene")
            self.switcher.go_to_scene(scene, fade=False)
            ended = self.switcher.end_program()
            if ended is not None:
                self._airing.ended(ended)
                self.switcher.stop_slot(ended.slot)
            self._save_session()

    def set_return_target(self, mode: ReturnMode, chosen_scene: str | None) -> dict[str, Any]:
        """The "Return to" control: previous scene, or a chosen one."""
        with self.lock:
            if chosen_scene and self.switcher.is_playback_scene(chosen_scene):
                raise SwitchError("a playback scene cannot be the return scene")
            self.return_target.set(mode, chosen_scene)
            return self.return_target.view()

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

    def on_connected(self, current_scene: str, scene_names: list[str]) -> None:
        """OBS (re)connected: force cuts into the playback scenes and re-read where we are."""
        with self.lock:
            self.current_scene = current_scene
            self.publish_scene_list(scene_names)
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
                self._airing.ended(program)
            cued = self.switcher.cued
            if cued is not None:
                self.switcher.cued = None
                if self.switcher.slot(cued.slot).scene == current_scene:
                    self.queue.add([cued.clip["id"]], "resync", True, cued.playlist_id)
                else:
                    try:
                        # OBS may have lost it.
                        self.switcher.cue(cued.clip, cued.playlist_id, cued.speed_percent)
                    except SwitchError as error:
                        self._cue_lost(cued, str(error))
            self.publisher.publish_queue()
            self._save_session()

    def publish_scene_list(self, scene_names: list[str]) -> None:
        """OBS's scenes, for the "chosen scene" dropdown (playback scenes left out)."""
        self.publisher.publish_obs(
            scenes=[name for name in scene_names if not self.switcher.is_playback_scene(name)]
        )

    def on_media_ended(self, input_name: str) -> None:
        """A media source ended: if it was the clip on air, apply auto-play or post-clip."""
        with self.lock:
            program = self.switcher.program
            if program is None or self.switcher.slot(program.slot).source != input_name:
                return
            self._program_finished(program)

    def on_scene_changed(self, scene_name: str) -> None:
        """Program changed in OBS; if someone cut away from the clip on air, it left the air."""
        with self.lock:
            self.current_scene = scene_name
            self.publisher.publish_obs(current_scene=scene_name)
            if self.switcher.is_our_scene_change(scene_name):
                return
            program = self.switcher.program
            if program is not None and self.switcher.slot(program.slot).scene != scene_name:
                self.switcher.end_program()
                self._airing.ended(program)
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

    def _take_cued(self) -> OnAir:
        """Cut to the cued clip; the first take of a run remembers the scene it left."""
        if not self.switcher.is_playback_scene(self.current_scene):
            self.return_target.capture_previous(self.current_scene)
        on_air, previous = self.switcher.take()
        if previous is not None:
            self._airing.ended(previous)
        self._airing.started(on_air)
        self._save_session()
        return on_air

    def _program_finished(self, program: OnAir) -> None:
        """The clip on air is over (it ended or was skipped): auto-play next, else post-clip.

        Auto-play is a take like the operator's: the finished clip stays the
        program until the next one replaces it, so pages never see "nothing
        on air" in between and there is exactly one scene change.
        """
        if self.publisher.auto_play_next and (self.switcher.cued or self.queue.entries()):
            self._confirmation.disarm()
            try:
                if self.switcher.cued is None:
                    self.cue_next()
                self._take_cued()
                return
            except (ObsError, SwitchError, LibraryError) as error:
                # Never leave program on a dead clip: fall back to post-clip.
                self._logger.warning("auto-play next failed: %s", error)
                self.publisher.publish_notice(f"auto-play next failed: {error}")
        self.switcher.end_program()
        self._airing.ended(program)
        self._apply_post_clip()
        self._save_session()

    def _apply_post_clip(self) -> None:
        post_clip = self._settings().obs.post_clip
        if post_clip == "hold":
            return
        scene = self.return_target.scene()
        if scene is None:
            warning = self.return_target.warning() or "no return scene"
            self._logger.warning("clip ended; holding: %s", warning)
            self.publisher.publish_notice(f"clip ended; holding the last frame: {warning}")
            return
        try:
            self.switcher.go_to_scene(scene, fade=post_clip == "fade")
        except ObsError as error:
            self._logger.warning("could not return to %r: %s", scene, error)
            self.publisher.publish_notice(f"could not return to {scene!r}: {error}")

    def _set_cue_problem(self, problem: str | None) -> None:
        if problem != self.cue_problem:
            self.cue_problem = problem
            self.publisher.publish_cue_problem(problem)
