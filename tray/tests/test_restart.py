"""Tests for restarting both services in order (restart.py)."""

from collections.abc import Sequence
from pathlib import Path

from baja_studio_tray.restart import restart_services, wait_until_answering
from baja_studio_tray.service import StudioClient
from tests.fake_studio import FakeStudio

NSSM = Path("Program", "nssm", "nssm.exe")


class FakeNssm:
    """Records NSSM commands; `failing` lists the verbs that exit with an error."""

    def __init__(self, failing: Sequence[str] = ()) -> None:
        self.commands: list[list[str]] = []
        self._failing = set(failing)

    def run(self, command: Sequence[str]) -> int:
        self.commands.append(list(command))
        return 1 if command[1] in self._failing else 0


def test_the_gateway_waits_for_the_studio_to_answer() -> None:
    nssm = FakeNssm()
    events: list[str] = []

    def wait(client: StudioClient) -> bool:
        # Record when the wait happens among the NSSM commands.
        events.append(f"wait after {len(nssm.commands)} commands")
        return True

    ok = restart_services(NSSM, FakeStudio().client(), nssm.run, wait)

    # Caddy depends on the studio: it goes down first and comes back last,
    # once the studio answers (it writes the Caddyfile before that).
    assert nssm.commands == [
        [str(NSSM), "stop", "BajaStudioGateway"],
        [str(NSSM), "restart", "BajaStudio"],
        [str(NSSM), "start", "BajaStudioGateway"],
    ]
    assert events == ["wait after 2 commands"]
    assert ok


def test_a_studio_that_never_answers_still_gets_its_gateway_started() -> None:
    nssm = FakeNssm()

    ok = restart_services(NSSM, FakeStudio().client(), nssm.run, lambda client: False)

    # NSSM keeps retrying Caddy anyway, so it is started; the result says it failed.
    assert nssm.commands[-1] == [str(NSSM), "start", "BajaStudioGateway"]
    assert not ok


def test_a_failed_restart_is_reported() -> None:
    nssm = FakeNssm(failing=["restart"])

    assert not restart_services(NSSM, FakeStudio().client(), nssm.run, lambda client: True)


def test_waiting_polls_until_the_service_answers() -> None:
    studio = FakeStudio(answering=False)
    now = [0.0]

    def sleep(seconds: float) -> None:
        # Time moves only when the code sleeps; the service starts after 3 s.
        now[0] += seconds
        studio.answering = now[0] >= 3.0

    assert wait_until_answering(studio.client(), 60.0, lambda: now[0], sleep)
    assert now[0] == 3.0


def test_waiting_gives_up_after_the_timeout() -> None:
    now = [0.0]

    def sleep(seconds: float) -> None:
        now[0] += seconds

    answered = wait_until_answering(
        FakeStudio(answering=False).client(), 5.0, lambda: now[0], sleep
    )

    assert not answered
    assert now[0] == 5.0
