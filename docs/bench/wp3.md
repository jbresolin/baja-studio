# WP3 bench session: camera pages, SMTAV and RS485 joystick

The checklist is §7 of the WP3 brief (Plane page "06 — WP3 brief"). It
includes the WP2 bench ([wp2.md](wp2.md)), which has not been run yet.
Record the results, the raw capture, the speed-step findings and the
position readings in a comment on **LIVESTACK-15**.

Start the service (`python -m baja_studio`). Open
`http://<streaming-pc>:8888/cameras/config` (**Camera setup**) and
`/cameras` (**Cameras**). Each page shows its own id in the footer, e.g.
`web:3fa1c2d4`. The command log names that page's actions with the same id.

Without hardware: `python -m baja_studio.fakes.fake_visca_camera --port 52381`
plays the SMTAV (host `127.0.0.1`), and `python -m baja_studio.fakes.fake_agent
--port 8890 --cameras 1` plays an agent.

## Steps

| # | Do | Expect / record |
|---|---|---|
| 0 | Start from a blank settings file (rename the data folder's database). On **Camera setup**: **Add camera**, id `smtav`, host `192.168.1.188`, controller address `1`, Save. **Add controller**: id `desk`; pick the port from the list (the CP2102N shows as *Silicon Labs CP210x*, hwid `10C4:EA60`); baud 2400; Save. | Both appear with `ok` within a few seconds. Nothing typed as JSON. *(brief step 9, first half)* |
| 1 | Run WP2 §8 steps 1–8 ([wp2.md](wp2.md), "2–8. What to look at"). Step 1 is step 0 above. Where wp2.md says "Modules → cameras → state", the Cameras page now shows owner, mode and route. | As in wp2.md. |
| 2 | **Frame monitor** (Camera setup, under *desk*): hold the stick still for 5 s, then let go and wait 5 s. | Note: do **move** frames keep coming while held? do **stop** frames keep coming at rest? If moves repeat, set *Controller silence* to 2000 (Motion). Then press **Copy last 100 raw frames** while moving the stick through every direction, zoom, a preset and Call, and paste the text into the LIVESTACK-15 comment (fixtures). |
| 3 | **Speed probe**: edit `smtav`, *Top pan speed step* 24, *Top tilt speed step* 20, Save. Pan and tilt at full deflection. Then tilt 23, again. | Which tilt step the SMTAV accepts (moves faster than the one below, does not ignore or overshoot). With the position readout on the Cameras page, time pan and tilt over a known angle at full and half deflection: °/s. |
| 4 | **Web joystick, desktop**: on Cameras, arrows (one, then two at once), Z/X, drag the on-screen stick; let go. Then hold → and close the tab. | Every direction right; the camera stops within 300 ms of letting go. Closing the tab while held: it stops by itself (ttl) within 300 ms. |
| 5 | **Phone** on the event Wi-Fi: open `/cameras`. | Camera dropdown at the top; the stick fills the width; touch-drag moves; presets scroll in one row and recall on tap; long-press stores. |
| 6 | **Two clients**: desktop holds →; phone drags the stick. | Phone shows "being moved by web:…" briefly; its release does **not** stop the desktop's move. When the desktop lets go, the phone's next drag moves the camera. |
| 7 | **Joystick vs web**: web holds a key, move the stick. Then hold the stick and press **Stop** (or Space) on the page. | Stick refused while the web holds (the log shows the refusal once); after the web lets go, the next stick *movement* takes over. Web Stop stops the joystick's camera. |
| 8 | **Fake agent camera**: start the fake agent, add it on the Agents page (`127.0.0.1`, id `laptop-a`). On Camera setup add `trackside`, owner *agent*, agent `laptop-a`, its camera `cam1`, controller address 2. On Cameras: Manual / Auto / Hold. Then, while in Auto, hold an arrow. | Mode badge follows; arrow → `auto_paused`, then back to `auto` after *Resume after*. To see the 409: put the fake's tracker in `not_ready` (`curl -X POST localhost:8890/_fake/cameras/cam1/tracker -H "Content-Type: application/json" -d "{\"tracker\": \"not_ready\"}"`), set Manual, then Auto — the agent's reason is shown under the mode buttons. |
| 9 | **Config live**: change `smtav`'s host to a wrong IP, Save; then back. | `offline` within ~3 s, `ok` again after the fix, no restart. *(brief step 9, second half)* |
| 10 | **Presets**: on Cameras, Shift+click slot 3 (or Shift+3) to store, move away, click slot 3 (or 3) to recall; ✎ to rename it "Jump". From the controller, press preset 3. Bind *Call 2* to *recall preset 3 on selected camera* (Camera setup → desk → Edit) and press Call 2. | Recall works from page, keys and controller; the name shows on every page and survives a restart. |

## Also worth noting

- **Position readings** at a few known angles (pan, tilt, zoom), as in wp2.md,
  so `DEGREES_PER_PAN_TILT_STEP` and `ZOOM_POSITION_TELE` in
  `cameras/drivers/visca_packets.py` can be set from measurements.
- After the session, a small follow-up commit:
  - replaces `SNIFFED_FRAMES` in `fakes/fake_serial.py` with the raw capture;
  - sets the default top speed steps and position units from the readings;
  - sets `controller_silence_ms` if the controller repeats.
