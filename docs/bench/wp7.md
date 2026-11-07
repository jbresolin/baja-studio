# WP7 bench session: installer and gateway on the streaming PC

The checklist is §9 of the WP7 brief (Plane page "09 — WP7 brief"). Record the
results in a comment on **LIVESTACK-11**; LIVESTACK-21, -22 and -23 close with
it. How-to details for each step are in [docs/install.md](../install.md).

Without Caddy (on a development machine): `python -m baja_studio.fakes.fake_caddy`
plays its admin API on 127.0.0.1:2019, so the gateway module has something
to apply to.

| # | Step | Expected | Result |
| --- | --- | --- | --- |
| 1 | Download the release installer, run it (SmartScreen → More info → Run anyway), accept the defaults | Browser opens on the home page; every module listed; version at the bottom | |
| 2 | Reboot | Both services running again (Services app); home page opens from a phone by `http://<LAN IP>:8888` | |
| 3 | Cloudflare A record `live` → LAN IP (DNS only) and a DNS-edit token; Settings → Gateway: `dns01`, paste token, Save | Gateway card ok; home page: *certificate valid until …*; phone opens `https://live.bajasaebrasil.net` with no warning | |
| 3a | Gateway page: *Test DNS* | "points at this PC" | |
| 4 | hEX: `/ip dns static add name=live.bajasaebrasil.net address=<LAN IP> ttl=1h comment="studio box"`; unplug the WAN | Name still resolves on a phone; page loads, no certificate warning | |
| 5 | Install the next build over it | Settings, queue, logs and the token survive; version changes; HTTPS still works | |
| 6 | Diagnostics bundle (home page button) | Zip downloads; search it for the token: not found | |
| 7 | Uninstall, keeping the data; reinstall | Everything is back, including HTTPS | |

## Also worth noting

- How long Let's Encrypt took to issue the first certificate (step 3).
- Anything the installer showed that a non-developer would not understand.
