# Ulanzi D200 profile for the studio's audio actions

The deck buttons are the operators' spec for which audio controls matter.
The studio builds the profile: **Audio setup → D200 → Download the D200
profile** gives a `Baja Studio.ulanziDeckProfile` to import in Ulanzi Studio
(Profiles → Import), every button already set for the desk as it is patched
(`server/baja_studio/audio/d200_profile.py`).

- It is built from the **saved** settings: Seats (each seat's microphone and
  in-ear bus), Production microphones (PROD 1, PROD 2), the macros, and
  whether PC AUDIO is on the audio page. Download it again after changing
  any of them.
- **Address the D200 calls**: `http://127.0.0.1:8888` when Ulanzi Studio runs
  on the studio PC (the default), otherwise the studio's address. Not
  `localhost`: Ulanzi's API Request plugin runs on Node.js, which tries
  `localhost` as IPv6 (`::1`) first, and the service listens on IPv4 only.
- Every macro button is there, even while its macro is missing from the
  saved settings; the D200 tab lists those (they answer an error until
  Macros → *Restore defaults…* adds them). A seat without a microphone gets
  no buttons and no in-ear page, and production gets no "talk to" button for
  a host without one.
- Each download imports as a new profile; delete the old one in Ulanzi Studio.
- A button whose image follows a state (mutes, macros) has no image of ours
  set on it, only the plugin's own icon until the first answer: with an image
  set, Ulanzi Studio kept showing it and never the state's (2026-11-14 bench).
  Don't set one by hand either. The in-ear nudges, with no state, keep theirs.

`Template.pptx` is what the button images were drawn from; the images are in
`server/baja_studio/audio/d200_images/` (the service ships them).
`GUEST 2 - PROD.png` was missing in 2026 and was made from `GUEST 2 -
PRODDED.png` with the blue background of the other PROD images; `PROD 1-2 -
TALK 2` / `TALKING 2` are the `TALK 1` images with the last "1" swapped for
the "2" of "PROD 2".

## Layout (2026's, kept)

The D200 has 5 columns by 3 rows; key 3_2 is its small screen.

| Page | Buttons |
|---|---|
| Main menu | HOSTS/GUESTS, PROD. CREW |
| Hosts & guests | a column per seat (HOST 1, HOST 2, GUEST 1, GUEST 2): mute, talk to production, that seat's in-ear page (GUEST 2's is top right, under MAIN MENU) |
| In-ears of one seat (one page per seat) | each seat's level in those in-ears, and VIDEO (PC AUDIO), up and down 3 dB; HOSTS/GUESTS back; EDITING shows whose in-ears |
| Production | a row per production microphone (PROD 1, PROD 2): mute, go live, talk to HOST 1, talk to HOST 2, talk to the camera operator; MAIN MENU |

Changed from the 2026 profile: the channels and buses come from the
settings (2026 called 1-4 for the seats and 5-6 for production); GUEST 1 and
GUEST 2 got their "talk to production" buttons; HOST 2's showed GUEST 1's
images; production got "talk to HOST 2" in the 4th column, with "talk to the
camera operator" moved to the last one (João, 2026-11-14). `server/tests/audio/d200_2026_layout.json` keeps every 2026 button
for the test that checks nothing else moved.

## What the buttons call

All requests are `GET`; the matching state URL is polled every second, and
the button image follows a field of its JSON being `true`.

| Buttons | Request | State poll (field) |
|---|---|---|
| Mute | `/action/mute/<channel>` | `/action/state/<channel>` (`muted`) |
| Seat → production | `/action/macro/host1-talk-prod` … `guest2-talk-prod` | `/action/macro-state/<id>` (`active`) |
| In-ear levels | `/action/nudge-aux/<channel>/<bus>/±3` (VIDEO is `aux`) | — |
| PROD N live, → HOST 1, → HOST 2, → camera | `/action/macro/<id>` (ids below) | `/action/macro-state/<id>` (`active`) |

Production macro ids (2026's kept; 2026's "talk to the hosts" is now "talk
to HOST 1", as its button image said):

| Button | Macro id |
|---|---|
| PROD 1 TALK HOST 1 | `prod1-talk-hosts` |
| PROD 2 TALK HOST 1 | `macro-1774581643177` |
| PROD 1 TALK HOST 2 | `prod1-talk-host2` |
| PROD 2 TALK HOST 2 | `prod2-talk-host2` |
| PROD 1 LIVE | `macro-1774582227201` |
| PROD 2 LIVE | `macro-1774582340101` |
| PROD 1 TALK CAM | `macro-1774582749617` |
| PROD 2 TALK CAM | `macro-1774582877605` |

The PROD 3 images exist but the deck has no room for a third production
row; the studio seeds `prod3-talk-hosts`, `prod3-talk-host2`,
`prod3-go-live` and `prod3-talk-cam` for the audio page and the M-VAVE. The D200 tab also lists
every URL for a button set by hand.
