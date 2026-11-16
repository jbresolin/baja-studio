# ADR 0023 notes — WP15 part B: the camera node's video pipeline

Notes for ADR 0023 (written by part A), to be folded into it. Branch
`wp15-node-video`. Code: `server/baja_studio/node/video/`. Brief: Plane page
"25 — WP15 brief", §3 and §4.

## What was measured first

All on Linux (a 2-core VM) with ffmpeg 6.1 (Ubuntu's, built with libzmq and
libsrt) and the pinned MediaMTX v1.21.3, a synthetic 4K30 camera (`lavfi`),
x264. CI repeats the functional checks with the pinned ffmpeg 9.0.2 on Linux
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

## Decisions

### The pipeline

1. **One ffmpeg per camera** (`ffmpeg_command.py`):
   `[0:v] split → (zmq → crop@window → null → scale=1920:1080 → setsar=1 →
   format) → encoder → MPEG-TS → output URL` and `(fps=1 → scale=320:-2 →
   JPEG) → stdout`. DirectShow input: `-f dshow -rtbufsize 32M -video_size
   WxH -framerate F -vcodec <decoder> -i video=<device>`. Output at constant
   rate (`-r F -fps_mode cfr`).
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

### The ffmpeg build

12. **BtbN FFmpeg-Builds, ffmpeg n9.0.2-39-g7adfbe40dc, win64 GPL static,
    release `autobuild-2026-10-31-12-59`** (the newest build at least two
    weeks old on 2026-11-15; BtbN keeps month-end builds and deletes daily
    ones after about two weeks), pinned with its SHA-256 in
    `installer/ffmpeg-node/pin.json` (same fields as `installer/ffmpeg`, plus
    `license_in_zip` and the matching `linux64-gpl` build for CI). Gyan.dev
    "essentials" (the studio's) has no zmq; gyan "full" has everything but no
    Linux build of the same version, so CI could not run what the laptops run.
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

### Tests and CI

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

## Proposed convention (for docs/conventions.md)

- **A long-running child process has every pipe read continuously** by its
  own thread, is always reaped, and on Windows joins a kill-on-close job, so
  neither a full pipe nor a crashed service leaves it frozen or orphaned.

## Left open; unverified on Windows hardware

- **DirectShow capture** of the S600 at 3840x2160 MJPEG 30 fps: mode
  negotiation, the 32 MB real-time buffer (drops vs. delay), the
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
