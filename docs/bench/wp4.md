# WP4 bench session: audio with the XR18, the M-VAVE and one D200

The checklist is §7 of the WP4 brief (Plane page "08 — WP4 brief"), plus
the points the bench must settle (marked **Confirm**). Record the results in
a comment on **LIVESTACK-34**.

Start the service (`python -m baja_studio`) and open
`http://<streaming-pc>:8888/audio/config`. In **Mixer & devices** set the
XR18's IP address and **Save**; the status line shows `XR18` green with the
mixer's network name once it answers `/xinfo`. Keep Mixing Station (or X AIR
Edit) open on another machine for the checks below.

Without the desk: `python -m baja_studio.fakes.fake_xr18` (see the README);
without the M-VAVE: start the service with `BAJA_STUDIO_MIDI_BACKEND=fake`
and press "buttons" with the `simulate_midi` command.

## Before you start

- **Confirm the bus address form.** Move IEM HOST 1's master fader (bus 1)
  on the audio page (tick *Bus 1 → visible* in **Channels** first). It must
  move in Mixing Station. We send `/bus/1/mix/fader` (the X Air form); the
  2026 tool sent `/bus/01/...`. If the mixer ignores it, change
  `channel_prefix` in `server/baja_studio/adapters/xr18_addresses.py`
  (both forms are already understood when the mixer reports them).
- **Confirm snapshot recall.** Save a harmless snapshot on the XR18 (e.g. at
  slot 64), add a scene with that snapshot in **Scenes**, recall it from the
  audio page. We send `/-snap/load 64` (1-based); the 2026 tool sent
  `/-action/goscene` with the index minus one. If nothing happens, note what
  Mixing Station's OSC log shows for a snapshot recall.

## Checklist

1. **Mixer `ok`; no fighting.** Move a fader in Mixing Station → the page
   follows within 1 s. Move it on the page → the mixer follows. Drag it fast
   on the page while watching Mixing Station: no jumping back.
2. **M-VAVE.** Every fader, M, S, R, □, bus-select and knob behaves as in
   **M-VAVE** in the setup (2026 mapping by default: M mutes; S closes the
   strip on the main mix; R on PRODUCTION; □ on IEM HOST 1; knobs turn the
   selected bus's sends).
   - Mute LEDs follow a mute made on the page and in Mixing Station; a
     button's LED is right again after you let go of it.
   - **Confirm pickup:** set HOST 1 to -20 dB on the page, then move the
     M-VAVE fader from the bottom up: nothing happens until it passes the
     -20 dB point, then it follows (the status line says "move the fader to
     the mixer's level" meanwhile). Set **Fader takeover → Jump** if
     operators prefer the 2026 behaviour.
   - **Confirm fader feedback**: with *Send fader positions back* on, check
     nothing odd happens on the device when a level changes elsewhere.
   - Unplug the M-VAVE → the page shows `M-VAVE offline` within ~2 s and the
     audio card turns amber `degraded`; plug it back → `ok`, all LEDs
     right, without restarting anything.
   - **MIDI learn**: setup → M-VAVE → *Learn*, press S on strip 3 → its slot
     is highlighted.
3. **D200.** Import `docs/d200/Baja.ulanziDeckProfile` in Ulanzi Studio
   (its URLs already point at `http://localhost:8888`; change the host if
   the D200 software runs on another PC). Mute toggles, nudges, macros and
   state images work; a mute made on the page changes the button image
   within the poll interval (1 s). The status line shows `D200 polling`.
4. **Link matrix.** HOST 1 → IEM HOST 1 linked (seeded): move HOST 1's
   fader, the send follows. Nudge it from the D200 (or a knob): the cell
   turns ◐ paused and the send stays put when the fader moves. Tap the cell
   → unlinked; tap twice more → linked again, the send jumps to the fader.
5. **Macro.** PROD 1 TALK CAM (`macro-1774582749617`): set the send, run it
   off (route closed, level remembered), on (same level back). Recall a
   scene: the page warns first.
6. **Edits survive.** In the setup, change a label and a macro step while
   someone moves faders in Mixing Station: nothing you typed is lost.
   **Save**. Open the setup in a second tab, save a change there, then save
   one in the first tab: refused as a conflict, with *Load latest*.
7. **30 minutes.** Leave the audio page open with the mixer connected for
   30 min: the page stays responsive; `pkt/s` in the status line stays at
   about 1↑ (keep-alives) plus whatever you move. Note the received rate
   with Mixing Station idle.

## Results

| Step | Result | Notes |
|---|---|---|
| Bus address form | | |
| Snapshot recall | | |
| 1 | | |
| 2 | | |
| 3 | | |
| 4 | | |
| 5 | | |
| 6 | | |
| 7 | | |
