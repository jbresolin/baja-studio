# Conventions

Every work package follows these rules and updates this file when it adds one.
They exist so that the code can be reviewed by reading it, and especially by
reading its tests.

## Naming and structure

- Names say what things are: `restart_worker_after_failure`, not `rw`. No
  single-letter names outside tiny loops.
- Files stay under about 300 lines. Split by responsibility, not by size.
- Every public function has a docstring saying what it does and when it is
  called. Comments explain *why*, not *what*.
- Type hints everywhere. CI runs `ruff check`, `ruff format --check` and
  `mypy --strict` (over the package *and* the tests); the frontend runs
  `eslint` and `prettier --check`.

## Modules

- A feature is a `Module` subclass in `server/baja_studio/modules/<name>/`,
  added to `build_module_list` in `main.py`. Nothing else needs editing.
- Settings: subclass `ModuleSettings` (which adds `enabled`). Mark secrets with
  `Field(json_schema_extra={"secret": True})`; they are masked in the API.
  React to edits by subscribing (`context.settings.subscribe`), never by
  requiring a restart.
- State: write only your own subtree through `context.update_state(path, value)`.
  Keep values JSON-serialisable.
- Commands: implement `handle_command`; refuse with `CommandError` and a
  message an operator can act on. Commands run on a thread-pool thread and
  may block briefly.
- Device I/O runs in a worker (`WorkerSpec`) that loops until
  `worker.should_stop`, sleeps with `worker.sleep(...)`, and raises
  `DeviceOfflineError` when its device is absent. Workers that talk to a
  device use `starts_offline=True` and call `worker.mark_online()` once the
  device answers. Never catch-and-ignore errors to look healthy; let the
  supervisor see them.
- Adapters built on native libraries that can crash Python run as child
  processes: write a handler for `childproc_child.run_child(handler)` under
  `baja_studio/childprocs/`, and drive it from a worker with
  `childproc.run_child_session`. The child side imports only
  `core/childproc_child.py`, never the host side.
- A child that must tell the service something unprompted (a MIDI message
  arrived, a port went away) creates a `ChildChannel`, sends events with
  `channel.send_event(...)` from any thread, and passes the same channel to
  `run_child(handler, channel=channel)` so replies and events never
  interleave. A device library that cannot even start in the child (no MIDI
  system) is reported through the child's answers, not by crashing it.
- A worker that looks after several devices and keeps running while some are
  away reports a summary with `worker.report_health(state, message)` instead
  of raising.
- A module whose devices come and go with its settings (cameras, one per
  camera and controller) starts and stops single workers with
  `context.start_worker(spec)` / `context.stop_worker(name)`, names them after
  their device (`camera:<id>:visca`, `controller:<id>`), and turns their
  healths into one with `supervisor.set_health_summary`. Never wait for a
  worker to stop while holding a lock that worker may take.
- Secrets inside nested models or lists of models are masked like top-level
  ones; give list items an `id` field so a masked secret sent back stays with
  its item.
- Log with `context.logger` (it tags records with the module). When using a
  plain logger, pass `extra={"studio_module": name}` — `module` is reserved
  by Python's `LogRecord`.

## Adapters

- Adapters are shared, modules own policy. A client for an external system
  that several modules use (OBS: playout now, crop filters in WP6) lives in
  `baja_studio/adapters/` and speaks the protocol only: connect, requests,
  events, timeouts, reconnect backoff. Which scene to cut to, and when, is
  decided in the module.
- An adapter never opens its socket directly; it takes a transport factory,
  so tests plug in the fake's in-process transport and open no port.
- Adapter event listeners run on the adapter's reader thread: they queue the
  event and wake a worker, and never make requests or take module locks.

- HTTP routes go under `/api/<module>` (`Module.routes`). Only a contract
  fixed by a device that cannot change goes at the root
  (`Module.root_routes`, e.g. the D200's `/action/...`); `/api` and `/ws`
  are refused there.

## Device protocols

- **Write protection.** After the studio sets a value on a device that
  echoes or pushes changes, ignore that address's incoming values while the
  write is queued and for a short window after it is sent (300 ms for the
  XR18), so a late echo cannot overwrite what was just set. After the
  window the device is the truth again: changes made elsewhere must show.
  Check the window and apply the value under the same lock the writes take,
  or a write on another thread can slip in between.
- **No bulk polling.** Never poll a device's whole parameter space (the
  XR18 hung under ~113 packets per 500 ms in 2026). Subscribe to its pushes
  where it has them; read once on connect only what something depends on;
  read more on demand; spend every packet (writes, reads, keep-alives) from
  one rate budget, latest value per address winning. Tests count packets.
- The first reading after connecting is not a change: do not react to it
  (mirror, notify) as if someone had moved something.
- State a module derives from a device (is this macro active, is this LED
  lit) is computed from the device's current values, never remembered, so
  restarts and changes made elsewhere cannot leave it wrong.

## Network clients and units

- Any network client has explicit connect/read timeouts and a documented
  offline rule (e.g. the agent client: 2 s per request, `offline` after 5 s
  without hearing from the agent).
- Normalised units at API boundaries: `-1..1` velocities, `0..1` crops and
  zoom. Device-specific tables (VISCA speed steps, UVC ranges) live only
  inside drivers.
- Async code waits with `await clock.sleep(...)`, never `asyncio.sleep`, so
  tests can run it on the fake clock.
- Protocol logic that depends on time (resends, timeouts, dead-man switches)
  is written as a plain class told `now` on every call (`ViscaLink`, the fake
  agent's `SimulatedCamera`); a thin worker loop around it does the I/O.
  Compare float deadlines with a small tolerance: ten 0.1 s steps are not 1.0.

## Camera motion

- Only a camera's `MotionArbiter` calls its driver. Inputs (joystick, web,
  API) are sources named like command-log sources, and go through the
  arbiter.
- One source owns a camera's velocity at a time. Another takes over once
  the owner is at rest or its TTL has expired.
- A latched velocity (the joystick's) has no TTL. The input that latched it
  must release it: on its own stop, a different command, or losing its
  link. The arbiter keeps re-sending it with a full TTL meanwhile.
- A stop from the web page or the API is always executed. A stop on behalf
  of the joystick applies only to a camera the joystick owns, because a
  controller at rest repeats "stop" and, on agent cameras, every stop
  pauses auto.
- Velocity shaping (deadband, inversion, scale, curve) happens in the
  arbiter. Drivers map normalised values to device steps, and nothing else.
- Never send a stop just because a connection opened (contract §7). A stop
  the device did not confirm is repeated until it is.

## Browser input

- The browser is one more input source, `web:<client id>`, through the
  arbiter; it never talks to drivers. The client id comes in every snapshot
  and is asked for again on reconnect (`/ws?client=`), so it is stable per tab.
- Hold-to-move: while a control is held, send `move` every 100 ms with
  `ttl_ms` 300; key auto-repeat changes nothing. On release send one
  `stop {camera, release: true}` (applies only to a camera this page moves).
  The Stop button sends a plain `stop`, always obeyed.
- Never send a blanket stop: not on page load, focus, reconnect or camera
  switch. A camera switch, hidden tab or lost focus *while held* is a release.
- Every command carries the client id (the server adds it as the source);
  the page shows its own id so operators can tell their actions apart.
- A module that receives steady command repeats keeps them out of the
  command log with `Module.wants_logged`.

## Tests

- pytest, one test file per source file, mirroring the source tree under
  `server/tests/`.
- Test names are sentences: `test_worker_is_restarted_after_exception`.
- **Test comments assume a complete beginner**: say what is being set up,
  what is being poked, what is expected and why.
- Warnings are errors (`filterwarnings = ["error"]`): close every file,
  pipe and socket you open.
- No network, no hardware, no sleep-based timing. Use the `fake_clock`
  fixture (`advance()`, `wake_up_times()`, `sleeping_count`), the
  `eventually(...)` helper for work done on other threads, and
  `HealthRecorder` for health transitions. Starting real Python child
  processes over pipes is allowed; they need no hardware.
- Fakes live next to the tests they serve (e.g. `tests/core/fake_children.py`,
  `frontend/tests/fakeSocket.ts`). No mocking library beyond `unittest.mock`
  (Python) / `vi.fn` (Vitest), and only for the rare case.
- Fakes that stand in for external systems are runnable programs, not just
  test fixtures, and live in the package under `baja_studio/fakes/` (e.g.
  `python -m baja_studio.fakes.fake_agent`). Tests reach them in-process,
  without opening ports.
- Threads on the fake clock: move time with `step_fake_time` /
  `step_fake_time_until` from `tests/conftest.py`, never a bare loop of
  `advance`. They wait for the workers (`module_worker_threads(module)`) to
  be asleep *before* each step as well as after: a worker that just
  published may not be asleep yet, and time moved then wakes nobody. Leave
  out workers that block on something other than the clock (`skip=`, e.g.
  a controller's serial read).
- Async tests are marked `@pytest.mark.anyio`. With the fake clock, let time
  pass with `advance_in_steps` / `advance_in_steps_async`, not one big
  `advance`, so pushes and timeouts interleave as they would for real.
- The agent conformance suite (`tests/agent_contract`) is the one place that
  runs in real time, because it must also check real agents
  (`--agent-url`). It waits for events with deadlines, never fixed sleeps.
- Frontend: Vitest + Testing Library under `frontend/tests/`, same naming and
  comment rules.

## Services and secrets

- **Services, not scripts.** On the streaming PC everything runs as a Windows
  service registered by the installer (NSSM): nothing is started by hand,
  from a scheduled task or a batch file, and no config file is edited by
  hand. A program the studio depends on (Caddy) gets its own service; its
  config is generated by a module from settings and applied through the
  program's own API.
- A module that needs the install folder or a service uses
  `core/system_services.py` (`InstallLayout`, `ServiceControl`), never a
  hard-coded path. Run from a checkout there is no service, and
  `NotInstalledServiceControl` says so.
- **Secrets never go to a file.** A secret a program needs (the Cloudflare
  token for Caddy) reaches it through the service's environment
  (`nssm set <service> AppEnvironmentExtra`), set by the module that owns it;
  generated config files name the variable (`{env.CLOUDFLARE_API_TOKEN}`).
  Secrets are never logged, never in state, never in a command result, and
  never in an error message (NSSM errors name the verb and service only).
  The diagnostics bundle masks settings and also scrubs every secret value
  from every file it collects.

## Decisions

- Each work package ends with a short ADR in `docs/adr/` listing the
  decisions it took and anything left open.
