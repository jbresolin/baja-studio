# WP9b bench session: hosts chat fixes and seat audio card

The checklist is §7 of the WP9b brief (Plane page "15 — WP9b brief").
Record the results in a comment on **LIVESTACK-57**.

You need the operator's browser on **Hosts** (`/hosts/control`) and on the
playout page (`/playout`), and the **hosts screen** (`/hosts`) on the
hosts' monitor or a tablet, set to **seat 1** (tap **Seats** in its corner).

Without the XR18: `python -m baja_studio.fakes.fake_xr18` and point
**Audio setup → Mixer & devices** at this PC. Its console now has
shortcuts for what the audio card reads (type `help`):

    mute 1 on | mute 1 off     channel 1 muted / playing
    lr 1 off                   channel 1 off the main mix (LR assign)
    fader 1 0.75               channel 1's fader (0.75 is 0 dB)
    send 5 bus1 0.75           PROD 1 into host 1's in-ears
    sendon 1 bus5 off          a send's on switch
    tap 1 bus5 pre | post      a send taken before or after the fader
    toggle /ch/01/mix/lr       flip any 0/1 switch

A fresh fake has every input on LR, every send switched on and taken
pre-fader, and every send at -inf.

## Setup

1. **Audio setup → Seats**: map seat 1's microphone to **HOST 1** (channel
   1), seat 2's to **HOST 2**; in-ears stay on buses 1-4; production bus
   **bus 5**; production microphones PROD 1-4 (channels 5-8). **Save**.
2. **Hosts → Seats**: seats 1 and 2 on, named (e.g. Ana, Bruno).
3. **Hosts → Canned messages and replies**: the hosts' one-tap phrases
   ("preciso de 1 min", "problema no áudio", "repete?", "pronto") and *Chime
   on this page when a host writes* (on).

## Steps

| # | Do | Expect / record |
|---|---|---|
| 1 | On the hosts screen tap **pronto**, then type a message and **Send**. On the Hosts page reply to it. | The Hosts page history shows "← Ana pronto" in Ana's colour marked **new**, the title shows "2 new from the hosts" and a chime plays. The playout page's *Message hosts* line shows the count and the latest text; **Read** clears it. Production's reply appears under Ana's message on the hosts screen, which grows for a few seconds. |
| 2 | Send Ana a message from production; on the hosts screen press its **✕**. Reload the hosts screen. | The message disappears at once and stays gone after the reload. The Hosts page history says *dismissed by Ana*. Seat 2's screen (if open) still shows it. |
| 3 | Watch the chat at rest; send a new message to Ana; react 👍. Then **Hosts → Chat area** = large. | At rest each message is one or two lines with small reactions. The new message grows (large text, reply field) and shrinks back about 4 s after the 👍. An **urgent** one stays grown and pulses until answered. *Large* applies at once, without Save. |
| 4 | Fake console: `mute 1 off` (fader at unity). Then run **Host 1 → Prod**. Then `mute 1 on`. Then `mute 1 off` and `send 5 bus1 0.75`. | Ana: **LIVE**; after the macro: see *Known issue* below (expected **MUTED** with the default macro); `mute 1 on`: **MUTED**; PROD 1 into bus 1: **production → you** on Ana's line. To see **TO PRODUCTION**: `lr 1 off` and `send 1 bus5 0.75` (pre-fader tap) with channel 1 unmuted. |
| 5 | Tap Ana's line on the audio card. Press **+** twice on a linked source (e.g. SHOTGUN). Move that source's main fader on the audio page. **Reset to main**. Then press the hosts' pad *vol up* (D200 `nudge-aux/<ch>/bus1/+1`). | The overlay lists the sources of bus 1 with *main* level and offset (+2 dB); unlinked sources say *independent*. Moving the main fader moves bus 1's send with the +2 dB kept. Reset puts every offset back to 0 dB. The pad shows a toast "Ana: SHOTGUN +1 dB" with the overlay closed. The overlay closes by itself after ~20 s without a touch. |
| 6 | Fake console: `drop on` (or unplug the XR18). | Within ~5 s the card says **Mixer offline** (no stale LIVE/MUTED); the overlay's buttons are disabled with "Mixer offline". `drop off`: states come back. |

## Known issue to look at: "talk to production"

The default **Host 1 → Prod** macro closes host 1's **main fader**
(store and silence `1:main`). Mirroring then copies that -inf into every
send linked to the fader, bus 5 included, so host 1 is not heard on the
production bus either: the card classifies the result as **MUTED**, not TO
PRODUCTION (the rules read the mixer, and the mixer says so). This is
reported, not special-cased (ADR 0013). To decide at the bench: how should
"talk to production" reach bus 5? For example LR assign off instead of the
fader, a pre-fader tap on bus 5 with 1→bus5 unlinked, or not mirroring a
fader closed by a macro.

## Also worth noting

- **To confirm on the real XR18** (one line each in
  `adapters/xr18_addresses.py`): LR assign at `/ch/NN/mix/lr`; a send's tap
  at `/ch/NN/mix/MM/tap` (post-fader values 4 and 5); whether the XR18 has a
  send on switch at `/ch/NN/mix/MM/on` at all (if it never answers, the card
  counts the send as on).
- Holding **+** on the overlay repeats about four times a second; the
  mixer's packet rate on the audio page should stay low.
- *Hosts may change their own in-ear mix* off (Audio setup → Seats): the
  overlay still opens, read-only, saying "View only".
- **Settings → Export**: the file now carries `audio_routing` (link
  matrix and offsets); importing it on another PC previews "audio routing"
  and applies it with the rest.
