# Studio tools — goals brief

Written 2026-10-28 as input for the implementation-design thread; revised the same evening after João's review (stack, auto-PTZ ownership, playout wording). Covers the three studio-side tools that run on the Streaming PC: **camera control** (LIVESTACK-2, new), **playout** (LIVESTACK-8, rewrite) and **audio control** (LIVESTACK-9, rewrite). The 2026 versions of playout (`obs-playback-baja`) and audio (`xr18-control`), and `ptz_test.py`, are **reference only** — hasty kludges that worked at one event. New code, same lessons.

## 0. Cross-cutting goals (all three tools)

* **Nothing needs code changes at the event.** Every device, address, port, mapping and preset is edited in the browser and persisted; restart-free where feasible.

* **Server-authoritative state over WebSocket**, many clients at once (desk PC, tablet, phone), no polling. This pattern worked in Playout; keep it.

* **Physical controls and web UI are peers.** Whatever the joystick, MIDI mixer or D200 does, the browser can do, and both see the same state.

* **Runs as a service on the Windows Streaming PC** (LIVESTACK-11), one config folder (LIVESTACK-12), reachable through the portal (LIVESTACK-5). Stable fixed port per tool.

* **Stack: Python backend, React frontend built to static files.** Decided 2026-10-28. Reasons: everything else on this side is already Python (`pixy_ptz`, AUTOPTZ tracker and hub app, pipeline worker); device libraries are mature (pyserial, python-rtmidi/mido, python-osc, obsws-python, duvc-ctl); device I/O in threads, asyncio for the server and WebSockets (FastAPI or similar). Node is a **build tool only** (Vite) — operator UIs with drag-and-drop queues and mapping editors need a real SPA. One shared frontend component kit and one shared Python base (config store, WebSocket state broadcast, device supervisor, command log) across the three tools. BajaOvrLay stays TypeScript; it is a different kind of app.

* **Code written for review by reading tests**: descriptive names, moderate file sizes, hardware behind small interfaces with fakes, beginner-level comments in tests.

* **Degrade gracefully:** a missing device (COM port, MIDI, camera, OBS, laptop agent) must never crash the service or block the UI; show it as offline and keep reconnecting.

* Keep a **command log** per tool (what was sent, by whom/which input, when) for post-event debugging.

## 1. Camera control service (LIVESTACK-2)

### Purpose

The studio's single point of control for camera motion: PTZ heads, the EMEET Pixy, and "digital PTZ" crops on fixed cameras, whether the camera hangs off the Streaming PC or off a trackside laptop. Inputs: the RS485 joystick controller and the web UI. For cameras that have a laptop beside them, the laptop agent **owns** the camera and the studio service is its client (see Ownership below).

### Must carry over from `ptz_test.py` (proven at the last event)

* VISCA-over-IP (UDP 52381) packet builders and sequence-number wrapper for the SMTAV; pan/tilt speed ranges 1–18 / 1–17, zoom 2–7; preset set/recall; pan-tilt and zoom position inquiries (camera does answer).

* Pelco-D parsing with the **empirically mapped flags** (0x08 up, 0x10 down, 0x02 right, 0x04 left, 0x40 zoom in, 0x20 zoom out, 0x00 stop; preset recall cmd2=0x03 with number in byte 5; "Call" button arrives as address 0x03, cmd2=0x07). Checksum = sum(bytes 1–5) & 0xFF. 2400 baud on the CP2102N adapter.

* **Stop is never deduplicated** and always sends both pan/tilt stop and zoom stop (fixed a zoom runaway).

* Hold-to-move watchdog (~80 ms) for keyboard/web control so a dropped key-up can't leave a camera moving.

### What was wrong and must change

* One hard-coded camera; Windows-only console (`msvcrt`); no config; no UI; controller address byte ignored.

### Ownership model (decided 2026-10-28)

* **Trackside PTZ with a laptop (AUTOPTZ):** the laptop agent owns the VISCA connection and runs the tracker. It arbitrates locally: any manual command from the studio pauses auto and auto resumes on release or explicit return. Arbitration happens where latency is lowest and keeps working if the studio link drops. The studio service talks to the agent over a published API (same move/stop/zoom/preset verbs plus mode get/set).

* **Cameras without a laptop (studio Pixy, any studio-side PTZ, local USB crops):** the studio service owns them directly.

* **Fallback:** if a laptop agent is unreachable, the studio service may drive that PTZ directly over VISCA/IP when the config allows it; the agent re-takes ownership when it returns.

* The **laptop agent is part of the AUTOPTZ hub app** (AUTOPTZ-21). Its API covers camera moves/mode, remote crops and remote image settings, and is the contract the studio service consumes. This closes the earlier open question on agent ownership; LIVESTACK-17 becomes "client of the agent API" rather than a handoff API inside the studio service.

### Goals

1. **Camera registry** in config: id, label, owner (studio direct or agent at host:port), driver, connection details, limits, presets, which controller address selects it, which OBS source it feeds.

2. **Drivers behind one interface** (move with velocity, stop, zoom, preset, optional position read, mode get/set): VISCA/IP (SMTAV, direct); **agent-proxied camera** (any verbs forwarded to a laptop agent); Pixy via UVC (reuse `pixy_ptz` findings: absolute pan ±150°, tilt ±90°, zoom 1.0–1.5× only at 1080p, position readback unreliable); **OBS crop filter** via obs-websocket (Crop/Pad _filter_ on the source so projector windows show it; digital PTZ on 4K webcams = lossless 2× zoom); **remote crop** through the agent.

3. **RS485 routing:** the controller's camera-address selection picks the target camera; the Call button is a camera-select/preset hook. Multiple controllers possible later.

4. **Web control UI:** camera selector with live status and current mode (manual/auto/offline), joystick/arrow control with speed, zoom, presets with names, keyboard shortcuts, and a **thumbnail or small preview per camera** if cheap to provide.

5. **Configuration pages:** add/edit cameras, owners, protocols and IPs, controller address map, presets, crop limits/safe zones, speed curves — all persisted, applied without restart.

6. **Mode control for auto-tracked cameras (LIVESTACK-17):** from the studio UI or joystick, set a camera to auto with a behaviour profile, take it manual, or send it to a preset; the agent enforces manual-wins locally. Blocked by the agent API (AUTOPTZ-21) and PTZ driver (AUTOPTZ-15).

7. **Per-camera motion shaping** for directly owned cameras: speed curves, acceleration ramp, and the pan-judder mitigations from the AUTOPTZ bench tests (smooth velocity commands). For agent-owned cameras this lives in the agent.

### Non-goals

* Camera image settings (exposure, WB, focus) — separate deliverable LIVESTACK-3, though it may live in the same process and share the camera registry.

* Video transport (NDI/RTSP/MediaMTX) and the tracker itself — AUTOPTZ hub app.

## 2. Playout (LIVESTACK-8, rewrite)

### Purpose

Queue and play finished clips in OBS during the stream, fed by the video pipeline v2 orchestrator **and selected manually from disk if needed**.

### Must carry over from `obs-playback-baja` (worked)

* Two Media Sources (`program`, `preview`) with configurable names; preview/cue without touching program.

* Library grouped by prova with search, bulk queue ops, drag-and-drop queue, "played" tracking, operator notes.

* Playback progress with colour-coded countdown (white/amber/red at 10 s/5 s); post-clip behaviour Return / Hold / Fade to a configurable return scene.

* Keyboard shortcuts (Space next, → skip, R return, Delete remove, ? legend).

* Multi-client real-time sync; settings in DB; server on 0.0.0.0.

* ffprobe scan of the local clip folder for duration/thumbnails.

### What was wrong and must change

* **End-of-previous-clip flash** on source swap → stop → set file → wait for media-ready event → play.

* **SQLite lived inside Dropbox** → conflicted copies. Data dir outside any synced folder, path configurable.

* GAS/Google Sheet as clip source → replaced by the orchestrator (LIVESTACK-28 done): clip list, metadata, played/notes write-back all via the orchestrator API; clips arrive through the delivery receiver.

* One-off Node/Express/Zustand/Tailwind stack → the common Python + React stack.

### Goals

1. Orchestrator-fed library with incremental sync and offline tolerance (last known list persists; local folder is truth for playable files).

2. **Manual clips from disk:** any video file in a watched folder, or picked through a file dialog, appears in the library with ffprobe metadata and can be queued like an orchestrator clip (for intros, sponsor videos, last-minute edits done by hand).

3. Correct, flash-free program switching; preview load never affects program.

4. Replay workflow: re-queue any played clip in two actions; "replay last" shortcut.

5. Session persistence: queue, played state and notes survive restarts; export of what aired and when (for post-event YouTube chaptering).

6. Optional hooks for BajaOvrLay (clip on air → overlay with car/team) through a simple event endpoint.

7. Two-operator safety: a "take" confirmation option so a second client can't fire program by accident.

## 3. Audio control (LIVESTACK-9, rewrite)

### Purpose

Fast operator controls for the XR18 from the M-VAVE SMC-Mixer, Ulanzi D200s and a web page; detailed mixing stays in Mixing Station / X AIR Edit.

### Must carry over from `xr18-control` (worked)

* OSC/UDP to XR18 on 10024 with `/xremote` keep-alive (<10 s); state cache with **write protection** so XR18 echoes don't overwrite optimistic updates; changes made in Mixing Station are reflected.

* XR18 addressing quirks: channels 17–18 are the stereo return at `/rtn/aux/...`; aux buses 1–6; `/xremote` pushes cover all sources.

* M-VAVE empirical map: strips 1–8, buttons R=base+0x00, S=+0x08, M=+0x10, □=+0x18; bus-select notes 0x2E, 0x2F, 0x60–0x63; knobs CC 0x10–0x17 relative (0x01 inc, 0x41 dec); faders as pitch-bend; **device clears its own LEDs on Note Off** (so bus selection is shown in the UI, not on the hardware).

* Avoid flooding: no polling of all aux sends (hung the mixer at ~113 packets/500 ms). MIDI device enumeration and I/O in a dedicated thread so a missing device never stalls the server.

* Feature set: per-strip fader/mute, **link matrix** (mirror main levels to aux buses: linked/paused/unlinked), **macros** with store/restore of levels (`storedLevels`), scene recall, D200 HTTP action + status endpoints (`/action/mute/:ch`, `/action/nudge/...`, `/action/macro/...`, state polled at 1 s) with the existing D200 profile and button images.

* Channel labels/colours and `visibleChannels` config.

### What was wrong and must change

* Mappings edited by hand in JSON; macro edits lost because any broadcast re-rendered the page and there was no explicit save.

* No tests; stale `midi` dependency alongside `@julusian/midi`.

* Bus-select state only visible in UI; LED feedback half-working.

### Goals

1. All mappings (MIDI strips/buttons/knobs, D200 actions, macros, link matrix, labels/colours) editable in the web UI with explicit save and conflict-free re-render.

2. Headset/talkback workflows as first-class macros: host/guest IEM sends, producer talk-to-camera-op, "LIVE" states — the D200 profile's button set is the spec (HOST 1–2, GUEST 1–2, PROD 1–3, VIDEO vol, SHOTGUN, PC AUDIO).

3. Reliable reconnect for XR18, MIDI and D200 without restart; status of each on the page.

4. Keep the D200 HTTP contract backward compatible so the existing Ulanzi profile keeps working.

5. Scene/snapshot management for event phases (pre-show, race, interviews).

## 4. Integration map

* Camera control ↔ OBS (crop filters, source names), ↔ AUTOPTZ laptop agents (camera ownership, mode, remote crops, later image settings), ↔ portal.

* Playout ↔ orchestrator (clips, metadata, write-back), ↔ delivery receiver (files), ↔ local disk (manual clips), ↔ OBS (media sources, scenes), ↔ BajaOvrLay (on-air event, optional).

* Audio ↔ XR18 (OSC), ↔ M-VAVE (MIDI), ↔ Ulanzi Studio (HTTP), ↔ portal.

* All three: service runner (-11), config/backup (-12), reverse proxy + landing (-22/-23).

## 5. Open questions for the design thread

1. One Python process per tool or one "studio" service with modules? Shared registry between camera control and image settings argues for at least those two together; a crash in one tool taking down the others argues against one process.

2. Python web framework and WebSocket layer (FastAPI + uvicorn is the default candidate); how the shared base package is versioned across the three tools.

3. The **agent API contract** with AUTOPTZ-21 must be written before camera-control code: verbs, mode semantics, discovery of agents on the LAN, what happens on agent restart.

4. Portal scheme (paths vs. subdomains) decides how the three UIs are mounted.

5. Whether D200s should talk to the services directly over HTTP (as today) or through a small shared "actions" endpoint on the portal.

6. Packaging on the Streaming PC: per-tool venv + NSSM, or one PyInstaller build per tool like the pipeline worker.

## 6. Reference material

* `ptz_test.py` (Mar 2026) — attached in the chat that produced this page; Pelco-D ↔ VISCA bridge.

* `D:\Dropbox\BAJA\Sistema\obs-playback-baja` — Baja Playout 2026 (Node/Express/React/SQLite), README documents OBS setup and shortcuts.

* `D:\Dropbox\BAJA\Sistema\xr18-control` — XR18 bridge 2026 (`xr18.js`, `midi.js`, `macros.js`, `server.js`, `mappings.json`, D200 profile + button images).

* `pixy_ptz` package (Oct 2026) — EMEET Pixy UVC control, Windows, Python 3.12.

* AUTOPTZ design-notes page — PTZ latency/transport findings and pan-judder notes.

## 7. Decisions on the open questions (2026-10-29)

Section 5 is resolved as follows; the design thread starts from here.

### 7.1 One studio service (modular monolith), native device code out of process

* One Python process, one port, one config/SQLite, one WebSocket, one install; modules: cameras, image settings, playout, audio, home/portal.

* An in-process supervisor runs each module's device I/O in its own worker; exceptions mark the module degraded and restart the worker, never the process.

* Adapters built on native libraries that can crash the interpreter — **MIDI (rtmidi)** and **UVC/DirectShow (duvc-ctl)** — run as small child processes talking to the service over a local socket; the supervisor restarts a dead child. Serial, OSC, obs-websocket and HTTP stay in-process.

* Rejected: three separate services (three installs/ports, duplicated camera registry) for isolation the child-process pattern already gives.

### 7.2 FastAPI + uvicorn

* Confirmed. One repo, one process: the shared base (config store, state broadcast, supervisor, command log) is an internal package — no cross-tool versioning.

### 7.3 Agent API contract first

* Writing the AUTOPTZ-21 agent API (verbs, mode semantics, LAN discovery, restart behaviour) is an early work package, before camera-control code.

### 7.4 Paths, one host

* `live.bajasaebrasil.net` → studio service; modules are routes (`/cameras`, `/playout`, `/audio`); the app's home is the landing page. Other apps (BajaOvrLay control, pipeline review) get reverse-proxy paths on the same host; a wildcard `*.live…` A record only if an app cannot live under a path.

* **Static DNS entry on the hEX S** for the hostname so it resolves with the event WAN down (LIVESTACK-21).

* **TLS via DNS-01** (Caddy + Cloudflare DNS module) so pages are a secure context on phones with no certificate warnings; works with a private-IP A record (LIVESTACK-22).

### 7.5 D200 actions

* Moot: the studio service is the shared actions endpoint. Existing `/action/...` paths kept so the current Ulanzi profile works unchanged.

### 7.6 One-click deploy, native on Windows (LIVESTACK-11)

* **Docker Desktop rejected for the studio service.** Containers live in the WSL2 VM: the RS485 adapter and USB MIDI would need usbipd-win (detaches them from Windows, re-attach after every unplug), and a webcam attached to WSL2 is no longer visible to OBS — so UVC image settings cannot work from a container. Docker stays on the VPS and TrueNAS.

* **PyInstaller dropped** in favour of the **python.org embeddable distribution** with dependencies pre-installed, shipped as a folder: no freezing step, no hidden-import surprises with duvc-ctl/rtmidi DLLs, upgrades are a folder swap, and a Python prompt is available on the box for debugging.

* **Inno Setup installer** carrying that folder, the built frontend, Caddy and NSSM: one UAC prompt, service registered and started, firewall rule, Start-menu shortcut opening the browser. All configuration in the web UI; data in `%ProgramData%\BajaStudio` (SQLite), preserved across updates. Update = run the new installer.

* **Diagnostics built in** (the Docker-style tooling we would otherwise miss): NSSM rotating log files; a Logs page streaming the service log live; a health page from the supervisor showing each module, device and child process; a one-click "diagnostics bundle" that zips logs, config and device state.

* Built on a GitHub Actions Windows runner from a tag; the same pipeline serves the AUTOPTZ hub app and the pipeline worker. Python 3.12 throughout (duvc-ctl constraint).
