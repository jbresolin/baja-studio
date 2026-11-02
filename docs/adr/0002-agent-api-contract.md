# ADR 0002 — WP1: camera-laptop agent API contract and client

Date: 2026-11-01. Work item: LIVESTACK-33. Brief: Plane page "04 — WP1 brief".
Contract: [docs/agent-api.md](../agent-api.md), version 1.0.

## Decisions taken beyond the brief

### Contract details the brief left open

1. **`GET /agent/info` needs no token.** A subnet scan knows no tokens, so
   it could not otherwise find a token-protected agent. Every other endpoint
   and the WebSocket require it when one is configured.
2. **WebSocket auth failure** refuses the handshake with HTTP 401, or closes
   the socket before accepting it (code 4401) if the server cannot answer the
   handshake with HTTP. The studio shows such an agent `degraded`
   ("the agent refused our token"), not `offline`.
3. **The agent sends a `state` message as soon as it accepts the
   WebSocket**, and the studio calls a link `ok` only once a message has
   arrived. Otherwise an agent that accepts connections but has hung would
   flap between `ok` and `offline` every few seconds. The tests caught this.
4. **Units**: pan/tilt positions in degrees from the device's home; zoom
   0 (wide) to 1 (tele); `limits` in degrees/s and zoom range/s.
   **Timestamps** are ISO 8601 UTC strings everywhere, as in the studio's own
   command log.
5. **`resume_after_ms` counts from the end of the last studio motion**
   (velocity expiry, or the moment of `/stop` and preset recall), not from
   when the command arrived. A held joystick keeps auto paused for as long as
   it is held.
6. **"`/stop` is never rejected"** is read as: never refused because of the
   mode. A bad token, an unknown camera or an offline device still refuse it.
7. **`hold` vs `manual`**: both leave the studio in charge. `hold` also
   forbids the agent from resuming tracking on its own triggers (hub button,
   target re-acquired) until `PUT /mode {auto}`.
8. **Error codes** beyond the brief's list: `bad_request`, `not_supported`
   (e.g. `/crop` on a PTZ), `unknown_control`, `unknown_camera`,
   `unknown_preset`, `unauthorized`. Validation errors are 400, not FastAPI's
   default 422.
9. **Events**: `mode_changed`, `manual_override_started`/`_ended`,
   `tracker_acquired`/`_lost`, `device_offline`/`_online`, and a new
   `presets_changed`, which tells the studio to re-read `/agent/info` (presets
   are listed there).
10. **Image controls are read back with their range** (`min`, `max`,
    `step`), and `auto: null` for controls without an automatic mode, so a UI
    can draw sliders without knowing the device. They are raw device values,
    not normalised: image settings are LIVESTACK-3's to shape.
11. **Preset numbers are 0–255** on the wire, and 0 to `limits.preset_max`
    per camera (item 13); labels 1–60 characters.
12. **Both sides ignore unknown JSON fields** (requests and answers), which
    is what makes MINOR versions safe.

13. **Amendments folded into 1.0 before it was pinned** (still one 1.0
    changelog entry, as asked). The brief's wording was turned into rules
    as follows:
    - **`/move` on crop cameras.** Pan and tilt move the window centre, zoom
      shrinks or grows the window around it, keeping its shape; positive
      tilt is up, as on a PTZ, so it makes the centre's y smaller. A crop
      camera's `position` is that centre in frame fractions, with
      `has_position_feedback: true`, and its `limits` speeds are frame
      fractions/s. How zoom maps to window size is the agent's (the fake:
      zoom 0 is the whole frame, 1 a window a tenth of it). `PUT /crop` is a
      motion command like a preset recall: the window jumps, any velocity
      stops, auto pauses and the countdown starts at once.
    - **`limits.preset_max`** is required and nullable: `null` for cameras
      without presets. A preset above it is `400 bad_request`. The fake's
      PTZs stop at 127, below the wire's 255, so the conformance suite's
      "highest free number ≤ `preset_max`" is really exercised.
    - **"State stale"** is `degraded` with that reason, checked by the same
      watchdog as the offline rule (one task, so fake-time tests still
      settle). It is cleared by the next `state`. An agent that answers
      pings but pushes no state stays `degraded` and never goes `offline`.
    - **Restore after restart.** A pause does not survive a restart:
      `auto_paused` comes back as `auto`. The `mode_changed` sent when auto
      cannot be restored would reach nobody, because a restart closes every
      socket. So the agent replays it, with its original `ts`, on each
      WebSocket it accepts, until the mode next changes or a studio sets
      it with `PUT /mode` (even to the same mode); the studio's
      registry ignores an event it has already recorded. The fake has a
      `/_fake/restart` switch for this, and a `/_fake/freeze_state` switch
      that stops state pushes but keeps pongs and events flowing.

### Studio side

14. **Layout.** The client library is `baja_studio/agents/` (`contract`,
    `transport`, `client`, `subscription`, `scan`, `registry`). The `Module`
    itself lives in `modules/agents/`, as the conventions require. The brief
    put the Module in `agents/registry.py`; there, `registry.py` is the
    `AgentRegistry`, the async core the module runs.
15. **HTTP library is `httpx2`, not `httpx`.** It is the same API and the one
    Starlette 1.10 already uses. It moved from the dev to the runtime
    dependencies.
16. **The module runs one worker holding an asyncio event loop** with every
    agent link on it. Settings changes are handed over with
    `call_soon_threadsafe`. Scan and probe commands run their own short
    `asyncio.run` on the command thread.
17. **Core additions:** `Clock.sleep` (async), so coroutines also run on the
    fake clock in tests; `WorkerContext.report_health`, so a worker that keeps
    running while some of its devices are away can say so (the home card
    shows "1 of 2 agents away: laptop-b offline"); secrets nested in lists of
    models are masked too, and a masked secret sent back is matched to its
    item by `id`.
18. **An extra `update` command** (host, port, token, enabled) beside the
    brief's `scan`, `add`, `remove` and `probe`. The Agents page needs it to set
    a token and to enable or disable an agent.
19. **Reconnect backoff is 0.5, 1, 2, then every 4 s**, kept under the 5 s
    offline rule. An agent that answers but cannot be used (wrong version or
    token) is heard on every retry and stays `degraded`, without flickering to
    `offline`.
20. **A scan shares one HTTP client** for all addresses. Creating an httpx2
    client blocks the event loop for about 12 ms (it loads the trust store);
    one per address used up every probe's 300 ms budget and found nothing.
    The end-to-end check caught this; there is now a regression test.
21. **`X-Studio-Id` is `baja-studio@<hostname>`.**
22. **Link statuses** are `connecting`, `ok`, `degraded` (wrong version or
    token, or state stale), `offline`, plus `disabled` for agents switched
    off in settings. The module is `offline`
    when every enabled agent is away and `degraded` when some are.

### Fake agent and tests

23. **The fake agent** (`fakes/fake_agent.py`, routes in
    `fake_agent_routes.py`, cameras in `simulated_ptz.py`) also takes
    `--crop-cameras` and `--contract-version`, and announces itself with the
    capability `fake_controls`. Its simulated camera is pure code told the
    time on every call, so the mode rules are unit-tested without any clock.
    Each `/_fake` switch has a matching contract behaviour: go silent, drop
    WebSockets, delay answers, take a device offline, set the tracker state.
24. **In-process transport.** The brief asked for the fake to be reached
    "through an in-process ASGI transport". Each fake runs on its own thread
    and event loop (`FakeAgentRunner`), like a separate laptop.
    `InProcessTransport` carries HTTP (httpx2's ASGI transport) and the
    WebSocket (ASGI messages over queues) to it. Tests can unplug an agent
    and plug a new one in at the same address, with no ports opened.
25. **The conformance suite runs in real time.** It is the one exception to
    "no sleep-based timing": a real agent's clock cannot be faked. It waits
    for things to happen, with deadlines of one push cycle plus a margin,
    never fixed sleeps. Checks that need the fake's switches are skipped
    against real agents, and mode checks are skipped if the real tracker
    answers `tracker_not_ready`. It takes about 8 s. It was also run over a
    real network, with the fake served by uvicorn on loopback and
    `--agent-url`.
26. **Fake-time tests advance in quarter-second steps** (`advance_in_steps`)
    and wait after each step until every woken coroutine sleeps again.
    Jumping five seconds at once would let a healthy agent look silent.
    "Goes offline" checks allow one second of delivery slack, because the
    rule counts from the last message *received*.

## Left open

- **Review and pinning.** João reviews `docs/agent-api.md`; it is then pinned
  into the AUTOPTZ repo, and AUTOPTZ-21 is updated to reference it and the
  conformance command. Not done in this WP.
- **"Offline within 5 s" vs "5 s of silence".** Offline comes 5 s after the
  last message received. Measured from killing an agent, that is 5.0–5.5 s
  (5.1 s in the end-to-end run), because of the push interval. The brief's
  checklist says "within 5 s". Either accept that reading, or lower the
  threshold to 4.5 s.
- **Not covered by conformance:** "a resent `/move` extends the hold" (it is
  covered by the fake's unit tests, but hard to time black-box), and "the
  agent restores each camera's last mode after a restart" (needs a restart
  the suite cannot cause).
- **`direct_fallback_after_s`** is stored and validated, but nothing uses it
  until camera control (WP2) adds `direct_fallback` to camera entries.
- **The scan probes port 8890 only.** An agent configured on another port
  must be added by address.
- **The Agents page is covered by Vitest only.** It has not been clicked
  through in a real browser yet.
- **Files over the ~300-line guideline:** `fakes/simulated_ptz.py` (~410
  after the 1.0 amendments; its image controls and crop window are already
  split out to `simulated_image.py` and `simulated_crop.py`, and what is left
  is one camera whose mode, motion, preset and restart rules change
  together), and
  `core/supervisor.py` (~370 after `report_health`). Revisit if they grow.
