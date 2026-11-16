"""Add to OBS: a Media Source reading one feed from the local MediaMTX.

Only ever runs when the operator presses Add to OBS (no OBS change without
a click). It goes through playout's one OBS connection (the `ObsGateway`
the cameras module also uses) and only *creates*: an input whose name is
taken is refused, never changed, and nothing else in OBS is touched.

The media source's settings (OBS's `ffmpeg_source`, keys as in
obs-ffmpeg-source.c):

- `is_local_file` false and `input` the RTSP URL: a network stream;
- `ffmpeg_options` `rtsp_transport=tcp`: RTSP over TCP from 127.0.0.1, no
  UDP ports, nothing lost (ADR 0022);
- `reconnect_delay_sec` 2: when the sender drops, OBS tries again every two
  seconds, so the picture comes back by itself;
- `hw_decode` true: decoding on the GPU when it can;
- `buffering_mb` 1: the smallest network buffer, for the least delay;
- `restart_on_activate` true: the stream reopens (at the live edge) when the
  scene is shown, instead of resuming a stale buffer;
- `close_when_inactive` false: it stays connected while its scene is not on
  program, so it is ready the moment it is cut to.
"""

from typing import Any

from baja_studio.adapters.obs_protocol import RequestStatus
from baja_studio.adapters.obs_requests import ObsRequestsMixin
from baja_studio.adapters.obs_transport import ObsError, ObsRequestFailedError
from baja_studio.cameras.drivers.obs_gateway import ObsGateway
from baja_studio.core.module import CommandError

MEDIA_SOURCE_KIND = "ffmpeg_source"
RECONNECT_DELAY_SECONDS = 2
BUFFERING_MB = 1
RTSP_OVER_TCP = "rtsp_transport=tcp"


def media_source_settings(read_url: str) -> dict[str, Any]:
    """The `inputSettings` of a media source reading `read_url` with low delay."""
    return {
        "is_local_file": False,
        "input": read_url,
        "input_format": "",
        "ffmpeg_options": RTSP_OVER_TCP,
        "reconnect_delay_sec": RECONNECT_DELAY_SECONDS,
        "hw_decode": True,
        "buffering_mb": BUFFERING_MB,
        "restart_on_activate": True,
        "close_when_inactive": False,
    }


def obs_scene_names(gateway: ObsGateway) -> list[str]:
    """OBS's scenes, top of its list first, for the scene picker."""
    client = _connected_client(gateway)
    try:
        return client.get_scene_list().scene_names
    except ObsError as error:
        raise CommandError(f"could not read OBS's scenes: {error}") from error


def add_media_source(
    gateway: ObsGateway, scene_name: str, source_name: str, read_url: str
) -> dict[str, Any]:
    """Create the media source `source_name` in `scene_name`; refuse if the name is taken."""
    client = _connected_client(gateway)
    try:
        if scene_name not in client.get_scene_list().scene_names:
            raise CommandError(f"OBS has no scene named {scene_name!r}; pick one from the list")
        if any(item.name == source_name for item in client.get_input_list()):
            raise CommandError(_name_taken(source_name))
        item_id = client.create_input(
            scene_name, source_name, MEDIA_SOURCE_KIND, media_source_settings(read_url)
        )
    except ObsRequestFailedError as error:
        if error.code == RequestStatus.RESOURCE_ALREADY_EXISTS:
            raise CommandError(_name_taken(source_name)) from error
        raise CommandError(f"OBS refused to create the source: {error.comment or error}") from error
    except ObsError as error:
        raise CommandError(f"could not reach OBS: {error}") from error
    return {"scene": scene_name, "source": source_name, "url": read_url, "scene_item_id": item_id}


def _connected_client(gateway: ObsGateway) -> ObsRequestsMixin:
    link = gateway.link()
    client = gateway.client()
    if not link.connected or client is None:
        raise CommandError(f"OBS is not connected: {link.reason or 'see the playout page'}")
    return client


def _name_taken(source_name: str) -> str:
    return (
        f"OBS already has a source named {source_name!r}; the studio never changes an existing "
        "source. Delete or rename it in OBS, or give the feed another label."
    )
