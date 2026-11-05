"""Tests for playout's OBS connection worker (modules/playout/obs_worker.py).

The worker keeps playout connected to the fake OBS: it reconnects with
backoff, re-reads the program scene, and turns OBS events into playout
actions. "Killing OBS" is unplugging the fake from the in-process network;
"restarting" it is plugging a fake back in.
"""

from baja_studio.core.supervisor import HealthState
from baja_studio.fakes.fake_obs import FakeObs
from tests.conftest import eventually
from tests.modules.playout_studio import (
    CLIP_SECONDS,
    OBS_HOST,
    OBS_PORT,
    PlayoutStudio,
    ready_studio,
)


def health(playout: PlayoutStudio) -> HealthState:
    return playout.services.supervisor.module_health("playout").state


def test_an_address_where_nothing_answers_is_offline_and_keeps_retrying(
    playout: PlayoutStudio,
) -> None:
    # Unplug OBS before configuring: every connection attempt is refused.
    playout.network.unplug(OBS_HOST, OBS_PORT)

    playout.configure(obs=playout.obs_settings())

    eventually(lambda: playout.state("obs.connection") == "offline")
    eventually(lambda: health(playout) == HealthState.OFFLINE)
    assert "cannot reach OBS" in playout.state("obs.message")

    # OBS starts: within the backoff (at most 4 s) the worker is connected.
    playout.network.plug(OBS_HOST, OBS_PORT, playout.obs)
    playout.soon(lambda: playout.state("obs.connection") == "connected", within_seconds=5)


def test_a_wrong_password_is_degraded_with_a_clear_message(playout: PlayoutStudio) -> None:
    locked = FakeObs.with_playout_scenes(playout.clock, password="right")
    playout.network.plug(OBS_HOST, OBS_PORT, locked)

    playout.configure(obs=playout.obs_settings(password="wrong"))

    eventually(lambda: playout.state("obs.connection") == "auth_failed")
    eventually(lambda: health(playout) == HealthState.DEGRADED)
    assert "password" in playout.state("obs.message")

    # Fixing the password in the settings connects without a restart.
    playout.configure(obs=playout.obs_settings(password="right"))
    playout.soon(lambda: playout.state("obs.connection") == "connected", within_seconds=5)


def test_killing_obs_mid_clip_goes_offline_keeps_the_queue_and_reconnects(
    playout: PlayoutStudio,
) -> None:
    # Bench step 5, without the bench.
    first, second, _ = ready_studio(playout)
    playout.command("queue_add", clip_ids=[first, second])
    playout.command("take", cue_if_needed=True)

    playout.network.unplug(OBS_HOST, OBS_PORT)

    eventually(lambda: playout.state("obs.connection") == "offline")
    eventually(lambda: health(playout) == HealthState.OFFLINE)
    assert [entry["clip_id"] for entry in playout.state("queue")] == [second]

    # OBS comes back, freshly started: program is on "Main", not on the clip.
    restarted = FakeObs.with_playout_scenes(playout.clock)
    playout.network.plug(OBS_HOST, OBS_PORT, restarted)
    playout.soon(lambda: playout.state("obs.connection") == "connected", within_seconds=5)

    # Playout re-read the program scene, so it no longer claims a clip on air.
    assert playout.state("obs.current_scene") == "Main"
    assert playout.state("program") is None
    assert [entry["clip_id"] for entry in playout.state("queue")] == [second]


def test_a_cue_lost_in_an_obs_restart_is_loaded_again_on_reconnect(
    playout: PlayoutStudio,
) -> None:
    first, _, _ = ready_studio(playout)
    playout.command("queue_add", clip_ids=[first])
    playout.command("cue_next")

    playout.network.unplug(OBS_HOST, OBS_PORT)
    eventually(lambda: playout.state("obs.connection") == "offline")
    restarted = FakeObs.with_playout_scenes(playout.clock)
    playout.network.plug(OBS_HOST, OBS_PORT, restarted)
    playout.soon(lambda: playout.state("obs.connection") == "connected", within_seconds=5)

    assert restarted.media_of("baja_program_a").file.endswith("a.mp4")
    assert playout.state("preview")["clip"]["id"] == first


def test_an_operator_cutting_away_in_obs_ends_the_airing_without_post_clip(
    playout: PlayoutStudio,
) -> None:
    first, _, _ = ready_studio(playout)
    playout.command("queue_add", clip_ids=[first])
    playout.command("take", cue_if_needed=True)

    # Someone clicks another scene in OBS itself.
    playout.obs.add_scene("Interview")
    playout.obs.change_program_scene("Interview")

    playout.soon(lambda: playout.state("program") is None)
    assert playout.state("obs.current_scene") == "Interview"
    # Playout does not fight the operator: no post-clip return to "Main".
    assert playout.obs.program_scene == "Interview"
    exported = playout.command("export_aired")
    assert exported["entries"][0]["ended_at"] is not None


def test_the_position_of_the_clip_on_air_is_published_while_it_plays(
    playout: PlayoutStudio,
) -> None:
    first, _, _ = ready_studio(playout)
    playout.command("queue_add", clip_ids=[first])
    playout.command("take", cue_if_needed=True)

    playout.soon(lambda: (playout.state("program.position_s") or 0) >= 3.0)

    program = playout.state("program")
    assert program["duration_s"] == CLIP_SECONDS
    assert abs(program["remaining_s"] - (CLIP_SECONDS - program["position_s"])) < 1e-6


def test_changing_the_obs_address_reconnects_to_the_new_one(playout: PlayoutStudio) -> None:
    playout.connect_obs()
    other = FakeObs.with_playout_scenes(playout.clock, other_scenes=("Studio",))
    playout.network.plug("obs2.test", OBS_PORT, other)

    playout.configure(obs=playout.obs_settings(host="obs2.test"))

    playout.soon(lambda: playout.state("obs.current_scene") == "Studio")
    assert playout.obs.session_count == 0
