"""Tests for the agent client's commands (agents/client.py).

The client talks to a fake agent (fakes/fake_agent.py) through the
in-process transport: real HTTP requests and replies, but no network. These
tests are `async` because the client is; `@pytest.mark.anyio` runs them.
The live subscription is tested in test_subscription.py.
"""

from collections.abc import AsyncIterator

import pytest

from baja_studio.agents.client import (
    AgentClient,
    AgentError,
    IncompatibleAgentError,
    incompatibility,
)
from baja_studio.agents.contract import AgentInfo, CropRect, ImageControlChange, Mode
from baja_studio.agents.transport import AgentUnreachableError
from baja_studio.fakes.in_process import InProcessTransport
from baja_studio.fakes.simulated_common import FakeTracker
from tests.conftest import FakeClock
from tests.fake_agents import AGENT_HOST, StartAgent

pytestmark = pytest.mark.anyio

AGENT_URL = f"http://{AGENT_HOST}:8890"


@pytest.fixture
async def client(
    fake_clock: FakeClock, transport: InProcessTransport
) -> AsyncIterator[AgentClient]:
    """A client for the agent at the default address, with the token "secret"."""
    agent_client = AgentClient(AGENT_URL, "secret", fake_clock, transport, studio_id="test-studio")
    yield agent_client
    await agent_client.aclose()


def info_with_version(version: str) -> AgentInfo:
    """A minimal /agent/info answer claiming `version`."""
    return AgentInfo(
        agent_id="a", name="A", contract_version=version, software_version="x", cameras=[]
    )


# --- the version rule (no agent needed) ----------------------------------------------


def test_an_agent_with_the_same_major_version_is_compatible_even_if_newer() -> None:
    # 1.3 only adds things to 1.0, so a 1.0 studio can still use it.
    assert incompatibility(info_with_version("1.0")) is None
    assert incompatibility(info_with_version("1.3")) is None


def test_another_major_version_or_a_malformed_one_is_incompatible() -> None:
    assert "MAJOR" in (incompatibility(info_with_version("2.0")) or "")
    assert "not MAJOR.MINOR" in (incompatibility(info_with_version("one")) or "")


# --- talking to a fake agent ---------------------------------------------------------


async def test_info_lists_the_agents_cameras(start_agent: StartAgent, client: AgentClient) -> None:
    start_agent(ptz_cameras=2, token="secret")

    info = await client.info()

    assert info.contract_version == "1.0"
    assert [camera.id for camera in info.cameras] == ["cam1", "cam2"]
    assert client.incompatible_reason == ""


async def test_a_command_returns_a_typed_reply_and_is_logged_with_our_studio_id(
    start_agent: StartAgent, client: AgentClient
) -> None:
    start_agent(token="secret")

    reply = await client.move("cam1", pan=0.5, ttl_ms=300)

    assert reply.accepted is True
    assert reply.mode == Mode.MANUAL
    # The agent's own log says which studio sent the command (X-Studio-Id).
    newest = (await client.log(limit=1))[0]
    assert (newest.command, newest.source) == ("move", "studio:test-studio")


async def test_an_error_answer_becomes_an_agent_error_with_the_contract_code(
    start_agent: StartAgent, client: AgentClient
) -> None:
    start_agent(token="secret")

    # Preset 7 was never stored, so the agent answers 404 unknown_preset.
    with pytest.raises(AgentError) as refused:
        await client.recall_preset("cam1", 7)

    assert refused.value.status_code == 404
    assert refused.value.code == "unknown_preset"


async def test_a_wrong_token_is_refused_with_401(
    start_agent: StartAgent, fake_clock: FakeClock, transport: InProcessTransport
) -> None:
    start_agent(token="the-real-token")
    wrong = AgentClient(AGENT_URL, "a-guess", fake_clock, transport)

    with pytest.raises(AgentError) as refused:
        await wrong.health()

    assert (refused.value.status_code, refused.value.code) == (401, "unauthorized")
    await wrong.aclose()


async def test_an_address_where_nothing_listens_raises_agent_unreachable(
    client: AgentClient,
) -> None:
    # No start_agent here: nothing is plugged in at the address.
    with pytest.raises(AgentUnreachableError):
        await client.info()


async def test_no_command_is_sent_to_an_agent_with_another_major_version(
    start_agent: StartAgent, client: AgentClient
) -> None:
    runner = start_agent(token="secret", contract_version="2.0")

    with pytest.raises(IncompatibleAgentError):
        await client.stop("cam1")

    # The refusal happened in the studio: the agent never saw a command.
    assert runner.run(lambda: runner.agent.log_tail(10)) == []


async def test_set_image_returns_what_the_device_actually_took(
    start_agent: StartAgent, client: AgentClient
) -> None:
    start_agent(token="secret")

    # Gain goes from 0 to 255 in steps of 1, so 300 is clamped to 255.
    # Exposure is on auto, and a webcam ignores a manual value while it is.
    controls = await client.set_image(
        "cam1",
        {"gain": ImageControlChange(value=300), "exposure": ImageControlChange(value=-3)},
    )

    assert controls["gain"].value == 255
    assert controls["exposure"].value == -6
    assert controls["exposure"].auto is True


async def test_position_moves_with_velocity_and_stops_when_the_ttl_runs_out(
    start_agent: StartAgent, client: AgentClient, fake_clock: FakeClock
) -> None:
    start_agent(token="secret")

    # Full speed right for 300 ms. The fake pans at 100 degrees per second,
    # so after the move has expired the camera has turned 30 degrees, no more.
    await client.move("cam1", pan=1.0, ttl_ms=300)
    fake_clock.advance(2)

    position = await client.position("cam1")
    assert position is not None
    assert position.pan == pytest.approx(30.0)


async def test_crop_is_set_and_read_back_on_a_crop_camera(
    start_agent: StartAgent, client: AgentClient
) -> None:
    start_agent(token="secret", ptz_cameras=0, crop_cameras=1)

    applied = await client.set_crop("crop1", CropRect(x=0.25, y=0.25, w=0.5, h=0.5))

    assert applied == CropRect(x=0.25, y=0.25, w=0.5, h=0.5)
    assert await client.crop("crop1") == applied


async def test_set_mode_auto_is_refused_while_the_tracker_is_not_ready(
    start_agent: StartAgent, client: AgentClient
) -> None:
    runner = start_agent(token="secret")
    runner.run(lambda: runner.agent.cameras["cam1"].set_tracker(FakeTracker.NOT_READY))

    with pytest.raises(AgentError) as refused:
        await client.set_mode("cam1", Mode.AUTO)

    assert (refused.value.status_code, refused.value.code) == (409, "tracker_not_ready")
