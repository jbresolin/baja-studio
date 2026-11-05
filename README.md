# Baja Studio

One service that runs the studio tools on the Streaming PC: camera control,
playout and audio, each as a **module**. It is a Python program, built on
FastAPI, that also serves a React web page. Every browser connected to it
sees the same live state.

Work package WP0 built the shared base that every module builds on. WP1
added the **agents** module: the studio's link to the camera laptops
("agents") at trackside. Their API is the contract in
[docs/agent-api.md](docs/agent-api.md). WP2 added the **cameras** module:
a registry of cameras, a VISCA-over-IP driver for the SMTAV, a driver for
cameras owned by an agent (with direct fallback), and the RS485 joystick
(Pelco-D). WP3 added the camera pages. WP5 added the **playout** module:
a clip library from the pipeline orchestrator and local folders, a queue,
and flash-free switching of clips in OBS; see
[docs/bench/wp5.md](docs/bench/wp5.md) for setting up OBS. WP4 added the
**audio** module: the Behringer XR18 over OSC, the M-VAVE SMC-Mixer over
MIDI, the Ulanzi D200 decks' HTTP actions (the 2026 URLs, at the service
root), link matrix, macros and scenes; see [docs/bench/wp4.md](docs/bench/wp4.md)
and [docs/d200/](docs/d200/). Two demo modules remain:

- **heartbeat** counts up once per second. You can change the interval in
  Settings, reset it, or make its worker crash once to watch it recover.
- **echo** runs a small helper program (a *child process*) that turns text
  into capitals. You can kill the child and watch the service restart it.

Background reading: [docs/goals.md](docs/goals.md) (what we are building and
why), [docs/conventions.md](docs/conventions.md) (how the code is written),
[docs/agent-api.md](docs/agent-api.md) (the camera-laptop agent API) and
[docs/adr/](docs/adr/) (decisions taken in each work package).

## What you need

- **Python 3.12** from [python.org](https://www.python.org/downloads/). On
  Windows, tick "Add python.exe to PATH" in the installer.
- **Node.js 22 or newer** from [nodejs.org](https://nodejs.org/). You only
  need it to build the web page; the service does not use it.

Check both in a terminal (PowerShell on Windows):

```
python --version     # should print Python 3.12.x
node --version       # should print v22 or newer
```

On Linux, use `python3` instead of `python` until the virtual environment
(next section) is active.

## First-time setup

Run these commands from the `baja-studio` folder.

**1. Create a virtual environment.** This is a private folder for this
project's Python packages:

```
python -m venv .venv
```

**2. Activate it.** Do this again in every new terminal:

```
.venv\Scripts\Activate.ps1        # Windows PowerShell
source .venv/bin/activate         # Linux / macOS
```

Your prompt now starts with `(.venv)`.

**3. Install the service and its development tools:**

```
pip install -e "server[dev]"
```

**4. Build the web page:**

```
cd frontend
npm install
npm run build
cd ..
```

This creates `frontend/dist`, which the service serves.

## Running the service

```
python -m baja_studio
```

Open <http://localhost:8888>. The home page shows a card for each module:
**home**, **cameras**, **playout**, **audio**, **camera agents**,
**heartbeat** and **echo**. Playout shows `offline` until OBS is configured,
audio until the XR18's address is set. Each card has a
health badge, which should be green `ok` after a second or two. Stop the
service with `Ctrl+C`.

Options:

```
python -m baja_studio --port 9000              # use another port
python -m baja_studio --data-dir D:\StudioData   # keep data somewhere else
```

The database and log files go in the **data folder**:

- Windows: `C:\ProgramData\BajaStudio`
- Linux: `~/.local/share/baja-studio`

You can also set the folder with the `BAJA_STUDIO_DATA` environment variable.
Log files are in its `logs` subfolder, one JSON object per line.

## Things to try

| What | How | What you should see |
|---|---|---|
| Watch a crash recover | Click the **echo** card, then **Kill child process** | The badge turns red `offline`, then green `ok` again within a few seconds. The **Logs** page shows "killing echo child…" and "device back online". |
| Change a setting without restarting | **Settings → Heartbeat (demo)**, set *Interval S* to `0.25`, **Save** | On the heartbeat page the count in *Live state* now climbs four times a second. |
| See a save conflict | Open Settings in two tabs. Save a change in one tab, then save a different change in the other | The second tab says someone else changed the settings and offers **Load latest**. |
| Live sync between browsers | Open the heartbeat page on your PC and on your phone (`http://<pc-ip>:8888`) | Both show the same count, updating together. |
| Watch a worker recover | Heartbeat page → **Crash worker once** | The badge turns amber `degraded`. Counting resumes after about 1 s, and the badge returns to `ok` after 10 s without errors. |
| Turn a module off | Settings → Heartbeat → untick *Enabled* → Save | The heartbeat card shows grey `stopped`. Tick it again to restart it. |

## A fake camera laptop on the bench

You do not need a trackside laptop to try the agents module. In a second
terminal (virtual environment active), start a fake agent:

```
python -m baja_studio.fakes.fake_agent --port 8890 --cameras 2
```

It speaks the full agent API, with two simulated PTZs. Options:
`--crop-cameras 1` adds a crop camera, `--token T` requires a token, and
`--host 127.0.0.1` keeps it off the network.

| What | How | What you should see |
|---|---|---|
| Find it | Home → **Camera agents** → type your network (e.g. `192.168.1.0/24`, or `127.0.0.1/32` on the same PC) → **Scan subnet** → **Add** | The agent appears, green `ok`, with cameras `cam1` and `cam2` in `manual`. |
| Lose it | Stop the fake agent with `Ctrl+C` | About five seconds later the agent turns red `offline` and the home card says so. Start it again (try `--cameras 3`): it comes back `ok` with the new camera list. |
| Manual wins over auto | `curl -X PUT localhost:8890/cameras/cam1/mode -H 'Content-Type: application/json' -d '{"mode":"auto","profile":{"resume_after_ms":3000}}'`, then `curl -X POST localhost:8890/cameras/cam1/move -H 'Content-Type: application/json' -d '{"pan":0.3}'` | On the Agents page cam1 goes `auto` → `auto_paused` → `auto` again about three seconds later, on its own. |
| Break it on purpose | `curl -X POST localhost:8890/_fake/silence -H 'Content-Type: application/json' -d '{"silent":true}'` | The agent hangs: no more state. It shows `degraded` ("state stale") after three seconds, then `offline` after five. The other switches are listed in `server/baja_studio/fakes/fake_agent_routes.py`. |

## Playout without OBS or the VPS

In two more terminals, start a fake OBS (already set up with the playback
scenes) and a fake orchestrator:

```
python -m baja_studio.fakes.fake_obs --port 4455
python -m baja_studio.fakes.fake_orchestrator --port 8000 --token T --from-folder <clips folder>
```

In **Settings → Playout**, set `obs` to `{"host": "127.0.0.1", "port": 4455,
"return_scene": "Main"}` and `sources` to a local folder plus the
orchestrator (`http://127.0.0.1:8000`, token `T`), as shown in
[docs/bench/wp5.md](docs/bench/wp5.md). Then open **Playout**: drop video
files in the folder, queue them, Cue and Take. The fake plays every clip for
10 seconds (`--duration` changes it). Clips laid out as
`<prova>/<car>/<passagem>.mp4` get their prova, car and team from the fake
orchestrator.

## Audio without the XR18 or the M-VAVE

Start a fake XR18 (real OSC over UDP) in another terminal, and start the
service with the fake MIDI device:

```
python -m baja_studio.fakes.fake_xr18 --port 10024
BAJA_STUDIO_MIDI_BACKEND=fake python -m baja_studio      # Linux / macOS
$env:BAJA_STUDIO_MIDI_BACKEND="fake"; python -m baja_studio   # PowerShell
```

In **Audio setup → Mixer & devices** set the mixer address to `127.0.0.1`,
**Save**, and open **Audio**. Type `set /ch/01/mix/fader 0.5` in the fake
XR18's terminal (a change "made in Mixing Station"): the HOST 1 fader moves
on the page. `drop on` makes the mixer go silent (the page shows `offline`
after 5 s); `drop off` brings it back. Press M-VAVE buttons with
`curl -X POST localhost:8888/api/commands -H 'Content-Type: application/json'
-d '{"module":"audio","command":"simulate_midi","args":{"messages":[[144,16,127]]}}'`
(M on strip 1), and try the D200 URLs: `curl localhost:8888/action/mute/1`.

## Running the tests

With the virtual environment active:

```
cd server
pytest                 # all server tests; no hardware or network needed
ruff check .           # style and common mistakes
ruff format --check .  # formatting
mypy                   # strict type checking
cd ..
```

Frontend:

```
cd frontend
npm test               # Vitest unit tests
npm run lint           # ESLint
npm run format:check   # Prettier
npm run build          # type-check and production build
```

CI runs all of these on every push: the server on both Windows and Linux,
and the frontend on Linux. See [.github/workflows/ci.yml](.github/workflows/ci.yml).

Start reading the tests at `server/tests/conftest.py`. It explains the fake
clock that lets the tests control time without waiting.

`pytest` includes the **agent conformance suite** (`tests/agent_contract`),
run against an in-process fake agent. The same suite checks a real agent,
such as the AUTOPTZ hub app on a laptop:

```
cd server
pytest tests/agent_contract --agent-url http://laptop:8890 --agent-token <token>
```

Its camera moves a little during the run. See section 10 of
[docs/agent-api.md](docs/agent-api.md) for what is checked and what is skipped.

## Working on the web page

To change the frontend with instant reload, keep the service running in one
terminal and start the Vite dev server in another:

```
cd frontend
npm run dev
```

Then open the address it prints (usually <http://localhost:5173>). API and
WebSocket calls are forwarded to the service on port 8888. To point them at
another address, set `BAJA_STUDIO_URL`.

## How the code is laid out

```
server/baja_studio/
  main.py          builds the app; the module list lives here
  core/            the shared base: settings, state + WebSocket, supervisor,
                   child processes, command log, logging, HTTP API
  agents/          the agent API: contract models, client, scan, live links
  cameras/         camera registry, drivers (VISCA/IP, agent), Pelco-D input,
                   motion arbiter
  adapters/        clients shared by modules (obs-websocket v5, OSC and the
                   XR18's addresses and timing, the M-VAVE's MIDI map)
  playout/         clip library, queue, OBS switching, orchestrator sync,
                   ffprobe, webhook, aired-log export
  audio/           audio settings, macros, stored levels and link states
  fakes/           runnable stand-ins for external systems (fake_agent,
                   fake_visca_camera, fake_serial, fake_obs, fake_orchestrator,
                   fake_xr18, fake_midi)
  modules/         one folder per module (home, cameras, playout, audio,
                   agents, heartbeat, echo)
  childprocs/      programs run as child processes (echo_child, midi_child)
server/tests/      one test file per source file
  agent_contract/  conformance suite for any agent (--agent-url)
frontend/src/
  lib/             WebSocket state client, React hooks, typed API helpers
  pages/           Home, AgentsPage, CamerasPage, PlayoutPage, AudioPage,
                   AudioConfigPage, ModulePage,
                   Settings, Logs
  components/      shared UI pieces (badges, the schema-driven form)
frontend/tests/    Vitest tests
```

To add a module, see "Modules" in [docs/conventions.md](docs/conventions.md).

## HTTP API at a glance

| Endpoint | Purpose |
|---|---|
| `GET /api/health` | health of every module |
| `GET /api/settings` | list of settings sections |
| `GET/PUT /api/settings/{section}` | read, or save `{version, values}` (409 if stale) |
| `GET /api/settings/{section}/schema` | JSON Schema for the settings form |
| `GET /api/commands?module=&limit=` | command log, newest first |
| `POST /api/commands` | run `{module, command, args, caller}` |
| `GET /api/logs`, `GET /api/logs/stream` | recent log entries, live tail (SSE) |
| `GET /api/playout/clips/{id}/media` | a clip's video file (range requests) |
| `GET /api/playout/clips/{id}/thumbnail` | a clip's cached thumbnail |
| `GET/POST /action/...` | the Ulanzi D200's audio actions (2026 contract; see docs/d200) |
| `WS /ws` | live state (snapshot + patches), commands and replies |
