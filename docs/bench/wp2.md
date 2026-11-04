# WP2 bench session: SMTAV and RS485 joystick

The checklist is §8 of the WP2 brief (Plane page "05 — WP2 brief"). This
page gives the settings and commands for each step; it was written before
the camera pages (WP3), which now do most of it. The WP2 steps are run as
part of the WP3 bench ([wp3.md](wp3.md)); record the results as a comment
on LIVESTACK-15 (and LIVESTACK-13).

Start the service as usual (`python -m baja_studio`). The examples use
`localhost:8888`; on Windows PowerShell, use `curl.exe`.

## 1. Configure

Since WP3, cameras and controllers are added on **Camera setup**
(`/cameras/config`); see [wp3.md](wp3.md). For reference, the equivalent
settings are:

```json
[{"id": "smtav", "label": "SMTAV", "owner": "studio", "driver": "visca_ip",
  "address": {"host": "192.168.1.188", "port": 52381}, "controller_address": 1}]
```

```json
[{"id": "desk", "port": "COM8", "baud": 2400}]
```

Or do it in one request:

```
curl -X PUT localhost:8888/api/settings/cameras -H "Content-Type: application/json" -d "{\"values\": {\"cameras\": [{\"id\": \"smtav\", \"address\": {\"host\": \"192.168.1.188\"}, \"controller_address\": 1}], \"controllers\": [{\"id\": \"desk\", \"port\": \"COM8\"}]}}"
```

## 2–8. What to look at

| Step | Do | Expect |
|---|---|---|
| 2 | Open the home page | **Cameras** card green, "smtav ok · desk ok". Unplug the RS485 adapter: within 2 s "desk offline (COM8 lost …)". Replug it: `ok` again about a second later. |
| 3 | Joystick: eight directions, zoom in and out; let go; half then full deflection | The camera moves the right way (right/up positive; fix with `limits.invert_pan` / `invert_tilt` if not); it stops within 300 ms of letting go; half deflection is slower. **Also: hold the stick perfectly still for 10 s; the camera keeps moving** (the joystick latches, ADR 0003 decision 15). Then pan with the stick while a web/API move holds another camera, and leave the stick at rest while the API moves this one (step 6 with `"ttl_ms": 2000`): the API move must not be cut short by the controller. |
| 4 | Hold the stick, pull the camera's network cable, let go, plug it back | The card turns "smtav offline" about a second after the pull; `ok` after the replug; the camera is stopped, not running away. Then hold the stick and unplug the RS485 adapter: the camera stops at once. |
| 5 | Press a preset button; press Call | The preset is recalled (button 1 = VISCA preset 0). Modules → cameras → state shows `controllers.desk.last_call`. |
| 6 | `curl -X POST localhost:8888/api/commands -H "Content-Type: application/json" -d "{\"module\": \"cameras\", \"command\": \"move\", \"args\": {\"camera\": \"smtav\", \"pan\": 0.3, \"ttl_ms\": 300}, \"caller\": \"bench\"}"` | It pans briefly and stops by itself. |
| 7 | Below | — |
| 8 | Modules → cameras → recent commands, or `curl "localhost:8888/api/commands?module=cameras"` | Every action, with its source: `joystick:desk`, `api:bench`, `web:…`. |

### Step 7: fallback

1. Start a fake agent: `python -m baja_studio.fakes.fake_agent --port 8890 --cameras 1`.
2. Add it on the Agents page (host `127.0.0.1`, id `laptop-a`).
3. Replace the cameras with one agent-owned camera, with the SMTAV as its
   direct fallback, answering to the joystick:

```json
[{"id": "trackside", "owner": "agent", "driver": "agent",
  "address": {"agent_id": "laptop-a", "remote_camera_id": "cam1"},
  "controller_address": 1,
  "direct_fallback": {"driver": "visca_ip", "address": {"host": "192.168.1.188"}}}]
```

4. Kill the fake agent (`Ctrl+C`). About 15 s later (5 s to go offline,
   then `direct_fallback_after_s` = 10 s), the camera's `route` becomes
   `fallback` and the joystick drives the SMTAV directly.
5. Start the fake agent again. The route goes back to `agent`, and
   `curl localhost:8890/agent/log` shows a `resync`.

## Worth noting while you are there

- **Position readings.** Note the raw pan, tilt and zoom at a few known
  angles, so the constants in `cameras/drivers/visca_packets.py` can be set
  from measurements.
- **Top speed steps (probe).** Full deflection sends the camera's
  `limits.pan_speed_max_step` / `tilt_speed_max_step`, default 0x18 / 0x14
  (24 / 20); the VISCA spec allows up to 0x18 / 0x17. In Settings, give the
  camera `"limits": {"pan_speed_max_step": 24, "tilt_speed_max_step": 23}`
  and pan and tilt at full deflection, then try `18` / `17` (what
  ptz_test.py used). Note the highest steps that are faster than the step
  below and that the camera neither ignores nor overshoots. Those become the
  defaults in `cameras/drivers/visca_packets.py`. Edits apply while moving;
  no restart.
- **Does the controller repeat frames?** With the service stopped (it
  holds the port), watch the port with a serial monitor or ptz_test.py's
  sniffer while holding the stick still. If
  frames keep coming, set `motion.controller_silence_ms` to `2000`, then
  hold the stick and pull the RS485 wire (not the USB side): the camera
  should stop within about 2 s. If frames stop when the stick is still,
  leave it at `0`.
- **Raw frames.** If you can, capture a minute of raw controller bytes, to
  replace the rebuilt fixtures in `fakes/fake_serial.py`.

Without hardware, `python -m baja_studio.fakes.fake_visca_camera --port 52381`
plays the SMTAV on the same PC (use host `127.0.0.1`).
