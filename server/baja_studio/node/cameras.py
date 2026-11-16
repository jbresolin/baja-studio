"""The cameras the node serves, kept matching its settings without restarting (ADR 0023).

`apply(settings)` runs on the node's `apply` worker after every settings
change:

- a camera that is new gets a `NodeCamera` and the backend of its video mode;
- a camera whose backend key changed (`backends.backend_key`: another video
  mode, another OBS port, another pipeline spec) has its old backend
  stopped cleanly (the driver's worker waits for its pipeline, the OBS
  lease is given back) and the new one started; the camera itself, with its
  mode and window, stays, so the studio sees the same camera throughout;
- a camera removed or disabled is stopped and leaves `/agent/info`;
- everything else (labels, limits, speeds, an OBS source or filter name) is
  read by the running driver on its next step.

Stopping a driver waits for its worker, so it happens outside every lock.
Every OBS-mode camera's OBS password is chosen before any lease, by the
studio's pool rule (the camera saved last wins; there is one local OBS).
"""

import threading
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from baja_studio.cameras.config import CameraConfig
from baja_studio.cameras.driver import DriverHost
from baja_studio.cameras.remote_obs import RemoteObsPool
from baja_studio.core.module import ModuleContext
from baja_studio.node import backends
from baja_studio.node.camera import Backend, NodeCamera
from baja_studio.node.settings import NodeCameraSettings, NodeSettings, VideoMode


@dataclass
class _Served:
    """One served camera: its settings now, its contract camera, and its backend's key."""

    settings: NodeCameraSettings
    camera: NodeCamera
    key: tuple[object, ...] | None = None
    # The crop settings its driver reads (rebuilt at each apply).
    config: CameraConfig | None = None


class NodeCameras:
    """Every served camera, by id, in settings order."""

    def __init__(
        self,
        context: ModuleContext,
        pool: RemoteObsPool,
        make_pipeline: backends.MakePipeline,
        ffmpeg_path: Callable[[NodeSettings], str],
        image_controls: Callable[[str], list[str]],
    ) -> None:
        """`ffmpeg_path(settings)` resolves the ffmpeg to run; `image_controls(id)` the names
        a camera lists."""
        self._context = context
        self._pool = pool
        self._make_pipeline = make_pipeline
        self._ffmpeg_path = ffmpeg_path
        self._image_controls = image_controls
        self._lock = threading.Lock()
        self._apply_lock = threading.Lock()
        self._served: dict[str, _Served] = {}

    def cameras(self) -> list[NodeCamera]:
        """The served cameras, in settings order."""
        with self._lock:
            return [served.camera for served in self._served.values()]

    def camera(self, camera_id: str) -> NodeCamera | None:
        """A served camera by id."""
        with self._lock:
            served = self._served.get(camera_id)
        return None if served is None else served.camera

    def settings_of(self, camera_id: str) -> NodeCameraSettings | None:
        """A served camera's settings now."""
        with self._lock:
            served = self._served.get(camera_id)
        return None if served is None else served.settings

    def details(self, camera_id: str) -> dict[str, Any]:
        """The current backend's details for the page (pipeline figures, OBS link)."""
        camera = self.camera(camera_id)
        backend = None if camera is None else camera.backend
        return {} if backend is None else backend.details()

    def apply(self, settings: NodeSettings) -> None:
        """Make the running cameras match `settings`."""
        with self._apply_lock:
            wanted = {camera.id: camera for camera in settings.served()}
            ffmpeg = self._ffmpeg_path(settings)
            obs_configs = [
                config
                for camera in wanted.values()
                if camera.mode == VideoMode.OBS
                and (config := backends.obs_camera_config(camera)) is not None
            ]
            self._pool.choose_passwords(obs_configs)
            with self._lock:
                leaving = [served for key, served in self._served.items() if key not in wanted]
                for served in leaving:
                    del self._served[served.settings.id]
            for served in leaving:
                self._stop_backend(served)
                self._forget_state(served.settings.id)
            for camera_id, camera_settings in wanted.items():
                self._apply_camera(camera_id, camera_settings, ffmpeg)
            with self._lock:
                self._served = {camera_id: self._served[camera_id] for camera_id in wanted}
            self._context.update_state("order", list(wanted))

    def stop_all(self) -> None:
        """Stop every backend (the module is stopping)."""
        with self._lock:
            served, self._served = list(self._served.values()), {}
        for each in served:
            self._stop_backend(each)

    # --- one camera ------------------------------------------------------------------------

    def _apply_camera(self, camera_id: str, settings: NodeCameraSettings, ffmpeg: str) -> None:
        with self._lock:
            served = self._served.get(camera_id)
            if served is None:
                camera = NodeCamera(
                    lambda: self._current_settings(camera_id, settings),
                    self._context.clock,
                    lambda: self._image_controls(camera_id),
                )
                served = self._served[camera_id] = _Served(settings, camera)
            served.settings = settings
        key = backends.backend_key(settings, ffmpeg)
        self._context.update_state(f"cameras.{camera_id}.label", settings.label or settings.id)
        self._context.update_state(f"cameras.{camera_id}.mode", settings.mode.value)
        if key == served.key and served.camera.backend is not None:
            served.config = self._config_for(settings)
            return
        window = backends.window_of(served.camera.field("crop"))
        self._stop_backend(served)
        served.key = key
        served.config = self._config_for(settings)
        if served.config is None:
            served.camera.use(None, backends.NO_OBS_SOURCE)
            self._context.update_state(f"cameras.{camera_id}.status", "offline")
            self._context.update_state(f"cameras.{camera_id}.status_reason", backends.NO_OBS_SOURCE)
            return
        self._start_backend(served, ffmpeg, window)

    def _config_for(self, settings: NodeCameraSettings) -> CameraConfig | None:
        if settings.mode == VideoMode.OBS:
            return backends.obs_camera_config(settings)
        return backends.pipeline_camera_config(settings)

    def _start_backend(self, served: _Served, ffmpeg: str, window: Any) -> None:
        camera_id = served.settings.id
        holder: list[Backend] = []
        host = self._host(camera_id, served.camera, holder)

        def config() -> CameraConfig:
            assert served.config is not None
            return served.config

        if served.settings.mode == VideoMode.OBS:
            backend = backends.obs_backend(
                host, self._pool, config, lambda: served.camera.field("crop")
            )
        else:
            spec = backends.pipeline_spec(served.settings, ffmpeg)
            backend = backends.pipeline_backend(host, config, spec, self._make_pipeline, window)
        holder.append(backend)
        served.camera.use(backend)
        self._context.logger.info(
            "camera %s: %s mode started", camera_id, served.settings.mode.value
        )
        backend.driver.connect()

    def _stop_backend(self, served: _Served) -> None:
        backend = served.camera.backend
        if backend is None:
            return
        served.camera.use(None)
        backend.driver.disconnect()  # waits for the driver's worker (and its pipeline)
        if backend.mode == VideoMode.OBS.value:
            self._pool.release(served.settings.id)
        self._context.logger.info("camera %s: %s mode stopped", served.settings.id, backend.mode)

    def _host(self, camera_id: str, camera: NodeCamera, holder: list[Backend]) -> DriverHost:
        context = self._context

        def publish(key: str, value: Any) -> None:
            if holder:
                camera.published(holder[0], key, value)
            if camera.backend is not None and holder and camera.backend is holder[0]:
                context.update_state(f"cameras.{camera_id}.{key}", value)

        return DriverHost(
            camera_id=camera_id,
            clock=context.clock,
            logger=context.logger,
            publish=publish,
            start_worker=context.start_worker,
            stop_worker=context.stop_worker,
        )

    def _current_settings(self, camera_id: str, fallback: NodeCameraSettings) -> NodeCameraSettings:
        with self._lock:
            served = self._served.get(camera_id)
        return fallback if served is None else served.settings

    def _forget_state(self, camera_id: str) -> None:
        entries = self._context.state.get(self._context.name, "cameras") or {}
        entries.pop(camera_id, None)
        self._context.update_state("cameras", entries)
