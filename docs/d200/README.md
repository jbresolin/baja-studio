# Ulanzi D200 profile for the studio's audio actions

The deck buttons are the operators' spec for which audio controls matter.

- `Baja.ulanziDeckProfile`: the 2026 profile (from
  `xr18-control/Baja.ulanziDeckProfile`) with every URL changed from
  `http://localhost:3000` to `http://localhost:8888`, the studio service.
  Nothing else was changed: only the `manifest.json` entries were rewritten,
  and every other file in the archive (button images, layout) was copied
  byte for byte. Import it in Ulanzi Studio; if Ulanzi Studio runs on a
  different PC from the service, change `localhost` to the service's
  address. **It has not been imported yet**: bench step 3
  (`docs/bench/wp4.md`) checks it and should export it again from Ulanzi
  Studio to replace this file.
- `images/`: the button images and `Template.pptx` they were drawn from.

## What the buttons call

All requests are `GET`; the matching state URL is polled every second, and
the button image follows a field of its JSON being `true`.

| Page | Buttons | Request | State poll (field) |
|---|---|---|---|
| Hosts & guests | HOST 1-2, GUEST 1-2 mute | `/action/mute/1` … `/4` | `/action/state/N` (`muted`) |
| Hosts & guests | HOST 1-2 talk to production | `/action/macro/host1-talk-prod`, `host2-talk-prod` | `/action/macro-state/<id>` (`active`) |
| Production | PROD 1-2 mute | `/action/mute/5`, `/6` | `/action/state/N` (`muted`) |
| Production | PROD 1-2 talk to hosts, go live, talk to camera | `/action/macro/<id>` (ids below) | `/action/macro-state/<id>` (`active`) |
| IEM pages (one per IEM bus 1-4) | HOST 1-2, GUEST 1-2, VIDEO volume up/down | `/action/nudge-aux/<ch>/bus<N>/±3` (VIDEO is channel 17, the aux return) | — |

Production macro ids (kept from 2026 so these URLs keep working):

| Button | Macro id |
|---|---|
| PROD 1 TALK (to hosts) | `prod1-talk-hosts` |
| PROD 2 TALK (to hosts) | `macro-1774581643177` |
| PROD 1 LIVE | `macro-1774582227201` |
| PROD 2 LIVE | `macro-1774582340101` |
| PROD 1 TALK CAM | `macro-1774582749617` |
| PROD 2 TALK CAM | `macro-1774582877605` |

The PROD 3 images exist but the 2026 profile has no PROD 3 buttons; the
studio seeds `prod3-talk-hosts`, `prod3-go-live` and `prod3-talk-cam` for
them. The audio setup page's **D200** tab lists every URL for the host it
is opened on.
