# ADR 0014 — WP10: studio housekeeping from the benches

Date: 2026-11-10. Work item: LIVESTACK-58. Brief: Plane page "16 — WP10
brief". Bench findings: comments on LIVESTACK-55 (tray Quit, readiness
badge). Follows ADRs 0003 (supervisor), 0008 (readiness), 0010 (tray) and
0012 (one place per setting). Ran in parallel with WP9b and WP11; the only
one of the three touching `core/`.

## Decisions taken beyond the brief

### Tray Quit (§1)

1. **Cause, as far as it can be found without a Windows desktop.** pystray
   0.19.5's `Icon.stop()`, called from the menu (which runs on the icon
   loop), joins pystray's *setup* thread for up to 5 s, and the tray ran
   its whole 5-second poll loop inside that setup function, calling
   `update_menu`/`Shell_NotifyIcon` from it while the loop thread waited.
   Separately, `already_running()` read `GetLastError()` through
   `ctypes.windll`, which ctypes does not guarantee: Python may reset the
   last error between the two calls, so the single-instance check could
   pass for a second tray (the installer's "show it now" plus the Run entry
   at logon) and Quit on one left the other's identical icon. Both are
   fixed; the bench (step 1) confirms which one it was.
2. **Our own poll thread.** pystray's setup function only starts a daemon
   thread (`tray-poll`) and returns, so `stop()` has nothing to wait for.
   Quit sets the stop event, calls `icon.stop()` and joins the poll thread
   for at most 1 s (one 2 s request may be in flight; being a daemon, it
   cannot hold the process). A status that arrives after Quit is not drawn.
   `run_tray` calls Quit again however the loop ended.
3. **An `IconBackend` seam.** `Tray` (poll thread, Quit) talks to the icon
   through `run / show / notify / stop`; `PystrayBackend` is the only code
   importing pystray. Tests use `tests/fake_icon.FakeIcon`, which behaves
   like pystray where it matters (loop on the calling thread, separate setup
   thread, clicks run on the loop), so `main()` returning after Quit is
   tested on both CI runners without a desktop. `main()` takes the client,
   the backend and an `on_started` hook for this; the installer calls it
   with none.
4. **The single-instance mutex** uses `WinDLL(..., use_last_error=True)` and
   `ctypes.get_last_error()`, and is remembered per process, so a second
   `main()` in the same process (the tests on Windows) is not "another tray".
5. **Labels:** *Sair* / *Quit*.

### Readiness in the badge (§2)

6. **`GET /api/readiness/last` is the readiness module's route**, not
   `core/api.py`: it reads the module's own last run (kept in memory beside
   `state.readiness.last_run`, the same object). Answer: `{"last_run": null}`
   before any run, else the run exactly as the page sees it (`started_at`,
   `duration_s`, `fails`, `warnings`, `modules[].checks[]`, ...). No auth,
   like `/api/health`; it never starts a run. A disabled readiness module
   keeps answering its last run.
7. **Badge:** amber when health is `degraded` *or* the last run has any
   warning or failure; a failed readiness check does not make the badge red
   (red stays "the service does not answer"). Tooltip: `verificação das
   14:32: 2 avisos, 1 falha` (local time of `started_at`), after the module
   names when both apply. A run with nothing to report adds nothing.
8. **Older services** answer 404, read as "no run": the badge follows health
   alone, so a new tray works with a v0.2.0 service.
9. The tray asks for readiness only when health answered (one request per
   poll when the service is down).

### Supervisor: warn once (§3)

10. **`FailureStreak`** (in `core/worker.py`, beside `RestartPolicy`) counts
    a worker's failures since it last ran well. Same error = same class name
    and message.
11. **Levels.** The first failure of a streak keeps its old level: WARNING
    "device offline: …" for `DeviceOfflineError`, ERROR "worker failed" with
    the traceback for anything else (a bug should stay loud once). Repeats
    are DEBUG "… again (N in a row): …". "restarting worker in N s" is INFO
    only after a logged failure, DEBUG otherwise. A different message logs
    again in full.
12. **Recovery** ends the streak: after ten stable seconds ("recovered after
    N failures") or `mark_online()` ("device back online: recovered after N
    failures"). The old "worker recovered" line without failures (a
    `starts_offline` worker that never called `mark_online`) is gone.
13. **The Logs page follows automatically**: it streams what the log hub
    receives, and the service logs at INFO, so the DEBUG repeats never
    reach it. Health is unchanged: every failure still sets it.
14. `supervisor.py` grew to about 360 lines; the logging decision lives in
    one method (`_log_failure`) and the counting in `worker.py`.

### Cameras settings (§4)

15. **Checked first** (ADR 0012 left it open): *Camera setup* edits every
    field of the section (cameras with fallback, limits, curve, presets
    flag; controllers with Call bindings; motion). Only the module's
    `enabled` flag is left to the JSON, as with playout.
16. `cameras` joins `MODULE_SETTINGS_PAGES`: `/settings/cameras` shows the
    JSON editor and a link. The ⚙ on the cameras page sits in the selected
    camera's heading. *Camera setup* was already in the top bar.
17. **The generic module page** (`/modules/<name>`, where the home cards
    lead) links a module listed in `MODULE_SETTINGS_PAGES` to its page
    (*⚙ Camera setup*) instead of the generic Settings page.
18. The `managed_elsewhere` markers on `cameras`/`controllers` are left; the
    generic form no longer draws the section at all.

### Installer (§5)

19. No change: upgrades and uninstall still stop trays by their
    `pythonw.exe` path, which does not depend on how Quit works.

## Left open

- Which of the two causes in decision 1 the bench saw: bench step 1, and a
  check that only one `pythonw` tray runs after install + logon.
- The tray's `update_menu` still runs on the poll thread (pystray offers no
  way to post it to the loop); harmless so far.
- Code that retries without the supervisor (adapters' reconnect loops)
  should adopt `FailureStreak` when one is seen flooding.
