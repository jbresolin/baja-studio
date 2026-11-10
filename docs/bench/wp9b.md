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
4. **Audio setup → Link matrix** (only on a PC that ran an earlier
   version; a fresh install starts this way, ADR 0013 decision 10): PC
   AUDIO and SHOTGUN linked to buses 1-4; PROD 1-4 **not** linked to buses
   1-4.
5. **Audio setup → Macros** (same PCs): an existing install keeps its old
   "Host 1/2 → Prod" (they close the fader). Check they now read: *On / off
   the main mix* off, then *Link / pause* of the other seats' in-ear
   buses, then *Close* of those sends; the off steps undo them in reverse
   (dry-run shows it). A fresh install also has "Guest 1/2 → Prod".

## Steps

| # | Do | Expect / record |
|---|---|---|
| 1 | On the hosts screen tap **pronto**, then type a message and **Send**. On the Hosts page reply to it. | The Hosts page history shows "← Ana pronto" in Ana's colour marked **new**, the title shows "2 new from the hosts" and a chime plays. The playout page's *Message hosts* line shows the count and the latest text; **Read** clears it. Production's reply appears under Ana's message on the hosts screen, which grows for a few seconds. |
| 2 | Send Ana a message from production; on the hosts screen press its **✕**. Reload the hosts screen. | The message disappears at once and stays gone after the reload. The Hosts page history says *dismissed by Ana*. Seat 2's screen (if open) still shows it. |
| 3 | Watch the chat at rest; send a new message to Ana; react 👍. Then **Hosts → Chat area** = large. | At rest each message is one or two lines with small reactions. The new message grows (large text, reply field) and shrinks back about 4 s after the 👍. An **urgent** one stays grown and pulses until answered. *Large* applies at once, without Save. |
| 4 | **First, on the real XR18:** run **Host 1 → Prod** and watch channel 1's LR button in Mixing Station (or the desk); run it again. **If LR does not switch off and back, stop the bench here and report it on LIVESTACK-57**: the macro depends on `/ch/NN/mix/lr` and there is no fallback. Readiness *Hosts can be taken off air* is green when the mixer reports the LR assign. | LR off, then back on: ____. |
| 5 | Fake console: `mute 1 off` (fader at unity). Run **Host 1 → Prod**; run it again. Then `mute 1 on`. Then `mute 1 off` and `send 5 bus1 0.75`. | Ana: **LIVE**; after the macro: **TO PRODUCTION** (LR assign off, the fader untouched, bus 5 still open); again: **LIVE**. `mute 1 on`: **MUTED**; PROD 1 into bus 1: **production → you** on Ana's line. |
| 6 | Run **Host 1 → Prod**, then move HOST 1's fader on the **M-VAVE** (strip 1). Listen to the stream and to host 2's in-ears; run the macro again. | The **stream stays silent** for host 1 and **host 2's in-ears stay silent** for host 1 while the fader moves; host 1's own in-ears, production (bus 5) and the camera operator (bus 6) follow the fader. Released: host 1 back on air at the fader's new level, and back in every in-ear at main + offset. |
| 7 | Tap Ana's line on the audio card. Press **+** twice on **PC AUDIO**. Move PC AUDIO's main fader on the audio page. **Reset to main**. Then press the hosts' pad *vol up* (D200 `nudge-aux/17/bus1/+1`). | The overlay lists the sources of bus 1 with *main* level and offset (PC AUDIO +2 dB); a source unlinked from bus 1 would say *independent*. Moving the main fader moves bus 1's send with the +2 dB kept. Reset puts every offset back to 0 dB. The pad shows a toast "Ana: PC AUDIO +1 dB" with the overlay closed. The overlay closes by itself after ~20 s without a touch. |
| 8 | Fake console: `tap 2 bus1 post` (HOST 2's linked send to host 1's in-ears, post-fader). Run **Readiness**; then `tap 2 bus1 pre` and run it again. | *Linked sends are pre-fader*: amber, "after the fader, so cut twice: HOST 2 → IEM HOST 1", with the fix at the desk; then green. Nothing is written to the mixer (the tap stays where the console put it). |
| 9 | Fake console: `drop on` (or unplug the XR18). | Within ~5 s the card says **Mixer offline** (no stale LIVE/MUTED); the overlay's buttons are disabled with "Mixer offline". `drop off`: states come back. |

## How "talk to production" works

The **Host 1 → Prod** macro turns HOST 1's **LR assign off**; the fader is
not touched. It pauses HOST 1's links to the other seats' in-ear buses
(2-4) and closes those sends with stored levels. HOST 1's own in-ears
(bus 1), production (bus 5) and the camera operator (bus 6) stay linked,
so they keep following the fader: the host hears themselves, production
hears them, and camera ops hear them as in 2026. A fader move meanwhile
cannot reach LR (unassigned) or the other seats (paused and closed).
Released, LR goes back on and the other seats' sends are restored, then
linked again (main + offset).

Linked sends must be **pre-fader** at the desk: the studio already copies
the fader into them, so a post-fader one is cut twice. Readiness warns.

## Also worth noting

- **To confirm on the real XR18** (one line each in
  `adapters/xr18_addresses.py`): LR assign at `/ch/NN/mix/lr` (step 4); a send's tap
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
