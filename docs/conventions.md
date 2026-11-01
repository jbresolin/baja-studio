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
  processes: write a handler for `childproc.run_child(handler)` under
  `baja_studio/childprocs/`, and drive it from a worker with
  `run_child_session`.
- Log with `context.logger` (it tags records with the module). When using a
  plain logger, pass `extra={"studio_module": name}` — `module` is reserved
  by Python's `LogRecord`.

## Tests

- pytest, one test file per source file, mirroring the source tree under
  `server/tests/`.
- Test names are sentences: `test_worker_is_restarted_after_exception`.
- **Test comments assume a complete beginner**: say what is being set up,
  what is being poked, what is expected and why.
- No network, no hardware, no sleep-based timing. Use the `fake_clock`
  fixture (`advance()`, `wake_up_times()`, `sleeping_count`), the
  `eventually(...)` helper for work done on other threads, and
  `HealthRecorder` for health transitions. Starting real Python child
  processes over pipes is allowed; they need no hardware.
- Fakes live next to the tests they serve (e.g. `tests/core/fake_children.py`,
  `frontend/tests/fakeSocket.ts`). No mocking library beyond `unittest.mock`
  (Python) / `vi.fn` (Vitest), and only for the rare case.
- Frontend: Vitest + Testing Library under `frontend/tests/`, same naming and
  comment rules.

## Decisions

- Each work package ends with a short ADR in `docs/adr/` listing the
  decisions it took and anything left open.
