"""A fake Caddy admin API: `POST /load` (Caddyfile) and `GET /config/`.

Run it on the bench, so the gateway module has something to talk to without
Caddy or a certificate:

    python -m baja_studio.fakes.fake_caddy --port 2019

Tests use the same object in-process through `FakeCaddy.transport()`, which
opens no port. Switches: `stop()` (Caddy is down: connections are refused),
`start()`, and `refuse_next_loads(message)` (Caddy rejects configs, as it does
one with a typo, and keeps the old one running).
"""

import argparse
import contextlib
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

import httpx2

CADDYFILE_CONTENT_TYPE = "text/caddyfile"


def check_caddyfile(text: str) -> str | None:
    """The fake's stand-in for Caddy's parser: braces must balance. None when fine."""
    depth = 0
    for number, line in enumerate(text.splitlines(), start=1):
        if line.lstrip().startswith("#"):
            continue
        depth += line.count("{") - line.count("}")
        if depth < 0:
            return f"Caddyfile:{number}: unexpected '}}'"
    return None if depth == 0 else "Caddyfile: unexpected end of file, missing '}'"


class FakeCaddy:
    """The admin API's behaviour; thread-safe, so tests and a server can share it."""

    def __init__(self) -> None:
        """Running, with an empty config, accepting every well-formed Caddyfile."""
        self._lock = threading.Lock()
        self._running = True
        self._refusal: str | None = None
        self._loaded: list[str] = []

    @property
    def loaded(self) -> list[str]:
        """Every Caddyfile accepted so far, oldest first."""
        with self._lock:
            return list(self._loaded)

    @property
    def running(self) -> bool:
        """False while `stop()` makes it refuse connections."""
        with self._lock:
            return self._running

    def stop(self) -> None:
        """Caddy goes away: every request now fails to connect."""
        with self._lock:
            self._running = False

    def start(self) -> None:
        """Caddy is back, with the config it had."""
        with self._lock:
            self._running = True

    def refuse_next_loads(self, message: str | None) -> None:
        """Refuse every `/load` with `message` (None: accept again)."""
        with self._lock:
            self._refusal = message

    def handle(self, request: httpx2.Request) -> httpx2.Response:
        """Answer one admin API request, as Caddy would."""
        with self._lock:
            if not self._running:
                raise httpx2.ConnectError(
                    "connection refused (fake Caddy stopped)", request=request
                )
            if request.method == "POST" and request.url.path == "/load":
                return self._load(request)
            if request.method == "GET" and request.url.path == "/config/":
                return httpx2.Response(200, json=self._config())
        return httpx2.Response(404, json={"error": "not found"})

    def transport(self) -> httpx2.MockTransport:
        """An httpx2 transport that reaches this fake without a network."""
        return httpx2.MockTransport(self.handle)

    def _load(self, request: httpx2.Request) -> httpx2.Response:
        content_type = request.headers.get("content-type", "")
        if not content_type.startswith(CADDYFILE_CONTENT_TYPE):
            return httpx2.Response(400, json={"error": f"unsupported adapter for {content_type!r}"})
        text = request.content.decode("utf-8")
        error = self._refusal or check_caddyfile(text)
        if error:
            return httpx2.Response(400, json={"error": f"adapting config: {error}"})
        self._loaded.append(text)
        return httpx2.Response(200)

    def _config(self) -> dict[str, Any]:
        latest = self._loaded[-1] if self._loaded else ""
        return {"fake": True, "caddyfile_lines": len(latest.splitlines())}


def serve(fake: FakeCaddy, port: int) -> None:
    """Serve `fake` over real HTTP on 127.0.0.1:`port` until Ctrl+C."""

    class Handler(BaseHTTPRequestHandler):
        def _answer(self) -> None:
            length = int(self.headers.get("Content-Length") or 0)
            request = httpx2.Request(
                self.command,
                f"http://127.0.0.1:{port}{self.path}",
                headers=dict(self.headers.items()),
                content=self.rfile.read(length),
            )
            try:
                response = fake.handle(request)
            except httpx2.ConnectError:
                self.close_connection = True
                return
            body = response.content
            self.send_response(response.status_code)
            self.send_header("Content-Type", response.headers.get("content-type", "text/plain"))
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        do_GET = _answer  # noqa: N815 (the names http.server looks for)
        do_POST = _answer  # noqa: N815

    with ThreadingHTTPServer(("127.0.0.1", port), Handler) as server:
        print(f"fake Caddy admin API on http://127.0.0.1:{port} (Ctrl+C to stop)")
        with contextlib.suppress(KeyboardInterrupt):
            server.serve_forever()
        latest = fake.loaded[-1:] or ["(nothing loaded)"]
        print(json.dumps({"last_loaded": latest[0]}, indent=2))


def main() -> None:
    """`python -m baja_studio.fakes.fake_caddy [--port 2019] [--refuse MESSAGE]`."""
    parser = argparse.ArgumentParser(description="A fake Caddy admin API for the gateway module.")
    parser.add_argument("--port", type=int, default=2019)
    parser.add_argument("--refuse", default=None, help="refuse every config with this message")
    arguments = parser.parse_args()
    fake = FakeCaddy()
    fake.refuse_next_loads(arguments.refuse)
    serve(fake, arguments.port)


if __name__ == "__main__":
    main()
