# ADR 0021 — WP13: crop cameras in a remote OBS

Date: 2026-11-15. Work item: LIVESTACK-64. Brief: Plane page "22 — WP13
brief". Builds on [ADR 0020](0020-obs-crop-cameras-and-previews.md) (OBS crop
cameras and previews). Runs beside WP14 (ADR 0022, the Feeds module), which
carries the laptops' finished pictures to the streaming PC. Bench:
[docs/bench/wp13.md](../bench/wp13.md).

At the race a 4K webcam sits on a trackside laptop; the laptop's own OBS
crops it with the "Baja crop" filter and sends only the finished 1080p
picture. The studio drives that filter exactly as it drives one in the
streaming PC's OBS: same joystick, web stick, keys, presets, click-to-centre
and previews.

## Decisions

### Settings

1. **`crop.obs`** on an `obs_crop` camera (`cameras/crop_config.py`,
   `ObsConnection`): `remote` (default false: playout's OBS on this PC,
   exactly as before), `host`, `port` (4455) and `password`, marked
   `json_schema_extra={"secret": True}` like playout's OBS password, so the
   settings API masks it, an export leaves it out and lists it, and the
   diagnostics bundle scrubs it. A remote OBS needs a host. Unticking
   "remote" keeps the address and password (they are simply not used).
2. **No settings version bump.** The new field has a default that means what
   older files meant (playout's OBS), so no older file changes meaning.
3. **`connection_key` includes the OBS** (`None` for playout's, else
   `(host in lower case, port)`) but **not the password**: another OBS means
   another driver on another connection; a new password only redials the
   shared connection, and the drivers on it stay (and re-read OBS when it is
   back, ADR 0020 rule 6).

### One connection per remote OBS

4. **A pool in the cameras module** (`cameras/remote_obs.py`,
   `RemoteObsPool`) hands each crop driver its `ObsGateway`: playout's for
   this PC, else the connection for its host and port. The registry leases
   when it builds a driver and releases after the driver's worker has
   stopped; the first lease opens the connection, the last release closes
   it (camera removed, disabled, or moved to another address or back to this
   PC). Stopping waits for the connection's worker outside every lock, like
   stopping a driver.
5. **Reuse, don't fork.** A connection (`cameras/remote_obs_connection.py`,
   `RemoteObsConnection`) is the client playout uses
   (`adapters/obs_websocket.ObsWebsocketClient`), reconnecting with the same
   `ReconnectBackoff` (0.5, 1, 2, then every 4 s) and logging repeated
   identical failures once (`FailureStreak`). It implements `ObsGateway`, so
   `obs_crop.py`, `previews.py` and `obs_commands.py` only learn "which
   gateway". **Playout is not touched**: the reusable parts (client, backoff,
   errors) were already in `adapters/`; what is left in playout's
   `ObsConnectionWorker` is playout policy (program scene, cue health, scene
   list), so nothing was extracted.
6. **Its worker** (`remote-obs:<host>:<port>`, a cameras worker, left out of
   the module's health summary because each camera already reports it)
   connects with 3 s connect and request timeouts (a radio link is slower
   than the PC's own OBS), subscribes to FILTERS and INPUTS events only
   (less traffic over a radio; playout's media and scene events are of no
   use here), and redials when the password it should use changes.
7. **Keep-alive.** obs-websocket sends no pings and the client sends none
   (`adapters/obs_transport.py`), so a pulled cable closes nothing: TCP keeps the socket for
   minutes while every request times out. While connected, the worker asks
   `GetVersion` every 2 s; no answer in 3 s drops the connection and redials,
   which also recovers at once when the cable is back. When the studio
   closes a connection itself (keep-alive, new password) it tells the
   listeners `CONNECTION_LOST`, as the client does when OBS closes it, so
   every camera re-reads its filter and size when the link is back.

### Health in operator words

8. **`ObsLinkView` gained `status` and `where`.** `where` is "" for
   playout's OBS and "host:port" for a remote one; `problem`, `lost(...)` and
   `refused(...)` put a connection's trouble into words. Playout's words are
   unchanged ("OBS offline: …", "OBS: …"). A remote OBS's are its own
   reason, the same for every camera on that laptop:
   - unreachable: `offline`, "OBS at 192.168.10.50:4455 unreachable: …";
   - wrong password: **`degraded`** (that OBS answered; the settings are
     wrong), "OBS at … refused the password (check it on Camera setup)";
     none set: "OBS at … asks for a password: set it on Camera setup";
   - dropped: "OBS at … closed the connection; reconnecting", or "stopped
     answering; reconnecting" (keep-alive);
   - a request that timed out: "OBS at … offline: OBS did not answer … in time".
   Cameras pick up a new connection state at their next step (within their
   1 s idle step), so the two cameras of one laptop agree within a second.
9. Readiness's fix for a remote crop camera sends the operator to the laptop
   (power, network, OBS's WebSocket server and port, the password on Camera
   setup), not to Playout setup.

### Two cameras, one laptop

10. They share the connection; each has its own source, filter, worker and
    window; events are already routed by source name (ADR 0020), so an event
    for one never moves the other.
11. **Passwords: the camera saved last wins.** One connection can use only
    one password. At each settings change, a camera whose password differs
    from what it had (or that is new) gives its password to its connection;
    if that camera leaves the laptop, the last remaining camera's is used.
    The others get a note in their state (`state.cameras.cameras.<id>.obs =
    {remote, address, note}`), shown on Camera setup: "camera 'lap-b' was
    saved later with another password for OBS at …, and the connection uses
    that one: save the same password on both cameras". No password is ever
    in the state or a note.

### Previews

12. **One cache and one set of slots per OBS.** Screenshots are cached per
    (OBS, source, width), so two laptops' webcams of the same name ("Video
    Capture Device") are never mixed up. Each OBS may hold at most 8 preview
    requests at once (as the one OBS could before), and 12 are served at
    once overall (the web server has 40 threads): a hung laptop makes only
    its own previews wait ("this OBS is slow to answer; trying again"), never
    playout's OBS's or another laptop's. ADR 0020's pacing is unchanged:
    about once a second, 320 px, one request in flight per source.
13. Errors name the remote OBS ("OBS at …: OBS refused GetSourceScreenshot:
    …"); playout's OBS's keep their words.

### Camera setup

14. `list_obs_sources {obs?, camera?}`: without `obs`, playout's OBS as before
    (the page still sends no arguments for "this PC"); with a remote `obs`,
    its open connection if a camera uses it, else a short connection of its
    own (`cameras/remote_obs_probe.py`), always closed.
15. `test_obs_connection {obs, camera?}`: connects with the form's address
    and password and answers `connected` (with OBS's and obs-websocket's
    versions), `unreachable` or `wrong_password`, with the message.
16. `create_crop_filter {camera}` creates the filter in the camera's own OBS.
17. A password sent back as the mask (`********`) means the one saved with
    `camera`. Both `list_obs_sources` and `test_obs_connection` stay out of
    the command log (their arguments hold a password); a malformed `obs` is
    refused naming the field and the problem, never the values.
18. The page: an *OBS* choice (This PC / Remote OBS) with address, port and a
    write-only password (`ObsConnectionFields.tsx`), *Test connection* with
    its answer, the source picker filled from the chosen OBS (asked again
    when the choice changes and after a successful test, not on every
    keystroke), the studio's note, and the camera list showing "OBS crop
    <source> @ this PC" or "@ host:port".

### What a slow laptop can hold up

Only the threads that asked it: its cameras' workers, its connection's
worker, previews of its sources (within its 8 slots) and a Camera setup
command about it (a few seconds). Never playout's connection, another
laptop's, the motion loop (the arbiter only queues for drivers) or a
settings save, except removing a laptop's last camera, which waits for its
connection to close (at most the 3 s connect timeout plus the 5 s join limit,
the same as stopping a driver).

### Tests, fakes, rig, files

19. The ADR 0020 driver suite runs against both gateways (`obs_bench.py`
    `GATEWAY_KINDS`, parametrized, not copied).
20. The in-process OBS network can **hold a remote OBS's answers**
    (`hold_answers` / `release_answers`): requests still reach OBS, nothing
    comes back until released. That is the network misbehaving (a fading
    radio, a pulled cable), not OBS, so the fake OBS itself is unchanged.
21. `crop_studio` (tests/modules) has a second fake OBS, a laptop's with two
    4K webcams, and its `settle` now also waits for workers to notice a
    wake-up (a woken worker counts as asleep for a few milliseconds, which
    made moves race with fake time).
22. The rig starts a second fake OBS (`laptop-obs`, two 4K webcams with
    their filters), the bundle has two remote crop cameras on it (one
    connection), and a `laptop-obs-restart` scenario.
23. Over the ~300-line guideline: `cameras/registry.py` 384 (was 374),
    `cameras/config.py` 360 (was 354), `modules/cameras/__init__.py` 315 (was
    291): the additions are hooks into the new files.

## Left open

- **Bench not run** (docs/bench/wp13.md): the keep-alive and timeouts over
  the real Airmax link, whether a full-size measuring screenshot (up to a 4K
  JPEG at the widest window, ADR 0020 rule 4) fits in the 3 s request
  timeout over the radio, and the laptop OBS's CPU with one and two 4K
  sources.
- A settings save the server refuses (422) echoes the submitted values,
  password included, back to the page that sent them. That is the settings
  API's generic behaviour for every secret (playout's OBS password too), not
  new here; a fix belongs in `core/api.py`.
- The two cameras of one laptop show a new connection state at their own
  next step, up to a second apart.
