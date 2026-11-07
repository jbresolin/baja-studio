# ADR 0008 — WP8: readiness checks and settings export/import

Date: 2026-11-07. Work item: LIVESTACK-12. Brief: Plane page "10 — WP8 brief:
readiness checks and settings export/import". Goals §0.

## Decisions taken beyond the brief

### Readiness checks

1. **Where checks live.** `Module.readiness_checks()` returns `ReadinessCheck`s
   (`core/readiness.py`); each module keeps them in a `readiness.py` beside
   it (`modules/gateway/readiness.py`, `cameras/readiness.py`, …). The
   `readiness` module (`modules/readiness`) only collects them from every
   *running* module and runs them; a stopped module contributes nothing. A
   module whose `readiness_checks()` raises gets one failing line saying so.
2. **Reading, not poking.** Where a worker already watches a device, the check
   reads what it publishes: camera and controller status, agent links, the
   XR18 connection (the mixer worker reads `/xinfo` on connect and keeps the
   link alive inside its packet budget; an extra `/xinfo` per run would spend
   that budget for nothing), the M-VAVE status, playout's OBS connection and
   orchestrator sync. Fresh probes are made only where they are cheap and
   change nothing: Caddy's `GET /config/`, `netstat`/`tasklist`, a DNS lookup,
   a TLS handshake, a `GET /` per proxied app, OBS `GetSceneList` /
   `GetSceneItemId` / `GetSceneTransitionList` (the last added to the adapter
   and the fake), free disk space, a one-byte temporary file in the data and
   clip folders, one SNTP query.
3. **The D200 self-test builds the body, it does not call the route.** A
   loopback `GET /action/state/1` would need the studio's own address and
   would mark the D200 as "seen". The route's body moved into
   `d200_routes.channel_state_body`, and the check builds it and checks the
   shape the Ulanzi profile reads.
4. **One cause, one red line.** With OBS stopped, "OBS is connected" fails and
   the scene/source/transition check only warns "not checked". The bench's
   three pulls (RS485, XR18 cable, OBS) give three reds.
5. **Severities.** Warn, never fail: M-VAVE absent, a controller with no frames
   in the last minute, orchestrator unreachable, a hostname that does not
   resolve (WAN down, no hEX entry), links on hidden channels, a camera
   marked as needing presets that has none, a camera driven through its
   direct fallback, CPU or memory ≥ 90 %, a clock 2–60 s off, no return
   scene with `post_clip` other than `hold`. A clock more than a minute off
   fails. No NTP answer (offline at the venue) passes as "not checked".
6. **Free space on the playout folders' drives is playout's check**, not
   home's (the brief lists it under home/system): the module that owns the
   folders owns the check. Thresholds as the brief: fail < 20 GB, warn < 50 GB.
7. **`needs_presets`** is a new field of a camera (default off), with a
   tick-box on the camera form: the brief's "cameras marked as needing them".
8. **Running.** `run` returns at once; the `runner` worker runs every check on
   its own daemon thread, publishes `progress` as results arrive, and gives
   up on a check after 5 s (`fail: timeout`; the thread is left to finish).
   The PC probes (disk, NTP, CPU/memory) come in a `SystemProbes` bundle so
   tests never depend on the runner's disk or network.
9. **The banner says READY whenever nothing fails**; the page and the home
   banner add "· n warnings". `last_run` lives in memory: a service restart
   forgets it (the home page then invites a new run).
10. **The toast** is shown in the app frame, on every page, only for a
    *scheduled* run that turned a previously `ok` check into `fail`
    (`newly_failed`), and never for a run that finished before the page
    opened. A scheduled run first happens one interval after it is switched on.

### Settings export/import

11. **What is exported**: every section of the settings store, including
    `system`. The link matrix, stored levels, queue, played flags and command
    log are in their own tables and are not (the brief's §1 lists "link
    matrix" among the exported settings; its §2 calls it operational state;
    §2 was followed).
12. **File format**: `format: "baja-studio-settings"`, `format_version: 1`,
    `app_version`, `exported_at`, `hostname`, `secrets_required`, and per
    section `{settings_version, values}`. `settings_version` is a class
    attribute of `ModuleSettings` (default 1), raised when a change makes old
    files mean something different; a file section with a newer one cannot
    be imported, an older one is flagged. Secrets become `"<secret>"`; an
    empty secret stays empty (nothing to re-enter).
13. **The file never travels inside a command.** Commands are logged with
    their arguments and a hand-edited file may hold real secrets, so the page
    uploads it to `POST /api/home/settings-import` (5 MB at most, kept in
    memory for an hour, five at most) and the commands name it by `upload_id`.
    Export returns the document to the page, which saves it as a file.
14. **The dry run's token** is a hash of the file and of every section's
    version. Apply re-plans and compares: if anyone saved a setting since the
    dry run, the operator is sent back to look at the diff again.
15. **Diff**: keys are dotted paths, list items named by `id` where they have
    one (`agents[laptop-a].token`). A key is *added* when the target lacks it
    or has only the default there, so a fresh install shows the whole file as
    added (bench step 3). Secrets show as `"<secret>"`. Sections are compared
    after validation, so normalised values (a lower-cased hostname) show as
    they will be saved.
16. **Merging**: a top-level key absent from the file is left as it is on the
    target; keys the target does not know are ignored with a note (as the
    store does when loading). Unchanged sections are not saved again, so
    their versions do not move and nothing restarts.
17. **UI**: Settings → *Export / import* (`/settings/transfer`), a page of its
    own beside the sections.

## Left open

- `last_run` is not persisted across restarts.
- The NTP server is fixed (`pool.ntp.org`); a setting if the venue needs another.
- A scheduled copy of the database (operational-state backup) is a later item.
- Imaging checks arrive with WP6.
