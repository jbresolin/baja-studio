"""Fakes for everything the feeds module reaches outside the process.

- `FakeMediamtx` (baja_studio/fakes/fake_mediamtx.py): MediaMTX's control
  API, reading the very `mediamtx.yml` the module writes in the test's data
  folder, with the reload rules measured on the real binary;
- `FakeServiceControl` (from the gateway's fakes): NSSM, remembering restarts;
- port holders: the programs `netstat` would find on MediaMTX's ports;
- the PC's LAN addresses, its free disk space, and MediaMTX's log file
  (the fake MediaMTX's own log lines, as NSSM would have saved them);
- `ConnectedGateway`: the `ObsGateway` playout lends, reduced to one client
  connected to a fake OBS (or none, with the reason OBS is away).

`feeds_fakes` is a fixture; `make_app(None)` (tests/conftest.py) uses it, so
the real module list never talks to a real MediaMTX either.
"""

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from baja_studio.adapters.obs_requests import ObsRequestsMixin
from baja_studio.adapters.obs_websocket import ObsWebsocketClient
from baja_studio.cameras.drivers.obs_gateway import ObsLinkView, ObsListener
from baja_studio.core.system_services import FEEDS_SERVICE
from baja_studio.fakes.fake_mediamtx import FakeMediamtx
from baja_studio.modules.feeds import FeedsSystem
from baja_studio.modules.feeds.mediamtx_config import config_path
from baja_studio.modules.feeds.ports import FeedPort, FeedPortHolder
from tests.conftest import FakeClock
from tests.modules.gateway_studio import FakeServiceControl

LAN_ADDRESS = "192.168.10.20"
PLENTY_OF_DISK = 500 * 1024**3


class ConnectedGateway:
    """An `ObsGateway` over one client connected to a fake OBS (or none: "OBS is away")."""

    def __init__(self, client: ObsWebsocketClient | None, reason: str = "") -> None:
        self._client = client
        self._reason = reason

    def link(self) -> ObsLinkView:
        connected = self._client is not None and self._client.is_connected
        return ObsLinkView(connected, "" if connected else self._reason)

    def client(self) -> ObsRequestsMixin | None:
        return self._client

    def add_listener(self, listener: ObsListener) -> Callable[[], None]:
        return lambda: None


@dataclass
class FeedsFakes:
    """One of each fake, and the `FeedsSystem` that uses them."""

    mediamtx: FakeMediamtx
    services: FakeServiceControl
    lan_addresses: list[str] = field(default_factory=lambda: [LAN_ADDRESS])
    taken_ports: dict[tuple[str, int], str] = field(default_factory=dict)
    ports_asked: list[list[FeedPort]] = field(default_factory=list)
    free_disk: int = PLENTY_OF_DISK
    disk_asked: list[Path] = field(default_factory=list)
    extra_log_lines: list[str] = field(default_factory=list)

    def take_port(self, protocol: str, port: int, holder: str = "obs64.exe (pid 4321)") -> None:
        """Another program listens on `port`: netstat finds it, and the fake MediaMTX cannot."""
        self.taken_ports[(protocol, port)] = holder
        self.mediamtx.occupy_port(port)

    def find_port_holders(self, ports: list[FeedPort]) -> list[FeedPortHolder]:
        self.ports_asked.append(list(ports))
        return [
            FeedPortHolder(port, self.taken_ports[(port.protocol, port.port)])
            for port in ports
            if (port.protocol, port.port) in self.taken_ports
        ]

    def free_disk_bytes(self, folder: Path) -> int:
        self.disk_asked.append(folder)
        return self.free_disk

    def read_log(self, path: Path) -> list[str]:
        return [*self.mediamtx.log_lines, *self.extra_log_lines]

    def system(self) -> FeedsSystem:
        return FeedsSystem(
            api_transport=self.mediamtx.transport(),
            port_holders=self.find_port_holders,
            list_addresses=lambda: list(self.lan_addresses),
            free_disk_bytes=self.free_disk_bytes,
            read_log=self.read_log,
        )


@pytest.fixture
def feeds_fakes(fake_clock: FakeClock, data_directory: Path) -> FeedsFakes:
    """MediaMTX running and watching the test's config file; the feeds service installed."""
    services = FakeServiceControl()
    services.statuses[FEEDS_SERVICE] = "SERVICE_RUNNING"
    return FeedsFakes(FakeMediamtx(fake_clock, config_path(data_directory)), services)
