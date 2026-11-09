"""What happens around a clip going on and off air, besides switching.

On air: an aired-log entry (with the playlist's name), the clip's played
count, the webhook's `{clip, started_at}` and the `on_clip_start` actions.
Off air: the log entry's end, `{clip, ended_at}` and the `on_clip_end`
actions. A playlist's on-air override replaces the settings' actions for
its clips. Nothing here can fail a take: the webhook and the actions are
sent later from their own workers.
"""

from collections.abc import Callable

from baja_studio.core.clock import Clock
from baja_studio.modules.playout.publisher import PlayoutPublisher
from baja_studio.playout.config import PlayoutSettings
from baja_studio.playout.hooks import WebhookNotifier, off_air_body, on_air_body
from baja_studio.playout.library import LibraryStore
from baja_studio.playout.on_air import OnAirActionRunner, actions_for
from baja_studio.playout.playlists import PlaylistStore
from baja_studio.playout.queue_store import QueueStore
from baja_studio.playout.switcher import OnAir


class AiringBookkeeper:
    """Logs airings and tells the outside world (webhook, other modules)."""

    def __init__(
        self,
        clock: Clock,
        settings: Callable[[], PlayoutSettings],
        library: LibraryStore,
        queue: QueueStore,
        playlists: PlaylistStore,
        notifier: WebhookNotifier,
        actions: OnAirActionRunner,
        publisher: PlayoutPublisher,
    ) -> None:
        """The stores and senders it writes to."""
        self._clock = clock
        self._settings = settings
        self._library = library
        self._queue = queue
        self._playlists = playlists
        self._notifier = notifier
        self._actions = actions
        self._publisher = publisher

    def started(self, on_air: OnAir) -> None:
        """`on_air` just went to air."""
        clip = on_air.clip
        on_air.aired_id = self._queue.start_airing(
            clip["id"],
            clip["title"],
            on_air.started_at,
            self._playlists.name_of(on_air.playlist_id),
        )
        self._queue.set_session_value("last_aired_clip_id", clip["id"])
        self._library.record_played(clip["id"])
        self._publisher.publish_clips([clip["id"]])
        settings = self._settings()
        self._notifier.notify(
            settings.hooks.on_air_webhook_url, on_air_body(clip, on_air.started_at)
        )
        override = self._playlists.on_air_override(on_air.playlist_id)
        self._actions.queue(
            actions_for("on_clip_start", settings.on_air_actions, override),
            "on_clip_start",
            clip["title"],
        )

    def ended(self, ended: OnAir) -> None:
        """`ended` just left the air (finished, skipped, cut away or replaced)."""
        ended_at = self._clock.now_utc().isoformat()
        self._queue.end_airing(ended.aired_id, ended_at)
        settings = self._settings()
        self._notifier.notify(settings.hooks.on_air_webhook_url, off_air_body(ended.clip, ended_at))
        override = self._playlists.on_air_override(ended.playlist_id)
        self._actions.queue(
            actions_for("on_clip_end", settings.on_air_actions, override),
            "on_clip_end",
            ended.clip["title"],
        )
