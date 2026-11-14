# ADR 0017 — Slow motion on air is a replay; the fake OBS models what OBS 33 does

Date: 2026-11-14. Work item: LIVESTACK-56. Supersedes ADR 0015 for the live
speed change. Companion change in baja-video-pipeline: the final render puts
a keyframe every 0.5 s (its PR for LIVESTACK-56). No `core/` changes.

## What the bench and the probe found

After ADR 0015, every live speed change on the studio PC failed safely:
"OBS did not move '…' to 6.52 s (it is at 0.17 s)", once "OBS did not start
… (OBS_MEDIA_STATE_STOPPED)". The fake OBS had been built from guesses, so
a probe script asked the real OBS (33.0.1, obs-websocket 5.7.6) exactly what
playout asks, with timestamps:

1. **A seek lands on the keyframe at or before the target**, playing or
   paused: 5 s → 0.03 s, 10 s → 8.23 s, 3 s → 0.03 s. The rendered clips had
   keyframes every 8.333 s (250 frames at 30 fps: the pipeline set no
   interval, so the encoder default applied).
2. **A file loaded into a stopped source does not start** (no started event,
   state stays STOPPED) until PLAY. Emptying it first changes nothing.
   Playout stops the source it leaves after every take and swap, so the next
   load there never started.
3. In that loaded-while-stopped state a paused seek did nothing, even onto
   an exact keyframe, and PLAY left the position frozen. A seek on a playing
   source works.
4. "Started" arrives ~30 ms after the reply to the load (ADR 0015's
   after-reply filter is right); pause is immediate; identical settings
   reload nothing; a new speed reloads the file from 0.
5. **RESTART does nothing** to a playing source. Playout never sends it.

## Decisions

1. **Slow motion on air is a replay** (João, 2026-11-14). From 100 %, 50 %
   or 25 % jumps back `obs.replay_jump_back_s` (default 3 s, 0–30) and plays
   from the keyframe there, slowly and muted. 100 %, or 50 ↔ 25 %,
   *continues* from about where the clip is: aimed 0.5 s (of air) ahead, it
   lands on the keyframe before, so at most half a second repeats. A visible
   jump, like a sports replay; nothing depends on an exact frame.
2. **Always in the other source, one cut** (`playout/live_speed.py`): load at
   the new speed (forced, PLAY if it was stopped), seek while it plays, take
   wherever OBS landed, pause it if the clip on air was paused, cut, stop the
   source left. A clip cued there is cued again after. The **reload** method
   and the `obs.live_speed_change` setting are gone (reload always showed
   black, and its exactness was the point); old saved settings and exports
   still load and drop the key (`settings_version` 3).
3. **`seek_playing`** (`parking.py`) accepts a landing anywhere up to the
   target plus what the clip played since. It has landed once the position
   jumps more than 0.25 s away from where playback alone would have taken it,
   or, when the keyframe is so close that no jump shows, 0.3 s after the seek
   (OBS took 15–60 ms on the studio PC). Up to three tries within the
   change's 3 s.
4. **PLAY after loading a stopped or ended source** (`MediaParker.load`),
   for cues too. This is also why a cue into a source a take had stopped
   could have failed on real OBS after ADR 0015 dropped RESTART.
5. **Cues still park paused on the first frame** (0 is always a keyframe).
   The read-back tolerance is at least 0.034 s: OBS reports 0.033 s for the
   first frame, which one frame of a 60 fps clip would refuse.
6. **No frame step.** One frame forward would land back on the keyframe
   before. The `step` transport action, its buttons and `,` / `.` are gone.
   The scrub bar and ±5 s stay, landing on keyframes.
7. **Too close to the end**: a change whose aimed point is less than 1 s
   from the end is refused (a replay goes back, so it is refused only on very
   short clips; a continue near the end is the usual case).

## The fake OBS now models the probe

`SimulatedMedia` seeks to the keyframe at or before the target
(`keyframe_interval_s`; `FakeObs(keyframe_interval_s=0.5)` by default,
`--keyframe-interval` on the command line, None for exact seeks), keeps a
stopped source stopped when a file or settings are loaded (and when
emptied), and ignores RESTART on a playing or paused source.

## Tests

- `tests/fakes/test_simulated_media.py`: keyframe landings (8.333 s and
  0.5 s), stopped-source loads waiting for PLAY, emptied stopped source,
  RESTART ignored.
- `tests/playout/test_parking.py`: PLAY sent to a stopped source only;
  `seek_playing` lands on the keyframe before, on old clips seconds early,
  and counts a close keyframe once settled.
- `tests/modules/test_playout_transport.py`: replay jumps back 3 s slowly and
  muted; back to 100 % continues within half a second; 50 → 25 continues;
  the jump-back setting; near the start; far keyframes; two changes in a row
  reuse the stopped source; a cued clip is cued again; a paused clip replays
  paused; no frame step; seeks land on keyframes.
- `tests/modules/test_playout_transport_races.py`: 100 → 50 → 100 → 25 over
  200 seeds against the seeded fake with 0.5 s keyframes; checks speed,
  playing with a frame, no black, and the landing window. Without the PLAY
  after loading a stopped source, all 200 seeds fail with the bench's own
  message ("did not start … OBS_MEDIA_STATE_STOPPED").

## Bench

`docs/bench/wp5c.md` steps 7–13 replace the old 7–11.
