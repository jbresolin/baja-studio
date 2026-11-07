# Installing Baja Studio on the streaming PC

This is for the streaming PC (Windows 10 or 11, 64-bit). You need an
administrator account. Nothing has to be installed first: the installer
brings its own Python, Caddy and NSSM.

## What the installer does

- Copies the program to `C:\Program Files\BajaStudio\` (Python, the web
  pages, Caddy, NSSM).
- Keeps the data in `C:\ProgramData\BajaStudio\` (you can pick another folder
  on the first install): the settings and playout queue (`baja-studio.sqlite3`),
  `logs\`, `diagnostics\`, and `caddy\` (Caddy's config and certificates).
- Registers two **Windows services** that start with Windows and restart by
  themselves if they stop:
  - **Baja Studio** (`BajaStudio`): the studio, on port 8888;
  - **Baja Studio gateway** (`BajaStudioGateway`): Caddy, on ports 80 and 443.
    It starts after the studio.
- Opens the firewall for TCP 80, 443 and 8888 on **private and domain**
  networks only (one rule, "Baja Studio (LAN)"). Untick the option if you do
  not want that.
- Adds a Start-menu folder **Baja Studio**: *Baja Studio* (opens the page),
  *Baja Studio logs* (opens the logs folder), *Uninstall Baja Studio*.

No file is ever edited by hand: everything is set in the web page, including
the gateway (Settings → Gateway).

## Install

1. Download `BajaStudio-Setup-<version>.exe` from the
   [GitHub Releases](https://github.com/jbresolin/baja-studio/releases) page.
2. Run it. Windows shows **"Windows protected your PC"** (SmartScreen),
   because the installer is not signed yet: click **More info**, then
   **Run anyway**. Accept the administrator prompt.
3. Click through: welcome → data folder → firewall option → install.
4. At the end, leave *Open Baja Studio in the browser* ticked: the home page
   opens at `http://localhost:8888`, with every module and the version at the
   bottom.

From a phone on the same network, open `http://<PC's LAN address>:8888`.

## Upgrade

Run the newer installer. It stops both services, replaces the program folder,
and starts them again. The data folder is not touched: settings, the queue,
logs and certificates survive. The version at the bottom of the home page
changes.

## Uninstall

Start menu → Baja Studio → *Uninstall Baja Studio* (or Windows Settings →
Apps). It stops and removes both services and the firewall rule, and deletes
the program folder. It asks whether to **keep the data** (the default): say
*Yes* to keep it for a later install, which then finds everything where it was.

## Silent install (scripts, re-installs)

```
BajaStudio-Setup-<version>.exe /VERYSILENT /SUPPRESSMSGBOXES [/DATADIR=D:\Studio]
"C:\Program Files\BajaStudio\unins000.exe" /VERYSILENT /SUPPRESSMSGBOXES [/PURGEDATA=1]
```

A silent uninstall keeps the data unless given `/PURGEDATA=1`.

## HTTPS on live.bajasaebrasil.net

Phones only treat the page as a secure context (needed for some browser
features) over HTTPS without warnings. The gateway gets a real Let's Encrypt
certificate through Cloudflare DNS (DNS-01), which works even though the name
points at a private address.

1. **DNS record.** In Cloudflare, zone `bajasaebrasil.net`, add an **A** record
   `live` → the PC's LAN address (e.g. `192.168.88.20`), **DNS only** (grey
   cloud, not proxied).
2. **API token.** Cloudflare → My Profile → API Tokens → *Create Token* →
   *Edit zone DNS* template, zone `bajasaebrasil.net`. Copy the token.
3. **Gateway settings.** Open Settings → Gateway: hostname
   `live.bajasaebrasil.net`, TLS mode `dns01`, paste the token, Save. The
   gateway restarts Caddy with the token; within a minute the home page shows
   *certificate valid until …*. The Gateway page (home → Gateway card) has
   *Test DNS* and *Test TLS* buttons.
4. Open `https://live.bajasaebrasil.net` on a phone: no warning.

The token is kept in the studio's database and in the gateway service's
environment; it never appears in a file, the logs, the page or a diagnostics
bundle.

Other apps (the BajaOvrLay control, pipeline review) can be served under a
path of the same name: add them under *Proxied apps* in the gateway settings,
e.g. `{"path_prefix": "/overlay", "upstream": "127.0.0.1:5000", "label": "Overlay"}`.
They appear on the home page with their health.

## Static DNS on the hEX S (LIVESTACK-21)

So the name resolves during an event with the WAN down, the router answers
for it itself. In a terminal on the hEX (Winbox → New Terminal):

```
/ip dns static add name=live.bajasaebrasil.net address=<PC's LAN address> ttl=1h comment="studio box"
```

The router must be the DNS server handed out by DHCP (`/ip dhcp-server network`)
and allow remote requests (`/ip dns set allow-remote-requests=yes`). To check:
unplug the WAN, then on a phone open `https://live.bajasaebrasil.net`. It still
loads, with no warning (the certificate was issued while online, and lasts 90
days; Caddy renews it whenever the PC is online).

## When something is wrong

- **Home page**: every module's health; the gateway card says if Caddy is down
  or the certificate is missing, and why.
- **Logs page**: the service log, live.
- **Diagnostics bundle** (button at the bottom of the home page): a zip with
  the logs, settings (secrets masked), health, state, recent commands,
  versions and the Caddyfile. Attach it to the Plane issue.
- **Logs folder** (Start menu → Baja Studio logs): `baja-studio.log` (the
  service's own log), `service-*.log` and `caddy-*.log` (what each service
  printed, rotated at 5 MB).
- The services can be restarted from Windows' *Services* app like any other.
