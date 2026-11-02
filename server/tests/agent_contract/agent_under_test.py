"""The conformance suite's view of an agent: plain HTTP plus the state WebSocket.

The suite is black-box: it only uses what docs/agent-api.md promises, so it
can check any implementation. `AgentUnderTest` hides whether that is a real
agent on the network (`--agent-url`) or the in-process fake, and
`ContractSocket` gives both kinds of WebSocket a receive with a timeout.

Unlike the rest of the tests, this suite runs in real time: a real agent's
clock cannot be faked. Waits are deadlines on things happening, never fixed
sleeps, and allow one push cycle plus a margin.
"""

import json
import queue
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager, contextmanager
from dataclasses import dataclass
from typing import Any

import httpx2
import pytest
import websockets.exceptions
import websockets.sync.client
from fastapi.testclient import TestClient
from starlette.testclient import WebSocketDenialResponse
from starlette.websockets import WebSocketDisconnect

from baja_studio.agents.contract import TOKEN_HEADER

# The contract's slowest state push interval (2 Hz) ...
PUSH_INTERVAL_MAX_SECONDS = 0.5
# ... and what we allow on top for scheduling and the network.
MARGIN_SECONDS = 0.4


class SocketRefusedError(Exception):
    """The agent refused the WebSocket handshake."""

    def __init__(self, status_code: int | None) -> None:
        super().__init__(f"WebSocket refused (HTTP {status_code})")
        self.status_code = status_code


class ContractSocket:
    """A state WebSocket read by a background thread, so receives can time out."""

    def __init__(self, send_text: Callable[[str], None], receive_text: Callable[[], str]) -> None:
        self._send_text = send_text
        self._messages: queue.Queue[dict[str, Any] | None] = queue.Queue()
        self._reader = threading.Thread(target=self._read, args=(receive_text,), daemon=True)
        self._reader.start()

    def _read(self, receive_text: Callable[[], str]) -> None:
        try:
            while True:
                self._messages.put(json.loads(receive_text()))
        except Exception:
            self._messages.put(None)  # closed (any way at all)

    def send_json(self, message: dict[str, Any]) -> None:
        self._send_text(json.dumps(message))

    def receive(self, timeout: float) -> dict[str, Any]:
        """The next message; fails the test if none comes within `timeout` seconds."""
        try:
            message = self._messages.get(timeout=timeout)
        except queue.Empty:
            pytest.fail(f"no message from the agent within {timeout:.1f} s")
        if message is None:
            pytest.fail("the agent closed the WebSocket")
        return message

    def receive_until(
        self, matches: Callable[[dict[str, Any]], bool], timeout: float, what: str
    ) -> dict[str, Any]:
        """Skip messages until one `matches`; fails the test after `timeout` seconds."""
        give_up_at = time.monotonic() + timeout
        while (remaining := give_up_at - time.monotonic()) > 0:
            try:
                message = self._messages.get(timeout=remaining)
            except queue.Empty:
                break
            if message is None:
                pytest.fail(f"the agent closed the WebSocket while waiting for {what}")
            if matches(message):
                return message
        pytest.fail(f"no {what} within {timeout:.1f} s")

    def wait_for_camera(
        self, camera_id: str, matches: Callable[[dict[str, Any]], bool], timeout: float, what: str
    ) -> dict[str, Any]:
        """Wait for a state message in which `camera_id`'s CameraState `matches`."""
        message = self.receive_until(
            lambda m: m.get("type") == "state" and matches(m["cameras"].get(camera_id, {})),
            timeout,
            what,
        )
        camera_state: dict[str, Any] = message["cameras"][camera_id]
        return camera_state

    def collect(self, seconds: float) -> list[tuple[float, dict[str, Any]]]:
        """Every message for `seconds`, each with the time it arrived."""
        received = []
        give_up_at = time.monotonic() + seconds
        while (remaining := give_up_at - time.monotonic()) > 0:
            try:
                message = self._messages.get(timeout=remaining)
            except queue.Empty:
                break
            if message is None:
                pytest.fail("the agent closed the WebSocket")
            received.append((time.monotonic(), message))
        return received


@dataclass
class AgentUnderTest:
    """One agent to check, and how to reach it."""

    http: httpx2.Client
    token: str
    open_socket: Callable[[dict[str, str]], AbstractContextManager[ContractSocket]]
    camera_option: str | None

    def info(self) -> dict[str, Any]:
        answer: dict[str, Any] = self.http.get("/agent/info").json()
        return answer

    def socket(self, token: str | None = None) -> AbstractContextManager[ContractSocket]:
        """Open the state WebSocket with the right token, or with `token` if given."""
        chosen = self.token if token is None else token
        return self.open_socket({TOKEN_HEADER: chosen} if chosen else {})

    def ptz(self) -> dict[str, Any]:
        """CameraInfo of the PTZ under test (`--agent-camera`, else the first PTZ)."""
        for camera in self.info()["cameras"]:
            if camera["id"] == self.camera_option or (
                self.camera_option is None and camera["kind"] == "ptz"
            ):
                return dict(camera)
        pytest.skip("the agent has no PTZ camera to test (or not the one --agent-camera names)")

    def camera_of_kind(self, kind: str) -> dict[str, Any]:
        for camera in self.info()["cameras"]:
            if camera["kind"] == kind:
                return dict(camera)
        pytest.skip(f"the agent has no {kind} camera")

    def free_preset_number(self, camera: dict[str, Any]) -> int:
        """The highest preset number the camera can store and has not, so real presets survive."""
        preset_max = camera["limits"]["preset_max"]
        if preset_max is None:
            pytest.skip(f"{camera['id']} stores no presets (limits.preset_max is null)")
        used = {preset["n"] for preset in camera["presets"]}
        free = [n for n in range(preset_max + 1) if n not in used]
        if not free:
            pytest.skip(f"every preset 0..{preset_max} of {camera['id']} is in use")
        return max(free)

    def has_fake_controls(self) -> bool:
        return "fake_controls" in self.info().get("capabilities", [])

    def fake_switch(self, path: str, body: dict[str, Any]) -> None:
        """Flip a fake-agent failure switch, or skip: real agents have none."""
        if not self.has_fake_controls():
            pytest.skip("needs the fake agent's /_fake switches to set up")
        self.http.post(f"/_fake{path}", json=body).raise_for_status()


@contextmanager
def in_process_socket(client: TestClient, headers: dict[str, str]) -> Iterator[ContractSocket]:
    """The fake agent's WebSocket through Starlette's in-process test client."""
    try:
        session = client.websocket_connect("/agent/ws", headers=headers)
        session.__enter__()
    except WebSocketDenialResponse as refused:
        raise SocketRefusedError(refused.status_code) from None
    except WebSocketDisconnect:
        raise SocketRefusedError(None) from None
    try:
        yield ContractSocket(session.send_text, session.receive_text)
    finally:
        session.__exit__(None, None, None)


def network_socket_opener(
    base_url: str,
) -> Callable[[dict[str, str]], AbstractContextManager[ContractSocket]]:
    """Opens a real agent's WebSocket over the network."""
    ws_url = "ws" + base_url.rstrip("/").removeprefix("http") + "/agent/ws"

    @contextmanager
    def open_socket(headers: dict[str, str]) -> Iterator[ContractSocket]:
        try:
            opened = websockets.sync.client.connect(
                ws_url, additional_headers=headers, open_timeout=3, proxy=None
            )
        except websockets.exceptions.InvalidStatus as refused:
            raise SocketRefusedError(refused.response.status_code) from None
        with opened as connection:

            def receive_text() -> str:
                message = connection.recv()
                return message if isinstance(message, str) else message.decode()

            yield ContractSocket(connection.send, receive_text)

    return open_socket
