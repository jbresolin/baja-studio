# ADR 0023 — WP15: the camera node (a laptop's USB cameras without the studio's OBS)

Date: 2026-11-16. Work item: LIVESTACK-66. Brief: Plane page "25 — WP15
brief: camera node", with its §9 addendum (two video modes). Builds on ADR
0002/0003 (agent contract, drivers), 0019 (image controls, the UVC child),
0020 (crop window maths and motion, previews), 0021 (crop cameras in a remote
OBS), 0022 (Feeds/MediaMTX), 0007/0010 (installer). Bench:
[docs/bench/wp15.md](../bench/wp15.md).

Built in two parts the same night and merged into one: the node service, the
studio side and the installer; and the ffmpeg pipeline, its pin and the
laptop benchmark ("Video pipeline (node mode)" below).

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
   pipeline) or `obs` (OBS on the same laptop holds the camera and
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
11. **Health in operator words**: `pipeline restarting: <the pipeline's reason>`
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
    API, left out of exports. The page shows an output URL with only its
    login hidden, `srt://host:port?streamid=publish:cam1:***&latency=200000`
    (`node/redact.py`; until 16 Nov every parameter was hidden, which hid
    the SRT latency too); every reason that came
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
    package (and, since 16 Nov, the tray: decisions 24-28), NSSM, the node's page and its
    ffmpeg; the `BajaCameraNode` service (auto start, nobody logged in,
    restart on exit, Ctrl+C stop with 15 s), firewall TCP 8891 on private and
    domain networks, upgrade in place (service stopped first, same data
    folder), uninstall keeps or purges the data. Smoke-tested on a Windows
    runner (`smoke-test-node.ps1`), and attached to the same GitHub Release.
22. **The node's ffmpeg is pinned** (`installer/ffmpeg-node/pin.json`, see
    "Video pipeline" below); `build.ps1 -Node` unpacks it to
    `{app}\ffmpeg\ffmpeg.exe`, checks the version it reports, and the smoke
    test checks it has the zmq filter and SRT. The studio's installer is
    unchanged.
23. **The rig** starts the real node with `--fake-video --fake-cameras 2` as
    agent `rig-node`, and the bundle adds its two crop cameras.

### The tray icon (added 2026-11-16)

João asked for a tray icon on the laptops too, like the studio's, with a
camera for its icon. Decision 21's "no tray: the page is the status" is
replaced by these.

24. **One tray package for both programs.** `tray/baja_studio_tray`, run with
    `--node`, is the node's tray: `profile.py` holds what differs (name, icon,
    single-instance mutex, registry key, data folder), and the poll loop,
    badge dot, Quit and menu machinery are shared. The two trays use
    different mutexes and Run values (`BajaStudioTray`,
    `BajaCameraNodeTray`), so a PC with both shows both. The icon is a plain
    camera in the Baja blue (`assets/camera-node.ico`, drawn by
    `installer/icons/make_camera_icon.py`), at the studio icon's six sizes,
    with the same green, amber or red dot.
25. **The tray reads a status answer meant for it, which needs no token:**
    `GET /api/node/tray-status` (`node/tray_status.py`). The tray runs as the
    logged-on user and has no way to know the token: it is a secret setting
    in the service's database, masked everywhere, and copying it to a file
    the tray could read would put it where any local user could read it too.
    Instead this one path is let through the token check, and:
    - it answers only the laptop itself (client 127.0.0.1 or ::1; there is
      no proxy in front of the node, so the address is the real one), and
      `403` to any other computer, with or without the token;
    - it holds nothing secret: the node's status and version, and per served
      camera its id, name, video mode, status and short reason. The reason is
      scrubbed again of the camera's output URL and its parts, its OBS
      password and the token, in case a reason ever quoted one.
    The rest of `/api/...` (settings, commands, health) still needs the
    token, from the laptop too. Tests check each of these.
26. **The port**: the node's port is a setting (8891 unless changed on its
    page), stored in the database the tray does not open. The node writes
    the port it serves on to `<data>\port.txt` at each start (a failure to
    write it is logged, never fatal), and the tray reads that file at every
    poll, falling back to 8891.
27. **What it shows**: green when the node is ok and every camera it serves
    is live; amber when a camera is not (the tooltip names it, with the
    node's reason), when the node is degraded, or when no camera is set up
    yet; red when the node does not answer. The menu: *Open the camera
    node's page* (also the double-click), one greyed-out line per camera
    ("Left S600 (node): live", "Right S600 (OBS): no OBS source"; a tray menu
    has a fixed number of items, so these are six slots that show or hide),
    *Restart the camera node* (`python -m baja_studio_tray.restart --node`
    through UAC, `nssm restart BajaCameraNode`), *Open logs folder*, the
    version, *Quit*. Portuguese on a Portuguese Windows, like the studio's.
28. **Installer**: `build.ps1 -Node` now installs the tray and its pinned
    dependencies in the node's Python too, and ships `camera-node.ico` (also
    the installer's, the uninstaller's and the shortcuts' icon).
    `BajaCameraNode.iss` copies the studio's tray handling: a ticked-by-default
    task, the Run value at every user's logon, a Start-menu shortcut, "show
    it now" at the end of the install, and the node's trays closed before an
    upgrade or uninstall replaces their Python. `smoke-test-node.ps1` checks
    the status answer, `port.txt`, the icon, the Run entry, and that
    `--node --exit-after-first-poll` reports amber (no camera yet) with this
    version; and that uninstall removes the Run entry.

## The interface between the node and its pipeline (brief §4)

The node and the video pipeline were written in parallel (WP15 parts A and B)
and joined at merge time. `node/video/pipeline_types.py` is the one place the
node imports a pipeline from: the `VideoPipeline` protocol, the
`PipelineFactory` and `CaptureDevices` protocols, and re-exports of the data
types, which live next to the code that fills them:

- `spec.py`: `PipelineSpec` (with `decoder` and `label`; `output_url` is
  `repr=False`), `SourceWindow(x, y, w, h)` (the one window type),
  `PipelineState` (`stopped` added to §4's four) and `PipelineStatus`
  (`fps_in`, `fps_out`, `dropped_frames`, `bitrate_kbps`, `encoder`, `since`,
  plus `restarts` and `window_apply_ms`);
- `capture_devices.py`: `CaptureDevice(name, alternative_name)` with its
  `device` property, and `CaptureMode(format, width, height, max_fps,
  min_fps)`; listing them runs ffmpeg, so the node passes the same resolved
  ffmpeg it runs the pipelines with.

`node/parts.py` gives a laptop `pipeline.CameraPipeline` and the DirectShow
device list directly (no "not in this build" stand-in any more). There is one
fake, `node/video/fake.py`: `FakeCameraPipeline` (start-up delay, scripted
states, `fail`/`recover`, recorded windows, a snapshot, a log tail),
`FakePipelines` and `FakeCaptureDevices`. The node's page shows the
pipeline's restarts, its window apply time and ffmpeg's last 15 lines (with
any piece of the output URL scrubbed again by the node), and a camera's MJPEG
decoder is a setting (CPU by default; the bench says whether QuickSync or
NVIDIA decoding works). A camera's label is not part of what restarts its
pipeline. `pipeline.stop()` can wait for an encoder test in progress; the
node only calls it from worker threads (the settings worker, the camera's
own worker, shutdown), never from the event loop. `tests/rig/test_node_on_real_video.py`
runs the whole node on the real pipeline and a synthetic camera: a `PUT
/crop` moves the picture, the snapshot and the page's figures come from
ffmpeg.

## Video pipeline (node mode)

Code: `server/baja_studio/node/video/`. One ffmpeg per camera: DirectShow
MJPEG capture, a crop window that moves live (ffmpeg's `crop` filter,
commanded through its `zmq` filter), a scale to 1920x1080, an H.264 encode,
MPEG-TS over SRT to the studio's MediaMTX, and a small whole-frame JPEG about
once a second for the studio's preview.

### What was measured first

All on Linux (a 2-core VM) with ffmpeg 6.1 (Ubuntu's, built with libzmq and
libsrt) and the pinned MediaMTX v1.21.3, a synthetic 4K30 camera (`lavfi`),
x264. CI repeats the functional checks with the pinned ffmpeg (8.1.3) on Linux
and on Windows (`tests/rig/test_node_*`).

1. **The zmq + crop route works.** The `crop` filter's `w`, `x`, `y` options
   changed through the `zmq` filter take effect at the next frame, without a
   restart: 885 commands in 10 s (30 window updates a second, three commands
   each) were all answered `0 Success`, the output stayed 1920x1080 (checked
   with ffprobe on the file: 149 frames, all 1920x1080, one keyframe), and
   the picture was where the window said (a test picture's brightness read
   back after each move).
2. **A crop size change stalls ffmpeg 6.1 when a `scale` follows the crop
   directly**, unless that scale started in passthrough (window exactly
   1920x1080). ffmpeg does not exit: its main thread idles, the demuxer
   blocks on a full queue, frames stop for good. Cause: the crop's command
   handler re-runs its output configuration, which rewrites the link into
   the scale, so the scale never sees that frames changed size. **A `null`
   filter between them fixes it** (verified for growing and shrinking, from
   the full frame and from 2x, with x264 and a null output). `setsar`,
   `copy` or `fps` there work too; `null` costs nothing.
3. **Latency** (window set → picture changed):
   - command to frame inside ffmpeg (the zmq filter's answer, sent when the
     frame carrying the change passes): 13–30 ms median. The pipeline
     reports it live as `window_apply_ms`;
   - filter graph only (probe right after the scale, prototype): median
     62 ms, max 75 ms, 20 moves;
   - **end to end without network** (encode with x264 veryfast zerolatency,
     MPEG-TS over local UDP, decode in a second ffmpeg): **median 125 ms,
     max 145 ms** (10 moves, rig test `test_window_move_latency_is_measured`);
     the bench measured 151 ms the same way;
   - through SRT (latency 200 ms) → MediaMTX → RTSP → decode: median 341 ms,
     max 396 ms: the SRT latency setting dominates.
4. **SRT failures** (ffmpeg 6.1 → MediaMTX): no receiver → ffmpeg exits after
   about 3.3 s (`Connection to srt://… failed: Input/output error`, exit 251);
   MediaMTX killed hard → ffmpeg exits after about 5.2 s (SRT's peer idle
   timeout). With the snapshot output present, MediaMTX stopping makes
   ffmpeg write `Error submitting a packet to the muxer` and close the live
   output, **but keep running** for the snapshots.
5. ffmpeg repeats the whole output URL, **with the feed's publish password**,
   in its error lines.
6. ffmpeg's `mpjpeg` muxer gives every part a `Content-length`.

### Decisions

#### The pipeline

1. **One ffmpeg per camera** (`ffmpeg_command.py`):
   `[0:v] split → (zmq → crop@window → null → scale=1920:1080 → setsar=1 →
   format) → encoder → MPEG-TS → output URL` and `(fps=1 → scale=320:-2 →
   JPEG) → stdout`. DirectShow input: `-f dshow -rtbufsize <0.25 s of
   MJPEG> -fflags nobuffer -analyzeduration 0 -video_size WxH -framerate F
   -vcodec <decoder> [-async_depth 1 for mjpeg_qsv] -i video=<device>`
   (32 MB and no start-up options until 16 Nov: see "Delay" below). Output
   at constant rate (`-r F -fps_mode cfr`).
2. **The window moves through the `zmq` filter, as the brief expected**,
   with the `null` of measurement 2. The crop's options are written so any
   part of a change is a correct window: height `ow*1080/1920` (never
   stretched), left/top as the centre minus half the size (`x=1700-ow/2`).
   A zoom is one command (`w`), a pan one (`x`), a pan and tilt two; only
   what changed is sent. Width is even, at least 64 px, at most the widest
   16:9 window; the centre is clamped so the window fits.
3. **Window sender** (`window_sender.py`): a ZeroMQ DEALER socket on its own
   thread (a REQ could send one command per frame). `set_window` only stores
   the newest window and wakes the thread: callers never wait for ffmpeg.
   One change in flight at a time (the next is sent when the previous one's
   answers came: one per frame, bursts collapse to the newest); an answer
   missing for 1 s is given up. A refused command is logged once per run.
   pyzmq 27.2.0 (27.3.0 was less than two weeks old).
4. **Encoders** (`encoders.py`): `auto` = NVENC → QuickSync → AMF → x264
   veryfast, each tested with a one-second 1080p test encode using the very
   options of the live feed; a forced encoder is tested too. All: CBR
   (`-b:v = -maxrate = -bufsize`), keyframe every `keyframe_seconds`, no
   B-frames, no scene-cut keyframes, low-latency mode (`-tune ll
   -zerolatency 1` NVENC, `-look_ahead 0 -async_depth 1` QSV with NV12,
   `-usage lowlatency -header_spacing <keyframe interval>` AMF (its H.264
   encoder has no `-header_insertion_mode`: the pin test caught that), `-tune zerolatency
   nal-hrd=cbr` x264). The test is repeated only after a run fails with an
   encoder error, not at every restart.
5. **Snapshots** split off before the crop (the whole frame), once a
   second, 320 px wide, as multipart JPEG on stdout, cut by length. Only the
   newest is kept; it is dropped when a run fails (no stale preview).
6. **Status** from `-progress pipe:2 -stats_period 1`: rates over the last
   four blocks (about 3 s, steadier than one); frames in =
   `frame - dup_frames + drop_frames`; drops = ffmpeg's plus one per
   DirectShow "frame dropped!" warning (counted, kept out of the log tail).
7. **Restarts** (`restart_policy.py`): 1 s doubling to 30 s, back to 1 s
   after 10 s of pictures. Watchdog: no frame 20 s after launch, or frames
   stopped for 5 s → stop the run. A run whose log says the live output
   failed (measurement 4) is ended at once. `failed` (not `restarting`) when
   ffmpeg is missing or no encoder works; still retried every 30 s. A bug in
   the supervisor is logged with its traceback and handled as a failed run.
   Reasons are operator words: "camera not found: …", "camera busy …",
   "camera refused the mode: …", "SRT receiver unreachable: host:port",
   "SRT receiver lost: host:port", "encoder failed: …", "the picture
   stopped for 5 s", "ffmpeg not found: …", each with "— trying again in N s".
8. **Process hygiene** (`ffmpeg_process.py`): stdout and stderr are each read
   by a thread from start to end (a full pipe would freeze ffmpeg, live
   feed included); every process is stopped (terminate, kill after 5 s) and
   reaped; `exited` is set only after both pipes are drained and the exit
   code is known. **On Windows each ffmpeg joins a kill-on-close job object**
   (`windows_job.py`), so a crashed or killed node never leaves an ffmpeg
   streaming and holding the camera. `CREATE_NO_WINDOW` as elsewhere.
9. **Secrets**: every ffmpeg line is scrubbed of the URL's password
   (stream id field 4, `passphrase`, `pass`) before it is kept, logged or
   explained; reasons show only `host:port`. The URL is on ffmpeg's command
   line, visible in the laptop's process list to its own users: ffmpeg has
   no other way to take it. Accepted on a dedicated laptop.
10. **Cameras are identified by DirectShow's alternative name** (the
    `@device_pnp_…` USB path), which tells two S600s apart; `label` (the
    friendly name) is what messages show.
11. **Hardware MJPEG decode is an option, not a default**: `decoder` =
    `mjpeg` (CPU) unless the settings name `mjpeg_qsv` or `mjpeg_cuvid`. The
    bench tries both and reports whether they work and what they save.

#### The ffmpeg build

12. **BtbN FFmpeg-Builds, ffmpeg n8.1.3-27-g8dca1586f4, win64 GPL static,
    release `autobuild-2026-10-31-12-59`** (the newest build at least two
    weeks old on 2026-11-15; BtbN keeps month-end builds and deletes daily
    ones after about two weeks), pinned with its SHA-256 in
    `installer/ffmpeg-node/pin.json` (same fields as `installer/ffmpeg`, plus
    `license_in_zip` and the matching `linux64-gpl` build for CI). Gyan.dev
    "essentials" (the studio's) has no zmq; gyan "full" has everything but no
    Linux build of the same version, so CI could not run what the laptops run.
    *Changed on 2026-11-16 (first laptop bench): the pin was ffmpeg 9.0.2,
    whose NVENC needs NVENC API 13.1, that is NVIDIA driver 610 or newer.
    Pascal cards (the XPS's GTX 1050 Ti) stop at driver branch 580 (API
    13.0), so NVENC failed with "Driver does not support the required nvenc
    API version. Required: 13.1 Found: 13.0". BtbN builds ffmpeg 8.0/8.1
    with nv-codec-headers `sdk/13.0` (driver 570+) and 9.x with the newest
    headers (`scripts.d/50-ffnvcodec.sh`), so the pin is the 8.1.3 build from
    the same release. Before moving the pin to 9.x or later, check which
    headers that build uses and that the oldest laptop's driver supports
    them; `test_ffmpeg_pin_file.py` refuses 9.x until then.*
13. **How its features were verified**: SHA-256 of both downloads checked
    against the release's `checksums.sha256`; the configuration string inside
    `ffmpeg.exe` (read with `strings`, not run) has `--enable-libzmq
    --enable-libsrt --enable-ffnvcodec --enable-cuda-llvm --enable-libvpl
    --enable-amf --enable-libx264`; the binary names `h264_nvenc`,
    `h264_qsv`, `h264_amf`, `mjpeg_qsv`, `mjpeg_cuvid` and `dshow`. CI then
    runs the pinned binary on both runners (`test_node_ffmpeg_pin.py`): its
    version, the filters the graph uses, the encoders and decoders, every
    option the pipeline passes each encoder (`-h encoder=…`), and `dshow` on
    Windows; and the real-pipeline rig tests run with it.

#### Tests and CI

14. Unit tests drive the supervisor on the fake clock with a scripted ffmpeg
    run, the window sender against a fake zmq filter (`inproc://`, behaving
    like f_zmq.c), and the process handling with a small Python program
    standing in for ffmpeg. Rig tests (`-m rig`) run the real pipeline:
    `test_node_video_real.py` (UDP sink, both OSes) and
    `test_node_video_mediamtx.py` (SRT to the pinned MediaMTX, Linux).
15. CI: a step downloads the pinned node ffmpeg on both runners (SHA-256
    checked) and sets `BAJA_STUDIO_TEST_NODE_FFMPEG`; with it set the tests
    never skip. Locally they use `ffmpeg` on PATH if it has zmq, SRT and
    x264, and skip otherwise. The server job also runs `mypy --platform
    win32`, so the Windows-only ctypes code is checked on Linux too.

## Left open

- **Nothing here has met a real laptop.** The ffmpeg pipeline, its encoders and
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

Unverified on Windows hardware, from the video pipeline (the bench,
`python -m baja_studio.node.video.bench`, answers each):

- **DirectShow capture** of the S600 at 3840x2160 MJPEG 30 fps: mode
  negotiation, the real-time buffer (drops vs. delay; see "Delay"), the
  alternative-name device string. The device/mode parsers follow ffmpeg's
  source, not a recorded laptop listing; `bench --raw` prints the real one.
- **Encoders**: NVENC on the XPS's GTX 1050 Ti, QuickSync (libvpl) on the
  ThinkPad T14 Gen 1, two sessions at once; AMF untried (no AMD laptop).
- **Hardware MJPEG decode**: whether `mjpeg_cuvid` works on the 1050 Ti
  (Pascal's NVDEC JPEG support is unclear) and whether `mjpeg_qsv` takes the
  S600's 4:2:2 MJPEG; and how much CPU either saves.
- **CPU**: 4K MJPEG decode + crop/scale per camera on 4 cores (ThinkPad),
  with two cameras, and with the MV app beside it.
- The bench (`python -m baja_studio.node.video.bench`) answers each: which
  encoders work, per camera fps in/out, drops, encoder, bitrate, window
  apply time and move latency, the laptop's CPU for one and two cameras,
  and the two hardware decoders on 20-second passes.

### Delay (16 Nov, first laptop bench)

**What the bench showed.** Camera to ffplay on the streaming PC was about
0.9 s, and to OBS's Media Source about 1.3 s. That was with SRT latency
200 ms and QuickSync encoding, while the node showed 30 fps in and out, 0
dropped, and the window applied in 16 ms. Two more results narrowed it down:
- SRT latency 80 vs 200 ms made no visible difference;
- the camera alone in the laptop's OBS was "effectively instant".

So about 0.7–0.8 s sat inside the node's ffmpeg.

**What it was.** While ffmpeg starts, the camera keeps sending:
- `avformat_find_stream_info` reads frames to look at the stream, and keeps
  them queued;
- DirectShow buffers what arrives while the encoder opens.

That backlog is delay. A laptop with headroom eats it down within seconds.
A laptop that only just manages 30 fps never does, and with no frame dropped
nothing says so.

**How it was measured.** `glass_to_output.py` was a scratch tool, not kept.
- **The stand-in camera:** an ffmpeg writing the wall clock's milliseconds
  into each frame as a 4x4 black and white grid, scaled to 4K and sent as
  MJPEG over local UDP in real time. Like DirectShow, frames keep arriving
  whether or not anyone reads them, and wait in the UDP input's FIFO, the
  `rtbufsize` stand-in.
- **The node:** the node's own `CameraPipeline`, x264, on that input, with
  output to local UDP.
- **The reader:** decodes, averages each cell, reads the time back and
  compares it with the clock.
- **The machine:** a 2-core Linux VM, BtbN ffmpeg 8.1.3.
- **The floor:** the measuring chain alone (camera stand-in straight to the
  reader) reads 155 ms.

Results, camera to output, median:

- **Before** (32 MB buffer, no start-up options):
  - 1.8 s for the first frames;
  - 1.6 s at 2–5 s;
  - 0.9 s at 5–10 s;
  - about 255 ms from 10 s on, once this VM had eaten the backlog.
- **After** (0.25 s buffer, `-fflags nobuffer -analyzeduration 0`):
  - 0.26 s from the first frames;
  - 250 ms throughout.
- **Each change alone:**
  - only the start-up options: first frames 0.24 s;
  - only the smaller buffer: first frames 1.9 s.

  So the start-up options remove the backlog. The smaller buffer caps what a
  later hiccup can add: DirectShow starts dropping (counted) at 62 % full,
  so about 0.15–0.25 s at most, where 32 MB allowed 1–2 s.
- **The node's own share** is about 100 ms (250 minus the 155 ms floor),
  close to the 125 ms move latency measured on 15 Nov.
- **SRT → MediaMTX → RTSP**, measured with the window-move probe on the
  synthetic source:
  - 362 ms (SRT latency 200) and 287 ms (80) with low-delay reading;
  - 430 ms when the reader does not use `nobuffer`.

**Decisions.**
- **The real-time buffer is about 0.25 s of MJPEG**
  (`capture_buffer_bytes`): 0.12 bytes a pixel, the high end of 4K MJPEG
  frames, at least 4 frames, about 7.5 MB at 4K30.
- **`-fflags nobuffer -analyzeduration 0`** on the camera input, and on the
  synthetic input too, so the rig and CI run them with the real ffmpeg.
  - `-probesize 32` was tried as well. On raw MJPEG it left ffmpeg unable to
    work out the frame rate, and the pipeline never produced a frame. It is
    not used: DirectShow gives the size and rate itself, so the default
    probe size costs only the frames `nobuffer` drops anyway.
- **QuickSync's MJPEG decoder at `-async_depth 1`** (its default of 4 is 4
  frames of delay, per its own help). If that costs throughput, it shows as
  input fps below 30 on the node's page. To bench.
- **`-muxdelay 0 -muxpreload 0 -flush_packets 1`** on the MPEG-TS output
  were tried and made no measurable difference with MediaMTX in between
  (354 vs 362 ms, within noise), so they are not used.
- **The snapshot branch cannot hold up the live one**: its stdout is read
  continuously by its own thread (`ffmpeg_process.py`), as the child-process
  convention requires.
- **Encoders** were already at their lowest-latency settings: no B-frames,
  NVENC `-tune ll -zerolatency 1`, QuickSync `-look_ahead 0 -async_depth 1`,
  x264 `zerolatency`. QuickSync's `-low_delay_brc` exists in 8.1.3 but is
  not set. It is untried on these iGPUs, and an option the encoder refuses
  would fail its test encode and lose QuickSync altogether.

**Still to measure on the laptops:** the same stopwatch photo, before and
after, per camera (bench sheet step 2b).
