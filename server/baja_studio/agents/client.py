"""Async client for one camera-laptop agent (contract: docs/agent-api.md).

`AgentClient` has one method per command verb, plus `subscribe`, which keeps
the state WebSocket open for as long as it runs: it reconnects with backoff,
pings, and applies the offline rule (five seconds without hearing from the
agent → `offline`). Every network call has explicit timeouts (transport.py).
"""

import logging
from collections.abc import Mapping
from typing import Any

import httpx2
from pydantic import TypeAdapter

from baja_studio.agents.contract import (
    CONTRACT_VERSION,
    DEFAULT_TTL_MS,
    STUDIO_ID_HEADER,
    TOKEN_HEADER,
    AgentHealth,
    AgentInfo,
    CommandReply,
    CropRect,
    ErrorBody,
    ImageControlChange,
    ImageControlValue,
    LogEntry,
    Mode,
    ModeReply,
    Position,
    Preset,
    contract_major,
)
from baja_studio.agents.subscription import Subscription, UpdateCallback
from baja_studio.agents.transport import (
    COMMAND_TIMEOUT,
    AgentTransport,
    AgentUnreachableError,
    NetworkTransport,
)
from baja_studio.core.clock import Clock

logger = logging.getLogger(__name__)

_IMAGE_CONTROLS = TypeAdapter(dict[str, ImageControlValue])
_LOG_ENTRIES = TypeAdapter(list[LogEntry])


class AgentError(Exception):
    """The agent answered with an error (`{error, detail}` and an HTTP status)."""

    def __init__(self, status_code: int, code: str, detail: str) -> None:
        """`code` is the contract's snake_case error code, e.g. "device_offline"."""
        super().__init__(f"{code}: {detail}" if detail else code)
        self.status_code = status_code
        self.code = code
        self.detail = detail


class IncompatibleAgentError(Exception):
    """The agent speaks another MAJOR contract version, so no commands are sent to it."""


def incompatibility(info: AgentInfo) -> str | None:
    """Why the studio cannot work with this agent, or None if it can (same MAJOR version)."""
    ours, theirs = contract_major(CONTRACT_VERSION), contract_major(info.contract_version)
    if theirs is None:
        return f"agent reports contract version {info.contract_version!r}, not MAJOR.MINOR"
    if theirs != ours:
        return (
            f"agent speaks contract {info.contract_version}; this studio speaks "
            f"{CONTRACT_VERSION} (the MAJOR versions must match)"
        )
    return None


class AgentClient:
    """Talks to one agent at `base_url`, e.g. "http://10.0.0.5:8890"."""

    def __init__(
        self,
        base_url: str,
        token: str,
        clock: Clock,
        transport: AgentTransport | None = None,
        studio_id: str = "baja-studio",
        timeout: httpx2.Timeout = COMMAND_TIMEOUT,
    ) -> None:
        """Create the client; nothing is sent until a method is called."""
        self.base_url = base_url.rstrip("/")
        self._clock = clock
        self._transport = transport or NetworkTransport()
        self._headers: dict[str, str] = {STUDIO_ID_HEADER: studio_id}
        if token:
            self._headers[TOKEN_HEADER] = token
        self._http = self._transport.http_client(self.base_url, self._headers, timeout)
        self._last_heard = clock.monotonic()
        # None until /agent/info has been read; then "" if compatible, else the reason.
        self._incompatible_reason: str | None = None

    @property
    def last_heard(self) -> float:
        """`clock.monotonic()` of the last answer or message from the agent."""
        return self._last_heard

    @property
    def headers(self) -> Mapping[str, str]:
        """Headers sent with every request: token (if any) and studio id."""
        return self._headers

    @property
    def transport(self) -> AgentTransport:
        """The transport this client uses (shared with its subscription)."""
        return self._transport

    def heard_from_agent(self) -> None:
        """Note that the agent just answered; called for every response and message."""
        self._last_heard = self._clock.monotonic()

    async def aclose(self) -> None:
        """Close the HTTP connection pool; call when the agent is removed."""
        await self._http.aclose()

    async def subscribe(self, on_update: UpdateCallback) -> None:
        """Keep the state WebSocket open and report everything through `on_update`.

        Runs until cancelled. `on_update` receives a `StatusUpdate` whenever
        the link status changes (with fresh `AgentInfo` on every reconnect),
        and a `StateUpdate` / `EventUpdate` for every message the agent pushes.
        It is called on the event loop, so it must not block.
        """
        await Subscription(self, self._clock, on_update).run()

    # --- agent level -----------------------------------------------------------

    async def info(self) -> AgentInfo:
        """`GET /agent/info`, also checking the contract version for later commands."""
        info = AgentInfo.model_validate(await self._call("GET", "/agent/info"))
        self._incompatible_reason = incompatibility(info) or ""
        return info

    @property
    def incompatible_reason(self) -> str | None:
        """Why the agent cannot be used ("" if it can; None before `info` was read)."""
        return self._incompatible_reason

    async def health(self) -> AgentHealth:
        """`GET /agent/health`."""
        return AgentHealth.model_validate(await self._call("GET", "/agent/health"))

    async def log(self, limit: int = 100) -> list[LogEntry]:
        """`GET /agent/log`: the agent's own command log, newest first."""
        return _LOG_ENTRIES.validate_python(
            await self._call("GET", "/agent/log", params={"limit": limit})
        )

    # --- per camera ----------------------------------------------------------------

    async def move(
        self,
        camera_id: str,
        pan: float = 0.0,
        tilt: float = 0.0,
        zoom: float = 0.0,
        ttl_ms: int = DEFAULT_TTL_MS,
    ) -> CommandReply:
        """Move at normalised velocities (-1..1) for `ttl_ms`; resend to keep moving."""
        body = {"pan": pan, "tilt": tilt, "zoom": zoom, "ttl_ms": ttl_ms}
        return CommandReply.model_validate(await self._command(camera_id, "POST", "/move", body))

    async def stop(self, camera_id: str) -> CommandReply:
        """Stop all axes now."""
        return CommandReply.model_validate(await self._command(camera_id, "POST", "/stop"))

    async def recall_preset(self, camera_id: str, n: int) -> CommandReply:
        """Go to stored preset `n`."""
        path = f"/presets/{n}/recall"
        return CommandReply.model_validate(await self._command(camera_id, "POST", path))

    async def store_preset(self, camera_id: str, n: int) -> Preset:
        """Store the current position as preset `n`."""
        path = f"/presets/{n}/store"
        return Preset.model_validate(await self._command(camera_id, "POST", path))

    async def label_preset(self, camera_id: str, n: int, label: str) -> Preset:
        """Rename preset `n`."""
        path = f"/presets/{n}"
        return Preset.model_validate(await self._command(camera_id, "PUT", path, {"label": label}))

    async def position(self, camera_id: str) -> Position | None:
        """Current position, or None for cameras without position feedback."""
        answer = await self._command(camera_id, "GET", "/position")
        return None if answer is None else Position.model_validate(answer)

    async def mode(self, camera_id: str) -> ModeReply:
        """Current mode and behaviour profile."""
        return ModeReply.model_validate(await self._command(camera_id, "GET", "/mode"))

    async def set_mode(
        self, camera_id: str, mode: Mode, profile: dict[str, Any] | None = None
    ) -> ModeReply:
        """Set manual, auto or hold; `AgentError` "tracker_not_ready" if auto cannot run."""
        body: dict[str, Any] = {"mode": mode.value}
        if profile is not None:
            body["profile"] = profile
        return ModeReply.model_validate(await self._command(camera_id, "PUT", "/mode", body))

    async def image(self, camera_id: str) -> dict[str, ImageControlValue]:
        """Every image control with its read-back value and range."""
        return _IMAGE_CONTROLS.validate_python(await self._command(camera_id, "GET", "/image"))

    async def set_image(
        self, camera_id: str, changes: Mapping[str, ImageControlChange]
    ) -> dict[str, ImageControlValue]:
        """Change some image controls; returns what the device actually took."""
        body = {name: change.model_dump(exclude_none=True) for name, change in changes.items()}
        return _IMAGE_CONTROLS.validate_python(
            await self._command(camera_id, "PUT", "/image", body)
        )

    async def crop(self, camera_id: str) -> CropRect:
        """Current crop of a `kind=crop` camera."""
        return CropRect.model_validate(await self._command(camera_id, "GET", "/crop"))

    async def set_crop(self, camera_id: str, rect: CropRect) -> CropRect:
        """Set the crop; returns the crop the agent applied."""
        body = rect.model_dump()
        return CropRect.model_validate(await self._command(camera_id, "PUT", "/crop", body))

    async def resync(self, camera_id: str) -> CommandReply:
        """Ask the agent to re-read the device after the studio drove it directly."""
        return CommandReply.model_validate(await self._command(camera_id, "POST", "/resync"))

    # --- plumbing ------------------------------------------------------------------

    async def _command(self, camera_id: str, method: str, path: str, body: Any = None) -> Any:
        """A per-camera call, refused locally if the agent's contract is incompatible."""
        if self._incompatible_reason is None:
            await self.info()
        if self._incompatible_reason:
            raise IncompatibleAgentError(self._incompatible_reason)
        return await self._call(method, f"/cameras/{camera_id}{path}", body)

    async def _call(
        self, method: str, path: str, body: Any = None, params: dict[str, Any] | None = None
    ) -> Any:
        """One HTTP request; returns the decoded JSON or raises `AgentError`."""
        try:
            response = await self._http.request(method, path, json=body, params=params)
        except httpx2.TransportError as error:
            raise AgentUnreachableError(
                f"{self.base_url} did not answer {method} {path}: {error!r}"
            ) from error
        self.heard_from_agent()
        if response.is_success:
            return response.json()
        try:
            error_body = ErrorBody.model_validate(response.json())
        except ValueError:
            raise AgentError(response.status_code, "unexpected_answer", response.text) from None
        raise AgentError(response.status_code, error_body.error, error_body.detail)
