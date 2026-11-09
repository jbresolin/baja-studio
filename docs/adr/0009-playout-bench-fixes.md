# ADR 0009 — WP5b: playout fixes and features from the bench

Date: 2026-11-09. Work item: LIVESTACK-54 (under LIVESTACK-8). Brief: Plane
page "11 — WP5b brief". Bench findings: comments on LIVESTACK-35
(2026-11-08/09). Follows ADR 0005; ran in parallel with WP7b.

## Decisions taken beyond the brief

### Cue and auto-play (§1)

1. **The fake OBS now opens media like OBS.** Loading or restarting a file
   puts the source in `OPENING`; it starts on the fake's next tick (any
   request, or the runnable fake's 0.1 s loop) and only then sends
   `MediaInputPlaybackStarted`. `SetMediaInputCursor` on a source that is
   not playing or paused is refused with OBS's words (status 604). WP5's
   fake started a file the moment it was loaded, which is why the bench bug
   never showed in the tests. A file in `FakeObs.stuck_files` never starts.
2. **Cue = set file → restart → wait until playing → pause → cursor 0.**
   The wait asks `GetMediaInputStatus` (every 0.1 s, at most 3 s) and is
   woken early by `MediaInputPlaybackStarted`; the status is the truth,
   because a started event left over from loading the file can arrive late.
   The adapter gained `ObsWebsocketClient.expecting_event(type, **match)`
   for that wake-up (protocol only, no policy). A timeout or a refused
   rewind raises `CueError`: the command is refused with OBS's words, the
   clip goes back to the head of the queue, `cue_problem` is published, and
   the `obs` worker reports `degraded` until a cue works.
3. **Auto-play is a take.** The finished clip stays the program until the
   next one replaces it (one scene change, and pages never see "nothing on
   air" in between; WP5 ended the program first, which also published a
   brief `program: null`). If auto-play cannot cue or take (OBS refused, the
   next file vanished — `LibraryError` used to escape and restart the `obs`
   worker), post-clip applies and a notice says why. The test that found
   this is `test_when_auto_play_cannot_cue_the_next_clip_post_clip_applies_with_a_notice`.

### Return target (§1)

4. **"Previous" is captured at any take made while program is not a
   playback scene.** That covers the first take of a run, an auto-play
   chain, and a take after a held clip (program still on a playback scene:
   previous is kept). Program as OBS reports it (`current_scene`) is used;
   no extra request at the take.
5. **Session state, `return_target` key.** Mode, chosen scene and captured
   previous scene survive a restart and are never exported. OBS's scene list
   is published as `obs.scenes` (playback scenes left out), read on connect
   and again after `SceneCreated/Removed/NameChanged/ListChanged`.
6. **`obs.return_scene` is gone; `settings_version` is 2.** Saved settings
   and exports that still carry it load (a `before` validator drops the
   key). The readiness `obs_scenes` check uses the return target instead:
   a chosen scene that does not exist fails; no target warns.
7. **No target never silently does nothing**: `return_target.warning` is on
   the strip all the time, a clip that ends without a target holds and
   sends a notice, and the Return button is refused with the same words.

### Settings (§1)

8. **Validation at save already existed** (the API validates against the
   model and answers 422); the bench's "accepted" list of strings was the
   page: the JSON box silently ignored text that was not valid JSON (a
   single backslash in `D:\clips`), and errors nested inside a section were
   keyed by a path no field had, so nothing was shown. Fixed on the generic
   page (errors shown under their section with the rest of the path; "Not
   valid JSON: this value is not changed" under the box), and
   `local_folders` items that are not objects get a message naming the
   index and the shape wanted.
9. **A dedicated page, `/playout/settings`**, with forms for folders, OBS,
   tools and on-air actions, and the whole document as JSON under
   *Advanced* ("Apply JSON" refuses invalid text out loud). The readiness
   links point there. The generic Settings page still edits playout too.
10. **`receiver_root` flag on a folder** (the form's radio, at most one).
    Without any flag the first folder stays the receiver root, as in WP5, so
    old settings mean the same.
11. **"browse" is a server route**, `GET /api/playout/browse?path=`: folder
    names only (never files), drives on Windows, an unreadable folder says
    why. The page may run on a phone, so a browser dialog would show the
    wrong disk.
12. **Tools banner**: the module publishes `tools` (where ffprobe/ffmpeg
    were found, via `shutil.which`, and the banner text) at start and when
    the tools settings change. Durations without a value show `?`.

12a. **OBS without an address or a password is `not_configured`**, with
    one fixed message ("OBS not configured: set its address and password
    in Playout → Settings"), and the `obs` worker never tries to connect.
    "No answer" is kept for a configured OBS that does not respond. The
    readiness `obs` check reads "not configured" from the settings, not
    from the worker's status (which could still be the initial
    `not_configured` with no message, so the line depended on timing), and
    leaves the worker's changing message out: the same situation always
    gives the same line (`no answer from OBS at host:port`, `OBS at
    host:port refused the password`). OBS's WebSocket server must therefore
    have authentication on; the fakes and tests use a password.

### Library (§3)

13. **Three kinds, computed per row** (`playout/clip_view.py`): `pipeline`
    (in the manifest; grouped by prova), `unmatched` (scanned through the
    receiver root without a manifest entry; group "Unmatched"), `manual`
    (any other folder; grouped by the folder's label). The scanner flags
    files from the receiver root (`clips.in_receiver_root`, added to older
    databases on start). `library_summary.per_prova` became `per_group`,
    plus an `unmatched` count.
14. **Playlists are `playout_playlists` + `playout_playlist_items`**, with
    `shuffle` and an optional `on_air_json` override. Queue entries keep
    `playlist_id`, cued and on-air clips carry it (also across a restart),
    and `playout_aired.playlist` keeps the name for the log. Queueing a
    playlist skips clips whose file is missing (refused only if none can
    play); shuffling reorders what is queued, not the stored list. A
    playlist is made from a selection, or from a whole group
    (`playlist_create {group}`; the page offers it on folder groups).

### On-air actions (§4)

15. **Sent through the registry, from their own worker.** `main.py` hands
    `PlayoutModule` the registry's `dispatch_command`; actions are queued at
    the take (under playout's lock, never blocking) and sent by an `actions`
    worker with source `playout:on_air`, so they are in the command log. A
    failure is logged and published as `notice`, which every playout page
    shows as a toast once. Recorded as a convention.
16. **Audio's `run_macro` takes `{macro: <id>}`**, not `{name}` as the brief
    sketched: the action passes `args` through untouched, and the editor
    lists macros by label (from audio's settings) and stores the id (from
    audio's state).
17. **A playlist override replaces the settings' lists**; an override with
    empty lists means "nothing for this playlist".
18. **In an auto-play chain both run**: the end actions of the clip that
    finished, then the start actions of the next. With a toggling macro on
    both, the channel flips twice; use separate "mute" and "open" macros if
    that matters.

### Strip and hosts view (§2, §5)

19. **Strip**: background is a gradient up to `--progress`; the right side
    shows `−m:ss` on the clip (colours as before) and `queue −m:ss` for the
    cued clip plus the queue (`+?` for unknown durations). The queue header
    says "Queue empty" instead of `0:00`.
20. **`playout.hosts_view`** is built from what the publisher last sent;
    remaining seconds are whole (rounded up), and the value is sent only
    when it changes, so it updates about once a second while a clip plays.
21. **Full width** with `main:has(.playout-page)`, so `App.tsx` (shared)
    is untouched and other pages keep their column.

## Left open

- **Historical jobs in the manifest**: asked on LIVESTACK-35 whether
  `/manifest` covers older events or only the current one; nothing built.
- **Bundling ffmpeg/ffprobe** is WP7b.
- **Files over the ~300-line guideline**: `modules/playout/controller.py`
  (~390; the on/off-air bookkeeping moved to `airing.py`, the return target
  to `return_target.py`, but take/skip/auto-play/OBS events change
  together), `fakes/fake_obs.py` (~330, unchanged in kind). Revisit if they
  grow.
- **Test timing**: the fake clock moves while a worker waits for an OBS
  answer, so end-to-end chains take a few fake seconds longer than the clip
  lengths; the chain tests allow for it.
- The bench (`docs/bench/wp5b.md`) has not been run.
