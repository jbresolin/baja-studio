"""Fixtures and helpers for tests with fake camera-laptop agents.

Loaded into every test through `pytest_plugins` in conftest.py. Fake agents
run on the fake clock and are reached through `InProcessTransport`, a pretend
network: nothing is opened, no port is used.
"""

from collections.abc import Callable, Iterator
from typing import Any

import pytest

from baja_studio.agents.contract import AGENT_PORT
from baja_studio.fakes.fake_agent import FakeAgent
from baja_studio.fakes.in_process import FakeAgentRunner, InProcessTransport
from tests.conftest import FakeClock, eventually, eventually_async

# Fake time moves this far per step in `advance_in_steps`: the fake agent's push interval.
SETTLE_STEP_SECONDS = 0.25

# The five-second offline rule counts from the last message *received*. On the
# pretend network a message sent just before an agent vanishes can arrive a
# moment later in fake time, so "offline" checks allow this much on top.
DELIVERY_SLACK_SECONDS = 1.0


def advance_in_steps(fake_clock: FakeClock, seconds: float) -> None:
    """Let `seconds` of fake time pass in quarter-second steps, as real time would.

    After each step, wait until every coroutine the step woke up has gone back
    to sleep. So fake agents push, and the studio's links hear them, at the fake
    time they would in reality; one big `advance` would let five "seconds" pass
    with no push heard, and a healthy agent would look silent.
    """
    for _ in range(round(seconds / SETTLE_STEP_SECONDS)):
        sleeping_before = fake_clock.async_sleeping_count
        fake_clock.advance(SETTLE_STEP_SECONDS)
        eventually(
            lambda: fake_clock.async_sleeping_count >= sleeping_before,  # noqa: B023
            "something woken by the fake clock never went back to sleep",
        )


async def advance_in_steps_async(fake_clock: FakeClock, seconds: float) -> None:
    """`advance_in_steps` for async tests, letting the test's own loop run meanwhile."""
    for _ in range(round(seconds / SETTLE_STEP_SECONDS)):
        sleeping_before = fake_clock.async_sleeping_count
        fake_clock.advance(SETTLE_STEP_SECONDS)
        await eventually_async(
            lambda: fake_clock.async_sleeping_count >= sleeping_before,  # noqa: B023
            "something woken by the fake clock never went back to sleep",
        )


# Where the first fake agent "lives" on the pretend network. Nothing is opened:
# InProcessTransport hands requests for this address straight to the fake.
AGENT_HOST = "10.0.0.5"

StartAgent = Callable[..., FakeAgentRunner]


@pytest.fixture
def anyio_backend() -> str:
    """Tests marked `@pytest.mark.anyio` run on asyncio (the only loop the service uses)."""
    return "asyncio"


@pytest.fixture
def transport() -> InProcessTransport:
    """A pretend network with no agents on it until a test starts one."""
    return InProcessTransport()


@pytest.fixture
def start_agent(fake_clock: FakeClock, transport: InProcessTransport) -> Iterator[StartAgent]:
    """Start a fake agent on the fake clock and plug it in at an address.

    Call it as `start_agent()` for the default address, or e.g.
    `start_agent(host="10.0.0.6", ptz_cameras=3, token="secret")`; any extra
    options go to `FakeAgent`. Every agent started is stopped after the test.
    """
    runners: list[tuple[str, int, FakeAgentRunner]] = []

    def start(host: str = AGENT_HOST, port: int = AGENT_PORT, **options: Any) -> FakeAgentRunner:
        runner = FakeAgentRunner(FakeAgent(fake_clock, **options)).start()
        transport.plug(host, port, runner)
        runners.append((host, port, runner))
        return runner

    yield start
    for host, port, runner in runners:
        transport.unplug(host, port)
        runner.stop()
