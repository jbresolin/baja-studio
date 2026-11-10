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

### The "talk to production" macro

8. **The 2026 macro, ported, with LR assign instead of the fader.**
   *What 2026 did* (`macros.js`, `midi.js`): "Host A/B → Prod" stored the
   host's fader and closed it, and restored it on release. The sends stayed
   where they were because the macro's own fader write was marked
   (`_macroSetFaders`) and its echo not mirrored, so production, the
   camera operator and every in-ear (the host's own and the other seats')
   kept hearing the host. Nothing protected that: any fader move during
   the macro (M-VAVE, desk, Mixing Station) was mirrored everywhere and put
   the host back on air. As first ported here (closing the fader through
   the normal mirror), the host went silent on bus 5 too and read MUTED.
   *The design now* (decided with João, 2026-11-10), one macro per seat
   (`host1-`, `host2-` (2026 ids, called by the D200 profile), `guest1-`,
   `guest2-talk-prod`): on, the seat channel's **LR assign goes off**
   (`/ch/NN/mix/lr`; the fader is not touched), its links to the **other
   seats'** in-ear buses are paused and those sends closed with stored
   levels (paused first, so nothing re-mirrors them); off, LR assign back
   on, those sends restored and then linked again (back to the fader plus
   their offsets). The seat's own in-ear send and buses 5-6 stay linked.
   The card reads **TO PRODUCTION** (off LR, on bus 5). A fader move during
   the macro moves only what is still linked (the host's own in-ears,
   buses 5-6) and never reaches LR or the other seats. Two new step types
   carry it, shown in the macro editor and the dry-run: `set_link
   {channel, bus, state}` and `set_lr_assign {channel, on}`; the editor's
   "mirror" builds the off steps in reverse order (open, then link, then
   LR on). Pinned by the three `test_talk_to_production_*` /
   `test_releasing_*` / `test_a_fader_move_*` tests in
   `test_audio_hosts_view.py`.
   **Active** is derived from the mixer, as for every macro: a macro that
   sets LR assigns is active exactly when they are as it says (so a host
   taken off LR at the desk lights it too).
   **Trade-off:** the camera operator (bus 6) still hears the host, as in
   2026; production hears them at the fader level. Unlike 2026 the other
   seats no longer hear the host in their in-ears meanwhile, and the host
   now hears themselves, as in 2026.
9. **The studio reads the desk's configuration, never writes it.** Two
   readiness checks, both "not checked" while the XR18 is not answering:
   *Linked sends are pre-fader* (`linked_taps`) warns for every linked
   send taken after the fader (the studio already copies the fader into
   it, so the fader would cut it twice) or never reported; *Hosts can be
   taken off air* (`lr_assign`) warns when a mapped seat microphone's LR
   assign was never reported, so a mixer that ignores `/mix/lr` does not
   make the macro fail silently on air. The taps of every link-matrix pair
   and the LR assigns are read once on connect (about 40 more reads with
   the default matrix, within the budget).
10. **The default link seed changed** (`audio/defaults.py`, fresh installs
    only: the seed is written once, so an existing install keeps its
    matrix until "Restore defaults…", decision 23). The sources of the
    main mix (HOST 1-2, GUEST 1-2, SHOTGUN, **PC AUDIO**) are linked to the
    in-ear buses 1-4 (LIVESTACK-34: in-ear = main plus offsets); 2026 left
    out PC AUDIO and the shotgun on buses 2-3. **Production microphones
    (PROD 1-4) are never linked into the in-ears** (2026 linked them): they
    are not main sources, and reach one in-ear bus only when production
    speaks to a host privately ("Prod N → Hosts", which now reopens them at
    their stored level instead of a fader that is down while the producer
    is off air). Buses 5-6 are 2026's: the seats linked to both, the
    production loop being main plus the production microphones.

### Personal mixes (§3)

11. **Every adjustment of a linked send on a seat's bus is an offset**:
   the D200 `nudge-aux` (the hosts' pads), the M-VAVE knobs, the audio
   page's send faders and the overlay. A level set directly becomes "level
   minus the fader" and is refused while the fader is off (there is
   nothing to be relative to). **Macros' `set_aux` still pause the link**:
   a macro sets a route, not a mix preference. Other buses keep WP4's rule.
12. **Offsets are stored even when the send cannot be written** (fader
    unknown, or the send closed by a macro); the next mirror or reopen
    applies them. Offsets stay when a link is paused or removed, so
    relinking restores the seat's mix. Offsets are rounded to 0.1 dB.
13. **A source's link-matrix row for a bus** is what the setup page's
    matrix lists for it: the inputs shown on the audio page, plus any
    linked or paused to that bus. A visible source not linked is
    "independent" in the overlay and its send is set directly (-90…+10 dB).
14. **The pad toast is any change that is not the overlay** (pad, knob or
    the audio page), published as `hosts_view.pad_change` with a sequence
    number, so two identical presses are two toasts. The screen ignores
    the one already there when it opens.
15. **Holding + or −** steps once, then every 250 ms after 400 ms (four
    writes a second); the link's latest-wins queue would absorb more. A
    test holds + for 60 steps against the fake and checks the budget.

### Chat (§1)

16. **Dismiss has two meanings now.** Production's Dismiss (no seats)
    fades the message everywhere, as before (`dismissed_at`). A hosts
    screen's ✕ sends `seat_ids` and the message is hidden on screens of
    those seats, kept in a new `dismissed_by` column, so a reload does not
    bring it back; the screen also hides it at once, before the answer.
17. **The messages table gains columns in place** (`dismissed_by_json`,
    `read_at`, `reply_by`, `reply_at`), added on start to a WP9 table, so
    a conversation survives the upgrade.
18. **Compact chat** is computed by the screen (`isExpanded`): a message
    for this screen is grown while unanswered (an urgent one pulses), and
    shrinks 4 s after the first answer, on expiry or when production
    dismisses it; the chat area is `chat_size` at rest and taller while a
    message is grown. Production's size picker applies on change, without
    Save, so it can be tried while watching the screen.
19. **Host messages** are `from: "seat:<id>"`, `to: "production"`, never
    expire, and are shown on the sender's screen only (with production's
    answer; no host-to-host). Production answers with `react`/`reply`
    *without* `seat_id`; answering marks the message read. `mark_read` is
    the Hosts page's "Mark read" and the playout line's "Read", and is
    kept out of the command log like delivery receipts.
20. **Hosts-facing commands are checked against the seat** (new
    convention): a seat answers and hides only messages addressed to it or
    to all (or its own), sends only while switched on, and changes only
    its own in-ear bus.

### Export and import (§4)

21. **No core change.** The settings export (core) carries settings only.
    Instead the **Settings → Export / import page** adds an
    `audio_routing` section to the file it saves (`audio.export_routing`)
    and, on import, previews and applies it through
    `audio.import_routing` (`dry_run` first), as WP9 did for the aired log
    and the messages. Pauses are not exported (a pair is linked or not);
    pairs not in the file are unlinked; offsets not in the file go to 0.
    A file without the section (older, or audio off) imports as before.
    A core hook (`Module.export_data`) would make this general; worth a
    follow-up once WP10 (which owns core) has merged.
22. **`/settings/audio` is JSON-only** with a link to Audio setup, and the
    audio page has the ⚙ link (WP5c's rule).
23. **"Restore defaults…" for the macros and the link matrix**, because an
    existing install keeps both when the defaults change (decision 10).
    It goes through the page's draft, Save and Discard, and shows what
    changes before saving (`modules/audio/restore_commands.py`):
    - Macros: the macros with a default id are replaced by the default,
      missing defaults are added, macros with other ids are kept. The
      result is saved with the usual settings save, so a stale version
      is still refused with 409.
    - Link matrix: the seed, pauses included; offsets are kept. It waits
      beside the draft and is applied on Save, refused if the matrix
      changed since the preview.
    - Both are in the command log under the page's client id. Settings
      saves are not logged by core, so after the macros are saved the
      page sends `audio.restore_defaults`, which checks the saved setup
      carries the defaults. No core change.

## Left open

- The XR18 addresses (decision 6), first of all `/ch/NN/mix/lr`, which
  "talk to production" depends on: **bench**. If the XR18 does not obey it
  over OSC, the macro cannot work as designed; there is no silent fallback
  (decision 9's check warns) and the decision comes back to João.
- Existing installs keep their link matrix and macros (decision 10) until
  someone presses "Restore defaults…" on each (decision 23); the bench
  sheet does it in setup.
- Production can display, not route, a production microphone into one
  host's in-ears (out of scope).
- The bench (`docs/bench/wp9b.md`) has not been run.
