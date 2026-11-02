"""Tests for the live link to an agent (agents/subscription.py, via AgentClient.subscribe).

The subscription keeps the agent's state WebSocket open, reconnects when it
drops, and says `offline` after five seconds without hearing anything. Both
the client and the fake agent run on the fake clock, so "five seconds" passes
exactly when the test calls `fake_clock.advance(5)`.

Each test starts `subscribe` as a background task and records every update it
reports in an `UpdateRecorder`, then checks what was recorded.
"""

import asyncio
import contextlib
from collections.abc import AsyncIterator, Callable

import pytest

from baja_studio.agents.client import AgentClient
from baja_studio.agents.contract import Mode
from baja_studio.agents.subscription import (
    AgentUpdate,
    EventUpdate,
    LinkStatus,
    ReconnectBackoff,
    StateUpdate,
    StatusUpdate,
)
from baja_studio.fakes.in_process import InProcessTransport
from tests.conftest import FakeClock, eventually_async
from tests.fake_agents import (
    AGENT_HOST,
    DELIVERY_SLACK_SECONDS,
    StartAgent,
    advance_in_steps_async,
)

pytestmark = pytest.mark.anyio


class UpdateRecorder:
    """Collects everything `subscribe` reports, in order."""

    def __init__(self) -> None:
        self.updates: list[AgentUpdate] = []

    def __call__(self, update: AgentUpdate) -> None:
        self.updates.append(update)

    def statuses(self) -> list[LinkStatus]:
        return [update.status for update in self.updates if isinstance(update, StatusUpdate)]

    def last_status(self) -> StatusUpdate:
        return [update for update in self.updates if isinstance(update, StatusUpdate)][-1]

    def states(self) -> list[StateUpdate]:
        return [update for update in self.updates if isinstance(update, StateUpdate)]

    def events(self) -> list[str]:
        return [update.message.event for update in self.updates if isinstance(update, EventUpdate)]


@pytest.fixture
def recorder() -> UpdateRecorder:
    return UpdateRecorder()


Subscribe = Callable[[], AgentClient]


@pytest.fixture
async def subscribe(
    fake_clock: FakeClock, transport: InProcessTransport, recorder: UpdateRecorder
) -> AsyncIterator[Subscribe]:
    """Call `subscribe()` (after starting the agent) to run `subscribe` in the background.

    The order matters: a subscription started before any agent is plugged in
    would wait for its first retry, and fake time only moves when told to.
    """
    started: list[tuple[AgentClient, asyncio.Task[None]]] = []

    def start() -> AgentClient:
        client = AgentClient(f"http://{AGENT_HOST}:8890", "", fake_clock, transport)
        started.append((client, asyncio.create_task(client.subscribe(recorder))))
        return client

    yield start
    for client, task in started:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
        await client.aclose()


async def wait_for_status(recorder: UpdateRecorder, status: LinkStatus) -> None:
    await eventually_async(
        lambda: bool(recorder.statuses()) and recorder.last_status().status == status,
        f"status never became {status}; saw {recorder.statuses()}",
    )


def test_reconnect_delays_double_from_half_a_second_up_to_four() -> None:
    # Four seconds is the cap because it is below the five-second offline rule.
    backoff = ReconnectBackoff()

    assert [backoff.next_delay() for _ in range(6)] == [0.5, 1.0, 2.0, 4.0, 4.0, 4.0]
    backoff.reset()
    assert backoff.next_delay() == 0.5


async def test_subscribe_reports_connecting_then_ok_with_info_then_state(
    start_agent: StartAgent, subscribe: Subscribe, recorder: UpdateRecorder
) -> None:
    start_agent(ptz_cameras=2)
    subscribe()

    await wait_for_status(recorder, LinkStatus.OK)
    # The agent sends one state message as soon as the socket opens.
    await eventually_async(lambda: len(recorder.states()) >= 1)

    assert recorder.statuses()[:2] == [LinkStatus.CONNECTING, LinkStatus.OK]
    info = recorder.last_status().info
    assert info is not None and [camera.id for camera in info.cameras] == ["cam1", "cam2"]
    assert set(recorder.states()[0].message.cameras) == {"cam1", "cam2"}


async def test_state_keeps_coming_as_time_passes(
    start_agent: StartAgent,
    subscribe: Subscribe,
    recorder: UpdateRecorder,
    fake_clock: FakeClock,
) -> None:
    start_agent()
    subscribe()
    await eventually_async(lambda: len(recorder.states()) >= 1)
    received = len(recorder.states())

    # The fake pushes four times a second; one fake second brings more state.
    fake_clock.advance(1)

    await eventually_async(lambda: len(recorder.states()) > received)


async def test_events_from_the_agent_are_passed_on(
    start_agent: StartAgent, subscribe: Subscribe, recorder: UpdateRecorder
) -> None:
    start_agent()
    client = subscribe()
    await wait_for_status(recorder, LinkStatus.OK)

    # Switching a camera to auto makes the agent announce the mode change.
    await client.set_mode("cam1", Mode.AUTO)

    await eventually_async(lambda: "mode_changed" in recorder.events())


async def test_a_silent_agent_is_offline_after_five_seconds_not_before(
    start_agent: StartAgent,
    subscribe: Subscribe,
    recorder: UpdateRecorder,
    fake_clock: FakeClock,
) -> None:
    runner = start_agent()
    subscribe()
    await wait_for_status(recorder, LinkStatus.OK)

    # The agent hangs: no more state, no pongs. Its socket stays open. (After
    # three seconds without state it is already degraded; that has its own test.)
    runner.run(lambda: setattr(runner.agent, "silent", True))
    await advance_in_steps_async(fake_clock, 4.75)
    assert recorder.last_status().status != LinkStatus.OFFLINE

    await advance_in_steps_async(fake_clock, 0.25 + DELIVERY_SLACK_SECONDS)

    await wait_for_status(recorder, LinkStatus.OFFLINE)
    assert "nothing heard for 5 s" in recorder.last_status().reason


async def test_no_state_for_three_seconds_is_degraded_then_ok_when_state_comes_back(
    start_agent: StartAgent,
    subscribe: Subscribe,
    recorder: UpdateRecorder,
    fake_clock: FakeClock,
) -> None:
    runner = start_agent()
    subscribe()
    await wait_for_status(recorder, LinkStatus.OK)

    # The agent stops pushing state but still answers pings: not silent, stale.
    runner.run(lambda: setattr(runner.agent, "state_frozen", True))
    await advance_in_steps_async(fake_clock, 2.5)
    assert recorder.last_status().status == LinkStatus.OK

    await advance_in_steps_async(fake_clock, 0.5 + DELIVERY_SLACK_SECONDS)
    await wait_for_status(recorder, LinkStatus.DEGRADED)
    assert recorder.last_status().reason == "state stale: no state for 3 s"

    runner.run(lambda: setattr(runner.agent, "state_frozen", False))
    await advance_in_steps_async(fake_clock, 0.5)
    await wait_for_status(recorder, LinkStatus.OK)


async def test_stale_state_alone_never_makes_an_agent_offline(
    start_agent: StartAgent,
    subscribe: Subscribe,
    recorder: UpdateRecorder,
    fake_clock: FakeClock,
) -> None:
    # The five-second offline rule counts anything heard; the pongs keep it away.
    runner = start_agent()
    subscribe()
    await wait_for_status(recorder, LinkStatus.OK)

    runner.run(lambda: setattr(runner.agent, "state_frozen", True))
    await advance_in_steps_async(fake_clock, 12)

    assert recorder.last_status().status == LinkStatus.DEGRADED
    assert LinkStatus.OFFLINE not in recorder.statuses()


async def test_an_agent_that_vanishes_goes_offline_and_comes_back_with_its_new_cameras(
    start_agent: StartAgent,
    subscribe: Subscribe,
    recorder: UpdateRecorder,
    fake_clock: FakeClock,
    transport: InProcessTransport,
) -> None:
    start_agent(ptz_cameras=2)
    subscribe()
    await wait_for_status(recorder, LinkStatus.OK)

    # The laptop disappears: its socket closes and new connections are refused.
    transport.unplug(AGENT_HOST, 8890)
    await advance_in_steps_async(fake_clock, 5 + DELIVERY_SLACK_SECONDS)
    await wait_for_status(recorder, LinkStatus.OFFLINE)

    # It restarts with a third camera. Reconnect attempts come at most four
    # seconds apart, and every reconnect re-reads /agent/info.
    start_agent(ptz_cameras=3)
    await advance_in_steps_async(fake_clock, 4)

    await wait_for_status(recorder, LinkStatus.OK)
    info = recorder.last_status().info
    assert info is not None and len(info.cameras) == 3


async def test_a_dropped_socket_is_reopened_without_going_offline(
    start_agent: StartAgent,
    subscribe: Subscribe,
    recorder: UpdateRecorder,
    fake_clock: FakeClock,
) -> None:
    runner = start_agent()
    subscribe()
    await wait_for_status(recorder, LinkStatus.OK)

    # The agent closes every socket (a network blip). The client tries again
    # after half a second and gets straight back in.
    runner.run(runner.agent.drop_websockets)
    await advance_in_steps_async(fake_clock, 1)

    await eventually_async(lambda: recorder.statuses().count(LinkStatus.OK) == 2)
    assert LinkStatus.OFFLINE not in recorder.statuses()


async def test_an_agent_with_another_major_version_is_degraded_and_stays_so(
    start_agent: StartAgent,
    subscribe: Subscribe,
    recorder: UpdateRecorder,
    fake_clock: FakeClock,
) -> None:
    start_agent(contract_version="2.0")
    subscribe()

    await wait_for_status(recorder, LinkStatus.DEGRADED)
    assert "speaks contract 2.0" in recorder.last_status().reason

    # It still answers /agent/info on every retry, so it never counts as offline.
    await advance_in_steps_async(fake_clock, 12)
    assert LinkStatus.OFFLINE not in recorder.statuses()


async def test_a_refused_token_is_degraded_not_offline(
    start_agent: StartAgent, subscribe: Subscribe, recorder: UpdateRecorder
) -> None:
    # The client has no token, but this agent wants one. /agent/info stays
    # open (so scans work); the WebSocket is refused with 401.
    start_agent(token="needed")
    subscribe()

    await wait_for_status(recorder, LinkStatus.DEGRADED)
    assert recorder.last_status().reason == "the agent refused our token"
