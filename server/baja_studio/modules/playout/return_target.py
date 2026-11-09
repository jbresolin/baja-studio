"""Where program goes after a clip: the page's "Return to" control (WP5b §1).

*Return to: ◉ previous scene / ○ chosen scene.* "Previous" is the scene that
was on program when a run of clips began: it is captured at a take whenever
program is not one of the playback scenes, so an auto-play chain (or a held
clip followed by another take) returns to what was on before the first
clip. "Chosen" is a scene picked from OBS's list.

Both live in the session table (they survive a restart, the settings export
never carries them). When no target can be determined the state says so in
`return_target.warning`, which the program strip shows; a return never
silently does nothing.
"""

from typing import Any, Literal

from baja_studio.modules.playout.publisher import PlayoutPublisher
from baja_studio.playout.queue_store import QueueStore

ReturnMode = Literal["previous", "chosen"]
RETURN_MODES: tuple[ReturnMode, ...] = ("previous", "chosen")


class ReturnTarget:
    """The return mode, the chosen scene and the captured previous scene."""

    def __init__(self, queue: QueueStore, publisher: PlayoutPublisher) -> None:
        """Read what the session saved; nothing is published until `publish`."""
        self._queue = queue
        self._publisher = publisher
        saved = queue.session_value("return_target", {}) or {}
        mode = saved.get("mode")
        self.mode: ReturnMode = mode if mode in RETURN_MODES else "previous"
        self.chosen_scene: str | None = saved.get("chosen_scene")
        self.previous_scene: str | None = saved.get("previous_scene")

    def scene(self) -> str | None:
        """The scene a return goes to now, or None when there is none."""
        return self.previous_scene if self.mode == "previous" else self.chosen_scene

    def warning(self) -> str | None:
        """Why there is no return target, worded for the strip; None when there is one."""
        if self.scene() is not None:
            return None
        if self.mode == "previous":
            return "no previous scene yet: after the clip, program stays where it is"
        return "no return scene chosen: pick one under Return to"

    def set(self, mode: ReturnMode, chosen_scene: str | None) -> None:
        """The operator changed the control."""
        self.mode = mode
        if chosen_scene is not None:
            self.chosen_scene = chosen_scene or None
        self._save()

    def capture_previous(self, scene: str | None) -> None:
        """A take happened while `scene` was on program (not a playback scene)."""
        if scene and scene != self.previous_scene:
            self.previous_scene = scene
            self._save()

    def publish(self) -> None:
        """Publish `return_target` for every page."""
        self._publisher.publish_return_target(self.view())

    def view(self) -> dict[str, Any]:
        """The state value: mode, both scenes, the resolved one and any warning."""
        return {
            "mode": self.mode,
            "chosen_scene": self.chosen_scene,
            "previous_scene": self.previous_scene,
            "scene": self.scene(),
            "warning": self.warning(),
        }

    def _save(self) -> None:
        self._queue.set_session_value(
            "return_target",
            {
                "mode": self.mode,
                "chosen_scene": self.chosen_scene,
                "previous_scene": self.previous_scene,
            },
        )
        self.publish()
