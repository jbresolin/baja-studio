# ADR 0006 — WP4: audio module

Date: 2026-11-05. Work item: LIVESTACK-34 (under LIVESTACK-9). Brief: Plane
page "08 — WP4 brief". Reference read in full: the 2026 `xr18-control`
(`xr18.js`, `midi.js`, `macros.js`, `server.js`, `mappings.json`,
`storedLevels.json`, the Ulanzi profile and its images). On João's
instruction the 2026 code and UI were a source of facts, not a template.

## Decisions taken beyond the brief

### Core (separate branch `core-root-routes`, merged in first)

1. **`Module.root_routes()`** mounts a router at the service root, ahead of
   the frontend's catch-all; `/api` and `/ws` paths are refused. Used for
   the D200's `/action/...`.
2. **`childproc_child.ChildChannel`**: a child sends unsolicited events from
   any thread; `run_child` writes replies through the same channel. The
   host side already routed `{"event": …}` lines.

### XR18

3. **Hand-rolled OSC** (`adapters/osc.py`), not `python-osc`: the message
   set is small, tests compare exact bytes, and no dependency is added.
   Messages with `i`/`f`/`s`/`b` arguments, bundles on the way in.
4. **Timing as a plain class told `now`** (`adapters/xr18_link.py`):
   `/xremote` every 8 s; `/xinfo` every 2 s, `offline` when none answered
   for 5 s; write protection 300 ms (and while the write is still queued);
   **one packet budget of 100/s, bursts of 20**, for everything sent.
   Writes queue per address (latest wins), go before queries; queries are
   de-duplicated. Pinned by tests that count packets, and by a fake mixer
   that hangs above 113 packets per 500 ms as the real one did.
5. **What is read, when** (`modules/audio/queries.py`): on connect (and
   after a snapshot load or the mixer coming back) every strip's fader,
   mute and name (72 queries), plus only the sends something depends on
   (link pairs, macro points, M-VAVE button points, the knobs' bus). One
   bus's sends for the visible and mapped strips on demand (page bus view,
   knob bus select). The 2026 500 ms poll of a bus's sends is gone.
6. **Write protection is checked and applied under the controller's lock**
   (`AudioController.receive_from_mixer`). Checking it on the mixer thread
   and applying later let a write from the MIDI thread slip in between; the
   tests caught it.
7. **The first reading after connecting is not a change.** It is not
   mirrored to linked sends (it would have overwritten every IEM mix on
   connect).
8. **Bus addresses are sent as `/bus/N`** (one digit, the X Air form), not
   `/bus/0N` as the brief and the 2026 code had; both are parsed. **To be
   confirmed at the bench** (docs/bench/wp4.md); the form is one line in
   `channel_prefix`.
9. **Snapshots load with `/-snap/load N`** (1-based) as the brief says; 2026
   sent `/-action/goscene N-1`. **To be confirmed at the bench.**
10. **The real fader law** (`adapters/xr18_levels.py`): four linear-in-dB
    segments, 0.75 = 0 dB, 0.5 = -10, 0.25 = -30, 0.0625 = -60. 2026's
    `40·log10(v/0.75)` read about 3 dB high mid-travel; D200 `db` texts and
    nudge results change accordingly (the shapes do not).
11. **UDP transport behind a factory**; the fake XR18's in-process network
    plugs in for tests. Windows' "connection reset" on UDP (mixer off) is
    ignored.

### Routing state: links and stored levels

12. **Link states and stored levels live in SQLite tables
    (`audio_links`, `audio_stored_levels`), not in the versioned settings**
    (a deviation from brief §3 for the link matrix). They change during the
    show from any control; in settings every knob that pauses a link would
    bump the version and make any open setup page's save conflict, the 2026
    lost-edits bug in a new form. `set_link` applies at once (like a mute);
    the setup page's grid uses it and is not part of Save.
13. **Seeded from 2026's explicit entries; a missing pair is `unlinked`.**
    In 2026 a missing entry silently meant `linked` (so channel 17 and
    channel 9's missing buses were linked to everything). Seeding happens
    once (`audio_meta`), so unlinking everything sticks.
14. **Link rules:** any direct change of a linked send (page, knob, **D200
    nudge-aux**, a macro's `set_aux`) pauses its link, so an IEM tweak
    survives the next fader move (2026 paused only on knob and page). A
    send *closed* by a macro or button (it has a stored level) is skipped by
    mirroring, so a talkback route stays closed when its fader moves.
    Reopening a linked send goes to the fader's current level. 2026's
    "an M-VAVE fader move re-links every paused send" is **dropped**:
    unexpected for operators who paused on purpose. Ask João at the bench
    if it was a deliberate workflow.

### Macros and scenes

15. **Macros are step lists** (`set_fader`, `set_mute`, `set_aux`,
    `store_levels {points, silence}`, `restore_levels {points}`,
    `recall_scene {snapshot}`), `toggle` (on/off steps) or `trigger`.
    `store_levels` silences by default: the 2026 `store-mute` is one step.
16. **"Active" is derived from the mixer, never remembered**
    (`audio/macros.py`): a toggle is active when the mixer already looks
    the way its on-steps would leave it. Restarts and changes made in Mixing
    Station no longer leave D200 images and LEDs wrong. A macro with nothing
    visible (only a snapshot) is never active.
17. **Storing a point that is already silent keeps the level stored
    before**, so a double press cannot store "off" and make the route
    impossible to reopen. Restoring with nothing stored goes to unity, as
    in 2026.
18. **`recall_scene` inside a macro loads a mixer snapshot**, not a studio
    scene, so scenes and macros cannot call each other in a loop.
19. **2026 macro ids are kept** (`macro-1774581643177`…) because the D200
    profile calls them; PROD 3's three macros are added (its images
    existed, its macros did not). `stored_levels` from 2026 are not seeded:
    they were the state of one event.
20. **Scenes**: `/action/scene/N` and `recall_scene {index}` run configured
    scene N; an unconfigured 1-64 loads that snapshot, as 2026 did. The
    page asks before any scene that loads a snapshot.

### M-VAVE

21. **MIDI child process** (`childprocs/midi_child.py`): opens the first
    input/output whose name contains `midi.device_name_match`; a thread
    checks every second and reports `port` events when it leaves or comes
    back. A MIDI system that cannot start at all (no ALSA sequencer, broken
    driver) is reported as the status message instead of crashing the
    child. `BAJA_STUDIO_MIDI_BACKEND=fake` runs the real child against the
    fake device; `simulate_midi` injects messages through it.
22. **Button actions generalised**: `none`, `mute`, `send_toggle` (on the
    button's own bus or its row's), `macro`. `row_buses {solo, rec, sel}`
    replace `s_button_bus`/`r_button_bus` (and 2026's undocumented
    `selButtonBus`). `production_bus` is **dropped**: 2026 read it but never
    used it. The brief's `buttons.channel` override is kept.
23. **LEDs derived from the mixer**: M lit when muted, a send toggle lit when
    its route is silent, a macro button lit when active. Re-sent after every
    change, after every release (the device clears its own LED), when the
    port opens (stale queued messages are dropped then) and every 10 s.
24. **Pickup takeover by default** (`midi.fader_takeover`): a fader whose
    channel was changed elsewhere does nothing until it reaches the mixer's
    level; the page shows which strips are waiting. Only that channel's
    *fader* changing elsewhere drops the pickup. `jump` is the 2026
    behaviour. **To be confirmed at the bench.**
25. Fader sweeps are coalesced to the latest position per strip per batch;
    knob values decode as signed relative (0x01-0x3F up, 0x41-0x7F down).
    Fader positions are sent back as pitch bend (`send_fader_feedback`).
26. **MIDI learn** captures the next control touched (not acting on it) and
    publishes it; the setup page that armed it highlights and focuses that
    control's slot.
27. Health: the XR18 away makes the module `offline`; only the M-VAVE away
    makes it `degraded`.

### D200

28. **Byte-compatible with 2026**: same paths (GET and POST), same bodies
    and key order, Express's compact JSON and JavaScript's number
    formatting (`0` not `0.0`), the 2026 error wording (en dash included),
    `404 {"ok":false,"error":"not found"}` for an unknown macro. The
    "recorded" responses in `tests/modules/d200_2026_responses.json` were
    **worked out from `server.js`, not captured** from a running 2026
    server (it could not be run here); they cover the cases where the old
    and new dB laws agree.
29. Actions are dispatched as `device:d200` (in the command log); state
    polls are not commands. `d200.enabled` off answers 503.
    `audio.d200.last_seen` (published at most every 5 s) lets the page show
    "D200 polling".
30. `docs/d200/Baja.ulanziDeckProfile` is the 2026 profile with only its
    URLs rewritten to `http://localhost:8888`, by script; not yet imported.

### Pages and commands

31. **Audio page** has a "sends on faders" view per bus (local to the page)
    with link chips and relink; the M-VAVE knobs' bus is shared state.
    Dragged faders send at most every 50 ms marked `dragging`, which keeps
    them out of the command log; the final value is logged.
32. **Setup page** edits a local draft that broadcasts never touch;
    explicit Save/Discard; saves `PUT /api/settings/audio` with the version
    the draft started from (structured 409/422). `audio.settings_version`
    tells an open page that someone saved: with no unsaved edits it reloads
    itself, otherwise it says so. The brief's `save_mappings` command exists
    with the same rules for other callers.
33. `dry_run` accepts unsaved steps, so the editor shows what its draft
    would change now.

## Left open

- **The bench (docs/bench/wp4.md) has not been run**, including the four
  points marked *Confirm*: bus address form, snapshot recall, pickup,
  fader feedback. The Ulanzi profile has not been imported.
- `childprocs/midi_rtmidi.py` (the python-rtmidi backend) is not covered by
  tests (no MIDI hardware or ALSA sequencer here); the UDP transport is not
  covered by pytest (conventions: no network) but was smoke-tested by hand
  against the runnable fake XR18.
- **Files over the ~300-line guideline:** `modules/audio/controller.py`
  (~385) and `audio/config.py` (~325). The controller could split its
  mixer-facing half (receive, mirror) from the operations; worth doing
  before it grows.
- Bus masters, `lr` and the effects returns are shown only as faders; no
  meters (the XR18's `/meters` would need its own budget).
- The setup page's channel list and M-VAVE grid are plain tables; on a
  phone they scroll sideways.
