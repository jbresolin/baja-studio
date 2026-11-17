# Bench sheet — the camera node on the NanoPi R3S LTS

For João, on the NanoPi R3S LTS with one EMEET S600 in its USB 3 port.
Decisions: [ADR 0024](../adr/0024-nanopi-camera-node.md). Write each result
next to its step (date, what you saw); a step that fails: what happened,
and the node's log (`journalctl -u baja-camera-node`, or its page).

## 0. The board

- [ ] Flash FriendlyElec's **Ubuntu Noble Core** image for the R3S (vendor
      kernel 6.1; not Armbian's mainline image, which has no video codec
      driver) to the eMMC or a card. Log in over SSH (user `pi`, see
      FriendlyElec's wiki), `sudo apt update && sudo apt full-upgrade`.
- [ ] `ls -l /dev/mpp_service /dev/rga /dev/dma_heap/` — all three exist.
- [ ] Plug in the S600; `ls -l /dev/v4l/by-path/` shows a `...-video-index0` link.

## 1. Install

- [ ] Copy `BajaCameraNode-<version>-linux-arm64.tar.gz` (GitHub Release, or
      the release workflow's artifact) to the board, `tar xzf` it, and
      `sudo ./BajaCameraNode-<version>-linux-arm64/install.sh`. It ends
      with "Baja camera node … is running" and the page's address.
- [ ] `ls -l /dev/mpp_service /dev/rga` now shows group `video`.
- [ ] The page (`http://<board>:8891/`) shows the node, in red "No token
      set". Name it, set a token, Save.

## 2. The video bench

- [ ] `sudo systemctl stop baja-camera-node` (it would hold the camera), then
      `sudo -u baja-node /opt/baja-camera-node/venv/bin/python -m baja_studio.node.video.bench --cameras 1`
      It prints `decoder: mjpeg_rkmpp (Rockchip board)`, the camera and its
      modes, and `encoder Rockchip MPP (h264_rkmpp): works`. Note: fps in and
      out, dropped frames, bitrate, window apply time, move latency and the
      board's CPU % for the 60 s pass; then the 20 s CPU-decoder pass, for
      comparison.
- [ ] If the encoder "does not work", keep its message (MPP/RGA permission,
      the patch missing, …) and the last lines it printed.
- [ ] `--synthetic`: the same without the camera (test card uploaded to the
      hardware): measures the RGA and the encoder alone. Its move latency is
      the one to note: the test card's corners differ, while a real scene's
      may look alike and the probe misses the move ("moves not seen"). The
      probe decodes the H.264 on the board's own CPU, so its latency and CPU
      include the probe; the studio's OBS is the real judge.
- [ ] `htop` during the pass: what takes the CPU (ffmpeg's demux thread, the
      snapshot JPEG, Python)? `cat /sys/class/thermal/thermal_zone0/temp`
      after 10 minutes (the R3S has a metal case and no fan).
- [ ] If 4K30 drops frames: run with `--mode 2560x1440@30`, then
      `--mode 1920x1080@30`, and note which holds 30 fps.
- [ ] `sudo systemctl start baja-camera-node`.

## 3. Image controls

- [ ] On the studio, add the node (Agents: address, port 8891, the token) and
      its camera (Camera setup). The Image page lists exposure (in −13 … −1,
      like a laptop's S600), gain, white balance, focus, brightness…
- [ ] White balance: auto off, 5000 K: the picture changes. Back to auto.
      Exposure: manual −6, then −5: one stop brighter.
- [ ] *Copy settings from* a laptop's S600 to this one: the same exposure
      stop and temperature arrive.
- [ ] Unplug and replug the S600 with manual settings: within a few seconds
      the node writes them back (its log says "reopened").

## 4. Feed and window

- [ ] Studio, Feeds: a feed for the NanoPi camera; paste its SRT URL into the
      node's page, Save. The feed goes live; OBS shows 1920x1080 at 30 fps.
- [ ] Move the camera from the studio (joystick or page): the picture pans,
      tilts and zooms smoothly to 2×; the node's page shows the window apply
      time. Note the move latency you see on the studio's OBS.
- [ ] Pull the network cable for 10 s: the node's page says "SRT receiver
      lost" then running again; the feed comes back by itself.
- [ ] Unplug the camera while streaming: "camera stopped sending", then back
      once replugged.

## 5. Power and boot

- [ ] Power the board off and on: the service starts by itself, the feed
      comes back without anyone logging in.
- [ ] Note the board's power draw while streaming (USB-C meter), for the
      trackside battery.
