# ADR 0024 — The camera node on a NanoPi R3S (a 4K webcam encoded by a small Rockchip board)

Date: 2026-11-16. Builds on ADR 0023 (the camera node), 0019 (image controls,
the UVC child), 0020 (crop window maths and motion), 0022 (Feeds/MediaMTX).
Bench: [docs/bench/nanopi.md](../bench/nanopi.md).

A NanoPi R3S LTS (Rockchip RK3566: four Cortex-A55 cores, 2 GB, two GbE, one
USB 3 port) is a camera node of its own: one 4K30 webcam in, one 1080p30
SRT feed out, the window moved live by the studio, the image controls on
the studio's Image page. To the studio it is the same agent as a laptop's
node (contract 1.1, `kind=crop` cameras, port 8891): nothing changes on the
studio's side.

## Decisions

### Same program, a hardware path in the pipeline

1. **The node is the WP15 node, unchanged in shape**: `python -m
   baja_studio.node`, its settings, page, agent API, supervisor and UVC
   child. What differs on the board is chosen where it is used: the capture
   API from the device's name (`/dev/...` → V4L2), the video path from the
   decoder, the image-control backend from the platform. No `if nanopi`
   anywhere else.
2. **Decoder `auto` (the new default)**: the Rockchip path on a board with
   `/dev/mpp_service` and `/dev/rga`, the CPU elsewhere
   (`rockchip.resolve_decoder`, settled once when a pipeline is made). On a
   laptop `auto` is the old default (`mjpeg`); saved settings keep what they
   say, so `settings_version` stays 1.
3. **The Rockchip path keeps the picture in hardware from camera to network**
   (`node/video/rockchip.py`): V4L2 MJPEG → `mjpeg_rkmpp` (VPU) → DRM PRIME
   frames → `vpp_rkrga` (the RGA crops the window *and* scales it to 1080p in
   one pass) → `h264_rkmpp` (VPU). The four A55 cores could not decode 4K30
   MJPEG in software, let alone crop and encode. The snapshot is shrunk by
   the RGA to 480 wide (its 1/8 limit) before the only copy to the CPU.
4. **The window moves through the same `zmq` filter and window sender**, with
   the RGA's options instead of the crop filter's
   (`rockchip.rga_window_commands`, given to `WindowSender`): `cw` (zoom),
   `cx` and `cy` as expressions of the centre (`cx=1700-cw/2`), height
   `ch=cw*1080/1920`; one command per change part, as on the laptop. The
   output size never changes, so nothing after the RGA is reconfigured (the
   laptop's `null` workaround is not needed).
5. **ffmpeg-rockchip cannot move the RGA crop while running, so the node's
   ffmpeg carries a patch** (0001; 0002 and 0003 are in decision 8)
   (`installer/ffmpeg-rockchip/0001-vpp_rkrga-move-the-crop-while-running.patch`,
   about 100 lines): `cw`/`ch`/`cx`/`cy` become runtime options of
   `vpp_rkrga`; a command re-evaluates them, clamps the crop into the frame,
   puts it on the RGA's 2-pixel grid, refuses a scale ratio beyond 8 (keeping
   the previous crop) and changes only the source rectangle the next frame is
   submitted with. Commands arrive between frames on the graph's thread (the
   zmq filter), so a frame never sees half a change. The patch only applies
   when the RGA is not also rotating (`transpose`).
6. **The narrowest window on the RGA is 240 px** (1920 / 8,
   `rockchip.min_rga_crop_width`), enforced by the pipeline before a window
   is sent; the node's default `min_window` (0.5, 2× on 4K) is far from it.
7. **Encoders**: `auto` on the Rockchip path is `h264_rkmpp` only (x264 on
   four A55s cannot do 1080p30). Its test encode runs the live path itself
   (a 4K picture uploaded, RGA crop and scale, MPP), then checks the ffmpeg
   carries the patch (`ffmpeg -h filter=vpp_rkrga` shows `T` on `cw`): an
   unpatched ffmpeg would stream fine but never follow the window, so it
   counts as not working, with the reason. `h264_rkmpp` forced without the
   Rockchip decoder is refused untested; x264 forced on the Rockchip path
   gets the picture downloaded first.
8. **Few 4K frame buffers, so a 1 GB board fits** (found on the bench,
   16 Nov: 1080p60 ran, 4K30 froze the 1 GB R3S as its memory ran out). A
   4K frame takes about 16 MB in every buffer that holds one, and stock
   ffmpeg asks the webcam driver for its maximum (32 for uvcvideo) and lets
   the MPP decoder keep 20: more than 850 MB. Two more patches of the
   node's ffmpeg make both counts options (`-v4l2_buffers` on the V4L2
   input, `-frame_pool` on `mjpeg_rkmpp`); the node asks for 4 and 10
   (`rockchip.V4L2_CAPTURE_BUFFERS`, `DECODER_FRAME_POOL`), about 230 MB.
   MJPEG keeps no reference frames, and the graph holds only a few at once
   (the RGA's two in flight, the snapshot's one or two). Only the Rockchip
   path passes them: a stock ffmpeg would refuse an unknown option.
9. **The synthetic camera on the board is uploaded to the hardware**
   (`-init_hw_device rkmpp`, `format=nv12,hwupload`), so the bench measures
   the RGA and the encoder without a webcam.

### V4L2: cameras, modes, controls

10. **ioctls, not a tool's text** (`adapters/v4l2_ioctl.py`): ctypes copies of
   the kernel structures, sizes and request numbers checked against
   `videodev2.h` in a test. A `V4l2System`/`V4l2Device` protocol so the fake
   (`fakes/fake_v4l2.py`) runs the real listing and control code. Nothing is
   kept open: every question opens, asks, closes (an unplug never leaves a
   dead handle). `fcntl` is imported on Linux only: the laptop's node imports
   this module too.
11. **A camera's identity is its `/dev/v4l/by-path/...-video-index0` link**:
    stable per USB port and different for two S600s, the same rule as
    DirectShow's device path (`imaging/usb_devices.py`). The pipeline opens
    the link; the UVC child finds the same camera by it.
12. **Image controls through the same UVC child**, with a V4L2 backend
    (`childprocs/uvc_v4l2.py`) beside the duvc one, chosen by platform
    (`uvc_child.make_backend`; `BAJA_STUDIO_UVC_BACKEND=fake-v4l2` for the
    fake). Auto partners are written first (turning auto off before writing
    a value: uvcvideo refuses a value while its auto is on), and a value sent
    with auto on is not written. A replug shows as a new device node under
    the same link and is reported as `reopened`, so the node writes the
    camera's settings again (as on Windows).
13. **Exposure in DirectShow's units** (whole log2 seconds, one stop a step):
    V4L2 counts 100 µs steps; the backend converts both ways, so every USB
    camera in the studio speaks one unit and *Copy settings from* between a
    laptop's camera and the NanoPi's does not turn 1/32 s into 1/10000 s.

### Packaging

14. **Target image: FriendlyElec's Ubuntu 24.04 (Noble) Core with its 6.1
    vendor kernel**: the MPP and RGA drivers are in the vendor kernel only
    (a mainline image, Armbian's "current", has no `/dev/mpp_service`), and
    Noble has Python 3.12 (`requires-python`) and glibc 2.39.
15. **The node's ffmpeg is built from source in CI**
    (`installer/ffmpeg-rockchip/build.sh`, pinned in its `pin.json`):
    nyanmisaka's MPP (jellyfin-mpp) and librga (jellyfin-rga), and
    ffmpeg-rockchip at a pinned commit with the patch, plus zmq, SRT and
    x264, in an arm64 `ubuntu:24.04` container on GitHub's arm64 runner. MPP
    and librga go in `ffmpeg/lib` beside it (RUNPATH `$ORIGIN/../lib`); zmq,
    SRT, x264 and libdrm come from Ubuntu. The build checks the decoders,
    encoders, filters, SRT and the patch's runtime flags. jellyfin-mpp is
    rebased onto Rockchip's MPP as a whole, so its newest commit is younger
    than the two weeks the other pins wait: noted in the pin.
16. **`BajaCameraNode-<version>-linux-arm64.tar.gz` and `install.sh`**
    (`installer/linux-node/`): a wheelhouse for arm64 Python 3.12 (the node's
    dependencies without python-rtmidi, which only the studio's audio uses:
    `release_tools linux-node-requirements`), the node's page, the ffmpeg
    tarball, a systemd unit and a udev rule. `install.sh` installs or
    upgrades in place (settings kept), `--uninstall` keeps the settings,
    `--purge` removes them. The **service user `baja-node`** (groups video,
    render) runs `baja-camera-node.service`: at boot, restart on exit, SIGINT
    with 15 s to stop, data in `/var/lib/baja-camera-node`. **systemd's
    control-group kill is the Linux side of the Windows job object**: every
    ffmpeg the node started goes with it. The udev rule gives group video
    `/dev/mpp_service`, `/dev/rga` and the DMA heaps.
17. **The release workflow builds and smoke-tests it on an arm64 runner**:
    install, `/agent/info` (contract 1.1), the page, the ffmpeg's patch
    flag, an upgrade in place, uninstall; a tag attaches the package to the
    release. The runner has no Rockchip hardware: the hardware path is the
    bench's.

## Left open

- **Nothing here has met the board.** The patch is compile-checked only by
  CI; MJPEG 4K30 decode on the RK3566's VPU (its JPEG decoder's throughput
  at 4K), the RGA's crop and scale per frame, the encoder at 8 Mb/s, the
  window move latency, the snapshot branch and the CPU left over: the
  bench sheet.
- Whether uvcvideo on the vendor 6.1 kernel refuses a value while its auto
  is on (EACCES) or takes it silently: the backend copes with both; the fake
  models the refusal.
- The S600's real V4L2 control ranges and its `auto_exposure` menu (the fake
  assumes manual and aperture priority).
- The failure messages for V4L2 and MPP/RGA follow ffmpeg's and
  ffmpeg-rockchip's sources, not a recorded board log.
- The node's `ffmpeg_path` setting defaults to the bundled build; a system
  ffmpeg without the patch is refused by the encoder test with a reason.
- Whether 10 decoded frames are enough for what the graph holds (`split`,
  the RGA's two in flight, `fps=1` on the snapshot branch); too few shows
  as stalls on the bench, and the pool grows by a frame or two.
- Whether the S600's 4K MJPEG buffer is really 16 MB
  (`v4l2-ctl --get-fmt-video` shows its `Size Image`).
- One camera per R3S: two 4K MJPEG streams would not fit its decoder (and
  it has one USB 3 port).
