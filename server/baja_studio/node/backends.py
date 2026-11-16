"""What drives a node camera in each video mode: the driver, its snapshots, its page details.

- `obs` mode: the studio's own `ObsCropDriver` (ADR 0020), on a connection to
  this laptop's OBS from the studio's `RemoteObsPool` (ADR 0021: one
  connection per OBS address, shared by both cameras, reconnecting by
  itself, with its keep-alive). The node camera is described to them as the
  `obs_crop` camera it is (`obs_camera_config`), so they run unchanged.
- `node` mode: `PipelineCropDriver` and the node's video pipeline
  (`video/`), given a `PipelineSpec` built from the camera's settings.

`backend_key` says when a camera needs a new backend: another mode, another
OBS port, or another pipeline spec. Everything else (source, filter,
limits, speeds) is read by the running driver on its next step.
"""

from collections.abc import Callable
from dataclasses import replace
from typing import Any

from baja_studio.cameras.config import CameraConfig, DriverKind
from baja_studio.cameras.crop_config import ObsConnection, ObsCropSettings
from baja_studio.cameras.crop_window import CropWindow
from baja_studio.cameras.driver import DriverHost
from baja_studio.cameras.drivers.obs_crop import ObsCropDriver
from baja_studio.cameras.drivers.obs_gateway import ObsGateway
from baja_studio.cameras.remote_obs import RemoteObsPool
from baja_studio.node.camera import Backend
from baja_studio.node.pipeline_driver import PipelineCropDriver
from baja_studio.node.redact import redacted_url
from baja_studio.node.settings import LOCAL_OBS_HOST, NodeCameraSettings, VideoMode
from baja_studio.node.snapshots import ObsSnapshots, Snapshot, no_snapshot
from baja_studio.node.video.pipeline_types import PipelineSpec, VideoPipeline

NO_OBS_SOURCE = "OBS mode: choose the camera's source in OBS on this node's page"
JPEG = "image/jpeg"
# The newest ffmpeg lines the node's page shows for a camera in node mode.
FFMPEG_LOG_LINES = 15

MakePipeline = Callable[[PipelineSpec], VideoPipeline]


def crop_settings(camera: NodeCameraSettings) -> ObsCropSettings:
    """The camera's window limits and speeds (and, in OBS mode, its OBS) as crop settings."""
    return ObsCropSettings(
        obs=ObsConnection(
            remote=True, host=LOCAL_OBS_HOST, port=camera.obs.port, password=camera.obs.password
        ),
        filter_name=camera.obs.filter_name,
        min_window=camera.min_window,
        update_hz=camera.update_hz,
        pan_speed=camera.pan_speed,
        tilt_speed=camera.tilt_speed,
        zoom_speed=camera.zoom_speed,
        speed_follows_zoom=camera.speed_follows_zoom,
        preview_source=camera.obs.preview_source or None,
    )


def obs_camera_config(camera: NodeCameraSettings) -> CameraConfig | None:
    """The camera as the studio's `obs_crop` driver knows cameras; None without a source."""
    if not camera.obs.source.strip():
        return None
    return CameraConfig(
        id=camera.id,
        label=camera.label,
        driver=DriverKind.OBS_CROP,
        obs_source=camera.obs.source,
        crop=crop_settings(camera),
    )


def pipeline_camera_config(camera: NodeCameraSettings) -> CameraConfig:
    """The camera's limits and speeds in the shape the shared crop motion reads them."""
    return CameraConfig(
        id=camera.id, label=camera.label, driver=DriverKind.NONE, crop=crop_settings(camera)
    )


def pipeline_spec(camera: NodeCameraSettings, ffmpeg_path: str) -> PipelineSpec:
    """The pipeline a `node` mode camera runs."""
    video = camera.video
    return PipelineSpec(
        device=video.device,
        input_width=video.input_width,
        input_height=video.input_height,
        input_fps=video.input_fps,
        output_url=video.output_url,
        bitrate_kbps=video.bitrate_kbps,
        encoder=video.encoder,
        ffmpeg_path=ffmpeg_path,
        decoder=video.decoder,
        label=camera.label or camera.id,
    )


def backend_key(camera: NodeCameraSettings, ffmpeg_path: str) -> tuple[object, ...]:
    """What a backend is built from; a change means stopping it and building another."""
    if camera.mode == VideoMode.OBS:
        return (VideoMode.OBS, camera.obs.port, bool(camera.obs.source.strip()))
    # Without the label: renaming a camera must not restart its ffmpeg (the running
    # pipeline keeps naming it by its old label in its messages until the next restart).
    return (VideoMode.NODE, replace(pipeline_spec(camera, ffmpeg_path), label=""))


def obs_backend(
    host: DriverHost,
    pool: RemoteObsPool,
    config: Callable[[], CameraConfig],
    window: Callable[[], dict[str, Any] | None],
) -> Backend:
    """OBS mode: the studio's crop driver on the pool's connection to this laptop's OBS."""
    first = config()
    gateway = pool.lease(first.id, first.crop.obs)
    driver = ObsCropDriver(host, gateway, config, save_window=lambda n, saved: None)
    snapshots = ObsSnapshots(host.clock, gateway)

    def snapshot(width: int) -> Snapshot:
        crop = config().crop
        return snapshots.take(config().obs_source or "", crop.preview_source or "", width, window)

    return Backend(VideoMode.OBS.value, driver, snapshot, lambda: _obs_details(gateway, config()))


def pipeline_backend(
    host: DriverHost,
    config: Callable[[], CameraConfig],
    spec: PipelineSpec,
    make_pipeline: MakePipeline,
    initial_window: CropWindow | None,
) -> Backend:
    """Node mode: the node's own pipeline, given the window the camera had."""
    driver = PipelineCropDriver(host, config, spec, make_pipeline, initial_window)

    def snapshot(width: int) -> Snapshot:
        try:
            picture = driver.snapshot()
        except Exception as error:  # a broken pipeline is no picture, never a 500
            raise no_snapshot(f"the pipeline gave no picture: {error}") from None
        if picture is None:
            raise no_snapshot(f"no picture yet: {host.camera_id} {_pipeline_words(driver)}")
        return Snapshot(picture, JPEG)

    return Backend(VideoMode.NODE.value, driver, snapshot, lambda: _pipeline_details(driver))


def _obs_details(gateway: ObsGateway, config: CameraConfig) -> dict[str, Any]:
    """For the node's page: the OBS link and what the camera uses in it (never the password)."""
    link = gateway.link()
    return {
        "obs": {
            "connected": link.connected,
            "problem": "" if link.connected else link.problem,
            "source": config.obs_source,
            "filter": config.crop.filter_name,
            "preview_source": config.crop.preview_source,
        }
    }


def _pipeline_details(driver: PipelineCropDriver) -> dict[str, Any]:
    """For the node's page: the pipeline's figures, and where it sends (without secrets)."""
    status = driver.pipeline_status()
    figures = None
    if status is not None:
        figures = {
            "state": status.state.value,
            "reason": status.reason,
            "input_fps": round(status.fps_in, 1),
            "output_fps": round(status.fps_out, 1),
            "dropped_frames": status.dropped_frames,
            "bitrate_kbps": round(status.bitrate_kbps),
            "encoder": status.encoder,
            "restarts": status.restarts,
            "window_apply_ms": status.window_apply_ms,
        }
    spec = driver.spec
    return {
        "pipeline": figures,
        "ffmpeg_log": driver.log_tail()[-FFMPEG_LOG_LINES:],
        "output": redacted_url(spec.output_url),
        "device": spec.device,
        "input": f"{spec.input_width}x{spec.input_height} at {spec.input_fps} fps",
    }


def _pipeline_words(driver: PipelineCropDriver) -> str:
    status = driver.pipeline_status()
    return "has no pipeline" if status is None else f"pipeline {status.state.value}"


def window_of(crop: Any) -> CropWindow | None:
    """A published `crop` entry as a window (to hand a new backend the window it had)."""
    if not isinstance(crop, dict) or not all(k in crop for k in ("centre_x", "centre_y", "w")):
        return None
    return CropWindow(float(crop["centre_x"]), float(crop["centre_y"]), float(crop["w"]))
