# WP5c bench session: playout polish from the v0.2.0 bench

The checklist is §5 of the WP5c brief (Plane page "14 — WP5c brief").
Record the results in a comment on **LIVESTACK-56**. OBS is set up as in
`wp5.md` (32.x, WebSocket server with a password). Use an installed studio
(the installer's `ffmpeg\` folder present) for steps 1 and 4.

Settings are on **Playout setup** (`/playout/settings`, also the ⚙ on the
playout page and *Playout setup* in the top bar). The generic Settings page
now shows playout's JSON only, with a link there.

The media controls are under the program strip while a clip is on air.
They are always available there (the two-second hold-to-unlock was removed,
2026-11-14), red and marked LIVE because they act on air. `K` pauses/plays
and `J` / `L` go back / forward 5 s whenever a clip is on air. *Replay last*
has no key any more: it is the program panel's button only.

Without OBS: `python -m baja_studio.fakes.fake_obs --port 4455 --password P`
(it reopens a file when its speed changes, which is what step 6 checks on
real OBS).

## Steps

| # | Do | Expect / record |
|---|---|---|
| 1 | Tools: set both paths to `ffprobe` / `ffmpeg` (bare names). Save. Then clear both fields. Save. With nothing on PATH (or PATH's copies renamed). | No banner on the playout page either way. Under each tools field: "In use: bundled (C:\…\ffmpeg\ffprobe.exe)". Readiness `tools` green, naming the same paths. |
| 2 | Folders: receiver root `E:\clips`; add `E:\clips\patrocinadores` labelled `Patrocinadores`. Save. | The sponsor files are grouped under *Patrocinadores* (not *Unmatched*); other files of the root without a manifest entry stay *Unmatched*. |
| 3 | Playlist: *Edit* an existing playlist. Search the library box and *Add* a clip; ✕ one; drag one up (and ▲/▼); rename; Save; *Queue*. | Each clip change applies at once (no recreating); the queue follows the new order and name. The command log shows each `playlist_update`. |
| 4 | Playout setup → **Rescan library**. | "n of N files measured" counts up to "Rescan done"; durations and thumbnails come back (thumbnails briefly missing meanwhile). |
| 5 | Take a clip. Pause (or `K`), scrub (drag shows "→ m:ss.t", seek on release), `J`/`L` ±5 s, Play. Repeat with auto-play next on and a clip queued. | Watch program: no black or flash on pause, seek or resume. Seeks land on the keyframe before the point asked for (within 0.5 s on clips rendered since LIVESTACK-56). The strip says *paused* while paused. A pause never starts the next clip and never runs on-air *end* actions; resuming continues the same airing. `L` never replays the last clip (that is the *Replay last* button). There is no frame step (LIVESTACK-56). |
| 6 | Replay at 50 %: pick *50 %* under the preview, Cue, Take. | Cued at 50 %: half speed from the first frame, clip sound muted; the next cue is at 100 % again, with sound. |

## Also worth noting

- Does OBS 32 reopen a media source when only `speed_percent` changes
  (the fake assumes yes)? With *reload* on a **paused** clip, does it stay
  paused?
- How far the frame after a swap is from the frame before it (should be
  within a frame or two of OBS's request latency).
- `playout.program.transport` in the state: state, position, fps (ffprobe's;
  30 when unknown) and speed.

## Slow-motion replay on air (LIVESTACK-56, ADR 0017)

An OBS probe on the studio PC (OBS 33.0.1) showed that a media source only
seeks to the keyframe at or before the point asked for, that a file loaded
into a stopped source does not start without PLAY, and that RESTART does
nothing. So slowing down on air is now a **replay**: from 100 %, *50 %* or
*25 %* jumps back *Replay jumps back* seconds (Playout setup → OBS, default
3 s) and plays slowly and muted from the keyframe there. *100 %*, or 50 ↔
25 %, continues from about where the clip is (up to half a second repeats).

**Clips must be rendered with a keyframe every 0.5 s** (baja-video-pipeline
since LIVESTACK-56). Older clips (one every 8.3 s) still replay, but may
jump back further, to the start at worst. To check a clip:
`ffprobe -v error -skip_frame nokey -select_streams v:0 -show_entries frame=pts_time -of csv=p=0 clip.mp4`
lists its keyframes.

Without OBS: `python -m baja_studio.fakes.fake_obs --port 4455 --password P
--seed 7` (random, repeatable timing; keyframes every 0.5 s, or
`--keyframe-interval 8.333` for old clips). To see each step, start the
service with `BAJA_STUDIO_DEBUG_LOGGERS=baja_studio.playout`; INFO has one
line per change: "replay from 12.00 s, aimed 12.10 s", or "continue …".

| # | Do | Expect / record |
|---|---|---|
| 7 | A clip rendered after LIVESTACK-56, taken at 100 %. About 6 s in, press *50 %* in the media controls. | Program cuts (no black) to about 3 s earlier, playing at half speed, muted. Record the INFO line ("replay from … aimed …"): the two within 0.5 s. |
| 8 | Then *100 %*; later *25 %*; then *50 %*; then *100 %*. Include a rapid double click. | 100 %: continues from about where it was (at most half a second repeats), sound back. 25 % from 100 %: a replay again (jumps back). 50 % from 25 %: continues, no jump. Each takes effect; buttons say "changing…" while one runs, and a double click makes one change. Nothing about "did not start" or "did not move". |
| 9 | Paused on air, press *25 %*. Then Play. | The replay is ready about 3 s earlier, still paused; Play starts it slowly. |
| 10 | Near the end: replay (*50 %*) half a second before the end; then, at 50 %, *100 %* less than a second before the end. | The replay works (jumps back 3 s). The 100 % is refused: "too close to the end of the clip" beside the buttons; the clip ends normally (post-clip / auto-play as usual). |
| 11 | An old clip (8.3 s keyframes) at 100 %, 5 s in: *50 %*. | It replays from much earlier (the start): early, but no error and no black. |
| 12 | Cues: Cue a clip at 100 % and one at 50 %, Take each; with auto-play next on and two queued, let the first end; let the last end with a return scene set. | Each cue shows its first frame (no black); Take cuts on that frame; auto-play takes the next clip without black. Nothing in the log about "did not start" (a cue into a source a take stopped now gets PLAY). |
| 13 | Playout setup: *Replay jumps back* = 5 s, Save. Replay a clip. | It jumps back about 5 s. |
