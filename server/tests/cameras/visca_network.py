"""A pretend network of fake VISCA cameras, by address, for tests of the whole cameras module."""

from baja_studio.fakes.fake_visca_camera import FakeViscaCamera, InProcessDatagramTransport
from tests.conftest import FakeClock


class FakeViscaNetwork:
    """`open` is the cameras module's datagram opener; cameras are added by host and port.

    Opening an address with no camera works (UDP needs nobody to listen) but
    nothing ever answers, like a camera that is switched off.
    """

    def __init__(self, fake_clock: FakeClock) -> None:
        self._clock = fake_clock
        self.cameras: dict[tuple[str, int], FakeViscaCamera] = {}
        self.opened: list[tuple[str, int, InProcessDatagramTransport]] = []

    def add(self, host: str, port: int = 52381) -> FakeViscaCamera:
        camera = FakeViscaCamera()
        self.cameras[(host, port)] = camera
        return camera

    def open(self, host: str, port: int) -> InProcessDatagramTransport:
        camera = self.cameras.get((host, port))
        transport = InProcessDatagramTransport(camera or FakeViscaCamera(), self._clock.monotonic)
        transport.unplugged = camera is None
        self.opened.append((host, port, transport))
        return transport

    def unplug(self, host: str, port: int = 52381, unplugged: bool = True) -> None:
        """Pull (or put back) the network cable of the camera at `host`."""
        for opened_host, opened_port, transport in self.opened:
            if (opened_host, opened_port) == (host, port):
                transport.unplugged = unplugged

    def open_transports(self, host: str) -> list[InProcessDatagramTransport]:
        return [t for h, _, t in self.opened if h == host and not t.closed]
