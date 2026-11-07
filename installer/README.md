# installer/

Builds `BajaStudio-Setup-<version>.exe`, the one-click installer for the
streaming PC. What it installs and how to use it: [docs/install.md](../docs/install.md).
Why it is built this way: [ADR 0007](../docs/adr/0007-installer-and-gateway.md).

| File | What it is |
| --- | --- |
| `build.ps1` | Assembles `dist\app`: embeddable Python with the service installed from a wheelhouse, the built frontend, Caddy, NSSM, `VERSION`, `wheelhouse-manifest.json` |
| `BajaStudio.iss` | Inno Setup script: services, firewall, Start menu, upgrades, uninstall |
| `smoke-test.ps1` | Installs silently, checks the services, health, port 80 and a diagnostics bundle, uninstalls |
| `python/pin.json` | Embeddable Python (3.12.10, the last 3.12 with Windows binaries) URL and SHA-256 |
| `nssm/pin.json` | NSSM 2.24-101 (the build that works on current Windows) URL and SHA-256 |
| `caddy/pin.json` | Caddy version, the Cloudflare DNS module, the Go and xcaddy versions it is built with, and the SHA-256 of the result |

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

- **Python or NSSM**: change the version and URL, download the file, put its
  SHA-256 (`Get-FileHash -Algorithm SHA256`) in the pin.
- **Caddy or the Cloudflare module**: change the versions and set `sha256` to
  `""`. The next build stops after building Caddy and prints the hash. Check
  that build, then put the hash in the pin. Builds are reproducible: the same
  Go, xcaddy, Caddy and module versions give the same bytes.
