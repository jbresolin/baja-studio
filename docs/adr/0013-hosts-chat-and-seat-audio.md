# ADR 0013 — WP9b: hosts chat fixes and seat audio card

Date: 2026-11-10. Work item: LIVESTACK-57 (under LIVESTACK-4). Brief: Plane
page "15 — WP9b brief". Read with ADR 0006 (audio) and ADR 0011 (hosts).

## Decisions taken beyond the brief

### Audio housekeeping first

1. **The controller split** (its own commit, no behaviour change), as ADR
   0006 suggested: `modules/audio/mixer_side.py` (`MixerSide`: the link,
   receiving, mirroring, derived state), `seat_mix.py` (`SeatMixing`: the
   seats' offsets) and `controller.py` (`AudioController`: the operations),
   one class extending the next, so the lock and the cache stay one. The
   settings models split the same way: `audio/config_base.py` (strict model,
   channel ids), `audio/midi_config.py` (the M-VAVE), `audio/seat_config.py`.

### Seat audio model (§2)

2. **The seat's microphone moved from hosts to audio.** WP9's
   `HostsSettings.seats[].audio_channel` is dropped: a saved or exported
   seat that still has it loads without it, and hosts' `settings_version`
   is 2, so an older file is flagged on import. Audio's `seats` default to
   "not mapped" (the brief), so the operator maps them once on **Audio
   setup → Seats**; the value WP9 had is not carried over.
3. **`production_channels` defaults to PROD 1-4 (channels 5-8)**, the
   2026 labels. Seats must use four different in-ear buses, none of them
   the production bus (refused when saved).
4. **What is read for the card**: each mapped seat microphone's LR assign
   and send to the production bus, each production microphone's send to
   every seat's in-ear bus (level, on switch, tap), and every source a
   seat's overlay shows (its send level). All on connect, through the same
   budget and de-duplicated with the other reads; pushes keep them fresh.
   The default setup reads 162 addresses on connect (58 sends), under the
   WP4 guard.
5. **Three-valued rules** (`audio/seat_states.py`): every fact is true,
   false or unknown; one known "no" decides, otherwise an unknown makes the
   state `null` ("…" on the card) rather than a guess. A send's on switch
   the mixer never reports counts as on; an unknown tap matters only when
   the fader is down.
6. **XR18 addresses, to be confirmed at the bench** (one line each in
   `adapters/xr18_addresses.py`): LR assign `/ch/NN/mix/lr`; send tap
   `/ch/NN/mix/MM/tap` with 4 (post) and 5 (sub-group) as the taps after the
   fader; send on `/ch/NN/mix/MM/on`, which the X32 has and the XR18 may not.
7. **`audio.hosts_view`** holds everything the screen needs: per seat the
   state and "production → you", the personal mixes' rows, talkback (now
   worked out by audio, so the card no longer reads `audio.macros`), the
   overlay's settings and the last pad change. The card is the new
   `seat_audio` component at `audio.hosts_view`; WP9's `audio_state` is
   gone, so a screen left open across the upgrade says "reload".

### The "talk to production" macro (reported, not special-cased)

8. **The default "Host 1/2 → Prod" macros classify as MUTED, not TO
   PRODUCTION.** They close the host's *main fader* (store and silence
   `1:main`); mirroring copies that -inf into every send linked to the
   fader, bus 5 included (seeded linked in 2026), and a post-fader send
   would be silent anyway. The rules read the mixer and the mixer says the
   host is heard nowhere. A test pins this
   (`test_the_default_talk_to_production_macro_leaves_the_host_muted_not_to_production`).
   **For João**: decide how the macro should reach bus 5 (LR assign off
   instead of the fader; or unlink 1→bus5 with a pre-fader tap; or stop
   mirroring a fader that a macro closed).

### Personal mixes (§3)

9. **Every adjustment of a linked send on a seat's bus is an offset**:
   the D200 `nudge-aux` (the hosts' pads), the M-VAVE knobs, the audio
   page's send faders and the overlay. A level set directly becomes "level
   minus the fader" and is refused while the fader is off (there is
   nothing to be relative to). **Macros' `set_aux` still pause the link**:
   a macro sets a route, not a mix preference. Other buses keep WP4's rule.
10. **Offsets are stored even when the send cannot be written** (fader
    unknown, or the send closed by a macro); the next mirror or reopen
    applies them. Offsets stay when a link is paused or removed, so
    relinking restores the seat's mix. Offsets are rounded to 0.1 dB.
11. **A source's link-matrix row for a bus** is what the setup page's
    matrix lists for it: the inputs shown on the audio page, plus any
    linked or paused to that bus. A visible source not linked is
    "independent" in the overlay and its send is set directly (-90…+10 dB).
12. **The pad toast is any change that is not the overlay** (pad, knob or
    the audio page), published as `hosts_view.pad_change` with a sequence
    number, so two identical presses are two toasts. The screen ignores
    the one already there when it opens.
13. **Holding + or −** steps once, then every 250 ms after 400 ms (four
    writes a second); the link's latest-wins queue would absorb more. A
    test holds + for 60 steps against the fake and checks the budget.

### Chat (§1)

14. **Dismiss has two meanings now.** Production's Dismiss (no seats)
    fades the message everywhere, as before (`dismissed_at`). A hosts
    screen's ✕ sends `seat_ids` and the message is hidden on screens of
    those seats, kept in a new `dismissed_by` column, so a reload does not
    bring it back; the screen also hides it at once, before the answer.
15. **The messages table gains columns in place** (`dismissed_by_json`,
    `read_at`, `reply_by`, `reply_at`), added on start to a WP9 table, so
    a conversation survives the upgrade.
16. **Compact chat** is computed by the screen (`isExpanded`): a message
    for this screen is grown while unanswered (an urgent one pulses), and
    shrinks 4 s after the first answer, on expiry or when production
    dismisses it; the chat area is `chat_size` at rest and taller while a
    message is grown. Production's size picker applies on change, without
    Save, so it can be tried while watching the screen.
17. **Host messages** are `from: "seat:<id>"`, `to: "production"`, never
    expire, and are shown on the sender's screen only (with production's
    answer; no host-to-host). Production answers with `react`/`reply`
    *without* `seat_id`; answering marks the message read. `mark_read` is
    the Hosts page's "Mark read" and the playout line's "Read", and is
    kept out of the command log like delivery receipts.
18. **Hosts-facing commands are checked against the seat** (new
    convention): a seat answers and hides only messages addressed to it or
    to all (or its own), sends only while switched on, and changes only
    its own in-ear bus.

### Export and import (§4)

19. **No core change.** The settings export (core) carries settings only.
    Instead the **Settings → Export / import page** adds an
    `audio_routing` section to the file it saves (`audio.export_routing`)
    and, on import, previews and applies it through
    `audio.import_routing` (`dry_run` first), as WP9 did for the aired log
    and the messages. Pauses are not exported (a pair is linked or not);
    pairs not in the file are unlinked; offsets not in the file go to 0.
    A file without the section (older, or audio off) imports as before.
    A core hook (`Module.export_data`) would make this general; worth a
    follow-up once WP10 (which owns core) has merged.
20. **`/settings/audio` is JSON-only** with a link to Audio setup, and the
    audio page has the ⚙ link (WP5c's rule).

## Left open

- The "talk to production" macro (decision 8) and the three XR18
  addresses (decision 6): **bench**.
- The 2026 link seed does not link PC AUDIO (aux) to any bus, so it is
  "independent" in every personal mix until linked on Audio setup → Link
  matrix (João's topology says the IEMs are a copy of main).
- Production can display, not route, a production microphone into one
  host's in-ears (out of scope).
- The bench (`docs/bench/wp9b.md`) has not been run.
