"""Tests for playout's OBS connection lent to the cameras module (modules/playout/obs_gateway.py).

The gateway hands the cameras module the client playout created, forwards
OBS's events to the cameras' listeners, and explains in playout's own words
why OBS cannot be asked anything. We drive it with real clients to a fake
OBS over the in-process network.
"""

from typing import Any

from baja_studio.adapters.obs_transport import ObsAddress
from baja_studio.adapters.obs_websocket import ObsWebsocketClient
from baja_studio.cameras.drivers.obs_gateway import CONNECTION_LOST
from baja_studio.fakes.fake_obs import FakeObs
from baja_studio.fakes.fake_obs_in_process import InProcessObsNetwork
from baja_studio.modules.playout.obs_gateway import CONNECTING, PLAYOUT_OFF, PlayoutObsGateway
from tests.conftest import FakeClock, eventually

ADDRESS = ObsAddress("obs", 4455)


def connected_client(fake_clock: FakeClock, network: InProcessObsNetwork) -> ObsWebsocketClient:
    client = ObsWebsocketClient(fake_clock, network.connect)
    client.connect(ADDRESS, "")
    return client


def test_without_playout_running_there_is_no_obs(fake_clock: FakeClock) -> None:
    gateway = PlayoutObsGateway(lambda: {})

    assert gateway.client() is None
    assert gateway.link().connected is False
    assert gateway.link().reason == PLAYOUT_OFF


def test_the_reason_is_playouts_own_message_while_obs_is_away(fake_clock: FakeClock) -> None:
    # Playout publishes why OBS is away; cameras show the same words.
    playout_state: dict[str, Any] = {"connection": "connecting", "message": ""}
    gateway = PlayoutObsGateway(lambda: playout_state)
    gateway.attach(ObsWebsocketClient(fake_clock, InProcessObsNetwork().connect))

    assert gateway.link().reason == CONNECTING
    playout_state.update(connection="offline", message="cannot reach OBS at ws://obs:4455")
    assert gateway.link().reason == "cannot reach OBS at ws://obs:4455"


def test_events_reach_every_listener_and_a_drop_is_announced(fake_clock: FakeClock) -> None:
    fake = FakeObs(fake_clock)
    fake.add_video_input("Cam", 1920, 1080)
    network = InProcessObsNetwork()
    network.plug("obs", 4455, fake)
    gateway = PlayoutObsGateway(lambda: {})
    heard: list[str] = []
    gateway.add_listener(lambda kind, data: heard.append(kind))
    client = connected_client(fake_clock, network)
    gateway.attach(client)
    assert gateway.link().connected

    # OBS changes the camera's resolution, then quits.
    fake.change_video_size("Cam", 3840, 2160)
    eventually(lambda: heard == ["InputSettingsChanged"])
    network.unplug("obs", 4455)

    eventually(lambda: heard == ["InputSettingsChanged", CONNECTION_LOST])
    client.close()


def test_listeners_follow_playout_to_its_next_client_and_the_old_one_is_ignored(
    fake_clock: FakeClock,
) -> None:
    fake = FakeObs(fake_clock)
    fake.add_video_input("Cam", 1920, 1080)
    network = InProcessObsNetwork()
    network.plug("obs", 4455, fake)
    gateway = PlayoutObsGateway(lambda: {})
    heard: list[str] = []
    remove = gateway.add_listener(lambda kind, data: heard.append(kind))
    # A second listener that stays, to know when an event has gone through.
    witness: list[str] = []
    gateway.add_listener(lambda kind, data: witness.append(kind))

    # Playout restarts: a first client, then a second one replaces it.
    old, new = connected_client(fake_clock, network), connected_client(fake_clock, network)
    gateway.attach(old)
    gateway.attach(new)
    fake.change_video_size("Cam", 3840, 2160)  # both clients hear it

    eventually(lambda: len(heard) >= 1)
    assert heard == ["InputSettingsChanged"]  # once: from the current client only
    assert gateway.client() is new

    # A removed listener hears nothing more (the witness shows the event went through).
    remove()
    fake.change_video_size("Cam", 1920, 1080)
    eventually(lambda: len(witness) == 2)
    new.close()
    old.close()
    assert heard == ["InputSettingsChanged"]
