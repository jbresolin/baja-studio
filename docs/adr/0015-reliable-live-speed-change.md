# ADR 0015 — WP5c fix: reliable live speed change

Date: 2026-11-10. Work item: LIVESTACK-56. Bench finding: on OBS 32 in swap
mode a live speed change sometimes worked, sometimes changed nothing, went
black or restarted the clip from 0, and every one was logged as a success.
Follows ADR 0012 (decisions 15–18, which this replaces for the live change
and the cue mechanics). No `core/` changes.

## Reproduced first

1. **A fake OBS with seeded timing** (`fakes/obs_timing.py`,
   `fakes/fake_obs_media.py`; `fake_obs --seed N` on the bench). Per input,
   from the seed: *opening* 0–30 ms (seeks refused, as on the 2026-11-08
   bench), then status says **playing with no frame** for 50–500 ms (black
   on screen; a seek is accepted and **ignored**), then the first frame and
   `MediaInputPlaybackStarted`. Seeks on a source with a frame land 0–100 ms
   later. Media events leave 0–150 ms after they happen, so the two
   sources' events interleave. Identical settings reopen nothing and send no
   event; any other settings change reopens the file. The fake records every
   look at program that found it black, and every cut with its time. Without
   a seed every delay is zero, as before.
2. **The seeded test** (`tests/modules/test_playout_transport_races.py`):
   100 → 50 → 100 → 50 on air, 200 seeds, swap and reload. It drives the
   real client, switcher and transport on `SimulationClock`
   (`tests/playout/obs_simulation.py`), which moves time itself whenever the
   code waits, straight to the fake's next event, so 400 runs take seconds.
   Checks: right speed, playing with a frame, playout's slot is OBS's
   program, no black (swap only), position within one frame of where it was
   plus the time at the old speed until the switch and at the new one after.
   On the code before this fix: **200 of 200 seeds failed in both modes**
   (the status said "playing" before the first frame, so the cut came too
   early: black, seek ignored, clip from 0).

## Decisions

3. **The state machine** (`playout/parking.py` steps, `playout/live_speed.py`
   order): force-load (a source already holding the file is emptied first,
   so OBS reopens it and a start is sure to come), wait for the start, pause
   until status says paused, seek and read back (up to 3 sends, 250 ms each),
   then for the swap: wait for program to reach the parked frame, cut, play,
   stop the old source. Cues use the same steps (`park`, 3 s in all).
4. **Pause before seek**, not after (the brief had seek, confirm, pause). On
   a paused source the read-back must equal the target; on a playing one it
   is always moving, so "matches" would need a guess at OBS's latency.
5. **Park ahead, then wait.** A seek takes OBS a moment, so a source parked
   on "now" lands behind program. The swap parks 0.25 s (on air) ahead of
   the frame on air and cuts when program gets there; if program already
   passed it, it parks again (3 times at most). In the fake the cut frame
   is exact; on OBS it is off by the request latency of reading program.
6. **"After this request".** `ObsWebsocketClient.expecting_event(...,
   after_next_request=True)` is armed by the response to the next request
   the thread sends (request numbers increase per client). OBS sends on one
   socket in order, so a start read before that response is stale and
   ignored; other inputs never match.
7. **2 s for the whole change.** On a timeout or refusal nothing is cut: the
   off-air source is stopped, a displaced cue is cued again, the clip on air
   plays on at its old speed, and the command fails with "speed change
   failed, still at N %: …". Also kept in state (`speed_change.error`) and
   shown beside the speed buttons.
8. **Near the end**: a swap whose parked frame would be past the last frame
   is refused ("too close to the end of the clip to change its speed") and
   the clip ends normally. Reload refuses up front when the clip could end
   within 2.25 s at the new speed, because by then it has already reloaded
   the source on air.
9. **One change at a time** (`modules/playout/speed_change.py`). The caller
   that starts a change carries it out and then the waiting wish; a request
   meanwhile replaces the waiting wish and answers `queued` at once, so only
   the last wish wins. State `speed_change {changing, target, error}`; the
   page disables the speed buttons and shows "changing to N %…" from the
   click until the answer, so a double click sends one command.
10. **Reload mode**, same steps on the source on air: forced reload at the
    new speed (OBS empties it first, so program is black until the first
    frame), pause, seek ahead of where the clip should be by then, confirm,
    wait until then, play. The position is right; the hiccup is inherent.
11. **Logging**: INFO one line per change with its result, method, change
    number, who and how long; DEBUG every step with the input, request
    number, target and confirmed positions (`baja_studio.playout.parking`,
    `baja_studio.playout.live_speed`). DEBUG is switched on per logger at
    startup with `BAJA_STUDIO_DEBUG_LOGGERS=baja_studio.playout` (`main.py`);
    there was no way to see DEBUG before.

## What the seeded test does and does not prove

- Removing the lead, the read-back, or the emptying of a source that holds
  the file each makes 200 of 200 seeds fail. Replacing the armed start with
  the old status check does **not**: the seek read-back retries cover it.
  The sequence never produces a stale start (the target is emptied first),
  so the arming is covered by the client's unit tests only.
- Requests take no time in the simulation; on OBS each takes a few ms,
  which is the remaining error at the cut.
- The fake's behaviour is our model of OBS 32, from the bench symptoms.
  Whether OBS really reports "playing" before the first frame, and whether a
  paused seek reads back exactly, is what the bench confirms.

## Left open

- **Files over the ~300-line guideline**: `adapters/obs_websocket.py`
  (~360) and `fakes/fake_obs.py` (~390), besides those in ADR 0012.
- Black on program during a reload is allowed by the test (the fake models
  OBS emptying the source); swap remains the default.
