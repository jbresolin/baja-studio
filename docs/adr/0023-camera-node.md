# ADR 0023 — WP15: the camera node (a laptop's USB cameras without the studio's OBS)

Date: 2026-11-16. Work item: LIVESTACK-66. Brief: Plane page "25 — WP15
brief: camera node", with its §9 addendum (two video modes). Builds on ADR
0002/0003 (agent contract, drivers), 0019 (image controls, the UVC child),
0020 (crop window maths and motion, previews), 0021 (crop cameras in a remote
OBS), 0022 (Feeds/MediaMTX), 0007/0010 (installer). Bench:
[docs/bench/wp15.md](../bench/wp15.md).

Built in two parts the same night: **Part A** (this branch: the node service,
the studio side, the installer) and **Part B** (`wp15-node-video`: the ffmpeg
pipeline, its pin and the laptop benchmark), which Claude merges into A. This
ADR records Part A's decisions; Part B adds its own under "The pipeline".

## Decisions

### The node is the studio's app with one module

1. `python -m baja_studio.node` runs `create_app` (main.py) with a single
   module, `NodeModule` (`node/module.py`): the studio's settings store
   (secrets masked, versioned saves), supervisor and workers, logging (file,
   Logs API), state store and `/ws`, command log and core API come as they
   are. Nothing in `core/` was forked or changed for it. Its database and
   logs live in `%ProgramData%\BajaCameraNode` (`BAJA_NODE_DATA`), its page in
   `{app}\frontend` (the `frontend/dist-node` build).
2. **The agent contract is the module's `root_routes`** (`node/agent_routes.py`):
   its paths are fixed by its clients (the studio, the MV app), like the
   D200's. The contract's error rules (`{error, detail}`, `400 bad_request`
   for a bad body) apply to `/agent/...` and `/cameras/...` only; the core API
   keeps its own answers (`add_contract_error_handlers(app, applies_to=...)`).
3. **What every agent server shares moved to `agents/server.py`**: errors and
   their handlers, the token rule (constant-time compare, `/agent/info` open,
   the refusal never repeats what was sent), the profile check, the command
   log and the state WebSocket (`StateHub`, `serve_state_socket`). The fake
   agent now uses them too; its behaviour and tests are unchanged.
4. **Default port 8891** (the MV app's zone agent has 8890). The port is a
   setting, read at start (`--port` overrides); the page says a change needs
   a service restart and that the installer's firewall rule covers 8891 only.

### Each camera is a `kind=crop` agent camera, in one of two video modes

5. A camera has a **video mode** (brief §9): `node` (the node's own ffmpeg
   pipeline, Part B) or `obs` (OBS on the same laptop holds the camera and
   its "Baja crop" filter, as in WP13). To the studio both are the same
   `kind=crop` camera: move, stop, crop, position, snapshot, image controls.
   `supports_auto: false`, presets none (`preset_max: null`), modes `manual`
   and `hold` (auto is `400 not_supported`, §7).
6. **One `CameraDriver` per mode, and the contract around it**
   (`node/camera.py`, `NodeCamera`): `/move` holds a velocity for `ttl_ms`,
   `PUT /crop` jumps the window (narrower than `min_window` is widened; the
   answer waits up to 1 s for the driver to publish the window it applied),
   `/stop` is never refused, motion to an `offline` camera is
   `503 device_offline`, status changes to and from offline are
   `device_offline`/`device_online` events.
7. **OBS mode reuses the studio's crop camera as it is**: the node describes
   the camera as the `obs_crop` `CameraConfig` it is (127.0.0.1, its port,
   password, source, filter, limits: `node/backends.py`) and runs the
   studio's own `ObsCropDriver` on a `RemoteObsPool` connection (ADR 0021:
   one connection per OBS address shared by both cameras, reconnect backoff,
   the `GetVersion` keep-alive, password rule). Test connection, the source
   list and Create filter are the studio's own `ObsCameraCommands`. Only one
   addition was needed: `WindowTarget` gained an optional `width`, so a jump
   can set the size too (`PUT /crop`); the obs_crop driver never sets it.
8. **Node mode** is `PipelineCropDriver` (`node/pipeline_driver.py`): the same
   held velocity and dead-man (`cameras/crop_motion.py`, moved there from
   `drivers/obs_crop_motion.py`), the same window maths (`crop_window.py`)
   and the same published `crop` state as an obs_crop camera; the window goes
   to the pipeline's `set_window` instead of a filter. The jump-and-motion
   step obs_crop's session did inline is now `crop_motion.next_window`, used
   by both (obs_crop's tests unchanged and green).
9. **Speeds**: both modes use the obs_crop settings' speeds and
   `speed_follows_zoom` (default on: the picture moves at the same speed on
   air at any zoom, João's rule). `limits` in `/agent/info` give the speeds at
   the widest window, as frame fractions per second.
10. **Switching a camera's mode, or its video settings, never restarts the
    node** (`node/cameras.py`): the old driver is stopped (its worker stops
    the pipeline and waits for it; an OBS lease is given back), the new one
    started; the camera, its mode and its window stay (a new pipeline is
    given the window the camera had). What a driver reads every step (source,
    filter, limits, speeds) changes without a new driver.
11. **Health in operator words**: `pipeline restarting: <Part B's reason>`
    (degraded: the window is the node's and keeps working), `pipeline failed:
    ...` (offline), `pipeline starting`, `no output URL: paste this camera's
    SRT URL from the studio's Feeds page`, `OBS mode: choose the camera's
    source in OBS on this node's page`, and the ObsCropDriver's own (`OBS at
    127.0.0.1:4455 unreachable: ...`, `source 'S600' has no filter ...`).
    `/agent/health` lists every troubled camera with its reason.

### Robustness

12. **A failing camera never takes the API down**: drivers run in supervised
    workers (restarted with backoff if they raise), the routes only read what
    drivers published, and one camera's backend is independent of the
    other's. **The dead-man is the velocity's own expiry** (`CropMotion`
    integrates only up to `expires_at`), whatever the pipeline or OBS does; a
    restarted node starts at rest; the window survives a pipeline restart
    (§4 rule) and a driver swap.
13. **Image controls in both modes through the UVC child** (`node/image_controls.py`,
    the studio's `UvcChildLink`): the names a camera lists are remembered
    from its last read (`/agent/info` must not ask the device); writes are
    checked against a fresh read (unknown name: nothing written), clamped and
    snapped, ignored while auto is on, read back. What was written is written
    again when the device comes back after an unplug (a webcam forgets; the
    studio reapplies only when the agent link returns).

### Secrets

14. The **token**, each **output URL** (a Feeds URL carries the feed's publish
    password) and the **OBS password** are `secret` settings: masked in the
    API, left out of exports. The page shows an output URL as
    `srt://host:port?streamid=***` (`node/redact.py`); every reason that came
    from a pipeline or OBS is scrubbed of the URL and its parts; `PipelineSpec`
    keeps `output_url` out of its `repr`. A test checks none of it appears in
    info, health, state, the settings API or the log.
15. **With a token, the node's page needs it too**: every `/api/...` request
    (`X-Agent-Token`) and the page's `/ws` (`?token=`, a browser cannot set a
    WebSocket header). Otherwise anyone on the LAN could read the settings,
    change the token or point a camera elsewhere. Without a token (a fresh
    install) everything is open and the page says so in red.

### The node's page

16. **A second small build of the studio's frontend** (`frontend/node/`,
    `src/node/`, `vite.node.config.ts` → `dist-node/`, made by the same `npm
    run build`), rather than pages inside the studio's app or a hand-written
    HTML file: it reuses the state socket, styles and badges, is linted,
    formatted and tested with the rest, and the node installer ships only it
    (≈235 kB). It shows each camera's status and the video's figures (or the
    OBS link), the laptop's CPU, the settings (both modes' fields, Test
    connection, source picker, Create filter, capture devices and modes), and
    the log tail.

### Contract 1.1 (additive) and the studio side

17. **Snapshots**: per-camera `capabilities` in `/agent/info`, `snapshot`,
    `GET /cameras/{id}/snapshot?width=` (`docs/agent-api.md` §6). Clarified
    from the brief's "404/not_supported": a camera that does not list it
    answers `400 not_supported` (the contract's code for "not for this
    camera"); one that lists it but has no picture yet answers the new
    `404 no_snapshot`. A node camera in OBS mode without an uncropped preview
    source can only picture its window: it says so (`X-Snapshot-Shows:
    window`, `X-Crop-Window`), and the studio pieces the frame together as
    for an obs_crop camera. The fake agent's crop cameras serve a JPEG test
    card (`fakes/blocky_jpeg.py`: a baseline JPEG of flat 8×8 blocks, no image
    library; decoded by ffmpeg in a test).
18. **Studio previews of agent cameras use the snapshot when advertised**
    (`cameras/agent_previews.py`): the same cache, one request in flight and
    per-agent slots as OBS screenshots; no OBS source needed on the studio.
    `DriverCapabilities.snapshot` tells the page.
19. **Studio-side presets for agent crop cameras** (the studio had none):
    stored windows in the camera's settings (`crop.windows`, as obs_crop),
    presets 0–127, names in `presets`, recalled with `PUT /crop` keeping the
    agent's window shape (`drivers/agent_crop_presets.py`). Only for crop
    cameras whose agent lists no presets of its own.
20. **The conformance suite runs on any movable camera** (`movable` fixture:
    `--agent-camera`, else the first PTZ, else the first crop camera), skips
    auto on cameras without it (and checks they refuse it), and checks
    snapshots where advertised. `--agent-impl node` runs it against an
    in-process node; CI runs it once per video mode, and the rig runs it
    against the node process over loopback.

### Installer, rig

21. **`BajaCameraNode-<version>.exe`** from `build.ps1 -Node` and
    `installer/BajaCameraNode.iss`: embeddable Python with the service
    package (no tray: the page is the status), NSSM, the node's page and its
    ffmpeg; the `BajaCameraNode` service (auto start, nobody logged in,
    restart on exit, Ctrl+C stop with 15 s), firewall TCP 8891 on private and
    domain networks, upgrade in place (service stopped first, same data
    folder), uninstall keeps or purges the data. Smoke-tested on a Windows
    runner (`smoke-test-node.ps1`), and attached to the same GitHub Release.
22. **The node's ffmpeg is Part B's pin** (`installer/ffmpeg-node/pin.json`);
    `build.ps1 -Node` reads it last and stops with a message saying Part B
    must be merged while it is missing. The studio's installer is unchanged.
23. **The rig** starts the real node with `--fake-video --fake-cameras 2` as
    agent `rig-node`, and the bundle adds its two crop cameras.

## The interface with Part B (brief §4)

`node/video/pipeline_types.py` holds the shared types; Part B's
`node/video/pipeline.py` provides `CameraPipeline(spec, clock, logger)`,
`list_capture_devices()` and `capture_modes(device)`, which `node/parts.py`
loads by name (a build without it says "the node's video pipeline is not in
this build: use OBS mode"). Changes from §4: `PipelineSpec.output_url` is
`repr=False`; the window type is `PipelineWindow(x, y, w, h)`;
`PipelineStatus` fields are `state, reason, input_fps, output_fps,
dropped_frames, bitrate_kbps, encoder, since`; `PipelineState` adds
`stopped`; `CaptureDevice(name, device)` and `CaptureMode(width, height, fps,
codec)`. `FakeCameraPipeline` (`node/video/fake_pipeline.py`) is Part A's.

## Left open

- **Nothing here has met a real laptop.** Part B's pipeline, its encoders and
  its window-move latency, two S600s at 4K on the XPS and the ThinkPad, the
  UVC controls while ffmpeg or OBS holds the camera under the service's
  LocalSystem account, and the node service reaching the user's OBS: the
  bench sheet.
- The node's camera mode (`manual`/`hold`) is not kept across a node restart:
  it comes back `manual` (contract §9 allows it: "manual if it does not know").
- ObsCropDriver's reasons say "Camera setup" for Create filter; on the node
  it is the node's page.
- `cameras/drivers/agent_proxy.py` is ~430 lines (was 373): the presets hooks;
  `agent_crop_presets.py` holds the logic.
- The port setting changes nothing until the service restarts; a port other
  than 8891 needs its own firewall rule.
