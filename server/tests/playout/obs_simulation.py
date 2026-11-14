"""A simulated studio for timing races: playout's switching against a fake OBS with seeded delays.

Why a simulation? The races of a speed change on air (a seek sent before the
file has a frame, a "started" event left over from an earlier open, events
of the two sources interleaving) depend on how long OBS takes for each step.
`FakeObs(timing=ObsTiming(seed))` draws those delays from a seed. To run
hundreds of seeds quickly, nobody steps time by hand here: `SimulationClock`
moves time itself whenever the code under test waits, straight to the next
moment something happens in the fake (a frame arrives, a seek lands, an
event is due) or to the waiter's deadline, whichever is first. Requests
take no time at all; only OBS's background work does.

One thread drives everything (the test calls playout's code directly). The
client's reader thread is the only other one; before each step the clock
waits until it has handled every message the fake sent, so a step never
races it.
"""

import contextlib
import logging
import threading
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from baja_studio.adapters.obs_protocol import MediaState
from baja_studio.adapters.obs_transport import ObsAddress, ObsTransport
from baja_studio.adapters.obs_websocket import ObsWebsocketClient
from baja_studio.fakes.fake_obs import FakeObs
from baja_studio.fakes.fake_obs_in_process import InProcessObsNetwork, InProcessObsTransport
from baja_studio.fakes.obs_timing import ObsTiming
from baja_studio.modules.playout.transport import ProgramTransport
from baja_studio.playout.config import PlayoutSettings
from baja_studio.playout.switcher import ProgramSwitcher

HOST, PORT = "obs.test", 4455
START = datetime(2026, 11, 10, 20, 0, tzinfo=UTC)
# Something in the fake that is due "now" but did not happen (float rounding at
# a clip's end): step this much instead, so a wait always makes progress.
SMALLEST_STEP_SECONDS = 0.001


class SimulationClock:
    """A clock whose time moves only while the (single) driving thread waits."""

    def __init__(self) -> None:
        """Time zero; `attach` the fake OBS and the transports before use."""
        self._now = 0.0
        self._fake: FakeObs | None = None
        self.transports: list[InProcessObsTransport] = []

    def attach(self, fake: FakeObs) -> None:
        """The fake whose media time this clock drives."""
        self._fake = fake

    def monotonic(self) -> float:
        """Simulated seconds since the start."""
        return self._now

    def now_utc(self) -> datetime:
        """A fixed evening plus the simulated time."""
        return START + timedelta(seconds=self._now)

    def wait(self, event: threading.Event, seconds: float) -> bool:
        """Let simulated time pass until `event` is set or `seconds` have passed."""
        deadline = self._now + seconds
        while True:
            self._settle()
            if event.is_set():
                return True
            if self._now >= deadline - 1e-9:
                return False
            self._step_towards(deadline)

    def let_time_pass(self, seconds: float) -> None:
        """The test's own wait: OBS keeps playing for `seconds`."""
        self.wait(threading.Event(), seconds)

    def call_later(self, seconds: float, callback: Callable[[], None]) -> threading.Timer:
        """Not used by the speed change (fades only); refuse loudly if it ever is."""
        raise AssertionError("call_later is not simulated")

    async def sleep(self, seconds: float) -> None:
        """Not used by playout's switching."""
        raise AssertionError("sleep is not simulated")

    def _step_towards(self, deadline: float) -> None:
        assert self._fake is not None
        upcoming = self._fake.next_change_at()
        if upcoming is None or upcoming > deadline:
            target = deadline
        else:
            target = max(upcoming, self._now + SMALLEST_STEP_SECONDS)
        self._now = min(target, deadline)
        self._fake.tick()

    def _settle(self) -> None:
        for transport in self.transports:
            assert transport.wait_until_idle(), "the OBS client never caught up"


@dataclass
class SimulatedStudio:
    """Fake OBS (seeded), a connected client, the switcher and playout's transport."""

    clock: SimulationClock
    fake: FakeObs
    client: ObsWebsocketClient
    switcher: ProgramSwitcher
    transport: ProgramTransport
    settings: PlayoutSettings
    lost_cues: list[str] = field(default_factory=list)

    def program_media(self) -> Any:
        """The `SimulatedMedia` of the source on OBS's program scene."""
        (item,) = self.fake.scenes[self.fake.program_scene]
        return self.fake.media_of(item.source_name)

    def program_cursor(self) -> float:
        """Where program is in the clip, in the file's seconds."""
        return float(self.program_media().cursor(self.clock.monotonic()))

    def is_playing(self) -> bool:
        """Program plays and shows frames."""
        media = self.program_media()
        return bool(media.state == MediaState.PLAYING and media.has_frame)


@contextlib.contextmanager
def simulated_studio(
    seed: int, clip_seconds: float = 30.0, keyframe_interval_s: float | None = 0.5
) -> Iterator[SimulatedStudio]:
    """A studio with one clip on air at 100 %, then OBS's seeded delays switched on.

    The clip is cued and taken with no delays (so every seed starts from the
    same, clean picture); everything after that takes OBS's random time.
    A context manager: the client is closed on leaving it.
    """
    clock = SimulationClock()
    fake = FakeObs.with_playout_scenes(
        clock, duration_for=lambda _file: clip_seconds, keyframe_interval_s=keyframe_interval_s
    )
    clock.attach(fake)
    network = InProcessObsNetwork()
    network.plug(HOST, PORT, fake)

    def connect(address: ObsAddress, timeout_s: float) -> ObsTransport:
        transport = network.connect(address, timeout_s)
        assert isinstance(transport, InProcessObsTransport)
        clock.transports.append(transport)
        return transport

    client = ObsWebsocketClient(clock, connect)
    client.connect(ObsAddress(HOST, PORT), "")
    settings = PlayoutSettings.model_validate({"obs": {"host": HOST, "port": PORT}})
    switcher = ProgramSwitcher(client, lambda: settings.obs, clock)
    switcher.prepare_scenes()
    logger = logging.LoggerAdapter(logging.getLogger("tests.playout.simulation"), {})
    lost_cues: list[str] = []
    transport = ProgramTransport(
        switcher,
        lambda: settings,
        clock,
        logger,
        save_session=lambda: None,
        cue_lost=lambda _cued, reason: lost_cues.append(reason),
    )
    clip = {"id": 1, "title": "Pass 3", "file_path": "E:/clips/pass3.mp4", "fps": 25.0}
    switcher.cue(clip)
    switcher.take()
    fake.timeline.timing = ObsTiming(seed)
    try:
        yield SimulatedStudio(clock, fake, client, switcher, transport, settings, lost_cues)
    finally:
        client.close()
