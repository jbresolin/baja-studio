"""A small client for Caddy's admin API (`127.0.0.1:2019`, loopback only).

- `POST /load` with `Content-Type: text/caddyfile`: Caddy adapts the
  Caddyfile and swaps to it without dropping connections; a config it
  cannot use is refused (HTTP 400) and the old one keeps running.
- `GET /config/`: the running config as JSON; answering at all is how we know
  Caddy is up.

Caddy answers on loopback in milliseconds, but a `/load` that has to provision
new routes takes longer, hence the separate, longer load timeout.
"""

from typing import Any

import httpx2

READ_TIMEOUT = httpx2.Timeout(connect=2.0, read=2.0, write=2.0, pool=2.0)
LOAD_TIMEOUT = httpx2.Timeout(connect=2.0, read=15.0, write=5.0, pool=2.0)


class CaddyUnreachableError(Exception):
    """Caddy's admin API did not answer: the gateway service is stopped or starting."""


class CaddyRejectedConfigError(Exception):
    """Caddy refused a config; the message is Caddy's own explanation."""


class CaddyAdminClient:
    """Talks to one Caddy admin endpoint; `transport` lets tests plug in the fake."""

    def __init__(self, admin_address: str, transport: httpx2.BaseTransport | None = None) -> None:
        """`admin_address` is host:port, e.g. 127.0.0.1:2019."""
        self._base_url = f"http://{admin_address}"
        self._transport = transport

    def load_caddyfile(self, text: str) -> None:
        """Replace Caddy's running config with this Caddyfile."""
        response = self._request(
            "POST",
            "/load",
            LOAD_TIMEOUT,
            content=text.encode("utf-8"),
            headers={"Content-Type": "text/caddyfile"},
        )
        if response.status_code >= 400:
            raise CaddyRejectedConfigError(_error_text(response))

    def read_config(self) -> Any:
        """The running config (JSON); raises `CaddyUnreachableError` when Caddy is down."""
        response = self._request("GET", "/config/", READ_TIMEOUT)
        if response.status_code >= 400:
            raise CaddyUnreachableError(f"admin API answered HTTP {response.status_code}")
        return response.json()

    def _request(
        self, method: str, path: str, timeout: httpx2.Timeout, **options: Any
    ) -> httpx2.Response:
        # Caddy's admin API refuses requests whose Origin/Host it does not expect;
        # a plain client sends Host: 127.0.0.1:2019, which it accepts.
        try:
            with httpx2.Client(
                base_url=self._base_url,
                timeout=timeout,
                transport=self._transport,
                trust_env=False,
            ) as client:
                return client.request(method, path, **options)
        except httpx2.TransportError as error:
            raise CaddyUnreachableError(f"no answer from Caddy at {self._base_url}: {error}") from (
                error
            )


def _error_text(response: httpx2.Response) -> str:
    """Caddy's error body is `{"error": "..."}`; fall back to the raw text."""
    try:
        body = response.json()
    except ValueError:
        return response.text.strip() or f"HTTP {response.status_code}"
    if isinstance(body, dict) and isinstance(body.get("error"), str):
        return str(body["error"])
    return response.text.strip()
