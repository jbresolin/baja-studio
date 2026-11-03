# ADR 0003 — WP2: camera registry, drivers, Pelco-D input and the motion arbiter

Date: 2026-11-02. Work item: LIVESTACK-13. Brief: Plane page "05 — WP2 brief".
Reference: `ptz_test.py` (March 2026), the Pelco-D → VISCA bridge used at the
last event. It was not added to the repository.

## Decisions taken beyond the brief

### Layout and core

1. **Library and module are separate**, as in WP1. The library is
   `baja_studio/cameras/`:
   - `config` (the registry's settings models), `driver` (the interface),
     `health`, and `registry` (applies settings: builds and removes drivers
     and controllers);
   - `drivers/` holds `visca_packets` (bytes), `visca_link` (the protocol
     logic, told the time), `visca_ip` (the worker and socket),
     `agent_gateway`, `agent_commands` and `agent_proxy`;
   - `inputs/` holds `pelco_d` (the parser) and `pelco_d_worker`;
   - `motion/` holds `shaping` and `arbiter`.

   The `Module` is `modules/cameras/`. It holds the lifecycle, the motion
   loop and the commands.
2. **Supervisor additions.** `start_worker` / `stop_worker` / `worker_names`
   let a running module start and stop single workers: one per camera
   driver, one per controller. `set_health_summary` lets a module compute
   its health from its workers' healths. A worker's callbacks are bound to
   its record, not its name, so a late report from a stopped worker cannot
   change a new worker with the same name. The worker-side types moved to
   `core/worker.py`, which brings `supervisor.py` back to about 330 lines.
   `supervisor.py` re-exports them, so no import changed.
   `ModuleContext.start_worker` / `stop_worker` wrap the new calls.
3. **Health is per device.** Worker names say whose they are:
   `camera:<id>:visca|agent|fallback`, `controller:<id>` and `motion`.
   The module is `ok` when every device is, `offline` when every device is
   away, and `degraded` otherwise. A failed motion loop always makes it
   `degraded`. The home card lists the devices, e.g.
   "smtav ok · desk offline (cannot open COM8: …)".
4. **The motion worker is the one that applies the settings** when it
   starts. Module workers cannot be started from `Module.start`, because
   the supervisor marks the module running only afterwards. A crashed and
   restarted motion loop re-applies them, and that is harmless because
   applying is idempotent.
5. **Workers are stopped outside the registry lock.** Stopping a driver
   waits for its worker, and those workers take the same lock to publish
   or route a frame. Holding the lock while waiting would deadlock until
   the join timeout ran out.

### VISCA over IP

6. **The packets are byte for byte those of ptz_test.py**, and the tests
   spell out every one of them in hex. Kept as they were:
   - the zoom speed range 2–7;
   - preset numbers masked to 7 bits;
   - no "reset sequence number" control command.

   The pan and tilt speed ranges are the one deliberate change. ptz_test.py
   clamped to 18 / 17 decimal (0x12 / 0x11), probably hex read as decimal:
   the VISCA spec gives 0x01–0x18 / 0x01–0x17. Packets are clamped to the
   spec, and the top step a velocity of 1.0 reaches is per camera,
   `limits.pan_speed_max_step` / `tilt_speed_max_step` (defaults 0x18 /
   0x14, read live), so the bench can probe it without a code change. It
   applies to direct VISCA/IP drivers and an agent camera's direct fallback.

   Two ptz_test.py reply-parser bugs were fixed and are covered by tests:
   `> 0x8000` read 0x8000 as positive, and the length check was shorter
   than the bytes it then read.
7. **Velocity to speed step** is `min + round_half_up(|v| × (max − min))`.
   Python's `round` rounds halves to even, so it is not used. The default
   joystick zoom velocity (0.6) gives zoom speed 5, the speed ptz_test.py
   always used.
8. **Replies are matched by sequence number.** The camera's ACK or its
   completion counts as the reply; the second one of the pair is ignored. A
   packet unanswered after 0.5 s is a miss. Three misses in a row make the
   camera `degraded`, ten make it `offline`. Any reply makes it `ok` again.
   While it is offline, the socket is reopened every 5 s.
9. **Position is polled twice a second** (pan-tilt and zoom inquiries).
   The poll also tells an idle camera's health. With nothing held, an
   unplugged camera is `offline` after about 2.5 s.
10. **A stop is repeated until the camera answers it**, every resend
    interval. If a stick is released while the cable is out, the stop lands
    when the cable is back and the camera does not run away. This finishes
    the operator's release; it is not a "stop on reconnect".
11. **The driver repeats a held velocity by itself** every
    `resend_interval_ms`, and stops when its TTL runs out. Changing the
    velocity sends it at once. The arbiter's own repeats only extend the
    TTL, so they cost no extra packets.
12. **Socket errors are lost packets.** A send error is swallowed, and the
    missing reply reports it. A Windows ICMP reset on receive is also
    swallowed. An address that cannot be opened (a name that does not
    resolve) raises `DeviceOfflineError`, so the camera is offline, not
    crashed.

### Pelco-D

13. **The flags are those sniffed from the real controller.** cmd2 with
    bit 0 set is a command, not a motion. That is how 0x03 (recall) and
    0x07 (Call) are told apart from the direction bits.
    - Pan and tilt can be combined with zoom in one motion; ptz_test.py
      ignored pan and tilt when a zoom flag was set.
    - Direction flags with speed 0 mean "at rest".
    - Controller preset 0 is ignored, because the controller counts from 1.
14. **A bad checksum drops one byte, not seven.** The parser resyncs at the
    next 0xFF, so a real frame right after a stray 0xFF is not lost.
15. **The joystick is a latched source.** Pelco-D says what the stick
    does, not for how long, and we do not know yet whether this controller
    repeats frames while a stick is held. So a move frame *latches* its
    velocity in the arbiter, with no TTL. It holds until:
    - a stop frame;
    - a different frame (a new direction or speed replaces the velocity
      without a stop; a preset, Call or unknown frame releases it);
    - a frame for another camera (the operator selected a different
      address), or the camera's address changing in the settings;
    - the serial link going: a port error, the controller's worker stopping
      (removed from the settings, shutdown), or, if
      `motion.controller_silence_ms` is set, no frame for that long.

    While latched, the arbiter re-sends the velocity to the driver every
    `resend_interval_ms` with a full `ttl_ms`, so an agent's 300 ms dead-man
    stays satisfied and a VISCA camera keeps its own. A stick held perfectly
    still never times out. Identical consecutive frames change nothing.

    `controller_silence_ms` is 0 (off) by default. It only makes sense for a
    controller that repeats frames while a stick is held; set it to 2000 if
    the bench shows ours does. With it off, a lost link is caught by the
    port error, or not at all if the RS485 wire is cut but the USB adapter
    stays: the price of not knowing the controller yet.
16. **Joystick stops are scoped to the joystick.** Every stop frame goes to
    the arbiter as a full stop, and none is dropped as a repeat. It stops
    the camera **only if the joystick owns it** (holds or latched its
    velocity). An idle controller repeating "stop" therefore does not cut
    into a move from the web page, and does not send `/stop` to an
    agent-owned camera, which would pause auto (contract §7). Stops from the
    web page or the API are always sent, latch or not.
17. **The serial worker reopens the port itself, once a second.** It does
    not raise, so it never falls into the supervisor's backoff, which grows
    to 30 s. A replugged adapter is back within about a second.
18. **The command log gets joystick actions, not frames.** It records the
    start of a move (not every speed change), each stop that was carried
    out, preset recalls (refused ones too) and Call presses, all with source
    `joystick:<controller id>`.
19. **The fixtures are rebuilt from the sniffed flags.** ptz_test.py logged
    flags, not raw captures, so `SNIFFED_FRAMES` (in `fakes/fake_serial.py`)
    are frames built from those flags with the controller's checksum. A raw
    capture from the bench should replace them.

### The arbiter

20. **Shaping is done in the arbiter, not in the drivers**, so every driver
    gets it, WP6's included. The order is deadband, inversion, scale, curve.
    - The deadband rescales what is outside it, so motion starts from 0
      just past the deadband.
    - The deadband applies to joystick sources only, and not to zoom, since
      the zoom buttons send a fixed speed.
21. **Holding a velocity.**
    - While a velocity is held, the arbiter passes it to the driver every
      `resend_interval_ms`, with the TTL it has left (a full `ttl_ms` when
      latched, decision 15).
    - A changed velocity goes out at once.
    - When the driver's route changes (agent ↔ fallback), the held velocity
      is resent at once.
    - A move that shapes to zero ends that source's own hold. It does not
      touch anyone else's.
22. **Presets.** A preset recall ends the held velocity and obeys the
    holder rule. A preset store does not move the camera, so anyone may
    store. Preset numbers are checked against the driver's `preset_max`
    (127 for VISCA; an agent camera's comes from `/agent/info`).

### Agent-owned cameras and fallback

23. **`AgentsModule` is the `AgentGateway`.**
    - `link(agent_id)` reads its published state.
    - `submit(agent_id, call)` runs `call(client)` on its event loop.
    - `direct_fallback_after_s()` reads its setting.

    `build_module_list` hands the agents module to the cameras module.
24. **Commands to an agent go one at a time, in order.** A stop must not
    overtake the move before it. A move waiting behind another is replaced,
    and a stop drops the moves still waiting. While the link is not `ok`,
    motion is dropped: the agent's TTL has stopped the camera already, and a
    backlog would replay old motion. A `set_mode` is still sent, so its
    caller gets an answer.
25. **The fallback starts only from `offline`.** It starts once the link has
    been `offline` for `direct_fallback_after_s`. `degraded` (stale state,
    wrong token) and `connecting` do not count.
    - While in fallback, every verb goes to the direct driver, and
      `set_mode` is refused (there is no tracker).
    - The direct driver's host publishes nothing: the agent driver reports
      the camera's state, whichever route is in use.
26. **Handing back to the agent.** When the link is `ok` again, the route
    switches back at once. The direct driver is sent a stop: VISCA has no
    dead-man, so the camera must not be left moving. The direct driver is
    closed once that stop is confirmed, or after 1 s. Then `/resync` is
    sent.
27. **The follow worker looks at the link 10 times a second.** The tests
    let fake time pass, `Studio.soon` in `tests/modules/test_cameras.py`.

### State, commands, settings

28. **State.** The module's state has two subtrees:
    - `state.cameras.cameras.<id>` holds the contract's `CameraState`
      fields, plus `label`, `owner`, `driver`, `controller_address`,
      `obs_source`, `presets`, `route`, `status_reason`, `last_error`,
      `capabilities` and `control`. `control` is the arbiter's view:
      `velocity_owner`, `velocity`, `latched`, `last_source`,
      `last_command`, `last_command_ts`.
    - `state.cameras.controllers.<id>` holds `port`, `baud`, `status`,
      `status_reason` and `last_call {n, ts}`.

    A direct camera reports mode `manual` and tracker `idle`.
29. **Commands.**
    - `move {camera, pan, tilt, zoom, ttl_ms?}`
    - `stop {camera}`
    - `recall_preset {camera, n}`
    - `store_preset {camera, n}`
    - `set_mode {camera, mode, profile?}`, which waits up to 3 s for the
      agent.

    Refusals are `CommandError`s worded for the operator.
30. **Settings details.**
    - Camera and controller ids may not contain `.`, because they become
      state paths.
    - Two *enabled* cameras may not share a controller address, but a
      disabled spare may.
    - Two enabled controllers may not share a port.
    - `resend_interval_ms < ttl_ms` is enforced.
    - The brief's fixed joystick zoom speed is `motion.joystick_zoom_velocity`.
    - `motion.controller_silence_ms` (0 = off, decision 15) and the camera's
      `limits.pan_speed_max_step` / `tilt_speed_max_step` (decision 6).
    - A driver is rebuilt only when `(driver, address, direct_fallback)`
      changes; everything else is read on every command.
31. **The Settings page shows `cameras`, `controllers` and `motion` as JSON
    text boxes** (the generic form's fallback). They are edited there until
    WP3.
32. **Float tolerance.** Deadlines are compared with 1 µs of tolerance, in
    the VISCA link, the arbiter's resends and the fallback timer. Ten 0.1 s steps add up to
    0.9999999999999999 s.

### Carry-overs and housekeeping

33. **`-W error` is now permanent** (`filterwarnings = ["error"]` in
    `pyproject.toml`). The `ResourceWarning`s were not only in `test_home`.
    The child-process host never closed its pipe readers, nor the stdin of a
    child that died on its own. Every test that started the echo child could
    trip them, wherever garbage collection happened to run. Both are now
    closed in the reader threads.
34. **`fakes/simulated_ptz.py` is split** (414 → 271 lines):
    - `simulated_common.py`: ranges, limits, errors, `Moment`, the tracker
      and profile rules;
    - `simulated_head.py`: velocity integration;
    - `simulated_modes.py`: the contract's mode rules.

    Every behaviour is unchanged; the existing tests pass untouched, apart
    from their imports.
35. **Contract.** `docs/agent-api.md` says that preset endpoints on
    non-PTZ cameras are `400 not_supported`. It is still 1.0, and the
    changelog is unchanged. The conformance suite checks it on the crop
    camera.
36. **Dependencies.** `pyserial==3.5` (runtime) and `types-pyserial` (dev).

## Left open

- **Bench session (brief §8) not run yet.** João runs it with the real
  SMTAV and controller ([docs/bench/wp2.md](../bench/wp2.md)), and records
  the results on LIVESTACK-13. Until then, the following come from
  ptz_test.py or guesses, not measurements:
  - **Position units.** `DEGREES_PER_PAN_TILT_STEP = 0.075` and
    `ZOOM_POSITION_TELE = 0x4000` are typical values; ptz_test.py printed
    raw numbers only.
  - **Top speed steps.** The defaults (pan 0x18, tilt 0x14) are not
    measured. The bench probes them through the camera's limits (decision 6).
  - **Does the controller repeat frames while a stick is held?** The latch
    (decision 15) works either way. If it does repeat, set
    `motion.controller_silence_ms` to 2000, so a cut RS485 wire stops the
    camera too.
  - **Does it repeat "stop" at rest?** Harmless either way (decision 16),
    but good to know.
  - **Raw frame captures** to replace the rebuilt fixtures.
- **CI:** the Windows leg (pyserial, UDP `WSAECONNRESET`) has only been
  exercised on Linux/WSL.
- `tests/modules/test_cameras.py` covers `cameras/registry.py`. There is no
  separate `test_registry.py`, because the registry needs the module's
  context.
- Files a little over the ~300-line guideline: `core/supervisor.py` (~330)
  and `cameras/drivers/agent_proxy.py` (~325, the follow loop and the
  fallback rule change together).
- No camera UI or config pages (WP3); no Pixy or OBS crop drivers (WP6).
