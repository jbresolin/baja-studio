"""Add to OBS: a Media Source or a Browser Source reading one feed from the local MediaMTX.

Only ever runs when the operator presses Add to OBS (no OBS change without
a click). It goes through playout's one OBS connection (the `ObsGateway`
the cameras module also uses) and only *creates*: an input whose name is
taken is refused, never changed, and nothing else in OBS is touched.

The media source's settings (OBS's `ffmpeg_source`, keys as in
obs-ffmpeg-source.c):

- `is_local_file` false and `input` the RTSP URL: a network stream;
- `ffmpeg_options` `rtsp_transport=tcp fflags=nobuffer`: RTSP over TCP from
  127.0.0.1, no UDP ports, nothing lost (ADR 0022); and the frames ffmpeg
  reads while it looks at a new stream are dropped instead of queued, so
  playback can start at the live edge instead of that far behind for good
  (16 Nov bench: OBS 1.3 s behind the camera, ffplay with `nobuffer` 0.9 s;
  how much of the gap this closes is for the bench to say). OBS passes these
  words to ffmpeg's `avformat_open_input` as space-separated `key=value`
  pairs, so only format options act here (a decoder option such as
  `flags=low_delay` would be ignored);
- `reconnect_delay_sec` 2: when the sender drops, OBS tries again every two
  seconds, so the picture comes back by itself;
- `hw_decode` true: decoding on the GPU when it can;
- `buffering_mb` 1: the smallest network buffer, for the least delay;
- `restart_on_activate` true: the stream reopens (at the live edge) when the
  scene is shown, instead of resuming a stale buffer;
- `close_when_inactive` false: it stays connected while its scene is not on
  program, so it is ready the moment it is cut to.

The browser source (OBS's `browser_source`, keys as in obs-browser) shows
MediaMTX's own WebRTC player page for the feed: WebRTC plays each frame as
it arrives, the lowest delay there is, at the cost of decoding inside OBS's
browser. It is named after the feed with " (WebRTC)" added, so a feed can
have both sources and be compared side by side:

- `url` the player page (`urls.obs_browser_url`), `width`/`height` the
  feed's picture size (1920x1080 until it has been live);
- `css`: a transparent page with no message text, so an offline feed is
  empty like an offline media source instead of a grey box with an error;
- `shutdown` false and `restart_when_active` false: the page keeps playing
  while its scene is not on program, and is not reloaded when it is cut to;
- `reroute_audio` false: the page is muted anyway.
"""

from typing import Any

from baja_studio.adapters.obs_protocol import RequestStatus
from baja_studio.adapters.obs_requests import ObsRequestsMixin
from baja_studio.adapters.obs_transport import ObsError, ObsRequestFailedError
from baja_studio.cameras.drivers.obs_gateway import ObsGateway
from baja_studio.core.module import CommandError

MEDIA_SOURCE_KIND = "ffmpeg_source"
BROWSER_SOURCE_KIND = "browser_source"
RECONNECT_DELAY_SECONDS = 2
BUFFERING_MB = 1
LOW_DELAY_RTSP_OPTIONS = "rtsp_transport=tcp fflags=nobuffer"
BROWSER_SOURCE_SUFFIX = " (WebRTC)"
DEFAULT_PICTURE_SIZE = (1920, 1080)
BROWSER_SOURCE_FPS = 30
# Applied on top of MediaMTX's player page: no grey box, no message text.
BROWSER_SOURCE_CSS = (
    "body, #video { background-color: rgba(0, 0, 0, 0) !important; margin: 0; overflow: hidden; }"
    " #message { display: none !important; }"
)


def media_source_settings(read_url: str) -> dict[str, Any]:
    """The `inputSettings` of a media source reading `read_url` with low delay."""
    return {
        "is_local_file": False,
        "input": read_url,
        "input_format": "",
        "ffmpeg_options": LOW_DELAY_RTSP_OPTIONS,
        "reconnect_delay_sec": RECONNECT_DELAY_SECONDS,
        "hw_decode": True,
        "buffering_mb": BUFFERING_MB,
        "restart_on_activate": True,
        "close_when_inactive": False,
    }


def browser_source_settings(page_url: str, width: int, height: int) -> dict[str, Any]:
    """The `inputSettings` of a browser source showing a feed's WebRTC player page."""
    return {
        "is_local_file": False,
        "url": page_url,
        "width": width,
        "height": height,
        "fps_custom": True,
        "fps": BROWSER_SOURCE_FPS,
        "css": BROWSER_SOURCE_CSS,
        "shutdown": False,
        "restart_when_active": False,
        "reroute_audio": False,
    }


def browser_source_name(source_name: str) -> str:
    """The browser source's name: the media source's with " (WebRTC)", so both can exist."""
    return source_name + BROWSER_SOURCE_SUFFIX


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
    return _add_input(
        gateway, scene_name, source_name, MEDIA_SOURCE_KIND, media_source_settings(read_url)
    ) | {"url": read_url}


def add_browser_source(
    gateway: ObsGateway,
    scene_name: str,
    source_name: str,
    page_url: str,
    size: tuple[int, int] = DEFAULT_PICTURE_SIZE,
) -> dict[str, Any]:
    """Create the WebRTC browser source for a feed whose media source is `source_name`."""
    name = browser_source_name(source_name)
    settings = browser_source_settings(page_url, *size)
    return _add_input(gateway, scene_name, name, BROWSER_SOURCE_KIND, settings) | {"url": page_url}


def _add_input(
    gateway: ObsGateway, scene_name: str, source_name: str, kind: str, settings: dict[str, Any]
) -> dict[str, Any]:
    """Create one input of `kind` in `scene_name`; refuse if the name is taken."""
    client = _connected_client(gateway)
    try:
        if scene_name not in client.get_scene_list().scene_names:
            raise CommandError(f"OBS has no scene named {scene_name!r}; pick one from the list")
        if any(item.name == source_name for item in client.get_input_list()):
            raise CommandError(_name_taken(source_name))
        item_id = client.create_input(scene_name, source_name, kind, settings)
    except ObsRequestFailedError as error:
        if error.code == RequestStatus.RESOURCE_ALREADY_EXISTS:
            raise CommandError(_name_taken(source_name)) from error
        raise CommandError(f"OBS refused to create the source: {error.comment or error}") from error
    except ObsError as error:
        raise CommandError(f"could not reach OBS: {error}") from error
    return {"scene": scene_name, "source": source_name, "scene_item_id": item_id}


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
