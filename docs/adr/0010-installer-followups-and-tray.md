# ADR 0010 — WP7b: installer follow-ups and tray app

Date: 2026-11-09. Work item: LIVESTACK-55 (under LIVESTACK-11). Brief: Plane
page "12 — WP7b brief". Follows the WP7 bench findings of 2026-11-08 (comment
on LIVESTACK-11) and [ADR 0007](0007-installer-and-gateway.md).

## Decisions taken beyond the brief

### Startup race (brief §1)

1. **The gateway module writes the Caddyfile in `start()`, if none exists.**
   Modules start in the FastAPI lifespan, before uvicorn listens, so
   `/api/health` cannot answer before the file is there. Until now the
   module's first write happened on its worker, after the service already
   answered. An existing file is left alone: it is the config Caddy last
   accepted (ADR 0007 decision 13), and the worker's apply replaces it.
2. **The installer starts `BajaStudio`, polls `/api/health` (WinHTTP, 1 s
   timeouts, up to 60 s), then starts `BajaStudioGateway`.** "Healthy" means
   *answers with HTTP 200*: overall `status` is `degraded` on a fresh
   install (playout and audio are not configured), so it cannot be the gate.
   If the studio never answers, that is a failed step and Caddy is started
   anyway, because NSSM retries it.
3. **A gateway start that fails is logged, never a failed step**: NSSM's
   `AppExit Restart` retries Caddy, and the home page names whatever holds
   port 80 (ADR 0007 decision 21). Failed steps are also written to the setup
   log as `Failed steps:`, because a silent install shows no message box. The
   smoke test reads that line.
4. **The smoke test's race assertion**: within 5 s of the installer
   exiting, port 80 answers through Caddy, and `caddy-stderr.log` holds
   exactly one `serving initial configuration`. One start means Caddy never
   exited and was restarted. This catches the bug even though the IIS stop
   (still needed on runners) happens before the install.

The bench's account ("Caddy launched before the studio had written the
Caddyfile") does not quite match the WP7 code: the installer already wrote
a default Caddyfile before either service started (ADR 0007 decision 9).
The fix above removes the dependency on that step either way. The cause on
João's PC was not reproduced; the race assertion is there to catch it if it
comes back.

### ffmpeg (brief §2)

5. **gyan.dev's "essentials" build 9.0.2 (GPL, x64), from its GitHub
   mirror** (`GyanD/codexffmpeg` release `9.0.2`). Versioned release assets
   do not move, unlike BtbN's rolling autobuilds, so a SHA-256 pin holds.
   The hash in `installer/ffmpeg/pin.json` was taken from the asset on
   2026-11-09. `build.ps1` also checks that `ffprobe -version` names the
   pinned version. Only `ffmpeg.exe` and `ffprobe.exe` ship (not `ffplay`),
   with `LICENSE.txt` and the build's `README.txt` (configuration and source
   links) next to them.
6. **The tools are resolved at use time, not written into the settings**
   (`playout/tools.py`). While Playout → Settings → Tools keep the bare names
   `ffprobe`/`ffmpeg` (the default), the bundled copies are used when
   `<program>\ffmpeg\` has them, and PATH otherwise; a typed path always
   wins. Writing absolute paths as defaults would freeze them into every
   saved settings row. An install upgraded from v0.1.0, whose saved settings
   say `ffprobe`, gets the bundled copies with nothing to do. The readiness
   line names each tool's source: `ffprobe: bundled (C:\…\ffprobe.exe);
   ffmpeg: bundled (…)`.
7. **Caddy's licence was not shipped by WP7** although the brief said "as
   with Caddy". `build.ps1` now copies Caddy's `LICENSE` (Apache 2.0) from
   Go's module cache to `caddy\LICENSE.txt`.

### Tray app (brief §3)

8. **pystray 0.19.5 + Pillow 13.0.0**, not pywin32. pystray brings the
   message loop, menu and notifications in pure Python over ctypes, and has
   no native wheel to pin per Python version. One override is needed:
   pystray loads its icon with `LR_DEFAULTSIZE` (32 px) from a single
   image, so the silhouette frames would never be used.
   `app.MultiSizeIcon._assert_icon_handle` loads the badge's multi-size ICO
   at `SM_CXSMICON` instead, and Windows picks the frame made for that size.
   That method is private to pystray, which is why the version is pinned
   exactly.
9. **The tray is its own package (`tray/`, `baja_studio_tray`)** with its
   own pyproject, tests and CI job. It never imports `baja_studio`, so it
   can only be a client. It is installed into the same embeddable Python
   and runs with `pythonw.exe`. Its package version is fixed at 1.0.0; the
   menu shows the service's version, read from `/api/health`.
10. **Badge mapping**: green for `status: ok`; amber for `degraded` (the
    tooltip names the modules that are degraded or offline); red for no
    answer (2 s timeout) or any other status. **Readiness warnings do not
    reach the badge**: the last readiness run is only in the WebSocket
    state, and an HTTP endpoint for it would be a `core/` change, which
    this package must not make. Left open below. The menu's *Readiness*
    opens the page.
11. **Restart services** runs `pythonw -m baja_studio_tray.restart` elevated
    (`ShellExecute "runas"`): gateway stop, studio restart, wait for
    `/api/health` (up to 60 s), gateway start. That is the installer's
    order, for the same reason. A cancelled UAC prompt shows a notification.
12. **Diagnostics bundle** runs the home module's `diagnostics_bundle`
    command (caller `tray`), downloads the zip to the user's Downloads
    folder and opens it. The data folder may not be readable without
    elevation.
13. **Folders**: the installer now also writes `InstallDirectory` under
    `HKLM\Software\BajaStudio`. Without it (a v0.1.0 registry), the tray
    uses the folders next to its own Python and the default data folder.
14. **Start at logon**: an `HKLM\…\Run` value `BajaStudioTray` for every
    user, behind the task *Show the Baja Studio status icon…* (ticked by
    default). Unticking it on an upgrade deletes the value. A Start-menu
    shortcut *Baja Studio status icon* starts it by hand, and an interactive
    install offers to start it at once. A named mutex
    (`Local\BajaStudioTray`) keeps one per logon session.
15. **Upgrades and uninstall stop running trays first**: a tray holds
    `pythonw.exe` and Pillow's DLLs in `{app}\python`, which the upgrade
    deletes. Only `pythonw` processes whose path is this install's are
    stopped (PowerShell `Stop-Process`), never other Python programs.
16. **Language**: pt-BR when Windows' display language is Portuguese
    (`GetUserDefaultUILanguage`), English otherwise. A test makes sure no
    pt-BR label is left untranslated.

### Icon (brief §3)

17. **The artwork is not a blue bird.** `baja-icone.svg` is the Baja car
    (green roll cage, dark wheels, yellow flag) inside the blue outline of
    Brazil. The brief's "head-and-neck silhouette" therefore became **the
    car's cage and wheels, thickened, in the artwork's blue (#386db4)**:
    the shape that stays readable at 16 px, and nothing like the worker's
    round "B". Blue rather than the cage's green, so the green status dot
    stands apart, and rather than dark grey, which vanishes on a dark taskbar.
18. **Frames**: full artwork at 256/48/32, silhouette at 24/20/16. **24 px
    is added** to the brief's sizes: it is the tray at 150 % scaling, where
    Windows would otherwise shrink the thin 32 px artwork. The status dot
    (half the icon's width, white ring) is drawn bottom-right on every frame
    at runtime. The `.ico` without a dot is the installer's, the uninstall
    entry's and the shortcuts' icon.
19. **The `.ico` is generated once and committed**
    (`tray/baja_studio_tray/assets/baja-studio.ico`, by
    `installer/icons/make_icons.py` with resvg-py and Pillow). The build
    needs no SVG renderer. The silhouette is picked by Inkscape path id, so
    a re-exported SVG may need `SILHOUETTE_PATH_IDS` updated.

### Small items (brief §4)

20. **Default hostname empty**, with `live.bajasaebrasil.net` as the field's
    placeholder (`Field(examples=…)`). `SchemaForm` now shows a schema's
    first example as the placeholder of any text field: a small frontend
    change, needed to have a placeholder at all. The DNS readiness check
    warns `not set: …` with the fix. Saved settings keep their full body, so
    an upgraded install keeps the hostname it had. TLS on still requires a
    hostname. The bench note said `studio.`, ADR 0007 said `live.`; the
    choice is João's.
21. The release notes start with `installer/release-notes.md` (install,
    "upgrade from v0.1.0: nothing to do"); GitHub's generated list follows.

## Scope

Outside `installer/`, `tray/`, the workflows and `docs/`, this package
changed: `modules/gateway` (decision 1, the hostname default and its check),
`playout/tools.py` (new) with one line each in `modules/playout/__init__.py`
and `readiness.py` (decision 6), and `SchemaForm`'s placeholder
(decision 20). `ci.yml` only gained a job.

## Left open

- **Bench** (João): fresh install → no failed-step dialog; tray after logon,
  green; kill the service → red within 5 s; *Restart services* brings it
  back; durations and thumbnails on a PC without ffmpeg on PATH; icon
  legible at tray size. Then tag `v0.2.0`. Checklist: `docs/bench/wp7b.md`.
- **The tray in CI**: the smoke test starts the tray on the runner's
  session with `--exit-after-first-poll`. If a hosted runner turns out to
  have no shell to hold a notification icon, that step will say so and
  needs a decision (skip on CI, keep for the bench).
- Readiness warnings in the badge need a small HTTP endpoint for the last
  readiness run (a `core/` or readiness-module change).
- The badge is amber on any PC where playout or audio is not configured,
  because that is what `/api/health` reports.
- Code signing (SmartScreen) and auto-update, as in ADR 0007.
