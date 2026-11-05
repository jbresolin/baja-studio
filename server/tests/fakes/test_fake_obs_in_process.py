"""Tests for reaching the fake OBS without a network (fakes/fake_obs_in_process.py).

The transport is a queue between "client" and fake. These tests use it
directly, without the client, to check plugging, unplugging and closing.
"""

import pytest

from baja_studio.adapters.obs_protocol import EventSubscription, OpCode, decode, identify_message
from baja_studio.adapters.obs_transport import (
    ObsAddress,
    ObsConnectionClosedError,
    ObsUnreachableError,
)
from baja_studio.fakes.fake_obs import FakeObs
from baja_studio.fakes.fake_obs_in_process import InProcessObsNetwork
from tests.conftest import FakeClock

ADDRESS = ObsAddress("obs", 4455)


@pytest.fixture
def network(fake_clock: FakeClock) -> InProcessObsNetwork:
    """A network with one fake OBS at obs:4455."""
    network = InProcessObsNetwork()
    network.plug("obs", 4455, FakeObs.with_playout_scenes(fake_clock))
    return network


def test_a_new_connection_receives_hello_first(network: InProcessObsNetwork) -> None:
    transport = network.connect(ADDRESS, 3.0)

    assert decode(transport.receive())[0] == OpCode.HELLO
    transport.close()


def test_messages_sent_reach_the_fake_and_answers_come_back(network: InProcessObsNetwork) -> None:
    transport = network.connect(ADDRESS, 3.0)
    transport.receive()  # Hello

    transport.send(identify_message(None, EventSubscription.ALL))

    assert decode(transport.receive())[0] == OpCode.IDENTIFIED
    transport.close()


def test_an_address_with_no_fake_is_unreachable(network: InProcessObsNetwork) -> None:
    with pytest.raises(ObsUnreachableError):
        network.connect(ObsAddress("elsewhere", 4455), 3.0)


def test_unplugging_closes_open_connections_and_refuses_new_ones(
    network: InProcessObsNetwork,
) -> None:
    # Like OBS quitting: the open socket closes, and nothing answers afterwards.
    transport = network.connect(ADDRESS, 3.0)
    transport.receive()  # Hello

    network.unplug("obs", 4455)

    with pytest.raises(ObsConnectionClosedError):
        transport.receive()
    with pytest.raises(ObsUnreachableError):
        network.connect(ADDRESS, 3.0)


def test_after_closing_every_receive_and_send_raises(network: InProcessObsNetwork) -> None:
    transport = network.connect(ADDRESS, 3.0)
    transport.receive()  # Hello

    transport.close()

    for _ in range(2):
        with pytest.raises(ObsConnectionClosedError):
            transport.receive()
    with pytest.raises(ObsConnectionClosedError):
        transport.send(identify_message(None, EventSubscription.ALL))


def test_closing_twice_is_harmless(network: InProcessObsNetwork) -> None:
    transport = network.connect(ADDRESS, 3.0)

    transport.close()
    transport.close()


def test_unplugging_an_empty_address_does_nothing(network: InProcessObsNetwork) -> None:
    network.unplug("nobody", 1)
