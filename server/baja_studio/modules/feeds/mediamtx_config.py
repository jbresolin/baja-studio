"""Writes `mediamtx.yml` from the feeds settings, where the BajaStudioFeeds service reads it.

The file lives in `<data>/mediamtx/`; the service starts MediaMTX with it,
so feeds come up even before the studio does. MediaMTX watches the file
and reloads it by itself when it changes (no restart, live feeds on other
paths keep going: measured with v1.21.3, ADR 0022).

The body is JSON, which is also YAML: MediaMTX reads it as YAML, and the
studio (and the fake MediaMTX) read it back with the standard library to
check what MediaMTX is running. Only the comment lines at the top are
YAML-only.

What the config always does, whatever the settings say:

- turns off every server the studio does not use: HLS (its default port is
  8888, the studio's!), MoQ, playback, metrics, pprof;
- WebRTC, for OBS Browser Sources (`add_to_obs` with `source: browser`): its
  page and signalling on 127.0.0.1 only, so only this PC can ask for a
  stream; the media on a UDP port of every address, because the browser
  connects to this PC's own address, and with no firewall rule for it (the
  installer opens none) no other PC reaches it. No STUN or TURN server:
  nothing leaves the PC;
- RTSP over TCP only, so MediaMTX opens no RTP/RTCP UDP ports, and on
  127.0.0.1 unless the settings open it to the LAN;
- the control API on 127.0.0.1 only;
- only the listed feeds are paths (plus a catch-all with "accept any name");
- publishing on a feed is allowed from anywhere (with its password if it
  has one); reading only from this PC unless RTSP is open to the LAN;
- `sysLogPrefix` carries a fingerprint of everything else in the file.
  MediaMTX only uses that field for syslog, which is off, but its API
  reports it: reading it back tells whether MediaMTX is running the file
  the studio wrote last (its file watcher ignores a second change within
  a second of the first, measured with v1.21.3; see controller.py).

The installer writes the defaults once, before the service first starts:

    python -m baja_studio.modules.feeds.mediamtx_config --data-dir <data folder> --if-missing
"""

import argparse
import hashlib
import json
import os
import time
from pathlib import Path
from typing import Any

from baja_studio.modules.feeds.config import FeedsSettings
from baja_studio.modules.feeds.passwords import install_salt, mediamtx_password_hash

CONFIG_DIRECTORY_NAME = "mediamtx"
CONFIG_FILE_NAME = "mediamtx.yml"
RECORDINGS_DIRECTORY_NAME = "feeds-recordings"
LOOPBACK = "127.0.0.1"
LOOPBACK_NETWORKS = ["127.0.0.1", "::1"]
# One file per segment; `%z` keeps daylight-saving changes from overwriting one (MediaMTX warns).
RECORD_FILE_PATTERN = "%Y-%m-%d_%H-%M-%S-%f_%z"
RECORD_SEGMENT_DURATION = "10m"
# MediaMTX deletes recordings after one day by default; the race's footage must stay.
RECORD_DELETE_AFTER = "0s"
# The name MediaMTX gives the path that matches every name not listed.
CATCH_ALL_PATH = "all_others"
REPLACE_ATTEMPTS = 5
REPLACE_RETRY_SECONDS = 0.1
FINGERPRINT_FIELD = "sysLogPrefix"
FINGERPRINT_PREFIX = "baja-studio-"
FINGERPRINT_LENGTH = 16
HEADER = (
    "# Written by Baja Studio from the feeds settings (Feeds page). Edits here are overwritten.\n"
    "# The body is JSON, which MediaMTX reads as YAML.\n"
)


def config_directory(data_directory: Path) -> Path:
    """`<data>/mediamtx`: MediaMTX's working folder, with its config and the password salt."""
    return data_directory / CONFIG_DIRECTORY_NAME


def config_path(data_directory: Path) -> Path:
    """Where MediaMTX's config file lives: `<data>/mediamtx/mediamtx.yml`."""
    return config_directory(data_directory) / CONFIG_FILE_NAME


def recordings_root(settings: FeedsSettings, data_directory: Path) -> Path:
    """Where recorded feeds go: the setting, or `<data>/feeds-recordings`."""
    if settings.recordings_folder:
        return Path(settings.recordings_folder)
    return data_directory / RECORDINGS_DIRECTORY_NAME


def config_document(settings: FeedsSettings, data_directory: Path, salt: bytes) -> dict[str, Any]:
    """MediaMTX's whole configuration for `settings`, as a dict (keys as MediaMTX names them)."""
    document = _document_without_fingerprint(settings, data_directory, salt)
    return {FINGERPRINT_FIELD: config_fingerprint(document), **document}


def config_fingerprint(document: dict[str, Any]) -> str:
    """A short hash of a config document (its fingerprint field left out)."""
    without = {key: value for key, value in document.items() if key != FINGERPRINT_FIELD}
    canonical = json.dumps(without, sort_keys=True).encode("utf-8")
    return FINGERPRINT_PREFIX + hashlib.sha256(canonical).hexdigest()[:FINGERPRINT_LENGTH]


def _document_without_fingerprint(
    settings: FeedsSettings, data_directory: Path, salt: bytes
) -> dict[str, Any]:
    recordings = recordings_root(settings, data_directory).as_posix()
    if "%" in recordings or '"' in recordings:
        raise ValueError(f"the recordings folder {recordings!r} cannot be used by MediaMTX")
    rtsp_host = "" if settings.rtsp_on_lan else LOOPBACK
    return {
        "logLevel": "info",
        # NSSM keeps what MediaMTX prints in <data>\logs\mediamtx-stdout.log.
        "logDestinations": ["stdout"],
        "api": True,
        "apiAddress": f"{LOOPBACK}:{settings.api_port}",
        "metrics": False,
        "pprof": False,
        "playback": False,
        "hls": False,
        "webrtc": True,
        "webrtcAddress": f"{LOOPBACK}:{settings.webrtc_port}",
        "webrtcEncryption": False,
        # The player page is served from the same address, so no other origin needs in.
        "webrtcAllowOrigins": [f"http://{LOOPBACK}:{settings.webrtc_port}"],
        "webrtcLocalUDPAddress": f":{settings.webrtc_media_port}",
        "webrtcLocalTCPAddress": "",
        # Offer this PC's own addresses (and 127.0.0.1), never a public one.
        "webrtcIPsFromInterfaces": True,
        "webrtcAdditionalHosts": [LOOPBACK],
        "webrtcICEServers2": [],
        "moq": False,
        "rtsp": True,
        "rtspTransports": ["tcp"],
        "rtspEncryption": "no",
        "rtspAddress": f"{rtsp_host}:{settings.rtsp_port}",
        "rtmp": True,
        "rtmpEncryption": "no",
        "rtmpAddress": f":{settings.rtmp_port}",
        "srt": True,
        "srtAddress": f":{settings.srt_port}",
        "authMethod": "internal",
        "authInternalUsers": _users(settings, salt),
        "pathDefaults": {
            "record": False,
            "recordPath": f"{recordings}/%path/{RECORD_FILE_PATTERN}",
            "recordFormat": "fmp4",
            "recordSegmentDuration": RECORD_SEGMENT_DURATION,
            "recordDeleteAfter": RECORD_DELETE_AFTER,
            # A sender that reconnects replaces its own stale connection at once.
            "overridePublisher": True,
        },
        "paths": _paths(settings),
    }


def _users(settings: FeedsSettings, salt: bytes) -> list[dict[str, Any]]:
    """Who may do what: one user per password-protected feed, then anonymous rules.

    MediaMTX tries the users in order and lets an action through if any user
    allows it. A feed with a password is therefore never in the anonymous
    publish list, and "accept any name" (which would cover it) cannot be on
    together with passwords (see config.py).
    """
    users: list[dict[str, Any]] = []
    open_feeds = []
    for feed in sorted(settings.feeds, key=lambda item: item.name):
        if not feed.password:
            open_feeds.append(feed.name)
            continue
        users.append(
            {
                "user": feed.name,
                "pass": mediamtx_password_hash(feed.password, feed.name, salt),
                "ips": [],
                "permissions": [{"action": "publish", "path": feed.name}],
            }
        )
    publish = [{"action": "publish", "path": name} for name in open_feeds]
    if settings.accept_any_name:
        # An empty path means every path.
        publish = [{"action": "publish", "path": ""}]
    if publish:
        users.append({"user": "any", "pass": "", "ips": [], "permissions": publish})
    reader_networks = [] if settings.rtsp_on_lan else LOOPBACK_NETWORKS
    users.append(
        {
            "user": "any",
            "pass": "",
            "ips": reader_networks,
            "permissions": [{"action": "read", "path": ""}],
        }
    )
    # The control API answers only on 127.0.0.1 anyway; this lets the studio in without a password.
    users.append(
        {"user": "any", "pass": "", "ips": LOOPBACK_NETWORKS, "permissions": [{"action": "api"}]}
    )
    return users


def _paths(settings: FeedsSettings) -> dict[str, Any]:
    """One path per feed (sorted, so the file does not change when the list is reordered)."""
    paths: dict[str, Any] = {
        feed.name: {"record": feed.record}
        for feed in sorted(settings.feeds, key=lambda item: item.name)
    }
    if settings.accept_any_name:
        paths[CATCH_ALL_PATH] = {}
    return paths


def render_config(settings: FeedsSettings, data_directory: Path, salt: bytes) -> str:
    """The whole `mediamtx.yml` text for `settings`."""
    body = json.dumps(config_document(settings, data_directory, salt), indent=2)
    return HEADER + body + "\n"


def parse_config(text: str) -> dict[str, Any]:
    """Read back a file `render_config` wrote (comment lines first, then JSON)."""
    body = "\n".join(line for line in text.splitlines() if not line.startswith("#"))
    document = json.loads(body)
    if not isinstance(document, dict):
        raise ValueError("mediamtx.yml does not hold a JSON object")
    return document


def write_config(path: Path, text: str, force: bool = False) -> bool:
    """Write `text` atomically (MediaMTX may read at any moment); False if already identical.

    `os.replace` makes MediaMTX's file watcher see one complete new file,
    never a half-written one. `force` replaces the file even when the text
    is the same, which MediaMTX's watcher still sees as a change.
    """
    if not force and path.is_file() and path.read_text(encoding="utf-8") == text:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(text, encoding="utf-8", newline="\n")
    for attempt in range(REPLACE_ATTEMPTS):
        try:
            os.replace(temporary, path)
            return True
        except PermissionError:
            # Windows refuses to replace a file another program has open; MediaMTX
            # holds it for the milliseconds a reload reads it. Try again shortly.
            if attempt == REPLACE_ATTEMPTS - 1:
                raise
            time.sleep(REPLACE_RETRY_SECONDS)
    return True


def render_for_data_directory(settings: FeedsSettings, data_directory: Path) -> str:
    """`render_config` with this install's password salt (made on first use)."""
    salt = install_salt(config_directory(data_directory))
    return render_config(settings, data_directory, salt)


def write_default_config(data_directory: Path, only_if_missing: bool) -> Path:
    """Write the config for default settings (fresh install); returns its path."""
    path = config_path(data_directory)
    if not (only_if_missing and path.is_file()):
        write_config(path, render_for_data_directory(FeedsSettings(), data_directory))
    return path


def main() -> None:
    """Command line used by the installer before it first starts the feeds service."""
    parser = argparse.ArgumentParser(description="Write the default MediaMTX config.")
    parser.add_argument("--data-dir", type=Path, required=True, help="the studio's data folder")
    parser.add_argument(
        "--if-missing", action="store_true", help="keep an existing mediamtx.yml (upgrades)"
    )
    arguments = parser.parse_args()
    print(write_default_config(arguments.data_dir, arguments.if_missing))


if __name__ == "__main__":
    main()
