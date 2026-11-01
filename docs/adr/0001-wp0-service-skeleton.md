# ADR 0001 — WP0: studio service skeleton and shared base

Date: 2026-11-01. Work item: LIVESTACK-32. Brief: Plane page "03 — WP0 brief".

## Decisions taken

1. **Default port is 8888.** The brief contradicts itself: §2 ("fixed
   decisions, do not revisit") says 8888, while the acceptance checklist in §7
   says 8800. We went with the fixed decision. Override with `--port`.
2. **Extra core files beyond the brief's layout**, each one responsibility:
   `clock.py` (the `Clock` protocol, so tests use a fake clock),
   `database.py` (the shared SQLite connection and schema), `registry.py`
   (module lifecycle and command routing, driven by the list in `main.py`)
   and `services.py` (the bundle of shared services plus the `system`
   settings section).
3. **Frontend additions**: `lib/stateHooks.ts` holds the React hooks
   (`useModuleState`, `useSendCommand`, `useConnectionStatus`), so
   `lib/stateSocket.ts` stays plain TypeScript that can be tested without
   React. `pages/ModulePage.tsx` is a generic per-module page (health, live
   state, recent commands, a command console, and quick buttons for the demo
   modules). This is how the UI sends `kill_child`. `components/SchemaForm.tsx`
   builds the settings form from the schema.
4. **Wire formats.** Server → browser messages carry a `type`:
   `snapshot {seq, state}`, `patch {module, path, value, seq}` (path is
   dotted, `""` means the whole module subtree), and
   `reply {request_id, ok, result|error}`. Browser → server messages are
   `{module, command, args, request_id}`, or `{type: "snapshot_request"}`
   after a gap. Coalescing keeps only the latest value per (module, path)
   within the 30 ms window and sends each one as its own patch.
5. **Settings API.** `PUT /api/settings/{section}` takes `{version, values}`.
   `values` is a partial patch, and leaving out `version` forces the save. A
   stale version returns 409 with `current_version`. Invalid values return
   422 with Pydantic's per-field errors. A secret sent back as the mask
   `********` means "unchanged". The Settings form sets `noValidate` so the
   server is the only source of validation messages.
6. **`system` settings section**, not owned by any module:
   `command_log_retention_days` (default 7). The command log prunes at
   startup and then at most hourly on append, so it needs no cleanup thread.
7. **`POST /api/commands`** (`{module, command, args, caller}`) runs commands
   for other programs, logged with source `api:<caller>`.
8. **Health rules.** A worker exception gives `degraded`. A
   `DeviceOfflineError` gives `offline`. Either way the worker restarts after
   1 s, 2 s, 4 s … up to 30 s. After 10 s without error the module is `ok`
   again and the backoff resets. A worker can also call `mark_online()` to
   turn `offline` back into `ok` straight away (the echo child does this on
   its first ping). Workers declared with `starts_offline=True` show
   `offline` ("starting") until then, so the UI never claims `ok` before a
   device has answered.
9. **Child processes talk JSON lines over stdin/stdout**, as the WP0 brief
   says (goals §7.1 mentioned "a local socket"; pipes need no port and die
   with the child). Each child is started with `python -m <module>` and a
   copy of the service's environment. Its stderr goes to the service log. A
   new child is started on every worker run.
10. **Log context key is `studio_module`**, because `module` is a reserved
    `LogRecord` attribute (Python raises if you pass it in `extra`).
11. **Test client dependency is `httpx2`.** Starlette 1.10 deprecates `httpx`
    for `TestClient`.
12. **mypy `--strict` covers the tests too**, not only the package.

## Left open

- Confirm the port (8888 vs 8800) and fix the brief whichever way is chosen.
- `core/supervisor.py` (~340 lines) and `core/childproc.py` (~310) are a
  little over the ~300-line guideline after formatting. Splitting them would
  separate code that changes together, so they were left as they are. Revisit
  if they grow.
- `npm audit` reports 11 moderate findings, all one `minimatch` advisory
  pulled in by the lint toolchain (dev only). Runtime dependencies report
  none. The only fix offered is a breaking `--force` upgrade, so it was not
  applied.
- The CI workflow has not run yet: the repository is not on GitHub. The
  Windows leg in particular (child processes, `%ProgramData%` path) has only
  been exercised on Linux/WSL so far.
- No authentication (out of scope for WP0, as the brief says).
