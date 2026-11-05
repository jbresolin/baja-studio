# ADR 0005 — WP5: playout module

Date: 2026-11-03. Work item: LIVESTACK-35 (under LIVESTACK-8). Brief: Plane
page "07 — WP5 brief". Orchestrator API read from baja-video-pipeline
`docs/api.md` §7.1 and `orchestrator/openapi.yaml` (cloned read-only, not
edited). The 2026 `obs-playback-baja` was not read (skipped on João's
instruction); the brief's shortcut list and OBS behaviour were used instead.

## Decisions taken beyond the brief

### Orchestrator

1. **Read-only sync against `GET /manifest`.** The pipeline API has no
   write-back for played state or notes (checked 2026-11-03; reported before
   any code was written, as the brief asks). On João's decision: played
   counts and notes are **local only**, and write-back is left open (below).
2. **Matching is on `relative_path`, not the file name.** Delivered clips
   land at `<prova-slug>/<car>/<passagem>.mp4` under the studio receiver's
   clip root. The **first local folder is that root**, scanned recursively,
   and a manifest entry matches the file `first folder / relative_path`. A
   manifest entry whose file has not arrived yet gets a `missing` row, which
   the scanner fills in when the file lands.
3. **`orchestrator.token` is the studio's worker bearer token** (issued at
   enrollment); `/manifest` accepts it.
4. **No generated client.** The API is one read endpoint, so
   `playout/orchestrator.py` mirrors `ManifestEntry` / `ManifestResponse` by
   hand as pydantic models, with `extra="ignore"` so a pipeline release
   that adds a field does not break the sync.
5. **Incremental sync.** The first pull of each worker run (and after the
   orchestrator settings change) is a full one. Later pulls send
   `since = newest delivered_at seen − 1 min`; merging is idempotent, so the
   overlap only costs a few repeated rows. An unreachable orchestrator, a
   refused token or no delivery folder make the module `degraded`;
   `sync_status` keeps `last_success_at` and the page shows its age.

### OBS adapter

6. **Own obs-websocket v5 client instead of `obsws-python`** (a deviation
   from §2). `obsws-python` opens its socket itself (websocket-client's
   `WebSocket().connect`, no injection point), so tests could only reach the
   fake OBS through a real port, and the conventions say tests open none. Its
   download was also blocked in the build environment. The protocol is
   small (Hello, Identify with the SHA-256 challenge, Request/Response by id,
   Event), so `adapters/obs_websocket.py` implements it over the `websockets`
   sync client the service already pins, behind a `TransportFactory`; the
   fake OBS's in-process transport plugs in there. No new dependency.
7. **Layout.** `adapters/obs_protocol.py` (op codes, auth, message builders,
   shared with the fake), `obs_transport.py` (the socket and the errors),
   `obs_websocket.py` (the client: handshake, a reader thread, requests with
   timeouts, event and disconnect listeners, `ReconnectBackoff`) and
   `obs_requests.py` (typed helpers, mixed into the client). Besides the
   brief's requests: `GetCurrentProgramScene`, `GetInputSettings`,
   `SetMediaInputCursor`, `GetSceneItemId`, `SetSceneSceneTransitionOverride`.
8. **Timeouts and offline rule.** 3 s to connect and identify, 2 s per
   request. A closed socket fails every waiting request at once. Requests
   cannot be made from an event listener (it would wait for itself);
   listeners hand events to the playout `obs` worker. Reconnect backoff is
   0.5, 1, 2, then every 4 s, like the agent links. A refused password is
   `degraded`, OBS absent or not configured is `offline`.

### Switching

9. **Cue = set file, restart, pause, seek to 0** on the off-air source, with
   `looping`, `restart_on_activate`, `close_when_inactive` and
   `clear_on_media_end` forced off. Real OBS starts a media source when its
   file changes; the fake does too, so the test covers it. The bench checks
   it on real OBS.
10. **Take = one `SetCurrentProgramScene`, then play**; the source that was on
    air is stopped once it is off air. Cutting first and then playing avoids
    losing the first frames. Bench step 3 checks the frame of the cut.
11. **A take is always a cut.** On every connect playout sets a transition
    override (`obs.cut_transition`, default "Cut") on both playback scenes,
    so a crossfade between the old and new clip cannot happen. **Fade** is a
    temporary override (`obs.fade_transition`, `fade_ms`) on the return scene,
    cleared 0.5 s after the fade; **return** uses whatever transition goes to
    the return scene. Transition names are localised in OBS (pt-BR "Corte",
    "Desvanecer"), hence the two extra settings.
12. **Our own scene changes are told apart from the operator's.** The
    switcher remembers the program scenes it asked for. A
    `CurrentProgramSceneChanged` it did not ask for, while a clip is on air,
    means the operator cut away in OBS: the airing ends, with no post-clip.
    The tests caught the case of a late echo from one take arriving after
    the next take.
13. **Command semantics the brief left open:**
    - `cue_next` takes the head of the queue *out* of the queue into the
      preview. With a clip already cued it is refused ("take it or drop it").
    - `take {cue_if_needed}`: Space sends it with `cue_if_needed: true`.
    - **`skip` (→) ends the clip on air now, exactly as if it had
      finished**: "this clip is dragging, get me out", the reflex operators
      carry from the 2026 tool. It takes the natural end's path: with
      auto-play next on (and something to play) the next clip goes to air,
      otherwise `post_clip` (return/hold/fade) applies. The source is
      paused first, so `hold` freezes the frame and a fade starts from a
      still picture. Refused when nothing is on air. (Reviewed by João: the
      first version of `skip` dropped the next clip instead.)
    - **`drop_next` (Shift+→ or Backspace) drops the next clip** (the cued
      one, else the head of the queue) and cues the one after; it never
      changes program.
    - `return_to_scene` takes the clip off air now.
    - **`replay_last`** puts the last aired clip at the front of the queue
      and cues it if nothing is cued.
    - Take confirmation: the first `take` arms for 3 s
      (`take_confirmation` in state, shown on every page); a second `take`
      from **any** client inside the window fires.
    - Auto-play next (off by default) cues the head of the queue if needed
      and takes it when the clip on air ends.
14. **Reconnect re-reads the program scene.** If it is no longer the clip's
    scene (OBS restarted, or the operator cut away), the airing ends. A cued
    clip is loaded again, since a restarted OBS has lost it.

### Library, queue, storage

15. **Tables are `playout_clips`, `playout_queue`, `playout_session` and
    `playout_aired`**, prefixed because the SQLite file is shared. Beyond the
    brief's columns: clips have `relative_path`, `folder_label`, `missing`
    and `probe_error` (a file ffprobe cannot read is not probed again until
    the tools settings change); queue entries have a stable `entry_id`, so
    two browsers moving and removing entries act on the entry they see;
    `playout_aired` is the log the export reads.
16. **`ModuleContext` gained `database` and `data_directory`** (core change),
    so a module can keep its own tables and caches. Thumbnails are cached in
    `<data dir>/playout-thumbnails/`, never next to the clips.
17. **Scanner:** plain directory walking every 5 s, no `watchdog`. Dot-files
    are skipped, which also skips the receiver's `.<name>.part` files. Files
    under a folder removed from the settings are marked missing, not deleted
    (their notes and played counts stay).
18. **`show_pass`** is kept as in the brief's table (default on). It decides
    whether the title includes "passagem N" and is passed to the webhook. It
    has no control on the page yet.
19. **State** follows brief §4, plus `library` (every clip keyed by id, so one
    clip changing is one small patch), `queue_total_s`, `folders` (scan
    errors), `auto_play_next`, `take_confirmation` and `export`.
20. **Webhook** bodies are exactly `{clip, started_at}` / `{clip, ended_at}`.
    `clip` carries id, title, source, prova, car_number, team, passagem,
    show_pass and duration_s, but not the file path. POSTs go out from their
    own `hooks` worker, 2 s each; failures are logged at most once a minute.
21. **Export** returns entries with `offset_s`/`offset` from the stream-start
    marker and a YouTube chapters text (`0:00 Stream start` added when the
    first clip aired later). Airings before the marker get no offset.
    `mark_stream_start {at?}` stores the marker in the `export` settings
    section; `at: null` clears it.

### Frontend

22. **No new dependency.** The queue reorders with native HTML5 drag and drop,
    with ▲/▼ buttons for keyboards and touch; dnd-kit was not needed.
23. **The page is `/playout`** (also `/modules/playout`). Notes and labels save
    when the field loses focus; clicking a queue row selects it for Delete.

### Fakes

24. **`fakes/fake_obs.py`** (+ `fake_obs_requests.py`, `simulated_media.py`,
    `fake_obs_in_process.py`) and **`fakes/fake_orchestrator.py`**, both
    runnable. The fake orchestrator serves the manifest only, because the real
    API has no write-back to accept; `--from-folder` announces the files
    already in a folder.

## Left open

- **Write-back of played state and notes to the orchestrator**, and its
  test "write-back queued while offline and flushed later" (brief §4, §7).
  It needs a studio-role endpoint in baja-video-pipeline first (for example
  `PUT /jobs/{job_id}/playout {played, played_at, notes}`, with the fields
  echoed in `ManifestEntry`). The change belongs in that repo; open a work
  item there.
- **Settings are edited on the generic Settings page**, where the nested
  sections are JSON boxes. A dedicated playout settings form would be
  friendlier.
- **A file dialog for manual clips** (goals §2.2) is not built: manual clips
  come from watched folders.
- **Missing manual clips are never removed** from the library. A "forget
  missing" action may be wanted after a folder is retired.
- **Studio mode**: playout drives program directly and does not touch OBS's
  preview scene.
- **Files over the ~300-line guideline:** `playout/library.py` (~320),
  `modules/playout/controller.py` (~325) and `fakes/fake_obs.py` (~330).
  Each holds one responsibility that changes together. Revisit if they grow.
- The bench (§8) has not been run.
