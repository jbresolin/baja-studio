"""Tests for the mixer worker's connection handling (modules/audio/mixer_worker.py).

Most of its behaviour is checked through the running module in
test_audio.py; these cover the address that cannot be used at all.
"""

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from baja_studio.adapters.xr18_transport import Xr18Transport, Xr18UnreachableError
from baja_studio.core.supervisor import HealthState
from baja_studio.modules.audio import AudioModule
from tests.conftest import AppFactory, FakeClock, module_worker_threads, step_fake_time_until
from tests.modules.audio_studio import InProcessMidiHost

WORKERS = module_worker_threads("audio")


def unresolvable(host: str, port: int) -> Xr18Transport:
    raise Xr18UnreachableError(f"cannot resolve mixer address {host!r}: no such host")


@pytest.fixture
def client(make_app: AppFactory) -> Iterator[TestClient]:
    from baja_studio.fakes.fake_midi import FakeMidiBackend

    module = AudioModule(
        mixer_transport_factory=unresolvable,
        midi_child_factory=lambda context, on_event: InProcessMidiHost(FakeMidiBackend(), on_event),
    )
    app = make_app(lambda registry: [module])
    with TestClient(app) as test_client:
        yield test_client


def test_an_address_that_does_not_resolve_is_offline_with_the_reason(
    client: TestClient, fake_clock: FakeClock
) -> None:
    services = client.app.state.services  # type: ignore[attr-defined]
    services.settings.update("audio", {"mixer": {"host": "xr18.nowhere", "port": 10024}})

    step_fake_time_until(
        fake_clock,
        lambda: services.state.get("audio", "mixer.connection") == "offline",
        3.0,
        WORKERS,
    )

    assert "no such host" in services.state.get("audio", "mixer.message")
    assert services.supervisor.module_health("audio").state == HealthState.OFFLINE
