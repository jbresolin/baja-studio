# ADR 0007 — WP7: installer, gateway (Caddy) and release pipeline

Date: 2026-11-06. Work items: LIVESTACK-11 (installer + release), LIVESTACK-22
(Caddy), LIVESTACK-23 (landing/health), LIVESTACK-21 (hEX static DNS, a
runbook only). Brief: Plane page "09 — WP7 brief". Goals §7.4 and §7.6.

## Decisions taken beyond the brief

### Packaging

1. **Caddy is built with xcaddy on the runner, not downloaded from the
   Caddy download endpoint** (a deviation from brief §2). The endpoint ignores
   the version asked for: on 2026-11-06, asking for `v2.11.9` and for `v2.11.8`
   with `caddy-dns/cloudflare@v0.2.4` returned byte-identical builds of
   v2.11.8 (`94a370ca…`). It serves whatever it last built, so a pinned hash
   would break at the next Caddy release, and "pin the version" could not be
   done. xcaddy with pinned **Go 1.26.10, xcaddy v0.4.7, Caddy v2.11.8 and
   cloudflare v0.2.4** (`GOTOOLCHAIN=local`, `CGO_ENABLED=0`) is reproducible;
   Go's module checksums cover the sources, and the result's SHA-256 is pinned
   in `installer/caddy/pin.json` like the brief asks. `build.ps1` also checks
   `caddy list-modules` has `dns.providers.cloudflare` and that **Caddy adapts
   every golden Caddyfile** from the gateway's tests, so the renderer is
   checked against the real parser on every build.
2. **Python 3.12.10** is the embeddable version: python.org publishes no
   Windows binaries for later 3.12 releases (security fixes are source-only).
   Moving to 3.13 waits on duvc-ctl (goals §7.6).
3. **NSSM 2.24-101** (pre-release build), not 2.24: 2.24 has known service
   start problems on Windows 10 1703 and later. Its download host (nssm.cc)
   is flaky, so `build.ps1` retries and caches.
4. **Version**: `baja_studio/__init__.py` holds it, pyproject reads it from
   there (`dynamic = ["version"]`), and `release_tools stamp-version` writes
   the tag in (`v1.2.3-rc.1` → `1.2.3rc1`). Builds that are not releases get
   `<x.y.z>.dev<run number>`. `/api/health` and the home page show it.
5. `release_tools.py` (version stamping, the requirements list, the wheelhouse
   manifest) lives **inside the package**, so it gets the same ruff, mypy and
   tests as the rest; it uses only the standard library and runs before any
   dependency is installed.

### Installer

6. **Paths reach the service through the environment, not the command
   line**: `BAJA_STUDIO_DATA`, `BAJA_STUDIO_INSTALL_DIR`, `BAJA_STUDIO_FRONTEND`
   in `AppEnvironmentExtra`; Caddy runs in `<data>\caddy` with
   `--config Caddyfile`. No quoting through NSSM to get wrong when a folder has
   spaces.
7. **The installer never sets the gateway service's environment**; that
   belongs to the gateway module (the token). Upgrades therefore keep the token.
8. **An upgrade skips the data-folder page** and keeps the folder it had
   (stored in `HKLM\Software\BajaStudio`): moving it would leave the data
   behind. `/DATADIR=` sets it for a silent first install. A drive root is
   refused.
9. **The installer writes the default Caddyfile** (`python -m
   baja_studio.modules.gateway.caddyfile --if-missing`) before starting the
   services, so Caddy's first start has a config. It is the renderer's output
   for default settings (a test pins that); an upgrade keeps the existing file.
10. **Uninstall data**: a Yes/No question (default keep) rather than a
    checkbox; silent uninstall keeps the data unless `/PURGEDATA=1`.
11. **NSSM never deletes rotated logs**; the service deletes all but the five
    newest of each at startup (`core/logging.prune_rotated_logs`).
12. Services stop with Ctrl+C and 15 s to finish (workers, child processes,
    SQLite) before NSSM escalates.

### Gateway module

13. **How the token reaches Caddy.** Caddy reads it from its environment
    (`{env.CLOUDFLARE_API_TOKEN}` in the Caddyfile). `apply` compares the
    gateway service's `AppEnvironmentExtra` with what the settings need; if it
    differs it writes the Caddyfile, sets the variable and **restarts** the
    gateway service. Otherwise it **`POST /load`s first and writes the file
    only once Caddy accepted it**, so the file Caddy starts from after a
    reboot is never one it refused. With Caddy down, the file is written for
    its next start.
14. **Settings edits apply by themselves** (conventions: no restart); `apply`
    forces it. Applying an identical config is a no-op in Caddy.
15. **Every setting is validated against injection**: hostname, `host:port`
    upstreams and admin address, path prefixes (letters, digits, `._-`), and
    the studio's own first segments (`/api`, `/ws`, `/action`, `/assets`, the
    page routes) are refused as prefixes. The admin API must be on loopback.
16. **Defaults: TLS `off`** (plain HTTP on :80), hostname
    `live.bajasaebrasil.net`. Choosing `dns01` is an explicit step in
    docs/install.md; a token alone does not switch TLS on.
17. **WebSocket apps (and the studio) get `stream_close_delay 5m`**, so
    applying a config does not drop every open page's `/ws`. That is all
    `websocket: true` changes; Caddy proxies WebSockets anyway.
18. **TLS status comes from a local handshake** (127.0.0.1:443 with the
    hostname as SNI, verified against the system's roots), every poll while
    there is no valid certificate and once a minute after. Caddy's admin API
    has no certificate endpoint for ACME certificates.
19. The gateway's `persist_config off` and `storage file_system <data>\caddy\data`
    keep Caddy's state in the data folder (not LocalSystem's profile), so
    certificates survive upgrades and uninstall-with-keep.
20. Health: `offline` when the admin API does not answer, `degraded` when
    Caddy refused the config, an app or the studio does not answer, or there
    is no valid certificate (with the reason).

### Diagnostics

21. Besides masking settings, **every secret value is scrubbed from every
    file in the bundle** (a library logging a token by mistake). Values under
    four characters are left alone.
22. Bundles go to `<data>\diagnostics` (five kept) and are served only by
    exact name (`/api/home/diagnostics/BajaStudio-diag-*.zip`). The button
    shows a download link once the bundle is built.

### Release pipeline

23. `release.yml` also runs on pull requests that touch `installer/` or
    itself, building and smoke-testing without publishing: the installer is
    only tested by that smoke test, so a change to it should run it before
    merge. `ci.yml`'s `dist` job (no Caddy, no installer) covers
    `server/pyproject.toml` changes as the brief asks.
24. The smoke test cannot require `/api/health` `status: ok`: on a runner,
    playout and audio are `offline: not configured`. It requires this version,
    `home` and `gateway` ok, both services running, the studio reachable
    through Caddy on port 80, a bundle with the Caddy version and no database,
    and a clean uninstall that keeps the data.

### Frontend

25. `SchemaForm` draws a nested model (the gateway's `tls`) as a group of
    real fields (dropdown, password), for every module. Lists of models
    (proxied apps) are still a JSON box.
26. Proxied apps link through the gateway: `https://<hostname><prefix>/` with
    TLS on, else `http://<this page's host><prefix>/` (port 80).

## Left open

- **The bench (docs/bench/wp7.md) has not been run**; LIVESTACK-21/-22/-23
  close with it.
- **The Caddy SHA-256** in the pin comes from the first CI build; if two runs
  ever give different hashes, reproducibility is broken and the check should
  fall back to the module list only.
- Code signing (SmartScreen click-through until then), auto-update.
- A dedicated gateway page with a table editor for proxied apps; for now the
  generic module page (status, Apply, Test DNS, Test TLS) and Settings.
- `core/settings.py` is ~320 lines (the secret helpers could move to their own
  file).
