# Baja Studio

One service that runs the studio tools on the Streaming PC: camera control,
playout and audio, each as a **module**. It is a Python program, built on
FastAPI, that also serves a React web page. Every browser connected to it
sees the same live state.

This first version (work package WP0) has no real devices yet. It ships the
shared base that later modules build on, plus two demo modules:

- **heartbeat** counts up once per second. You can change the interval in
  Settings, reset it, or make its worker crash once to watch it recover.
- **echo** runs a small helper program (a *child process*) that turns text
  into capitals. You can kill the child and watch the service restart it.

Background reading: [docs/goals.md](docs/goals.md) (what we are building and
why), [docs/conventions.md](docs/conventions.md) (how the code is written)
and [docs/adr/](docs/adr/) (decisions taken in each work package).

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
**home**, **heartbeat** and **echo**. Each card has a health badge, which
should be green `ok` after a second or two. Stop the service with `Ctrl+C`.

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
  modules/         one folder per module (home, heartbeat, echo)
  childprocs/      programs run as child processes (echo_child)
server/tests/      one test file per source file
frontend/src/
  lib/             WebSocket state client, React hooks, typed API helpers
  pages/           Home, ModulePage, Settings, Logs
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
| `WS /ws` | live state (snapshot + patches), commands and replies |
