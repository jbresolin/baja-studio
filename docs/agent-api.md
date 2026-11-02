# Camera-laptop agent API — contract 1.0

Status: **draft for review** (LIVESTACK-33). Implemented by the studio service
(client, `server/baja_studio/agents/`) and by the AUTOPTZ hub app (server,
AUTOPTZ-21). The machine-readable twin of this document is
[`server/baja_studio/agents/contract.py`](../server/baja_studio/agents/contract.py);
the two change together.

The key words MUST, MUST NOT, SHOULD and MAY mean what RFC 2119 says.

## 1. Roles and versioning

- An **agent** runs on a trackside laptop, owns the cameras attached to it
  (VISCA PTZs, crop cameras) and runs their tracker. It only listens; it never
  connects to the studio.
- The **studio** (the studio service on the Streaming PC) connects to agents,
  reads their state and sends commands. Several studios MAY talk to one agent.
- The contract is versioned `MAJOR.MINOR`. This document is **1.0**. A MINOR
  release only adds (fields, endpoints, events, error codes); anything else is
  a MAJOR release. The studio refuses to command an agent whose MAJOR differs
  from its own, and shows that agent as `degraded` with the reason.
- Both sides MUST ignore JSON fields they do not know, in requests and in
  answers. That is what makes MINOR releases safe.

## 2. Transport

| | |
|---|---|
| Commands and configuration | HTTP/1.1, JSON bodies (`Content-Type: application/json`) |
| Live state | one WebSocket per studio, `GET /agent/ws`, agent → studio |
| Port | **8890**, on all interfaces |
| TLS | none (LAN only) |
| Discovery | static addresses in the studio's settings, plus a subnet scan that calls `GET /agent/info` on port 8890 of every address in a CIDR |

### 2.1 Headers sent by the studio

| Header | Meaning |
|---|---|
| `X-Agent-Token` | Shared token, if one is configured on the agent. |
| `X-Studio-Id` | Who is calling, e.g. `baja-studio@STREAMPC`. The agent records it in its command log and in `last_command_source`. |

### 2.2 Token

An agent MAY be configured with a token. When it is:

- every HTTP request without the right `X-Agent-Token` MUST be answered
  `401 {"error": "unauthorized", ...}`, **except `GET /agent/info`**, which
  stays open so that a subnet scan (which knows no tokens) can find the agent;
- a WebSocket handshake without the right token MUST be refused, preferably
  with an HTTP `401` answer to the handshake. A server that cannot answer the
  handshake with HTTP MAY close the socket before accepting it (code 4401).

The studio shows an agent that refuses its token as `degraded` ("the agent
refused our token"), not `offline`.

### 2.3 Common conventions

- **Units.** Velocities are normalised `-1..1` (1 = the camera's top speed).
  Pan and tilt positions are degrees from the device's home position; zoom is
  normalised `0` (wide) `..1` (tele). Crops are fractions `0..1` of the source
  frame. Device-specific tables (VISCA speed steps, zoom positions) stay
  inside the agent.
- **Timestamps** are ISO 8601 strings in UTC, e.g. `2026-11-01T17:46:24.164976+00:00`.
- **Errors** are always `{"error": "<snake_case_code>", "detail": "<sentence for an operator>"}`
  with the HTTP status from §9. A malformed body, a value out of range or a
  missing field is `400 bad_request` (not FastAPI's default 422).

## 3. Agent-level endpoints

### `GET /agent/info`

Who the agent is and which cameras it owns. MUST answer within **300 ms** and
have no side effects: the subnet scan calls it on every address. Needs no token.

```json
{
  "agent_id": "laptop-a",
  "name": "Trackside laptop A",
  "contract_version": "1.0",
  "software_version": "autoptz-hub 0.4.2",
  "capabilities": [],
  "cameras": [
    {
      "id": "cam1",
      "label": "Finish line PTZ",
      "kind": "ptz",
      "driver": "visca-ip",
      "has_position_feedback": true,
      "has_zoom": true,
      "supports_auto": true,
      "image_controls": ["exposure", "gain", "white_balance", "focus"],
      "presets": [{"n": 1, "label": "Start gate"}, {"n": 2, "label": "Jump"}],
      "limits": {"pan_speed_max": 100.0, "tilt_speed_max": 60.0, "zoom_speed_max": 0.5}
    }
  ]
}
```

- `kind`: `ptz` (moves), `crop` (a digital window on a fixed sensor), `fixed`.
- `limits`: top speeds at velocity 1.0, in degrees/s (pan, tilt) and zoom range/s.
- `presets` lists the stored presets. When they change, the agent sends a
  `presets_changed` event and the studio reads `/agent/info` again.
- `capabilities`: free-form feature flags. `fake_controls` marks the fake agent.

### `GET /agent/health`

```json
{"status": "degraded", "detail": "offline: cam2", "uptime_s": 5231.4,
 "cameras": {"cam1": "ok", "cam2": "offline"}}
```

`status` is `ok`, `degraded` or `offline`; `cameras` holds one status per camera.

### `GET /agent/log?limit=100`

The agent's own command log, newest first, `limit` from 1 to 1000 (default
100). Same shape as the studio's command log:

```json
[{"ts": "2026-11-01T17:46:24.162961+00:00", "module": "cam1",
  "source": "studio:baja-studio@STREAMPC", "command": "move",
  "args": {"pan": 0.4, "tilt": 0.0, "zoom": 0.0, "ttl_ms": 300}, "result": "ok"}]
```

`result` is `ok` or `error: <code>`. Every command in §6 is logged, including
refused ones; reads (`GET`) need not be.

## 4. Timings

| Rule | Value |
|---|---|
| Agent pushes a `state` message | at least every **0.5 s** (≥ 2 Hz), even when nothing changed |
| Agent sends the first `state` | as soon as it accepts a WebSocket |
| `GET /agent/info` answers within | **300 ms** |
| Studio pings over the WebSocket | every 2 s |
| Studio marks an agent `offline` | after **5 s** without receiving anything from it |
| Velocity `ttl_ms` | default **300**, from 1 to **2000** |
| Auto resumes after studio motion | `resume_after_ms`, default **3000** |
| Mode change visible in `state` | within one push cycle |

## 5. The state WebSocket: `GET /agent/ws`

The studio opens one WebSocket per agent and keeps it open. Agent → studio:

```json
{"type": "state", "ts": "2026-11-01T17:46:24.164976+00:00",
 "cameras": {"cam1": {
    "status": "ok",
    "mode": "auto",
    "tracker": {"state": "tracking", "target_id": "car-12", "confidence": 0.91},
    "position": {"pan": 12.5, "tilt": -3.0, "zoom": 0.4},
    "moving": true,
    "last_command_source": "studio:baja-studio@STREAMPC",
    "last_command_ts": "2026-11-01T17:46:20.001200+00:00"}}}
```

- `cameras` holds **every** camera from `/agent/info`.
- `status`: the device behind the agent, `ok`, `degraded` or `offline`.
- `mode`: `manual`, `auto`, `auto_paused` or `hold` (§7).
- `tracker.state`: `idle` (not in an auto mode), `searching`, `tracking` or `lost`.
- `position`: `null` for cameras without position feedback.
- `moving`: true while any axis is being driven.

```json
{"type": "event", "ts": "2026-11-01T17:46:24.170000+00:00", "camera_id": "cam1",
 "event": "mode_changed", "detail": {"from": "auto", "to": "auto_paused", "reason": "studio motion command"}}
```

| `event` | When | `detail` |
|---|---|---|
| `mode_changed` | any mode change | `from`, `to`, `reason` |
| `manual_override_started` | studio motion paused auto | `source` |
| `manual_override_ended` | auto took over again | `reason` |
| `tracker_acquired` / `tracker_lost` | tracker found / lost its target | `target_id` (acquired) |
| `device_offline` / `device_online` | the camera stopped / started answering | — |
| `presets_changed` | a preset was stored or renamed | `n` |

`camera_id` is `null` for events about the whole agent. Agents MAY send other
events; the studio logs events it does not know.

Studio → agent: only `{"type": "ping"}`. The agent MUST answer `{"type": "pong"}`.

The studio considers the link healthy (`ok`) only once a message has arrived
on the socket, so an agent that accepts connections but has hung is `offline`.

## 6. Per-camera commands: `/cameras/{id}/...`

An unknown `{id}` is `404 unknown_camera`. A command to a camera whose device
is offline is `503 device_offline`. Commands that move the camera or change
its configuration answer with what actually happened.

### Motion

**`POST /cameras/{id}/move`**: velocities with a time-to-live.

```json
{"pan": 0.4, "tilt": -0.1, "zoom": 0.0, "ttl_ms": 300}
→ 200 {"accepted": true, "mode": "manual"}
```

- Each axis is `-1..1`; an omitted axis is 0; `ttl_ms` is 1–2000 (default 300).
- The agent maps velocities to device speeds and **stops every axis when
  `ttl_ms` runs out** without a newer `/move`. This is the dead-man switch: if
  the studio link drops while a joystick is held, the camera stops anyway.
- A new `/move` before expiry replaces the velocity and extends the hold.
- All axes 0 is the same as `/stop`.
- `mode` in the answer is the mode *after* the command (`auto_paused` if the
  camera was in `auto`, §7).
- Only `kind=ptz` cameras move; others answer `400 not_supported`.

**`POST /cameras/{id}/stop`** → `200 {"accepted": true, "mode": "..."}`. Stops
every axis at once. It is never refused because of the mode; it can still be
refused for a bad token, an unknown camera or an offline device. Always logged.

### Presets (`kind=ptz`)

| Request | Answer |
|---|---|
| `POST /cameras/{id}/presets/{n}/recall` | `{"accepted": true, "mode": "..."}`; `404 unknown_preset` if not stored |
| `POST /cameras/{id}/presets/{n}/store` | `{"n": 3, "label": "Preset 3"}` (an existing label is kept) |
| `PUT /cameras/{id}/presets/{n}` `{"label": "Jump"}` | `{"n": 3, "label": "Jump"}`; `404 unknown_preset` if not stored |

`n` is 0–255; labels are 1–60 characters. Store and rename send `presets_changed`.

### Position

`GET /cameras/{id}/position` → `{"pan": 12.5, "tilt": -3.0, "zoom": 0.4}`, or
`null` when `has_position_feedback` is false.

### Mode

`GET /cameras/{id}/mode` → `{"mode": "auto", "profile": {"resume_after_ms": 3000}}`

`PUT /cameras/{id}/mode`:

```json
{"mode": "auto", "profile": {"resume_after_ms": 2000, "zone": "finish", "priority_targets": ["car-12"]}}
→ 200 {"mode": "auto", "profile": {"resume_after_ms": 2000, "zone": "finish", "priority_targets": ["car-12"]}}
```

- `mode` is `manual`, `auto` or `hold`. `auto_paused` cannot be requested
  (`400 bad_request`).
- `profile` is opaque to the studio except for these keys:
  `resume_after_ms` (whole ms, 0–600000, default 3000), `zone`,
  `priority_targets`. A profile replaces the previous one; leaving `profile`
  out keeps it.
- `auto` while the tracker cannot run → `409 {"error": "tracker_not_ready", ...}`.
- `auto` on a camera with `supports_auto: false` → `400 not_supported`.

### Image controls

`GET /cameras/{id}/image`:

```json
{"exposure": {"value": -6, "auto": true, "locked": false, "min": -13, "max": -1, "step": 1},
 "gain":     {"value": 0,  "auto": null, "locked": false, "min": 0,   "max": 255, "step": 1}}
```

The keys are exactly `CameraInfo.image_controls`. `auto` is `null` for a
control that has no automatic mode. `locked` means the agent will not change
the control on its own (e.g. an exposure scheduler).

`PUT /cameras/{id}/image` changes some controls. Each part is optional:

```json
{"gain": {"value": 300}, "exposure": {"auto": false, "value": -5}}
→ 200 (every control, read back from the device, as for GET)
```

The answer is what the device actually took: values clamped to the range,
snapped to the step, and ignored while `auto` is on. An unknown control
name is `400 unknown_control`, and then nothing is changed.

### Crop (`kind=crop`)

`GET /cameras/{id}/crop` → `{"x": 0.0, "y": 0.0, "w": 1.0, "h": 1.0}`

`PUT /cameras/{id}/crop` `{"x": 0.25, "y": 0.1, "w": 0.5, "h": 0.5}` → the
crop applied. All four are fractions of the source frame, `w` and `h` above 0,
and the crop must lie inside the frame (`x + w ≤ 1`, `y + h ≤ 1`), else
`400 bad_request`. On another kind of camera: `400 not_supported`.

### Resync

`POST /cameras/{id}/resync` → `{"accepted": true, "mode": "..."}`. Re-read the
device state, because the studio has been driving the camera directly while
the agent was unreachable (§8).

## 7. Mode semantics (normative)

**The studio always wins, and arbitration happens on the agent**, where
latency is lowest and which keeps working when the studio link drops.

| Mode | Who drives | Studio motion command | Returns to `auto` |
|---|---|---|---|
| `manual` | the studio only; tracker output ignored | executed | only on `PUT /mode {auto}` |
| `auto` | the tracker | executed, **and the mode becomes `auto_paused`** | — |
| `auto_paused` | the studio; the tracker keeps estimating but does not drive | executed; restarts the countdown | by itself after `resume_after_ms`, or at once on `PUT /mode {auto}` |
| `hold` | the studio only | executed | only on `PUT /mode {auto}` |

- *Studio motion commands* are `/move` (including all-zero moves), `/stop` and
  preset recall.
- The `resume_after_ms` countdown starts when the last studio motion **ends**:
  when its velocity's `ttl_ms` runs out, or at once for `/stop` and preset
  recall. A joystick held for ten seconds therefore pauses auto for ten
  seconds plus `resume_after_ms`.
- `PUT /mode {manual}` ends auto entirely. `PUT /mode {hold}` does too, and
  in addition the agent MUST NOT resume tracking on any trigger of its own
  (a button on the hub, a target re-acquired) until the studio sends
  `PUT /mode {auto}`. Operators use `hold` to park a camera on a shot.
- Pausing sends `mode_changed` and `manual_override_started`; resuming sends
  `mode_changed` and `manual_override_ended`. The new mode appears in `state`
  within one push cycle.

## 8. Ownership and fallback

- An agent owns the cameras it lists. While the agent is reachable, the
  studio never opens its own connection to those cameras.
- If an agent has been `offline` for longer than the studio setting
  `direct_fallback_after_s` (default 10 s), and the studio's entry for a camera
  has a `direct_fallback` (driver and address), the studio drives that camera
  directly (camera control, WP2). When the agent is back, the studio stops
  driving it within one push cycle and calls `POST /cameras/{id}/resync`.
- Several studios may command one agent. The agent accepts commands from all
  of them and logs `X-Studio-Id`; the last command wins. Arbitrating between
  studios is not a goal.

## 9. Failure behaviour

| Situation | Behaviour |
|---|---|
| Studio link lost while a joystick is held | Velocities expire after `ttl_ms`; the camera stops. Auto tracking is unaffected. |
| Agent restarts | The studio reconnects (after 0.5 s, then 1, 2, 4, 4 … s), reads `/agent/info` again (the camera list may have changed) and re-subscribes. It re-applies **nothing**: the agent restores each camera's last mode from its own configuration (`manual` if it does not know). |
| Agent silent for 5 s | The studio shows it `offline` (and the home page says which). |
| Device behind the agent offline | `CameraState.status = "offline"`; commands answer `503 device_offline`; the studio greys the camera out and shows the reason. |
| Different MAJOR version | The studio shows the agent `degraded` and sends it no commands. |

### Error codes

| HTTP | `error` | When |
|---|---|---|
| 400 | `bad_request` | malformed JSON, missing or out-of-range values, `auto_paused` requested |
| 400 | `not_supported` | the command does not apply to this kind of camera |
| 400 | `unknown_control` | an image control the camera does not have |
| 401 | `unauthorized` | missing or wrong `X-Agent-Token` |
| 404 | `unknown_camera` | no camera with that id |
| 404 | `unknown_preset` | preset not stored |
| 409 | `tracker_not_ready` | `auto` requested while the tracker cannot run |
| 503 | `device_offline` | the camera behind the agent is not answering |

## 10. Conformance

The contract is checked by a black-box test suite that ships with the studio
service. It passes against the fake agent in CI. **The acceptance criterion
for AUTOPTZ-21 is:**

```
cd server
pytest tests/agent_contract --agent-url http://laptop:8890 --agent-token <token>
```

Options: `--agent-token` (token checks are skipped without it) and
`--agent-camera cam1` (which PTZ to test; default: the first PTZ). Against a
real agent:

- the PTZ under test moves a little: slow, short velocity commands, each
  followed by `/stop`, and the camera is left in `manual`;
- a preset is stored at the highest free number;
- the mode tests need a running tracker. If the agent answers
  `tracker_not_ready` they are skipped with that reason; start the tracker and
  run them again;
- checks that need the fake agent's failure switches (an offline device, a
  tracker that is not ready) are skipped;
- crop checks run only if the agent has a `kind=crop` camera.

The suite runs in real time and takes about ten seconds.

A complete fake agent is also available for bench work without a laptop:

```
python -m baja_studio.fakes.fake_agent --port 8890 --cameras 2 [--crop-cameras 1] [--token T]
```

## 11. Changes

| Version | Date | Change |
|---|---|---|
| 1.0 | 2026-11-01 | First version (LIVESTACK-33). |
