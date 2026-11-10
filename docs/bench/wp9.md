# WP9 bench session: hosts screen and production chat

The checklist is §6 of the WP9 brief (Plane page "13 — WP9 brief"). Record
the results in a comment on **LIVESTACK-4**.

You need two screens: the operator's browser on the playout page
(`/playout`), and the **hosts screen** (`/hosts`) on a second monitor or a
tablet on the same network. On first open the hosts screen asks which
seats it is for; tap them and **Done**, then **Fullscreen**. The seats are
remembered by that browser (tap **Seats** in the corner to change them).

Production's side is **Hosts** in the navigation bar (`/hosts/control`):
seat strip, composer with canned messages, history with delivery and
reactions, the card list (order, visibility, **Push**), seats setup and
canned messages / quick replies.

Without OBS: `python -m baja_studio.fakes.fake_obs --port 4455 --password P`.
Without the XR18: `python -m baja_studio.fakes.fake_xr18` (see `wp4.md`).

## Setup

On **Hosts → Seats**: switch on seats 1 and 2, name them (e.g. Ana and
Bruno), and check their mic channels (1 and 2 by default). On **Audio
setup → Channels**, check that *Camera operator's bus (talkback)* is the
camera operator's bus on today's patch (bus 6, "CAMERA OP", by default). Open the hosts
screen and choose **seat 1** only, so that steps 1 and 3 show both sizes.
For step 1's "small on seat 1", open a second hosts screen (another
browser window or the tablet) for **seat 2**; the seat strip on the Hosts
page shows both seats *online*.

## Steps

| # | Do | Expect / record |
|---|---|---|
| 1 | From the playout page's **Message hosts** box (under the strip): To = seat 2, pick canned **30 s**. On seat 2's screen tap 👍. | On seat 2's screen "30 s" slides in **large**, in seat 2's colour, with the reactions row. On seat 1's screen it is **small**. The Hosts page history shows *shown: Bruno*, then 👍 Bruno, and the message fades. Time from send to shown: ____. |
| 2 | Queue two clips and **Take**. Watch the *Now playing* card. As the first clip nears its end, press **Push** on *Next up* (Hosts page). | The countdown counts whole seconds, white, then **amber at 10 s**. *Next up* lists the next clips with durations and the queue total. Pushed, *Next up* jumps to the top with a blue outline; **Unpush** puts it back. Nothing on air: *Now playing* shows a quiet "live" badge. |
| 3 | On the audio page, mute host 1's channel; unmute it. Then press **Prod 1 → Cam**. | The *Audio* card shows Ana **MUTED** then **LIVE** in her colour; with talkback on it shows "Talkback → CAMERA OP". |
| 4 | Pull the hosts screen's network cable (or turn Wi-Fi off). Send a message to its seat while it is off. Plug it back. | A **red border** and "Not live" banner within 5 s (time it: ____). Back on, the border clears and the message sent meanwhile appears without a reload. The seat goes *offline* on the Hosts page about 6 s after the cable is pulled. |

## Also worth noting

- Readability from 1.5 m on the 24" monitor: countdown, message text, seat
  names. Anything too small?
- Touch targets on the tablet (reactions, dismiss ✕, reply field): can a
  host hit them without looking twice?
- An **urgent** message pulses until a reaction; with *Chime* on
  (Hosts → Canned messages and replies) a soft two-note sound plays for a
  new message to that screen's seats.
- **Export aired log** on the playout page: the JSON download now carries
  `hosts_messages` (the whole session); **Clear history** on the Hosts page
  starts a new session.
- Readiness (`/readiness`): *Hosts seats* and *Hosts screens* lines.
