# WP6a bench session: image settings, the S600s and the Pixy

The checklist is §6 of the WP6a brief (Plane page "19 — WP6a brief"). Record
the results in a comment on **LIVESTACK-18**. Decisions are in
[ADR 0019](../adr/0019-image-settings-and-pixy.md).

On the studio PC (installed studio, Windows): two EMEET S600s and the EMEET
Pixy on USB, each on its own port, OBS open with the S600s as sources. In
the **EMEET app, turn the Pixy's AI tracking off** before starting (it moves
the camera without UVC knowing, see step 7).

Pages: **Camera setup** (`/cameras/config`), **Image** (`/cameras/image`,
also "Image…" on the Cameras page and in Camera setup's header), **Cameras**.

Without the hardware: start the studio with `BAJA_STUDIO_UVC_BACKEND=fake`
(two S600s and a Pixy, `fakes/fake_duvc.py`), or run the rig.

## Steps

| # | Do | Expect / record |
|---|---|---|
| 1 | Camera setup → Add camera, driver *none (no motion)*, open **USB device**. | All three cameras listed: two "EMEET SmartCam S600 #1/#2" with **different port parts** and the Pixy. Record the names exactly as listed. Add `s600-a` and `s600-b` (driver none), and `pixy` (driver *USB PTZ*, controller address free). Home card: "s600-a ok · s600-b ok · pixy ok · usb ok (3 USB cameras)". |
| 2 | Image page. | Three cards. Record, per camera, **every control with its range, step, default and whether it has an Auto box** (a screenshot of each card is enough). Does any control the S600 really has on auto lack the Auto box, or the reverse? |
| 3 | On `s600-a`: exposure Auto off, drag exposure back and forth for 10 s; white balance Auto off, set 5000. Meanwhile move the Pixy with the web stick from another tab. | The OBS picture follows while dragging, and ends where the slider stopped. The Pixy does not stutter. The settings version on Camera setup goes up once per drag, not per step. |
| 4 | Unplug `s600-a`'s USB cable; wait 5 s; plug it back into **the same port**. Then pull and replug it **as fast as you can** (under a second). | Card offline "…not plugged in…" meanwhile, home card amber; within ~3 s of replugging the exposure and white balance from step 3 are back (check in OBS and on the card). After the quick replug too (the log says "USB camera reopened after a replug"). |
| 5 | Plug it into **another** port. | Card: "…moved to another port? pick it again in Camera setup". Pick it again in Camera setup → settings applied. Note whether the path changed (it should). |
| 6 | Lock: point `s600-a` at a window, all on auto; press **Lock**, then cover the window / change the light. | "Locked: exposure, …" lists the controls; the picture no longer adapts. **All auto** → it adapts again. **Record whether pressing Lock freezes the picture as it was, or makes it jump**: many UVC cameras do not update the exposure/white-balance readback while on auto, and then Lock saves a stale value. Note the values the card showed before and after Lock, and whether they changed while the light changed on auto. |
| 7 | Copy: tune `s600-a`; on `s600-b`'s card pick *Copy settings from… S600 A*, Copy. | The two pictures match in OBS. Any "this camera has no …" message noted. |
| 8 | Pixy from the Cameras page: web stick, arrow keys, Z/X zoom (capture at **1080p** in OBS, zoom does nothing at 4K). | Smooth motion, stops on release. Record: are left/right and up/down the right way round (else tick Invert in Camera setup)? Top speed feel at full stick (60°/s pan, 45°/s tilt). Zoom 100–150 works at 1080p. |
| 9 | Pixy presets: store 1 (some pan, tilt and **zoom**), move away, recall 1; restart the service (tray → Restart) and recall 1 again. | Same framing, zoom included, both times. The position shown is "commanded" (it is where the studio told it to go). |
| 10 | With the Pixy open in OBS (and the preview visible), move it from the web page. Then close OBS's use of it and move again. | **Record whether the Pixy can be moved while OBS has it open.** If not: the error shown, if any. |
| 11 | Move the Pixy by hand (or let AI tracking move it briefly). | The page's position does not change (UVC cannot tell); the next web move continues from where the studio last put it, not from where the hand left it. |
| 12 | Kill the UVC child: Task Manager → the `python.exe` whose command line has `uvc_child` → End task. | Home card briefly "usb offline", then back within a few seconds; the S600 settings are reapplied (change one by hand in the EMEET app first to see it come back). |
| 13 | Agent camera image controls with the fake agent: `python -m baja_studio.fakes.fake_agent --port 8890`, add it on the Agents page, add its `cam1` as an agent camera. | Its card on the Image page ("agent …"), no Reset button. Set gain; stop the fake agent (card offline), start it again: the gain comes back. |

## Also worth noting

- The DirectShow paths of the three cameras (Camera setup shows the port
  part; the full path is in the service log, "USB camera plugged in: …").
- Anything the EMEET app changes behind the studio's back while the studio
  runs (does the studio's value win on the next replug?).
- CPU of the `uvc_child` process while the Pixy is held moving (20 writes/s).
