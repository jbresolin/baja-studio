"""Tests for the agent driver's command queue (cameras/drivers/agent_commands.py).

The gateway here is a stand-in that holds every submitted call until the
test says it has been answered, so the tests can see what waits behind what.
"""

from concurrent.futures import Future
from typing import Any

from baja_studio.cameras.drivers.agent_commands import MOVE, STOP, AgentCommandQueue
from baja_studio.cameras.drivers.agent_gateway import AgentCall, AgentLinkView


class HoldingGateway:
    """Records each submitted call by name and answers only when told to."""

    def __init__(self) -> None:
        self.submitted: list[tuple[str, Future[Any]]] = []

    def link(self, agent_id: str) -> AgentLinkView | None:
        return None

    def submit(self, agent_id: str, call: AgentCall) -> "Future[Any]":
        future: Future[Any] = Future()
        self.submitted.append((getattr(call, "label", "?"), future))
        return future

    def direct_fallback_after_s(self) -> float:
        return 10.0

    def answer_next(self, result: Any = None, error: BaseException | None = None) -> None:
        """Answer the oldest unanswered call."""
        for _, future in self.submitted:
            if not future.done():
                if error is not None:
                    future.set_exception(error)
                else:
                    future.set_result(result)
                return

    def names(self) -> list[str]:
        return [name for name, _ in self.submitted]


def labelled(label: str) -> AgentCall:
    """A call the gateway can name; it is never really run here."""

    async def call(client: Any) -> Any:
        return None

    call.label = label  # type: ignore[attr-defined]
    return call


def test_commands_go_out_one_at_a_time_in_order() -> None:
    gateway = HoldingGateway()
    queue = AgentCommandQueue(gateway, "laptop-a", lambda kind, error: None)

    queue.send(MOVE, labelled("move 1"))
    queue.send("recall_preset", labelled("recall"))

    # The recall waits until the move has been answered.
    assert gateway.names() == ["move 1"]
    gateway.answer_next()
    assert gateway.names() == ["move 1", "recall"]


def test_a_waiting_move_is_replaced_by_a_newer_one() -> None:
    gateway = HoldingGateway()
    queue = AgentCommandQueue(gateway, "laptop-a", lambda kind, error: None)
    queue.send(MOVE, labelled("move 1"))  # in flight

    queue.send(MOVE, labelled("move 2"))
    queue.send(MOVE, labelled("move 3"))
    gateway.answer_next()

    assert gateway.names() == ["move 1", "move 3"]


def test_a_stop_drops_the_moves_waiting_before_it() -> None:
    gateway = HoldingGateway()
    queue = AgentCommandQueue(gateway, "laptop-a", lambda kind, error: None)
    queue.send(MOVE, labelled("move 1"))  # in flight
    queue.send(MOVE, labelled("move 2"))  # waiting

    queue.send(STOP, labelled("stop"))
    gateway.answer_next()

    assert gateway.names() == ["move 1", "stop"]


def test_a_result_future_receives_the_answer_or_the_error() -> None:
    gateway = HoldingGateway()
    queue = AgentCommandQueue(gateway, "laptop-a", lambda kind, error: None)
    answered: Future[Any] = Future()
    refused: Future[Any] = Future()

    queue.send("set_mode", labelled("first"), answered)
    queue.send("set_mode", labelled("second"), refused)
    gateway.answer_next(result={"mode": "auto"})
    gateway.answer_next(error=RuntimeError("tracker_not_ready"))

    assert answered.result() == {"mode": "auto"}
    assert isinstance(refused.exception(), RuntimeError)


def test_failures_are_reported_and_the_queue_carries_on() -> None:
    gateway = HoldingGateway()
    failures: list[str] = []
    queue = AgentCommandQueue(gateway, "laptop-a", lambda kind, error: failures.append(kind))
    queue.send(STOP, labelled("stop"))
    queue.send("recall_preset", labelled("recall"))

    gateway.answer_next(error=RuntimeError("device_offline"))

    assert failures == [STOP]
    assert gateway.names() == ["stop", "recall"]


def test_clear_drops_what_waits_and_fails_its_result_futures() -> None:
    gateway = HoldingGateway()
    queue = AgentCommandQueue(gateway, "laptop-a", lambda kind, error: None)
    waiting: Future[Any] = Future()
    queue.send(MOVE, labelled("move"))  # in flight
    queue.send("set_mode", labelled("mode"), waiting)

    queue.clear()
    gateway.answer_next()

    assert gateway.names() == ["move"]
    assert isinstance(waiting.exception(), RuntimeError)
