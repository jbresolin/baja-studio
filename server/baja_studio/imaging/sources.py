"""Where a camera's image controls are read and written: a USB device or a laptop agent.

Both give the same `ControlState`s, so the imaging service treats them alike.

- `UsbImageSource` asks the UVC child (`read_controls`, `write_controls`).
  UVC takes a value and a mode together, so turning auto on sends the
  current value with it.
- `AgentImageSource` uses the agent's `GET/PUT /cameras/{id}/image` through
  the agents module. The `PUT` answers with every control read back, so no
  second read is needed.
"""

from collections.abc import Callable
from concurrent.futures import TimeoutError as FutureTimeoutError
from dataclasses import dataclass
from typing import Any, Protocol

from baja_studio.agents.client import AgentError
from baja_studio.agents.contract import ImageControlChange
from baja_studio.cameras.drivers.agent_gateway import AgentGateway
from baja_studio.core.childproc import ChildRequestError
from baja_studio.core.worker import DeviceOfflineError
from baja_studio.imaging.config import ImageSetting
from baja_studio.imaging.controls import IMAGE_CONTROL_NAMES, ControlState

AGENT_REPLY_TIMEOUT_SECONDS = 3.0

ChildRequest = Callable[[str, dict[str, Any]], Any]


class ImageUnavailableError(Exception):
    """The camera's image controls cannot be reached now; the message says why."""


@dataclass
class WriteOutcome:
    """Per-control errors of a write, and the controls read back if the source gave them."""

    errors: dict[str, str]
    readback: dict[str, ControlState] | None = None


class ImageSource(Protocol):
    """A camera's image controls."""

    kind: str

    def read(self, names: list[str] | None = None) -> dict[str, ControlState]:
        """The image controls now (only `names` when given); raises `ImageUnavailableError`."""

    def write(
        self, changes: dict[str, ImageSetting], current: dict[str, ControlState]
    ) -> WriteOutcome:
        """Send `changes`; `current` fills in what a change leaves out."""


class UsbImageSource:
    """A USB camera on the studio PC, through the UVC child."""

    kind = "usb"

    def __init__(self, request: ChildRequest, path: str) -> None:
        """`request` is the UVC link's; `path` the device's DirectShow path."""
        self._request = request
        self.path = path

    def read(self, names: list[str] | None = None) -> dict[str, ControlState]:
        """The image controls the device has (pan, tilt and zoom are the driver's).

        After a write only the controls written are read back (`names`): each
        read is a round trip to the device.
        """
        wanted = IMAGE_CONTROL_NAMES if names is None else names
        answer = self._ask("read_controls", {"path": self.path, "names": wanted})
        return {name: ControlState.from_usb(data) for name, data in answer["controls"].items()}

    def write(
        self, changes: dict[str, ImageSetting], current: dict[str, ControlState]
    ) -> WriteOutcome:
        """One UVC write per control, value and mode together."""
        body: dict[str, Any] = {}
        for name, change in changes.items():
            control = current[name]
            auto = bool(change.auto) if change.auto is not None else control.auto is True
            value = change.value if change.value is not None and not auto else control.value
            body[name] = {"value": round(value), "auto": auto}
        answer = self._ask("write_controls", {"path": self.path, "controls": body})
        # The library's messages are generic ("Failed to set camera property"):
        # say what was refused. The card shows which control.
        return WriteOutcome(
            {
                name: f"the camera refused {_asked(body[name])}: {error}"
                for name, error in answer.items()
                if error
            }
        )

    def _ask(self, method: str, params: dict[str, Any]) -> Any:
        try:
            return self._request(method, params)
        except DeviceOfflineError as error:
            raise ImageUnavailableError(str(error)) from None
        except ChildRequestError as error:
            raise ImageUnavailableError(f"the USB camera did not answer: {error}") from None


class AgentImageSource:
    """A camera owned by a laptop agent."""

    kind = "agent"

    def __init__(self, gateway: AgentGateway, agent_id: str, remote_camera_id: str) -> None:
        """Calls go through the agents module's one client per agent."""
        self._gateway = gateway
        self._agent_id = agent_id
        self._remote = remote_camera_id

    def read(self, names: list[str] | None = None) -> dict[str, ControlState]:
        """`GET /cameras/{id}/image` (the agent answers with every control)."""
        remote = self._remote
        try:
            answer = self._wait(self._gateway.submit(self._agent_id, lambda c: c.image(remote)))
        except AgentError as error:
            raise ImageUnavailableError(f"the agent refused: {error.detail}") from None
        return {name: ControlState.from_agent(value) for name, value in answer.items()}

    def write(
        self, changes: dict[str, ImageSetting], current: dict[str, ControlState]
    ) -> WriteOutcome:
        """`PUT /cameras/{id}/image`; a refusal is every control's error (none is changed)."""
        remote = self._remote
        body = {
            name: ImageControlChange(value=change.value, auto=change.auto)
            for name, change in changes.items()
        }
        try:
            answer = self._wait(
                self._gateway.submit(self._agent_id, lambda c: c.set_image(remote, body))
            )
        except AgentError as error:
            return WriteOutcome({name: f"the agent refused: {error.detail}" for name in changes})
        return WriteOutcome(
            {}, {name: ControlState.from_agent(value) for name, value in answer.items()}
        )

    def _wait(self, future: Any) -> Any:
        try:
            return future.result(AGENT_REPLY_TIMEOUT_SECONDS)
        except AgentError:
            raise
        except FutureTimeoutError:
            raise ImageUnavailableError(f"agent {self._agent_id} did not answer in time") from None
        except Exception as error:
            raise ImageUnavailableError(f"agent {self._agent_id}: {error}") from None


def _asked(sent: dict[str, Any]) -> str:
    """What a UVC write asked for, in words: "auto" or the value."""
    return "auto" if sent["auto"] else str(sent["value"])
