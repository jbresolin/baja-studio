# ADR 0004 — WP3: camera control UI and configuration pages

Date: 2026-11-03. Work item: LIVESTACK-15. Brief: Plane page "06 — WP3 brief".

## Decisions taken beyond the brief

### The browser as an input source

1. **Client ids are stable per tab.** The server used to give each WebSocket
   a fresh id. Now a page may ask for one (`/ws?client=<id>`), and every
   snapshot carries the id the page got (`client_id`). The page keeps it in
   `sessionStorage` and asks for it again on reconnect or reload. So after a
   Wi-Fi blip it is still the same source (`web:<id>`), and it still owns
   the camera it was moving. An id that is malformed or already connected
   (a duplicated tab) gets a fresh one: two pages never share a source.
2. **A release is not a Stop.** Letting go of a control sends
   `stop {camera, release: true}`. The server applies it only if this page
   holds the camera (the arbiter's `only_if_owner`, as for joystick stops).
   Otherwise the operator who was refused (bench step 6) would stop the
   operator who refused them on letting go. The Stop button and Space send
   a plain `stop`, which is always obeyed (bench step 7). Tab hidden, window
   blurred, camera switch and leaving the page are releases too.
3. **After a camera switch, a Stop or a hidden tab, the stream waits for
   every control to be let go.** A key still held must not start moving the
   newly selected camera.
4. **The command log keeps the start of a move stream.** A page holding a
   key sends `move` ten times a second. `Module.wants_logged` (new, default
   True) lets the cameras module keep only:
   - the first move of a stream;
   - the first refusal of a run of refusals;
   - release stops that stopped something.

   `list_serial_ports` and `raw_frames` are reads and are not logged. The
   registry asks the module after each command.
5. **The on-screen stick uses the joystick deadband** (`motion.joystick_deadband`),
   applied in the browser before sending. The arbiter applies its deadband to
   `joystick:` sources only, so it is not applied twice. The speed slider
   scales pan and tilt. Zoom goes at `motion.joystick_zoom_velocity`, like the
   controller's zoom buttons. Velocities are rounded to 3 decimals so float
   noise does not count as a new velocity.
6. **Keys.** The brief has number keys both selecting cameras (§3.1) and
   recalling presets (§3.2). Presets win, as in ptz_test.py. Cameras are
   selected with `[`/`]` and **Alt+1–9**. Alt+digit is a tab switch in Chrome
   on Linux, but not on the Windows Streaming PC. Z zooms in, X out. Key
   auto-repeat is ignored. Arrow keys still drive the camera while the speed
   slider has focus. Keys typed into text fields are left alone.
7. **Presets.** Slots 1–9 are always shown (slot 1 = preset 0, as on the
   controller), plus any named preset beyond them. Click recalls; long-press
   (600 ms) or Shift+click stores.
8. **Follow controller** is a per-page checkbox (`localStorage`). The page
   follows `controllers.<id>.selected_camera`, the newest of any controller,
   only when it changes. A camera picked by hand afterwards stays picked.
9. **The command tail is read from `/api/commands`** whenever the camera's
   `control.last_command_ts` changes (250 ms debounce), not on a timer. The
   log is in SQLite, not in the live state.

### Server additions

10. **Commands moved to `modules/cameras/commands.py`.** New ones:
    - `nudge {camera}`: pan 0.2 for 200 ms, ended by the arbiter's ttl. It
      goes through shaping, so it shows inversion.
    - `rename_preset {camera, n, label}`: a direct camera's names are saved
      in its settings, and an empty label removes the name. An agent camera's
      name goes to the agent (`PUT /presets/{n}`, via the arbiter and the new
      `ModeCapableDriver.label_preset`). That is refused in fallback, like
      `set_mode`.
    - `list_serial_ports {}`: device, description, hwid, and which controller
      uses the port.
    - `raw_frames {controller}`: the last 100 frames as hex.
11. **An agent camera's `presets` come from the agent.** The proxy driver
    mirrors them from `/agent/info`, re-read on `presets_changed`. The
    registry publishes the settings' preset names for direct cameras only.
12. **State additions:**
    - `order`: enabled cameras in settings order;
    - `motion`: the motion settings;
    - `controllers.<id>.recent_frames`: 20 decoded frames with hex, published
      at most every 100 ms;
    - `controllers.<id>.selected_camera`.
13. **Call bindings** (`controllers[].call_bindings`, items `{n 1–9, action,
    camera?, preset?}`):
    - `select_camera` sets the controller's selection, which pages can follow.
      It moves nothing.
    - `recall_preset` recalls on the controller's selected camera. Before any
      selection, it recalls on the camera the controller last addressed.
      Addressing by frames does not change the selection: an idle controller
      repeats frames to one address, and that would undo every Call selection.
    - A binding's camera must exist in the list (validated).
    - An edit that changes only the bindings keeps the controller's worker and
      port. Reopening the port would release the latch.
14. **`cameras` and `controllers` are `managed_elsewhere`.** The generic
    Settings form no longer shows them as JSON boxes; `motion` stays there too.

### Frontend

15. **No new dependencies.** Pointer events with capture for the stick,
    native HTML5 drag-and-drop plus ↑/↓ buttons (touch and keyboard) for
    reordering.
16. The configuration page saves only the part it edited (`{cameras}`,
    `{controllers}` or `{motion}`), with the version it loaded (WP0 conflict
    handling). Server validation messages are shown under the field their
    `loc` names. A camera form keeps the *saved* preset names, which the
    preset table may have changed meanwhile.
17. **Raw frames are copied and also shown in a text box.** The studio is
    served over plain http, where browsers may refuse the clipboard.

## Left open

- **Bench session not run** ([docs/bench/wp3.md](../bench/wp3.md), which
  includes WP2's). Until then these are still unmeasured:
  - position units;
  - top speed steps;
  - whether the controller repeats frames;
  - the fixtures rebuilt from flags.

  The raw capture now comes from the frame monitor.
- A joystick refused while the web held the camera takes over only on its
  next *different* frame (WP2 decision 15 dedupes identical frames).
- A page that reconnects while the server still has its old socket open gets
  a fresh id. Its old hold then lapses by ttl (≤ 300 ms).
- Previews (WP6), authentication, image settings.
