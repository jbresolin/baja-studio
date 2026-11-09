"""The `obs` worker: keeps playout connected to OBS and feeds it OBS's events.

It connects (reconnecting after 0.5, 1, 2, then every 4 s), re-reads the
program scene on every connect, polls the clip on air at 5 Hz, and hands
events from the client's reader thread to the controller on this thread.
OBS away is `offline`; a refused password is `degraded`. Library and queue
keep working either way.
"""

import collections
import logging
import threading
from collections.abc import Callable

from baja_studio.adapters.obs_transport import (
    ObsAddress,
    ObsAuthenticationError,
    ObsError,
    ObsNotConnectedError,
    ObsRequestTimeoutError,
    ObsUnreachableError,
)
from baja_studio.adapters.obs_websocket import ObsWebsocketClient, ReconnectBackoff
from baja_studio.core.worker import HealthState, WorkerContext
from baja_studio.modules.playout.controller import PlayoutController
from baja_studio.playout.config import PlayoutSettings

POLL_INTERVAL_SECONDS = 0.2
IDLE_INTERVAL_SECONDS = 1.0
# Events after which the "Return to" scene dropdown is read again.
SCENE_LIST_EVENTS = frozenset(
    {"SceneCreated", "SceneRemoved", "SceneNameChanged", "SceneListChanged"}
)

InboxItem = tuple[str, str]


class ObsConnectionWorker:
    """One OBS connection at a time, for the playout module."""

    def __init__(
        self,
        client: ObsWebsocketClient,
        controller: PlayoutController,
        settings: Callable[[], PlayoutSettings],
        logger: logging.LoggerAdapter[logging.Logger],
    ) -> None:
        """Listen to the client's events; the worker loop starts with `run`."""
        self._client = client
        self._controller = controller
        self._settings = settings
        self._logger = logger
        self._inbox: collections.deque[InboxItem] = collections.deque()
        self._inbox_lock = threading.Lock()
        self._worker: WorkerContext | None = None
        client.add_event_listener(self._on_event)
        client.add_disconnect_listener(self._on_disconnect)

    def wake(self) -> None:
        """Look again now (settings changed, or an event arrived)."""
        worker = self._worker
        if worker is not None:
            worker.wake()

    def run(self, worker: WorkerContext) -> None:
        """Worker body: connect, serve the connection, reconnect; until asked to stop."""
        self._worker = worker
        backoff = ReconnectBackoff()
        publisher = self._controller.publisher
        try:
            while not worker.should_stop:
                obs = self._settings().obs
                if not obs.host:
                    message = "OBS not configured: set its address in the playout settings"
                    publisher.publish_obs(connection="not_configured", message=message)
                    worker.report_health(HealthState.OFFLINE, message)
                    worker.sleep(IDLE_INTERVAL_SECONDS)
                    continue
                address = ObsAddress(obs.host, obs.port)
                publisher.publish_obs(connection="connecting", message="", address=address.uri)
                if not self._connect(worker, address, obs.password):
                    worker.sleep(backoff.next_delay())
                    continue
                backoff.reset()
                self._serve_connection(worker, (address, obs.password))
                self._client.close()
        finally:
            self._client.close()

    def _connect(self, worker: WorkerContext, address: ObsAddress, password: str) -> bool:
        publisher = self._controller.publisher
        with self._inbox_lock:
            self._inbox.clear()
        try:
            self._client.connect(address, password)
            scenes = self._client.get_scene_list()
            scene = scenes.current_program_scene
            self._controller.on_connected(scene, scenes.scene_names)
        except ObsAuthenticationError as error:
            publisher.publish_obs(connection="auth_failed", message=str(error))
            worker.report_health(HealthState.DEGRADED, str(error))
            return False
        except ObsError as error:
            self._client.close()
            message = str(error) if isinstance(error, ObsUnreachableError) else f"OBS: {error}"
            publisher.publish_obs(connection="offline", message=message)
            worker.report_health(HealthState.OFFLINE, message)
            return False
        publisher.publish_obs(connection="connected", message="", current_scene=scene)
        worker.mark_online()
        self._report_cue_health(worker)
        self._logger.info("connected to OBS at %s", address.uri)
        return True

    def _serve_connection(self, worker: WorkerContext, credentials: tuple[ObsAddress, str]) -> None:
        """Handle events and poll until the connection drops or its settings change."""
        while not worker.should_stop:
            obs = self._settings().obs
            if (ObsAddress(obs.host, obs.port), obs.password) != credentials:
                self._logger.info("OBS address or password changed; reconnecting")
                return
            try:
                if not self._handle_inbox():
                    break
                self._controller.poll_program()
            except (ObsNotConnectedError, ObsRequestTimeoutError) as error:
                self._logger.warning("lost OBS: %s", error)
                break
            except ObsError as error:
                # OBS refused something (a scene renamed in OBS, say); keep the connection.
                self._logger.warning("%s", error)
            self._report_cue_health(worker)
            on_air = self._controller.switcher.program is not None
            worker.sleep(POLL_INTERVAL_SECONDS if on_air else IDLE_INTERVAL_SECONDS)
        if not worker.should_stop:
            message = "lost the connection to OBS; reconnecting"
            self._controller.publisher.publish_obs(connection="offline", message=message)
            worker.report_health(HealthState.OFFLINE, message)

    def _report_cue_health(self, worker: WorkerContext) -> None:
        """Connected: `ok`, or `degraded` with OBS's words while the last cue failed."""
        problem = self._controller.cue_problem
        if problem:
            worker.report_health(HealthState.DEGRADED, f"cue failed: {problem}")
        else:
            worker.report_health(HealthState.OK)

    def _handle_inbox(self) -> bool:
        """Apply queued events; False if the connection was lost."""
        while True:
            with self._inbox_lock:
                if not self._inbox:
                    return True
                kind, value = self._inbox.popleft()
            if kind == "lost":
                return False
            if kind == "ended":
                self._controller.on_media_ended(value)
            elif kind == "scene":
                self._controller.on_scene_changed(value)
            elif kind == "scene_list":
                self._controller.publish_scene_list(self._client.get_scene_list().scene_names)

    def _on_event(self, event_type: str, event_data: dict[str, object]) -> None:
        """Reader thread: queue the events playout cares about and wake the worker."""
        if event_type == "MediaInputPlaybackEnded":
            self._push(("ended", str(event_data.get("inputName", ""))))
        elif event_type == "CurrentProgramSceneChanged":
            self._push(("scene", str(event_data.get("sceneName", ""))))
        elif event_type in SCENE_LIST_EVENTS:
            self._push(("scene_list", ""))

    def _on_disconnect(self, reason: str) -> None:
        self._push(("lost", reason))

    def _push(self, item: InboxItem) -> None:
        with self._inbox_lock:
            self._inbox.append(item)
        self.wake()
