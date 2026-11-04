"""Flash-free program switching by double-buffering two media sources.

Two scenes (`Playback A`, `Playback B`) each hold one media source. The next
clip is loaded into the source that is *not* on air and cued: restarted,
paused and rewound to its first frame. Going to air is one
`SetCurrentProgramScene` to that source's scene, then play. The source on
air is never touched while cueing, so there is no frame of the previous clip
to flash. The old stop → swap file → wait → play sequence on one source is
not used.

Transitions *to* the two playback scenes are overridden to a cut when OBS
connects, so a take is a cut whatever transition the operator has selected.
Every method is called with the playout controller's lock held.
"""

import collections
import contextlib
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Literal, Protocol

from baja_studio.adapters.obs_protocol import MediaAction
from baja_studio.adapters.obs_requests import MediaStatus
from baja_studio.adapters.obs_transport import ObsError
from baja_studio.core.clock import Clock
from baja_studio.playout.config import ObsSettings

SlotName = Literal["a", "b"]
# Scene changes we asked for whose OBS event has not come back yet (a few at most).
MAX_REQUESTED_SCENE_CHANGES = 8
# After a fade, the temporary transition override is cleared this long after it ended.
FADE_OVERRIDE_CLEAR_MARGIN_SECONDS = 0.5
# Media source settings for a clip: play once, keep the last frame (hold), never
# restart on its own when its scene becomes visible.
MEDIA_SOURCE_SETTINGS = {
    "looping": False,
    "restart_on_activate": False,
    "close_when_inactive": False,
    "clear_on_media_end": False,
    "is_local_file": True,
}


class SwitchError(Exception):
    """A switching action was refused; the message is for the operator."""


class ObsControl(Protocol):
    """The OBS requests switching needs (`ObsWebsocketClient` provides them)."""

    def set_input_settings(
        self, input_name: str, settings: dict[str, Any], overlay: bool = True
    ) -> None:
        """See `ObsRequestsMixin`."""

    def trigger_media_input_action(self, input_name: str, action: str) -> None:
        """See `ObsRequestsMixin`."""

    def set_media_input_cursor(self, input_name: str, cursor_s: float) -> None:
        """See `ObsRequestsMixin`."""

    def set_current_program_scene(self, scene_name: str) -> None:
        """See `ObsRequestsMixin`."""

    def get_media_input_status(self, input_name: str) -> MediaStatus:
        """See `ObsRequestsMixin`."""

    def set_scene_transition_override(
        self, scene_name: str, transition_name: str | None, duration_ms: int | None = None
    ) -> None:
        """See `ObsRequestsMixin`."""


@dataclass(frozen=True)
class Slot:
    """One half of the double buffer."""

    name: SlotName
    scene: str
    source: str


@dataclass
class OnAir:
    """The clip on program."""

    clip: dict[str, Any]
    slot: SlotName
    started_at: str
    aired_id: int = 0


@dataclass
class Cued:
    """The clip loaded and paused in the off-air source (the preview)."""

    clip: dict[str, Any]
    slot: SlotName


def other_slot(slot: SlotName) -> SlotName:
    """The other half of the buffer."""
    return "b" if slot == "a" else "a"


class ProgramSwitcher:
    """Knows which slot is on air and what is cued, and drives OBS accordingly."""

    def __init__(self, obs: ObsControl, settings: Callable[[], ObsSettings], clock: Clock) -> None:
        """`settings` is read on every action, so edits apply to the next one."""
        self._obs = obs
        self._settings = settings
        self._clock = clock
        self.program: OnAir | None = None
        self.cued: Cued | None = None
        # The slot used last; the first cue goes into the other one ("a").
        self.last_slot: SlotName = "b"
        self._requested_scenes: collections.deque[str] = collections.deque(
            maxlen=MAX_REQUESTED_SCENE_CHANGES
        )

    def slot(self, name: SlotName) -> Slot:
        """Scene and source of a slot, from the current settings."""
        settings = self._settings()
        if name == "a":
            return Slot("a", settings.scene_a, settings.source_a)
        return Slot("b", settings.scene_b, settings.source_b)

    def slot_for_scene(self, scene_name: str) -> SlotName | None:
        """Which slot's scene this is, if either."""
        for name in ("a", "b"):
            if self.slot(name).scene == scene_name:
                return name
        return None

    def off_air_slot(self) -> SlotName:
        """Where the next clip is loaded: never the slot on program."""
        return other_slot(self.program.slot if self.program else self.last_slot)

    def prepare_scenes(self) -> None:
        """Make transitions to both playback scenes cuts (called on every connect)."""
        cut = self._settings().cut_transition
        for name in ("a", "b"):
            self._obs.set_scene_transition_override(self.slot(name).scene, cut)

    def cue(self, clip: dict[str, Any]) -> Cued:
        """Load `clip` into the off-air source and park it on its first frame."""
        slot = self.slot(self.off_air_slot())
        self._obs.set_input_settings(
            slot.source, {**MEDIA_SOURCE_SETTINGS, "local_file": clip["file_path"]}
        )
        # Loading a file starts it playing; restart, pause and rewind leave it on frame one.
        self._obs.trigger_media_input_action(slot.source, MediaAction.RESTART)
        self._obs.trigger_media_input_action(slot.source, MediaAction.PAUSE)
        self._obs.set_media_input_cursor(slot.source, 0.0)
        self.cued = Cued(clip, slot.name)
        return self.cued

    def take(self) -> tuple[OnAir, OnAir | None]:
        """Cut to the cued clip and play it; returns (now on air, what it replaced)."""
        if self.cued is None:
            raise SwitchError("nothing is cued: cue a clip first")
        slot = self.slot(self.cued.slot)
        self._switch_program(slot.scene)
        self._obs.trigger_media_input_action(slot.source, MediaAction.PLAY)
        previous = self.program
        self.program = OnAir(self.cued.clip, slot.name, self._clock.now_utc().isoformat())
        self.cued = None
        self.last_slot = slot.name
        if previous is not None and previous.slot != slot.name:
            self._stop_quietly(self.slot(previous.slot).source)
        return self.program, previous

    def end_program(self) -> OnAir | None:
        """Forget the clip on air (it ended, or the operator cut away); returns it."""
        ended, self.program = self.program, None
        return ended

    def discard_cue(self) -> Cued | None:
        """Drop the cued clip and stop its (off-air) source; returns it."""
        dropped, self.cued = self.cued, None
        if dropped is not None:
            self._stop_quietly(self.slot(dropped.slot).source)
        return dropped

    def go_to_return_scene(self, fade: bool) -> bool:
        """Switch program to the return scene (cut or fade); False if none is configured."""
        settings = self._settings()
        if not settings.return_scene:
            return False
        if fade:
            self._obs.set_scene_transition_override(
                settings.return_scene, settings.fade_transition, settings.fade_ms
            )
        self._switch_program(settings.return_scene)
        if fade:
            scene = settings.return_scene
            delay = settings.fade_ms / 1000 + FADE_OVERRIDE_CLEAR_MARGIN_SECONDS
            self._clock.call_later(delay, lambda: self._clear_override_quietly(scene))
        return True

    def is_our_scene_change(self, scene_name: str) -> bool:
        """True if a program-scene event is the echo of a change we requested.

        OBS reports changes in order, so the echo of an earlier take can
        arrive after a later one; it must not read as "the operator cut away".
        """
        if scene_name not in self._requested_scenes:
            return False
        while self._requested_scenes.popleft() != scene_name:
            pass
        return True

    def pause_slot(self, slot: SlotName) -> None:
        """Pause a slot's source, freezing its frame (raises if OBS refuses)."""
        self._obs.trigger_media_input_action(self.slot(slot).source, MediaAction.PAUSE)

    def stop_slot(self, slot: SlotName) -> None:
        """Stop a slot's source (after leaving the air, so it holds nothing)."""
        self._stop_quietly(self.slot(slot).source)

    def program_status(self) -> MediaStatus | None:
        """Position of the clip on air, asked from OBS (None when nothing is on air)."""
        if self.program is None:
            return None
        return self._obs.get_media_input_status(self.slot(self.program.slot).source)

    def _switch_program(self, scene_name: str) -> None:
        self._requested_scenes.append(scene_name)
        try:
            self._obs.set_current_program_scene(scene_name)
        except ObsError:
            self._requested_scenes.pop()
            raise

    def _stop_quietly(self, source: str) -> None:
        # Stopping an off-air source only saves OBS some work; it must not fail a take.
        with contextlib.suppress(ObsError):
            self._obs.trigger_media_input_action(source, MediaAction.STOP)

    def _clear_override_quietly(self, scene: str) -> None:
        with contextlib.suppress(ObsError):
            self._obs.set_scene_transition_override(scene, None)
