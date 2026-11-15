"""A fake MediaMTX: its control API (v3), the config file it watches, publishers and readers.

Run it on the bench or in the rehearsal rig, so the feeds module has
something to talk to without MediaMTX or a drone:

    python -m baja_studio.fakes.fake_mediamtx --port 9997 --config <data>/mediamtx/mediamtx.yml

Tests use the same object in-process through `FakeMediamtx.transport()`,
which opens no port. Every behaviour below was measured with the real
MediaMTX v1.21.3 (Linux build, November 2026; ADR 0022), and the JSON it
answers has the shape of the recordings in `tests/modules/feeds_fixtures/`:

- **The config file is reloaded when it changes**, but a change less than
  one second after the previous reload is *ignored*, not postponed (its
  file watcher drops the event). The fake looks at the file whenever it is
  asked something, and applies the same rule.
- **A config it cannot load, or a port it cannot open, makes it exit**, on
  start and on a reload alike. The fake then refuses connections, like a
  stopped service.
- **Only configured paths accept a publisher.** Another name is refused and
  logged as `closed: path '<name>' is not configured`; a wrong password as
  `failed to authenticate: authentication failed`. With an `all_others`
  path, any name is accepted and listed under its own name.
- **A reload keeps live publishers** on paths whose config did not change.
- **Passwords never come back**: `/v3/config/global/get` says `<redacted>`.

Switches (tests call them; the runnable fake has them under `/_fake/`):
`publish`, `unpublish`, `add_reader`, `receive_bytes`, `stop`, `start`,
`occupy_port`.
"""

import argparse
import contextlib
import json
import threading
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

import httpx2
from argon2.exceptions import VerificationError
from argon2.low_level import Type, verify_secret

from baja_studio.core.clock import Clock, SystemClock

VERSION = "v1.21.3"
# Measured: a second file change within this long of a reload is dropped.
RELOAD_DEBOUNCE_SECONDS = 1.0
CATCH_ALL_PATH = "all_others"
# Where the fake's senders connect from, in its log lines.
SENDER = "192.168.10.50:50000"
ARGON2_PREFIX = "argon2:"
# What the real `source.type` says for each protocol a sender can use.
SOURCE_TYPES = {"RTMP": "rtmpConn", "SRT": "srtConn", "RTSP": "rtspSession"}


class FakeMediamtxExitedError(Exception):
    """The fake stopped the way MediaMTX exits: a config it could not load or a busy port."""


@dataclass
class FakePublisher:
    """A sender on one path."""

    protocol: str
    video_codec: str
    width: int
    height: int
    audio_codec: str | None
    online_since: str
    inbound_bytes: int = 0
    readers: int = 0


def parse_config_text(text: str) -> dict[str, Any]:
    """The studio's mediamtx.yml: comment lines, then a JSON body (which is also YAML)."""
    body = "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("#"))
    document = json.loads(body)
    if not isinstance(document, dict):
        raise ValueError("not a mapping")
    return document


def _ports_of(document: dict[str, Any]) -> set[int]:
    ports = set()
    for key in ("rtmpAddress", "srtAddress", "rtspAddress", "apiAddress"):
        value = str(document.get(key, ""))
        if value.rpartition(":")[2].isdigit():
            ports.add(int(value.rpartition(":")[2]))
    return ports


class FakeMediamtx:
    """MediaMTX's API and behaviour; thread-safe, so tests and a server can share it."""

    def __init__(
        self, clock: Clock, config_file: Path | None = None, senders: tuple[str, ...] = ()
    ) -> None:
        """Running, with the config in `config_file` if there is one (else no paths).

        `senders` are names that publish as soon as the config has a path for
        them (the rig's drone and laptop, always pushing).
        """
        self._clock = clock
        self._lock = threading.RLock()
        self._config_file = config_file
        self._running = True
        self._document: dict[str, Any] = {"paths": {}}
        self._last_seen_signature: tuple[int, int, int] | None = None
        self._last_reload_at: float | None = None
        self._publishers: dict[str, FakePublisher] = {}
        self._occupied_ports: set[int] = set()
        self._started = clock.now_utc().isoformat()
        self._senders = senders
        self.log_lines: list[str] = []
        self.reloads = 0
        self.look_at_config_file()

    # --- switches -------------------------------------------------------------------

    @property
    def running(self) -> bool:
        """False after `stop()` or an exit (bad config, busy port)."""
        with self._lock:
            return self._running

    def stop(self) -> None:
        """The service stops: every request now fails to connect."""
        with self._lock:
            self._running = False
            self._publishers.clear()

    def start(self) -> None:
        """The service starts again, reading its config file afresh (an exit if it cannot)."""
        with self._lock:
            self._running = True
            self._started = self._clock.now_utc().isoformat()
            self._last_seen_signature = None
            self._last_reload_at = None
            self.look_at_config_file()

    def occupy_port(self, port: int) -> None:
        """Another program takes `port`: MediaMTX exits if its config needs it."""
        with self._lock:
            self._occupied_ports.add(port)

    def publish(
        self,
        name: str,
        protocol: str = "RTMP",
        size: tuple[int, int] = (1920, 1080),
        video_codec: str = "H264",
        audio_codec: str | None = "MPEG-4 Audio",
        user: str = "",
        password: str = "",
    ) -> bool:
        """A sender pushes to path `name`; True if MediaMTX accepted it, as the real one decides."""
        with self._lock:
            self.look_at_config_file()
            if not self._running:
                return False
            paths = self._document.get("paths") or {}
            if name not in paths and CATCH_ALL_PATH not in paths:
                self._log(f"[{protocol}] [conn {SENDER}] closed: path '{name}' is not configured")
                return False
            if not self._may_publish(name, user, password):
                self._log(
                    f"[{protocol}] [conn {SENDER}] failed to authenticate: authentication failed"
                )
                return False
            self._publishers[name] = FakePublisher(
                protocol,
                video_codec,
                size[0],
                size[1],
                audio_codec,
                self._clock.now_utc().isoformat(),
            )
            self._log(f"[{protocol}] [conn {SENDER}] is publishing to path '{name}'")
            return True

    def unpublish(self, name: str) -> None:
        """The sender on `name` stops (or its link drops)."""
        with self._lock:
            self._publishers.pop(name, None)

    def add_reader(self, name: str) -> None:
        """Something (OBS) reads the path."""
        with self._lock:
            self._publishers[name].readers += 1

    def receive_bytes(self, name: str, count: int) -> None:
        """The publisher on `name` sent `count` more bytes."""
        with self._lock:
            self._publishers[name].inbound_bytes += count

    # --- the API --------------------------------------------------------------------

    def handle(self, request: httpx2.Request) -> httpx2.Response:
        """Answer one control API request, as MediaMTX would."""
        with self._lock:
            self.look_at_config_file()
            if not self._running:
                raise httpx2.ConnectError(
                    "connection refused (fake MediaMTX stopped)", request=request
                )
            path = request.url.path
            if request.method != "GET":
                return httpx2.Response(404, json={"status": "error", "error": "not found"})
            if path == "/v3/info":
                return httpx2.Response(200, json={"version": VERSION, "started": self._started})
            if path == "/v3/paths/list":
                items = self._path_items()
                return httpx2.Response(
                    200, json={"itemCount": len(items), "pageCount": 1, "items": items}
                )
            if path == "/v3/config/global/get":
                return httpx2.Response(200, json=self._global_config())
        return httpx2.Response(404, json={"status": "error", "error": "not found"})

    def transport(self) -> httpx2.MockTransport:
        """An httpx2 transport that reaches this fake without a network."""
        return httpx2.MockTransport(self.handle)

    # --- inside ---------------------------------------------------------------------

    def look_at_config_file(self) -> None:
        """Reload the config file if it changed, with MediaMTX's one-second blind spot.

        The real MediaMTX hears of a change from the operating system at once;
        the fake looks whenever it is asked something, and tests call this to
        make it look now. The first load (at start) is not a reload: it does
        not open the blind spot, as in MediaMTX's file watcher.
        """
        if self._config_file is None or not self._running or not self._config_file.is_file():
            return
        # A replaced file is a change even with the same text (a new file, a new inode).
        status = self._config_file.stat()
        signature = (status.st_ino, status.st_mtime_ns, status.st_size)
        if signature == self._last_seen_signature:
            return
        first_load = self._last_seen_signature is None
        self._last_seen_signature = signature
        text = self._config_file.read_text(encoding="utf-8")
        now = self._clock.monotonic()
        if (
            self._last_reload_at is not None
            and now - self._last_reload_at < RELOAD_DEBOUNCE_SECONDS
        ):
            return  # the real watcher drops this event: the change is never loaded
        if not first_load:
            self._last_reload_at = now
        try:
            document = parse_config_text(text)
        except ValueError as error:
            self._exit(f"ERR {error}")
            return
        busy = _ports_of(document) & self._occupied_ports
        if busy:
            self._exit(f"ERR listen tcp :{min(busy)}: bind: address already in use")
            return
        self._document = document
        if first_load:
            self._log("INF configuration loaded")
        else:
            self.reloads += 1
            self._log("INF reloading configuration (file changed)")
        paths = document.get("paths") or {}
        for name in list(self._publishers):
            if name not in paths and CATCH_ALL_PATH not in paths:
                del self._publishers[name]
        for name in self._senders:
            if name in paths and name not in self._publishers:
                self.publish(name, "SRT" if name.startswith("laptop") else "RTMP")

    def _exit(self, message: str) -> None:
        self._log(message)
        self._running = False
        self._publishers.clear()

    def _may_publish(self, name: str, user: str, password: str) -> bool:
        for entry in self._document.get("authInternalUsers") or []:
            permissions = entry.get("permissions") or []
            allowed = any(
                item.get("action") == "publish" and item.get("path", "") in ("", name)
                for item in permissions
            )
            if not allowed:
                continue
            if entry.get("user") == "any":
                return True
            if entry.get("user") == user and _password_matches(
                str(entry.get("pass", "")), password
            ):
                return True
        return False

    def _path_items(self) -> list[dict[str, Any]]:
        paths = self._document.get("paths") or {}
        names = [name for name in paths if name != CATCH_ALL_PATH]
        names += [name for name in self._publishers if name not in paths]
        return [self._path_item(name, paths) for name in sorted(names)]

    def _path_item(self, name: str, paths: dict[str, Any]) -> dict[str, Any]:
        publisher = self._publishers.get(name)
        conf_name = name if name in paths else CATCH_ALL_PATH
        if publisher is None:
            return {
                "name": name, "confName": conf_name, "ready": False, "readyTime": None,
                "available": False, "availableTime": None, "online": False, "onlineTime": None,
                "source": None, "tracks": [], "tracks2": [], "readers": [],
                "inboundBytes": 0, "outboundBytes": 0, "inboundFramesInError": 0,
                "bytesReceived": 0, "bytesSent": 0,
            }  # fmt: skip
        tracks2: list[dict[str, Any]] = [
            {
                "codec": publisher.video_codec,
                "codecProps": {"width": publisher.width, "height": publisher.height,
                               "profile": "High", "level": "4.1"},
            }
        ]  # fmt: skip
        if publisher.audio_codec:
            tracks2.append(
                {
                    "codec": publisher.audio_codec,
                    "codecProps": {"sampleRate": 48000, "channelCount": 2},
                }
            )
        since = publisher.online_since
        return {
            "name": name, "confName": conf_name, "ready": True, "readyTime": since,
            "available": True, "availableTime": since, "online": True, "onlineTime": since,
            "source": {"type": SOURCE_TYPES.get(publisher.protocol, "rtmpConn"), "id": name},
            "tracks": [track["codec"] for track in tracks2], "tracks2": tracks2,
            "readers": [{"type": "rtspSession", "id": f"r{n}"} for n in range(publisher.readers)],
            "inboundBytes": publisher.inbound_bytes, "outboundBytes": 0, "inboundFramesInError": 0,
            "bytesReceived": publisher.inbound_bytes, "bytesSent": 0,
        }  # fmt: skip

    def _global_config(self) -> dict[str, Any]:
        config = {key: value for key, value in self._document.items() if key != "paths"}
        config["authInternalUsers"] = [
            {**user, "pass": "<redacted>" if user.get("pass") else ""}
            for user in self._document.get("authInternalUsers") or []
        ]
        return config

    def _log(self, line: str) -> None:
        stamp = self._clock.now_utc().strftime("%Y/%m/%d %H:%M:%S")
        level_and_text = line if line[:4] in ("INF ", "ERR ", "WAR ") else f"INF {line}"
        self.log_lines.append(f"{stamp} {level_and_text}")


def _password_matches(stored: str, given: str) -> bool:
    if not stored.startswith(ARGON2_PREFIX):
        return stored == given
    try:
        return verify_secret(stored[len(ARGON2_PREFIX) :].encode(), given.encode(), Type.ID)
    except VerificationError:
        return False


def serve(fake: FakeMediamtx, host: str, port: int, log_file: Path | None) -> None:
    """Serve `fake` on `host`:`port` until Ctrl+C; `/_fake/<switch>?...` drives it."""

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            url = urlparse(self.path)
            if url.path.startswith("/_fake/"):
                self._switch(url.path.removeprefix("/_fake/"), parse_qs(url.query))
                return
            request = httpx2.Request("GET", f"http://127.0.0.1:{port}{self.path}")
            try:
                response = fake.handle(request)
            except httpx2.ConnectError:
                self.close_connection = True
                return
            finally:
                if log_file is not None:
                    # Where NSSM would keep MediaMTX's output (the studio reads its end).
                    log_file.parent.mkdir(parents=True, exist_ok=True)
                    log_file.write_text("\n".join(fake.log_lines) + "\n", encoding="utf-8")
            self._send(response.status_code, response.content)

        def _switch(self, name: str, query: dict[str, list[str]]) -> None:
            feed = query.get("name", [""])[0]
            if name == "publish":
                protocol = query.get("protocol", ["RTMP"])[0]
                accepted = fake.publish(
                    feed, protocol, password=query.get("pass", [""])[0], user=feed
                )
                self._send(200, json.dumps({"accepted": accepted}).encode())
            elif name == "unpublish":
                fake.unpublish(feed)
                self._send(200, b"{}")
            elif name in ("stop", "start"):
                getattr(fake, name)()
                self._send(200, b"{}")
            else:
                self._send(404, b'{"error": "unknown switch"}')

        def _send(self, status: int, body: bytes) -> None:
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format: str, *args: Any) -> None:
            """Quiet: the studio polls every two seconds."""

    with ThreadingHTTPServer((host, port), Handler) as server:
        print(f"fake MediaMTX control API on http://{host}:{port} (Ctrl+C to stop)")
        with contextlib.suppress(KeyboardInterrupt):
            server.serve_forever()


def main() -> None:
    """Command line: `--host`, `--port`, the `--config` file to watch, a `--log` to write."""
    parser = argparse.ArgumentParser(description="A fake MediaMTX control API.")
    parser.add_argument("--host", default="127.0.0.1", help="like MediaMTX's API: loopback")
    parser.add_argument("--port", type=int, default=9997)
    parser.add_argument("--config", type=Path, help="the mediamtx.yml the studio writes")
    parser.add_argument("--log", type=Path, help="where to write MediaMTX-style log lines")
    parser.add_argument(
        "--senders", default="", help="comma-separated names that publish once configured"
    )
    arguments = parser.parse_args()
    senders = tuple(name for name in arguments.senders.split(",") if name)
    fake = FakeMediamtx(SystemClock(), arguments.config, senders)
    serve(fake, arguments.host, arguments.port, arguments.log)


if __name__ == "__main__":
    main()
