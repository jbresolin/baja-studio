# ADR 0019 — WP6a: image settings for every camera, the UVC child and the Pixy driver

Date: 2026-11-14. Work items: LIVESTACK-18 (part a), LIVESTACK-3, LIVESTACK-19.
Brief: Plane page "19 — WP6a brief". WP6b (OBS crop cameras, previews, ADR
0020) ran in parallel; this package leaves `preview-slot` divs for its
component and touches the shared files only additively.

## Decisions

### Layout

1. **Image settings are part of the cameras module**, not a module of their
   own. They share the registry (one list of cameras, one settings section)
   and the Pixy driver needs the same UVC child, so a second module would
   have had to reach into the first. Library code is `baja_studio/imaging/`:
   - `config.py`: `UsbDeviceRef`, `ImageSetting`, `StoredPosition` (fields
     of `CameraConfig`, kept apart for size);
   - `usb_devices.py`: the device list and how a camera's device is found;
   - `uvc_link.py`: the `uvc` worker that runs the child;
   - `controls.py`: the rules (Lock, All auto, Reset, Copy, what is saved);
   - `sources.py`: USB (the child) and agent (`GET/PUT /image`) sources;
   - `service.py`: the operator actions and the state cards;
   - `watch.py`: the `imaging` worker that reapplies by itself.

   The commands are `modules/cameras/image_commands.py`. The cameras module
   now runs three workers of its own: `motion`, `uvc`, `imaging`.
2. **The UVC child** (`childprocs/uvc_child.py`) speaks `status`,
   `list_devices`, `read_controls`, `write_controls`, `check`, and sends a
   `devices` event when its 2 s look finds the list changed. It runs on every
   start, also with no USB camera configured, because Camera setup needs the
   device list to pick one. Off Windows, or without duvc-ctl, it stays up and
   says why (`status.available` false), like the MIDI child.
3. **The backend takes the library as an argument** (`childprocs/uvc_duvc.py`):
   the real `duvc_ctl` module, or `fakes/fake_duvc.py`, a fake *library*
   with the same calls and result types. The quirks are the library's, so
   only a fake library catches code that trips on them: a write's result has
   no `value()`, every control of a write is judged on its own (the
   `pixy_ptz` two-axis bug), Pixy readback is the last value written. This
   became a rule in `docs/conventions.md`. duvc-ctl is imported by name with
   `importlib` and kept as `Any`, so mypy never needs its (missing) stubs.
4. **Control names are the studio's** (`exposure`, `white_balance`, …),
   mapped to `CamProp`/`VidProp` in the backend. Pan, tilt and zoom are
   motion: they never appear on an image card.
5. **Which controls have an automatic mode.** DirectShow's range does not say.
   A control has `auto` when UVC defines one for it (exposure, focus, iris,
   white balance, hue) or the device reports it on auto, now or by default.
   The bench sheet checks the S600 and the Pixy.

### Finding a USB camera

6. **The DirectShow path is the identity.** It is stable per USB port and
   tells two S600s apart. A camera whose device moved to another port is
   reported missing ("moved to another port? pick it again in Camera
   setup"), never guessed by name: with two S600s and one configured, a
   name fallback took over the *other* S600 when the first was unplugged
   (a test caught it). **Name + index** (the index counts devices with the
   same name) is the fallback only for a device DirectShow lists without a
   path. Two enabled cameras may not use the same path.

### Image settings

7. **Stored per camera** in `cameras[].image` (control → `{value, auto}`):
   what the device *took*, read back after the write (`auto: true` saves no
   value). A value outside the range is refused before it is sent and shown
   on its control ("500 is outside 0..100"); a refusal by the device is shown
   with the device's words. Neither is saved. An error stays on its control
   until that control is written successfully or the settings are reapplied.
8. **Reapplied by the `imaging` worker**, twice a second looking at every
   camera with image settings: a USB camera is reapplied when its device is
   present and has not had its settings since the child (re)started or the
   device (re)appeared; an agent camera when its link turns `ok`. A camera
   away keeps its card, offline with the reason and its last controls.
9. **Lock** reads afresh and saves every control on auto as manual at the
   value the camera chose. **All auto** is a `set_image` from the page.
   **Reset** writes the device defaults (value and mode) and clears the saved
   settings; refused for agent cameras, which report no defaults. **Copy**
   reads both cameras afresh; only controls both have; auto stays auto where
   the target has an automatic mode; values are clamped to the target's
   range and put on its step.
10. **Agent cameras** use the existing client calls; image commands are
    refused while the camera is in direct fallback ("…driven directly, which
    has no image controls") and while the link is not `ok`.
11. **State**: `state.cameras.image.<camera>` = `{source, status,
    status_reason, device, agent, controls}`, controls in display order;
    `state.cameras.usb` = `{status, message, devices[{name, path, index,
    used_by}]}`.
12. **The command log** keeps the start of a run of `set_image` writes per
    (source, camera): a write to other controls, or after a pause of more
    than 1 s, starts a new run. `get_image` and `list_usb_devices` are reads
    and are not logged. The page sends a dragged slider at most every 150 ms,
    and always its last value.
13. **Health**: the `uvc` worker counts as the device `usb` on the home card
    only while an enabled camera uses a USB device, so a PC without USB
    cameras (or a dev box without duvc-ctl) stays green.

### The Pixy and fixed cameras

14. **Driver `uvc`** (`cameras/drivers/uvc_ptz.py`, arithmetic in
    `uvc_motion.py`): velocities integrated at 20 Hz into a target clamped to
    the ranges read from the device; only axes that moved by a whole step are
    written. Top speeds at full stick: pan 60°/s, tilt 45°/s, zoom 25 units/s
    (cautious guesses; the speed scales can only lower them). Stop = stop
    integrating; nothing is sent. The ttl ends the motion like a dead-man.
    The published position is the commanded one with `position_is_commanded:
    true`, and `has_position_feedback` is false. The worker looks for the
    device twice a second when idle and recovers by itself after a replug,
    without the supervisor's backoff.
15. **Presets** are `cameras[].stored_positions` (pan, tilt, zoom in device
    units), `preset_max` 127; recalling one that was never stored is refused.
    Names stay in `presets`, as for VISCA cameras.
16. **Driver `none`** (`cameras/drivers/fixed.py`) for a camera with no motion
    of its own: `kind` fixed, no presets, `move`/`nudge` refused by the
    commands ("has no motion of its own"); joystick moves addressed to it do
    nothing. With a USB device its status follows the device (worker
    `camera:<id>:usb`); without one it is `ok`.
17. **Settings**: `uvc` needs a `usb_device`; `uvc` and `none` need no host;
    an agent camera cannot have a `usb_device`. A camera's driver is rebuilt
    when its USB device changes (uvc/none only); image settings and stored
    positions never rebuild it. Camera setup keeps the saved `image` and
    `stored_positions` when a camera form is saved.

### UI

18. **Image page** `/cameras/image`: one card per camera with image settings,
    in the Cameras page's order: status, source, `preview-slot` with
    `data-camera`, a slider per control (greyed while on auto or offline),
    Auto boxes, errors, Lock, All auto, Reset (USB), Copy settings from. One
    card per row under 600 px. Links: "Image…" next to the selected camera on
    the Cameras page, and in Camera setup's header.
19. **Camera setup**: driver `uvc` and `none` for studio cameras, and a USB
    device picker (name, `#n` when names repeat, the port part of the path,
    "in use by …", "not plugged in now" for a saved device that is away).

### Rig

20. The rig's studio runs with `BAJA_STUDIO_UVC_BACKEND=fake`; its bundle has
    the fake's two S600s (`none`) and the Pixy (`uvc`). Scenarios reach the
    fake's switches through `simulate_usb {action: unplug | replug | scene |
    hand_move}`.

## Left open

- **Bench** ([docs/bench/wp6.md](../bench/wp6.md)) not run: the S600's real
  ranges and automatic controls, whether the Pixy moves while OBS has it open,
  the Pixy's top speeds and pan/tilt directions, zoom only at 1080p.
- **Settings version churn.** Every image change saves the cameras settings,
  so a Camera setup form opened before an Image-page change gets the usual
  conflict notice on Save ("Load latest"). Image settings could move to the
  module's own table if that bites at the event.
- `duvc-ctl>=2.1,<3` is a range, as the brief asked; every other dependency is
  pinned exactly. The installer's wheelhouse manifest records the version
  that shipped (2.1.0 today).
- `cameras/registry.py` is ~350 lines and `imaging/service.py` ~340.
