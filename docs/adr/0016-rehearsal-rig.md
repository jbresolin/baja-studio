# ADR 0016 — WP11: rehearsal rig

Date: 2026-11-11. Work item: LIVESTACK-59. Brief: Plane page "17 — WP11 brief:
rehearsal rig (all fakes, one command)", with its amendments of 2026-11-11.

`python -m baja_studio.rig` starts every fake on free 127.0.0.1 ports, makes
a demo clip library once, writes a settings bundle in the export format,
starts a studio on port 8889 with its own data folder, sets it up over its
HTTP API, and plays a scenario. Code in `server/baja_studio/rig/`, tests in
`server/tests/rig/`.

## Decisions taken

1. **Children in their own process group, output to files.** Every fake and
   the studio run as `python -m …` with their output in `<rig>/logs/<name>.log`,
   so the terminal shows only the rig's summary and scenario steps. Their own
   process group (`CREATE_NEW_PROCESS_GROUP` / a new session) keeps a terminal
   Ctrl-C away from them: the rig stops them itself, CTRL_BREAK or SIGTERM
   first, a kill after 8 s. Checked on Linux by hand: Ctrl-C leaves no fake,
   studio, MIDI child or echo child running.
2. **The studio is set up only through what the pages use.** Settings import
   (upload, dry run, apply, no "partial"), then Audio setup's "Restore
   defaults…" for the macros and the link matrix (preview, save, restore, as
   `AudioConfigPage.tsx` sends them), then playout's "Return to: chosen scene
   Main". The return scene is session state, so it cannot travel in the bundle;
   without it the readiness run warns "no return scene" until the first take.
3. **The bundle's secrets are real values, written to the file.** They are the
   fakes' own constants (`rig-obs-password`, …), protect nothing outside
   127.0.0.1, and let `--no-studio` users import in one step. This is the one
   exception to "secrets never go to a file", and it holds only for the rig,
   because nothing it starts is reachable from another PC: every fake and the
   rig's studio get `--host 127.0.0.1`. Pinned by tests: the command lines
   (`tests/rig/test_fakes.py`, default suite) and the real sockets, which
   answer on 127.0.0.1 and not on the PC's network address
   (`test_no_fake_can_be_reached_from_another_pc`, `rig` suite).
   A fake added without `--host` fails both.
3a. **`--open`** (on by default) opens the home page and
   `/hosts?seats=1,2,3,4` once the studio is set up, so bench step 1 is green
   without opening or tapping anything. The hosts screen gained the `seats`
   address parameter for it (`seatsFromUrl` in `frontend/src/lib/hosts.ts`):
   seats named there are used and remembered instead of asking; an address
   without it behaves as before. A page no browser takes is printed instead.
4. **What the bundle sets beyond the brief's list**: channels 7 and 8 (PROD 3
   and 4) are made visible, because the default link matrix links them and the
   readiness run would warn about links on hidden channels; the hosts seats are
   all switched on with names (Ana, Bruno, Carla, Diego); seat microphones are
   channels 1-4. Unknown keys (checked recursively against this checkout's
   models) are left out with a warning; the tests fail on any.
5. **Cameras**: two PTZs per agent; each agent's `cam1` falls back to its own
   fake VISCA camera, so `flaky-agent` exercises the direct fallback.
6. **Fake OBS timing** is on by default with the fixed seed 2026, printed at
   start; `--seed N` replays another, `--fast-obs` turns it off. The same seed
   drives the scenarios' random parts. With ffprobe found, the fake OBS plays
   the demo clips at their real length (`--ffprobe`).
7. **Demo clips** are made by playout's own resolver's ffmpeg (bundled, else
   PATH), 640×360 or 360×640 `testsrc2`, some with a `sine` tone, written under
   a `.part` name and renamed, so an interrupted run never leaves a clip that
   looks finished. "Matches nothing" is `delivery/enduro/42/extra-take.mp4`:
   inside the receiver root, but not a `<passagem>.mp4` the fake orchestrator
   (`--from-folder`) announces.
8. **Scenario clock** starts when the fakes are up, before the studio starts.
   That is what makes `lr-assign-silent` work: the studio keeps mixer values
   across a reconnect to the same address, so an address the fake stops
   answering is only "never reported" if it stops before the first connect.
   The other scenarios' times (20-45 s) leave room for the studio's start.
9. **Scenario actions**: `console` (XR18 console line), `switch` (an agent's
   `/_fake` route), `stop`/`start` (the fake's process), `lossy` (expanded
   into seeded `drop on`/`drop off` lines in 0.5 s slots, since the fake XR18
   has no partial loss). `obs-restart` stops and restarts the fake OBS process
   on the same port: the socket closes as when OBS quits, and OBS comes back
   with its start-up scenes, as a real restart does.
10. **`flaky-agent`** sets agent 2 silent *and* answering HTTP 5 s late (longer
    than the client's 2 s timeout), so both the WebSocket and the requests
    stop; a request already waiting when it recovers may answer up to 5 s late.
11. **Tests.** Unit tests (bundle keys, clips, scenarios on the fake clock,
    processes, layout) run in the default suite. The integration tests open
    loopback sockets, so they are marked `rig` and deselected by default
    (`addopts = -m 'not rig'`); CI installs ffmpeg and runs `pytest -m rig` on
    both OSes. Their studio runs in-process with the fake PC probes (free disk,
    NTP, CPU), so a small CI disk still gives an all-green run; it is set up
    through the same `StudioApi` the rig uses. `flaky-agent` runs at a tenth of
    its speed and checks the fake's switches flip and come back.

## Additions to fakes and adapters (no behaviour change)

- `fake_xr18.py`: console command `forget ADDRESS` (the mixer stops answering
  and accepting that address), for `lr-assign-silent`. The simulated mixer is
  unchanged.
- `adapters/obs_transport.py`: `connect(..., legacy=True)`. **A bug the rig
  found:** websockets 17.1+ warns (`DeprecationWarning`) on the first receive
  of a connection opened without a `with` block. In the service the warning
  is hidden, but under the suites' `-W error` every receive raised, so playout
  could never connect to a real (network) OBS in a test. `legacy=True` is the
  documented way to keep today's behaviour without the warning. No in-process
  test ever opened the real transport, which is why it went unseen.
  websockets stays pinned at `==17.4` (an exact pin, already below 18, with a
  comment saying why) until LIVESTACK-61 moves the transport to the current
  client.

- `frontend/src/pages/HostsScreen.tsx`: `/hosts?seats=…` (decision 3a).

Nothing in `core/` changed.

## Left open

- The hosts readiness check warns "no hosts screen is open" while the hosts
  tab `--open` opened is closed (or with `--no-open` until it is opened).
- With real PC probes, the rig studio's disk and clock checks are this PC's
  own: a nearly full disk or no internet shows there, as it would on the
  streaming PC.
- The Windows path (CTRL_BREAK stop, `%LOCALAPPDATA%`, choco's ffmpeg in CI)
  was written for but not yet run on Windows: the CI run and bench step 5
  will tell.
- Ports are new on every run, so `--no-studio` needs a fresh import each time.
