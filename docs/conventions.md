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
- Threads on the fake clock: after `advance`, wait for the woken threads to
  sleep again before checking. If a step can stop a thread, use
  `fake_clock.all_asleep_or_gone(fake_clock.sleeping_threads())`, not
  `sleeping_count`; a thread that ended never sleeps again.
- Async tests are marked `@pytest.mark.anyio`. With the fake clock, let time
  pass with `advance_in_steps` / `advance_in_steps_async`, not one big
  `advance`, so pushes and timeouts interleave as they would for real.
- The agent conformance suite (`tests/agent_contract`) is the one place that
  runs in real time, because it must also check real agents
  (`--agent-url`). It waits for events with deadlines, never fixed sleeps.
- Frontend: Vitest + Testing Library under `frontend/tests/`, same naming and
  comment rules.

## Decisions

- Each work package ends with a short ADR in `docs/adr/` listing the
  decisions it took and anything left open.
