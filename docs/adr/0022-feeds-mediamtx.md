# ADR 0022 — WP14: Feeds, a bundled MediaMTX for the drone and remote cameras

Date: 2026-11-15. Work item: LIVESTACK-65. Brief: Plane page "23 — WP14
brief". Runs beside WP13 (ADR 0021: crop cameras in a remote OBS, whose
laptops send their picture here over SRT). Bench:
[docs/bench/wp14.md](../bench/wp14.md).

## What was measured first

MediaMTX **v1.21.3** (released 2026-10-25, the newest stable release at least
two weeks old on 2026-11-15; v1.21.4 came out that day) was run on Linux with
the config the studio writes, and ffmpeg 6.1 pushing test pictures. These
results decided the design; each is modelled by the fake
(`fakes/fake_mediamtx.py`) and re-checked by the rig tests
(`tests/rig/test_mediamtx_real.py`, run in CI against the pinned Linux build):

1. **Default ports** (from its sample `mediamtx.yml` and `conf.go`): RTSP 8554
   (plus RTP/RTCP UDP 8000/8001 and multicast 8002/8003 unless RTSP is TCP
   only), RTMP 1935, **HLS 8888 — the studio's port**, WebRTC 8889 (+ UDP
   8189), SRT 8890/UDP, **MoQ 8892 (TCP and QUIC) and 8893** (new in 1.21),
   API 9997, metrics 9998, pprof 9999, playback 9996. The API, metrics, pprof
   and playback are off by default in 1.21; HLS, WebRTC, MoQ, RTSP, RTMP and
   SRT are on.
2. **Hot reload works**, and live publishers on paths whose config did not
   change keep going (same publisher, same `onlineTime`).
3. **A second change within one second of a reload is dropped**, not
   postponed: its file watcher (`internal/confwatcher`) ignores events less
   than a second after the last reload. Two writes 0.3 s apart: only the
   first was ever loaded.
4. **A reload that fails stops MediaMTX**: a config it cannot load, or a port
   it cannot open (rtmpAddress moved onto a taken port: "bind: address
   already in use", then exit). As a service, NSSM would restart it into the
   same failure, with every feed down.
5. **Unknown path names are refused**, and the only trace is a log line
   (`closed: path 'x' is not configured`); a wrong password logs
   `failed to authenticate`. The API shows neither.
6. **`authInternalUsers[].pass` accepts `argon2:<encoded hash>`**; an
   argon2id hash made with Python's `argon2-cffi` was accepted for RTMP
   (`?user=&pass=`) and SRT (`streamid=publish:path:user:pass`). The API
   answers `<redacted>` for passwords.
7. **Environment variables override the file** (`MTX_<FIELD>`), on start and
   on every reload; the rig test uses that to move a golden config's ports.
8. `recordPath` must contain `%path`, and MediaMTX warns without `%z`.
   Recordings are deleted after one day by default (`recordDeleteAfter: 1d`).

## Decisions

### The service

1. **MediaMTX is its own Windows service, `BajaStudioFeeds`**, registered by
   the installer with NSSM like Caddy's `BajaStudioGateway`, from
   `{app}\mediamtx\mediamtx.exe`, working folder `<data>\mediamtx`, started with
   `mediamtx.yml`. Unlike the gateway it has **no `DependOnService`**: stopping
   or restarting the studio must not stop the feeds OBS shows. The installer
   writes the default config before the first start
   (`python -m baja_studio.modules.feeds.mediamtx_config --if-missing`), starts
   the service right after the studio (it needs nothing from it), stops it
   before an upgrade replaces the program folder, and removes it on
   uninstall. Its output goes to `<data>\logs\mediamtx-stdout.log`.
2. **Pinned like ffmpeg**: `installer/mediamtx/pin.json` (version, Windows zip
   URL and SHA-256, plus the Linux build's URL and SHA-256 for CI);
   `build.ps1` checks the hash, copies `mediamtx.exe` and its MIT `LICENSE`
   (as `LICENSE.txt`), and checks `mediamtx.exe --version`. The fake's
   `VERSION` must equal the pin (a test fails otherwise), so a new pin means
   measuring again.

### The config

3. **The studio writes `mediamtx.yml`; MediaMTX reloads it by itself.** Its
   body is JSON (which is YAML), so the studio and the fake can read it back
   with the standard library. It turns off HLS, WebRTC, MoQ, playback,
   metrics and pprof; RTSP is TCP only (no UDP ports) and on 127.0.0.1 unless
   the setting `rtsp_on_lan` is on; the API is on 127.0.0.1 only. Only the
   listed feeds are paths; "accept any name" (bench) adds the `all_others`
   catch-all. Reading is allowed from loopback only (unless RTSP is on the
   LAN), publishing from anywhere. Recordings: fMP4, 10-minute segments,
   `<recordings>/%path/%Y-%m-%d_%H-%M-%S-%f_%z`, never deleted
   (`recordDeleteAfter: 0s`).
4. **Applying is writing, with three precautions** (`controller.py`), one
   per measured behaviour: ports the new config *adds* are checked before
   writing (4), and a taken one refuses the change, names the program and
   leaves MediaMTX running; writes are at least two seconds apart (3); and
   every write is confirmed: the file carries a fingerprint of its content in
   `sysLogPrefix` (unused: logs go to stdout), which the API reports back. If
   it does not match within 4 s the file is written again (a new file, which
   the watcher sees), twice, then the service is restarted. Nothing else ever
   restarts it: no setting needs a restart.
5. **The studio's start writes nothing if nothing changed.** Rendering is
   deterministic (feeds sorted by name, password hashes stable), so a studio
   restart or upgrade leaves the file, MediaMTX and every feed alone.
6. **The studio only reads the control API** (`/v3/info`, `/v3/paths/list`,
   `/v3/config/global/get`), every two seconds, on 127.0.0.1 with 2 s
   timeouts. The file is the only way it changes MediaMTX, so a MediaMTX that
   restarts comes back exactly as configured.

### Passwords

7. **A publish password is a secret** (`json_schema_extra={"secret": True}`):
   masked in the settings API and the export, scrubbed from diagnostics, never
   in state, logs or command results. Feeds are edited **through the settings
   API, never by command**, because commands and their arguments go to the
   command log.
8. **MediaMTX gets an argon2id hash, never the password** (`passwords.py`,
   new dependency `argon2-cffi`). The conventions say secrets never go to a
   file and reach a program through its service environment (Caddy's token).
   That rule is for secrets a program *uses*; MediaMTX only *checks* the
   password, which is what a hash is for. Through the environment it would
   have been `MTX_AUTHINTERNALUSERS_<index>_PASS`, tied to the user's position
   in the file: every feed added or removed would have needed a service
   restart, dropping every live feed, with a window where old variables
   applied to the new file. The hash is salted with a random per-install
   value and the feed's name, so it is the same at every write (decision 5).
   A rule was added to the conventions.
9. **"Accept any name" and passwords exclude each other** (settings
   validation): with the catch-all anyone may publish on every name, so a
   password would protect nothing. Sender URLs show `<password>` where the
   password goes; the page never has it.

### What OBS reads, and Add to OBS

10. **OBS reads RTSP over TCP from 127.0.0.1** (`rtsp://127.0.0.1:8554/<name>`,
    `ffmpeg_options: rtsp_transport=tcp`). Reasons, documented rather than
    measured in OBS (the bench sheet measures the delay): on loopback nothing
    is lost, so TCP costs no retransmission delay, and RTSP has no jitter
    buffer of its own; SRT would add its receiver latency window (120 ms at
    least) to a hop that never loses a packet. No setting: if the bench finds
    otherwise, it becomes one.
11. **Add to OBS creates, and only creates** (`obs_media_source.py`): through
    playout's OBS connection (the `ObsGateway` of ADR 0020), after the
    operator picked a scene and confirmed, one `ffmpeg_source` named after the
    feed's label (`is_local_file` false, `reconnect_delay_sec` 2, `hw_decode`,
    `buffering_mb` 1, `restart_on_activate`, `close_when_inactive` false). A
    name OBS already uses (an input *or a scene*: OBS names are shared) is
    refused, never changed. `CreateInput` joined the typed requests
    (`adapters/obs_requests.py`) and the fake OBS.

### Health and the page

12. **Health**: `offline` while the API does not answer, saying why (a port
    holder named through `netstat`/`tasklist` on Windows, asked at most every
    30 s; the service stopped; not installed); `degraded` when settings could
    not be applied or a reload was not confirmed; waiting feeds are normal and
    do not degrade it. The worker keeps looping while MediaMTX is down
    (`report_health` instead of raising), so a settings change that fixes the
    cause is written at once rather than after the supervisor's backoff.
13. **Readiness** (`readiness.py`): MediaMTX answering; its ports (passed
    while it answers, since it would have exited otherwise); its config
    confirmed; every feed live (a warning, not a failure: the drone may be on
    the ground); room on the recordings disk when a feed records.
14. **The Feeds page** (`/feeds`, in the top bar): cards (live / waiting /
    unknown, since, protocol, size and codecs, bitrate from the byte counter's
    growth, readers), sender URLs per LAN address with copy buttons (falling
    back to selecting the text where the browser offers no clipboard), the
    URL OBS reads, Add to OBS, edit and remove; senders refused for an unknown
    name (read from MediaMTX's log); MediaMTX's last log lines while it is
    down. Ports, accept-any, the recordings folder and the LAN address go
    through the generic settings form on the same page (the feed list is
    `managed_elsewhere`), and the generic Settings page links there.
15. **Firewall**: the installer's task adds TCP 1935 to the existing rule and
    a second rule, "Baja Studio feeds (LAN, SRT)", for UDP 8890 (netsh rules
    have one protocol). RTSP stays closed. Ports changed in the settings must
    be opened by hand (the settings say so).

### Tests and rig

16. The fake MediaMTX is a runnable program in the rig (started on loopback,
    watching the rig studio's config, the bundle's two feeds publishing), and
    in-process for tests. Its JSON has the shape of answers recorded from the
    real binary (`tests/modules/feeds_fixtures/`). Golden configs live in
    `tests/modules/feeds_golden/`; the real binary loads each one in the rig
    tests.

## Left open

- **OBS's SRT `latency` unit.** The laptop URL carries `latency=<ms × 1000>`
  (microseconds, libsrt's URL syntax, as ffmpeg reads it); OBS's own SRT
  output is believed to read it the same way. The bench sheet checks it.
- **`buffering_mb` 1 and `restart_on_activate`** are what the brief asked
  for; the bench measures the delay and whether reopening on scene changes is
  wanted with a live feed.
- **The firewall for changed ports** is not followed: the service runs as
  LocalSystem and could, but changing the firewall from a web page is a
  privilege the studio does not take yet.
- **Diagnostics bundle**: it already carries `mediamtx-stdout.log` (the logs
  folder); `mediamtx.yml` could join the Caddyfile there.
- **Windows-only paths are not exercised in CI**: `netstat` UDP parsing is
  tested on recorded-shaped output, and the installer's service and firewall
  steps are reviewed by hand (PR description) and by the release smoke test.
