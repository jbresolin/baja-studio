# ADR 0012 — WP5c: playout polish from the v0.2.0 bench

Date: 2026-11-10. Work item: LIVESTACK-56 (under LIVESTACK-8). Brief: Plane
page "14 — WP5c brief". Bench findings: comment on LIVESTACK-54
(2026-11-09). Follows ADRs 0005 and 0009; ran after WP9 (ADR 0011) merged.
No `core/` changes.

## Decisions taken beyond the brief

### Fixes (§1)

1. **One resolver, injected.** `playout/tools.py` owns resolving *and* the
   banner text (`tools_status`, moved out of `probe.py`, which had its own
   `shutil.which` lookup: the bench bug). `PlayoutModule` takes a
   `tool_resolver` (default `resolve_tools`; tests fake only the PATH
   lookup) and every consumer asks `module.resolved_tools()`: the prober
   factory (now given `ResolvedTools`), the banner, readiness. The
   module's `find_tool` argument is gone. The hosts view never touched the
   tools, so it needed nothing.
2. **Blank means automatic.** `ToolsSettings` defaults to `""` (was the
   bare names); blank, whitespace or the bare name all mean "bundled copy,
   else PATH". Older settings and exports holding `ffprobe` mean the same
   thing, so `settings_version` stays 2.
3. **The banner is looked up again on every settings save**, not only when
   the tools section changes, and on "Rescan library": an installer run
   that adds the bundled copies clears it without a restart. Probe
   failures are still cleared only when the tools change.
4. **Nested folders: the most specific folder claims a file.** The scanner
   walks folders deepest first (settings order between equals), and
   compares paths case- and separator-normalised (`E:/clips/x/` is
   `e:\clips\x`). A file claimed by `E:\clips\patrocinadores` has that
   label, `in_receiver_root` false (so it is *manual*, never *unmatched*)
   and a path relative to that folder. Pipeline clips still match by
   absolute path, so the manifest is unaffected.
5. **Settings consolidation.** *Playout setup* (`/playout/settings`) is in
   the top bar next to *Playout* (named like *Camera setup*/*Audio setup*;
   the ⚙ is on the playout page beside the status line and in the page's
   heading). The generic page's playout section is a JSON editor with a
   link (`MODULE_SETTINGS_PAGES` in `Settings.tsx`, the new
   `SettingsJsonEditor`). Only playout is listed: cameras and audio also
   have setup pages, but whether those cover every field was not checked
   (left open). Recorded as a convention.
6. **Rescan library** is a module command (`rescan_library`, so it is in
   the command log): forget duration, fps, thumbnail and probe error of
   every row, delete every file in the thumbnail cache, re-publish, wake
   the scanner and the prober. It returns how many present files will be
   measured; progress is `library_summary.pending_probe` (present, not
   measured, not failed), which the page counts down.

### Playlists (§2)

7. **No server change**: `playlist_update {clip_ids}` already replaced the
   list. The editor sends the whole new order on each add, remove or move
   (so each edit is one logged command); name and on-air actions still save
   with *Save*. A playlist keeps at least one clip (the server refuses an
   empty list; the page disables the last ✕): delete it instead. The add
   box searches the library like the library's search and offers present
   clips only; a clip may be added twice.

### Media controls (§3)

8. **One command, `transport {action}`**: `play`, `pause`, `toggle` (K),
   `restart`, `seek {position_s}`, `step {frames}`, `nudge {seconds}`. It
   answers the new `program.transport` and publishes it at once (the 5 Hz
   poll keeps it current). Pausing is logged by the module with the
   client id (`'Intro' paused on air by web:ab12`), besides the command log.
9. **Restart is seek 0 + play**, not OBS's `RESTART`, which reopens the
   file on air (a black frame on some files). It is a transport action: no
   new airing, no webhook, no on-air actions.
10. **Seeks stop one frame before the end.** A ±5 s or scrub to the end
    would make OBS end the clip, which is a natural end (auto-play,
    post-clip, end actions). Ending a clip is Skip's job; the controls
    never do it by accident.
11. **Frame step needs a pause** (refused while playing, "pause first") and
    moves `frames / fps` seconds of the file: fps is ffprobe's average
    frame rate of the first video stream (new `clips.fps` column; one
    ffprobe call now returns duration and rate as JSON), else 30.
12. **Hold-to-enable is the page's**: hold *Hold to unlock media controls*
    2 s (pointer, or Enter held: Space is Take). The unlock belongs to one
    airing (`started_at`): the next clip on air locks the bar again, as
    does *Lock*. While unlocked, `K`, `J`/`L`, `,`/`.` act on air and `L`
    is +5 s instead of Replay last; locked, those keys do what they did.
    The server does not know about the lock (any client may send
    `transport`, like any other command).
13. **The strip shows *paused* and the speed** (e.g. *50 %*) to every
    operator, unlocked or not.

### Slow motion (§4)

14. **Replay speed belongs to one cue.** `set_cue_speed {speed_percent}`
    (100/50/25): with a clip cued, it is cued again at that speed (off air,
    so harmless); otherwise the next cue gets it, after which it is 100 %
    again (`cue_speed_percent` in state, kept in the session table). A
    sticky slow-motion setting would leave the next sponsor clip at half
    speed.
15. **Cue mechanics moved to `playout/parking.py`** (`MediaParker.park`:
    settings incl. `speed_percent` → mute rule → restart → wait for playing
    → pause → seek), shared by the cue and the swap. `CueError` wraps its
    `ParkError`.
16. **Mute rule.** Below 100 % the source is muted (`SetInputMute`, new
    adapter request) *before* the restart; back at 100 % it is unmuted only
    if playout muted it, so a source muted in OBS by the operator stays
    muted. Which sources playout muted is kept in memory only: a source
    muted by a slow replay when the service restarts stays muted until the
    operator unmutes it in OBS (left open, minor).
17. **Live change (`set_program_speed`), `obs.live_speed_change`**:
    - `reload`: read status, set `speed_percent` on the source on air,
      wait for it to play (or, if it was paused, to be paused again: OBS
      may not reopen it), seek to the old position plus the reload time at
      the new speed, pause again if it was paused. No cut.
    - `swap` (default): park the clip at the new speed in the off-air
      source at 0, read the on-air position, seek the parked one there, cut
      (`ProgramSwitcher.move_program_to`: our own scene change, so not
      "the operator cut away"), play unless paused, stop the old source. A
      clip cued in that source is cued again in the freed one afterwards
      (back to the queue head with the cue problem if that fails).
    Either way the airing goes on: same start, log entry and webhooks; no
    on-air actions. The program's speed is saved in the session.
18. **The fake OBS reopens a file when only `speed_percent` changes**
    (`SimulatedMedia.reload`) and plays at `speed` (position in the file's
    seconds, as OBS reports it). That is an assumption about OBS 32; bench
    step 6 checks it. Playout copes either way (decision 17).

## Left open

- **Bench** (`docs/bench/wp5c.md`) not run; in particular which live
  method is clean on OBS 32, and whether OBS reopens on a speed change.
- **Cameras and audio settings pages** in `MODULE_SETTINGS_PAGES`, once
  someone checks that their pages cover every field.
- **Muted-by-playout after a restart** (decision 16).
- **Files over the ~300-line guideline**: `modules/playout/controller.py`
  (~390; transport and speed went to `transport.py`, but the controller
  gained the speed bookkeeping of cue and session), `playout/library.py`
  (~325), `frontend/src/lib/playout.ts` (~480; the keyboard map could be
  its own file), `PlayoutSettingsPage.tsx` (~320). Revisit if they grow.
