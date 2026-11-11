# WP5c bench session: playout polish from the v0.2.0 bench

The checklist is §5 of the WP5c brief (Plane page "14 — WP5c brief").
Record the results in a comment on **LIVESTACK-56**. OBS is set up as in
`wp5.md` (32.x, WebSocket server with a password). Use an installed studio
(the installer's `ffmpeg\` folder present) for steps 1 and 4.

Settings are on **Playout setup** (`/playout/settings`, also the ⚙ on the
playout page and *Playout setup* in the top bar). The generic Settings page
now shows playout's JSON only, with a link there.

The media controls are under the program strip while a clip is on air.
They are locked: hold **Hold to unlock media controls** for two seconds
(mouse, touch, or Enter held). Unlocked, the bar is red and marked LIVE;
**Lock**, or the next clip going to air, locks it again.

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
| 5 | Take a clip. Unlock the media controls. Pause (or `K`), frame-step `.` and `,` a few times, scrub (drag shows "→ m:ss.t", seek on release), `J`/`L` ±5 s, Play. Repeat with auto-play next on and a clip queued. | Watch program: no black or flash on pause, step, seek or resume. The strip says *paused* while paused. A pause never starts the next clip and never runs on-air *end* actions; resuming continues the same airing. `L` is ±5 s only while unlocked (Replay last otherwise). |
| 6 | Replay at 50 %: pick *50 %* under the preview, Cue, Take. Then on air: 100 → 50 → 100 with the media controls' speed buttons, once with *Speed change on air: swap* (default) and once with *reload* (Playout setup → OBS). | Cued at 50 %: half speed, clip sound muted; 100 % again restores sound. Note for each method whether program shows a hiccup on OBS 32 (reload reopens the file on air; swap cuts to the other source on the same frame). Record which is clean. |

## Also worth noting

- Does OBS 32 reopen a media source when only `speed_percent` changes
  (the fake assumes yes)? With *reload* on a **paused** clip, does it stay
  paused?
- How far the frame after a swap is from the frame before it (should be
  within a frame or two of OBS's request latency).
- `playout.program.transport` in the state: state, position, fps (ffprobe's;
  30 when unknown) and speed.

## Live speed change, fixed (LIVESTACK-56, ADR 0015)

Without OBS, `python -m baja_studio.fakes.fake_obs --port 4455 --password P
--seed 7` makes opens, seeks and events take random (repeatable) time, as
OBS does. To see each step, start the service with
`BAJA_STUDIO_DEBUG_LOGGERS=baja_studio.playout` (PowerShell:
`$env:BAJA_STUDIO_DEBUG_LOGGERS="baja_studio.playout"`): every step logs its
input, request number, and target and confirmed positions (Logs page, log
file); INFO has one line per change with its result.

| # | Do | Expect / record |
|---|---|---|
| 7 | *Swap* (default). Take a clip, unlock the media controls, then 10 speed changes in a row on air (100/50/25), including rapid double clicks. | Each change takes effect (strip and OBS agree on the speed); the clip keeps its position (no jump back, no restart from 0); no black on program. While a change runs the speed buttons say "changing…" and are disabled; a double click makes one change. Note the longest change (INFO log, "in N s"). |
| 8 | *Reload* (Playout setup → OBS). One change 100 → 50 on air. | Takes effect, position kept (program may show a reload hiccup). |
| 9 | *Swap*, a clip less than 2 s from its end: change the speed. | Either it changes on the frame, or "too close to the end of the clip" beside the speed buttons; the clip ends normally either way (post-clip / auto-play as usual). |
| 10 | Optional: make OBS slow (open a huge file in the other source) or stop OBS's media source mid-change. | After at most 2 s: no cut, the clip plays on at its old speed, "Speed not changed: …" shown. |
| 11 | Cues now use the same steps (load, wait for the start, pause, seek and read back). Re-check: Cue a clip at 100 % and one at 50 %, Take each; turn auto-play next on with two clips queued and let the first end; let the last one end with a return scene set. | Each cue shows its first frame in preview (no black); Take cuts on that frame; auto-play takes the next clip without black; after the last clip program goes to the return scene. Nothing in the log about "did not start". |
