# WP8 bench session: readiness checks and settings export/import

The checklist is §7 of the WP8 brief (Plane page "10 — WP8 brief"). Run it on
the streaming PC after the WP7 installer bench, and record the results in a
comment on **LIVESTACK-12**. How-to details: [docs/install.md](../install.md),
*Before an event* and *Moving the settings to another PC*.

| # | Step | Expected | Result |
| --- | --- | --- | --- |
| 1 | Fully configured box: Readiness → *Run all checks* | READY, or a list that matches reality (every red line is a real problem) | |
| 2 | Unplug the RS485 adapter, pull the XR18 network cable, stop OBS; *Run all checks* | Three reds (joystick controller, XR18, OBS), each with the right fix; OBS's scene check amber "not checked" | |
| 2a | Restore all three; *Run all checks* | Greens again | |
| 3 | Settings → Export / import → *Export settings*; copy the data folder aside, empty it; reinstall | A `BajaStudio-settings-<PC>-<date>.json` file; the studio starts with defaults | |
| 3a | Drop the file on Export / import | Dry run lists every module, its keys under *Added*; the Cloudflare token and the OBS password under *Enter again* | |
| 3b | *Apply*; re-enter the Cloudflare token (Settings → Gateway) and the OBS password (Settings → Playout); *Run all checks* | READY | |
| 4 | Settings → Readiness: *scheduled run* on, interval 1 min; pull a cable (XR18) | Within a minute, a red message on the open page naming the XR18 | |

## Also worth noting

- How long a full run takes on the box (shown under the banner).
- Any red line whose wording or fix did not make sense at the rack.
- Copy as text, pasted into the crew chat from a phone: readable?
