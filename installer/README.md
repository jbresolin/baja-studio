# installer/

Builds `BajaStudio-Setup-<version>.exe`, the one-click installer for the
streaming PC. What it installs and how to use it: [docs/install.md](../docs/install.md).
Why it is built this way: [ADR 0007](../docs/adr/0007-installer-and-gateway.md).

| File | What it is |
| --- | --- |
| `build.ps1` | Assembles `dist\app`: embeddable Python with the service and the tray installed from a wheelhouse, the built frontend, Caddy, NSSM, ffmpeg, the icon, `VERSION`, `wheelhouse-manifest.json` |
| `BajaStudio.iss` | Inno Setup script: services (gateway started once the studio answers), firewall, tray at logon, Start menu, upgrades, uninstall |
| `smoke-test.ps1` | Installs silently, checks no failed step, Caddy up within 5 s and started once, the services, health, ffmpeg, the tray, a diagnostics bundle; uninstalls |
| `release-notes.md` | The top of every GitHub Release's notes (install, upgrade); GitHub's list of changes follows |
| `python/pin.json` | Embeddable Python (3.12.10, the last 3.12 with Windows binaries) URL and SHA-256 |
| `nssm/pin.json` | NSSM 2.24-101 (the build that works on current Windows) URL and SHA-256 |
| `caddy/pin.json` | Caddy version, the Cloudflare DNS module, the Go and xcaddy versions it is built with, and the SHA-256 of the result |
| `ffmpeg/pin.json` | ffmpeg 9.0.2, gyan.dev "essentials" build (GPL, x64), from its GitHub release, and its SHA-256 |
| `icons/baja-icone.svg` | The Baja mascot artwork the icon is made from |
| `icons/make_icons.py` | Makes `tray/baja_studio_tray/assets/baja-studio.ico` (full artwork at 256/48/32, single-fill silhouette at 24/20/16); run by hand, the result is committed |

The tray app itself is in [tray/](../tray/) (ADR 0010).

The release workflow (`.github/workflows/release.yml`) runs all of this on a
Windows runner for every `v*` tag and attaches the installer to a GitHub Release.

## Building locally (Windows)

Needs PowerShell 7, a 64-bit Python 3.12 on `PATH`, Node 22, Go at the
version in `caddy/pin.json`, and Inno Setup 6.

```powershell
# 1. The version goes into the package (a tag for a release, a build number otherwise).
cd server; python -m baja_studio.release_tools stamp-version --dev 1; cd ..
# 2. dist\app (add -WhatIf to see the steps without running them).
pwsh installer\build.ps1
# 3. The installer, in dist\.
iscc /DAppVersion=0.1.0.dev1 installer\BajaStudio.iss
```

`build.ps1 -SkipCaddy` assembles everything but Caddy (no Go needed). CI does
that on pull requests that touch the dependencies, to catch a dependency with
no Windows wheel early.

## Updating a pin

- **Python, NSSM or ffmpeg**: change the version and URL (and ffmpeg's
  `folder_in_zip`), download the file, put its SHA-256
  (`Get-FileHash -Algorithm SHA256`) in the pin. For ffmpeg, use a numbered
  release of `GyanD/codexffmpeg`, never a `git` build: those are replaced.
- **The tray's dependencies** (pystray, Pillow): pinned in `tray/pyproject.toml`.
  pystray is pinned exactly because the tray overrides one of its private
  methods (ADR 0010, decision 8); check `app.py` when changing it.
- **The icon**: after changing `icons/baja-icone.svg`, run
  `pip install resvg-py==0.5.0 pillow==13.0.0` and
  `python installer/icons/make_icons.py`, look at the frames, commit the `.ico`.
- **Caddy or the Cloudflare module**: change the versions and set `sha256` to
  `""`. The next build stops after building Caddy and prints the hash. Check
  that build, then put the hash in the pin. Builds are reproducible: the same
  Go, xcaddy, Caddy and module versions give the same bytes.
