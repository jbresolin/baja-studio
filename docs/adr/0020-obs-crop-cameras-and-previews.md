# ADR 0020 — WP6b: OBS crop cameras and camera previews

Date: 2026-11-14. Work item: LIVESTACK-18 (WP6, part b). Brief: Plane page
"20 — WP6b brief". Runs beside WP6a (ADR 0019: image settings, the UVC child,
the Pixy driver). Bench: [docs/bench/wp6b.md](../bench/wp6b.md).

## Decisions

### One OBS connection

1. **Playout lends its connection as an `ObsGateway`**
   (`cameras/drivers/obs_gateway.py`, implemented by
   `modules/playout/obs_gateway.py`), the way the agents module is handed to
   cameras as an `AgentGateway`. `build_module_list` creates playout first and
   passes `playout.obs_gateway` to `CamerasModule(obs=...)`. The gateway gives
   the current client, forwards OBS events (and a `StudioObsConnectionLost`
   pseudo-event) to the cameras' listeners, keeps them across a playout
   restart, and drops events from a replaced client.
2. **Why OBS is away is playout's own words** (`state.playout.obs.message`:
   "OBS not configured: …", "cannot reach OBS …"), or "the playout module is
   not running". Crop cameras are `offline` with "OBS offline: <reason>";
   previews answer 503 with the same text. Without a gateway (tests, a
   cameras module alone) `NoObsGateway` says "no OBS connection".

### The OBS crop driver (`obs_crop`)

3. **Crop/Pad filter in its "relative" mode** (edges cut from the source).
   The driver writes only `relative, left, top, right, bottom`
   (`SetSourceFilterSettings`, overlay). A filter with Relative unticked is
   `degraded` with that reason and left alone: the studio never changes a
   filter's mode, and never creates one except through Camera setup's
   **Create filter** (`create_crop_filter`: `CreateSourceFilter`, kind
   `crop_filter`, all edges 0). A missing filter is `degraded` ("use Create
   filter on Camera setup"); a missing source `offline`; a disabled filter
   `degraded` but still moved.
4. **The source's size.** obs-websocket has no request for a source's own
   size: `GetSourceScreenshot` (and a scene item's `sourceWidth`) are the
   size *after* filters (`obs_source_get_width`, read in obs-websocket's
   source). So the driver takes one full-size screenshot and adds the
   filter's edges back: `width = shot + left + right`. OBS applies new edges
   at its next frame, so it measures only after 0.25 s without a write of
   its own, and reads the filter again after the screenshot (a change
   meanwhile throws the measurement away). It measures on connect, after
   OBS comes back, when the filter appears, and on `InputSettingsChanged`
   for the source (a new resolution). PNG and JPEG sizes are read from the
   image header (`adapters/obs_images.py`), no image library.
5. **A source that shrank under its crop** (4K → 1080p with 1920 px cut)
   leaves OBS nothing to show, and nothing to measure. If the
   `InputSettingsChanged` names a `resolution` ("1920x1080", what a video
   capture device with a custom resolution reports), the driver writes the
   window for that size and measures again. Otherwise the camera is
   `degraded` ("no picture"), and **Reset crop** (`reset_crop`) shows the
   whole picture again.
6. **Connecting never moves the picture.** The driver adopts the crop OBS
   has as its window (the nearest allowed one) and writes nothing until a
   command moves it. An edit made in OBS's filter dialog (a
   `SourceFilterSettingsChanged` that is not our echo) is adopted the same
   way. Our own writes come back as that event too (obs-websocket 5.4+):
   one equal to the last write, or within 0.5 s of a write, is an echo
   (the write-protection rule).
7. **Units are the contract's crop units** (`cameras/crop_window.py`): centre
   in source fractions from the top-left, positive tilt up, width as a
   fraction of the source's width with the height from the output aspect
   (`crop.aspect`, default 16:9), so the window always has the output's
   shape. Zoom 0 is the widest window that fits (the whole width on a 16:9
   source; less on a wider one), 1 is `crop.min_window` (default 0.5: 1080p
   on 4K); never narrower. Zoom is linear in the width, around the centre;
   the window is clamped inside the frame after every step. Speeds are
   per camera (`crop.pan_speed`/`tilt_speed`: frames per second at full
   deflection, default 0.3; `zoom_speed`: zoom range per second, 0.5). They
   do not slow down when zoomed in, as the contract defines them.
8. **Velocity integration in the worker** (`camera:<id>:obs`) at
   `crop.update_hz` (default 25) while moving, 1 s idle (commands and OBS
   events wake it). The held velocity has the arbiter's ttl as its
   dead-man (`obs_crop_motion.py`). Pixels are rounded and a write happens
   only when they changed (holding a direction at an edge sends nothing). A
   new velocity applies from the last step, at most one update early.
9. **Presets are stored windows** (`crop.windows`: `n`, `centre_x`,
   `centre_y`, `width`) in the camera's settings, `preset_max` 127.
   `store_preset` is saved by the driver's worker through the registry
   (never under the arbiter's lock); a recall jumps there, as `PUT /crop`
   does on an agent. Preset *names* stay in `presets`, as for VISCA.
10. **Click-to-centre and reset** are `centre_window {camera, x, y}` and
    `reset_crop {camera}`, through the arbiter's new `place_window`, which
    ends any velocity and obeys the holder rule like a preset recall.
    Drivers that can jump a window implement `WindowPlacingDriver`.
11. **State**: a crop camera publishes `crop {x, y, w, h, centre_x,
    centre_y, zoom, magnification, source_width, source_height, filter}`
    and the contract's crop `position` (centre and zoom), at most ten
    times a second while moving and at once at rest.
12. The `obs_source` field is the crop camera's address (required for
    `obs_crop`, no host). `connection_key` is unchanged, so editing the
    source, filter or limits needs no new driver: they are read every step.

### Agent crop cameras

13. The agent pushes the window's centre and zoom but not its size, which
    the preview needs. `AgentCropMirror` (`agent_crop.py`) reads `GET /crop`
    when the link comes up and whenever the pushed position changed (at
    most every 0.2 s), never on a timer at rest, and publishes the same
    `crop` keys it can know (no source size; magnification `1/max(w, h)`).
    Click-to-centre is a `PUT /crop` of the same size, queued in order with
    the camera's other commands; the answer is published at once.

### Previews

14. **Transport: an HTTP endpoint the page polls**, `GET
    /api/cameras/preview/{camera}?width=320` (JPEG from OBS). Not the state
    tree: state goes to every page; a picture only to the page that shows
    it, at its own pace. The page fetches again only after the previous
    answer arrived, about once a second, so a slow OBS slows the page and
    is never flooded.
15. **OBS is asked only on demand**: per (source, width) an answer (or
    OBS's refusal) is reused for 1 s, and at most one request is in
    flight; pages asking meanwhile wait for it. With no page showing
    previews nothing is asked. The component stops fetching when
    unmounted, off screen (IntersectionObserver) or when the tab is hidden.
16. **A crop camera's preview cannot show the unfiltered source**: a
    screenshot is rendered with the source's filters, and a capture device
    cannot be opened twice to make an unfiltered copy. A scene holding the
    source shows it filtered too. Decided:
    - the screenshot (the window) is placed on the whole frame at the
      window it was taken at (`X-Preview-Shows: window`, `X-Crop-Window:
      x,y,w,h`, read just before the screenshot); the page keeps the last
      12 windows it saw at different places, dimmed, around the live one,
      so the frame fills in as the camera moves (a fixed camera's
      surroundings change little); the window's outline is drawn from the
      live state, so it moves at once;
    - `crop.preview_source` (optional) names an OBS source that shows the
      camera uncropped (e.g. a Source Clone without filters, if that
      plugin is installed); then the preview is that whole picture with the
      outline on it (`X-Preview-Shows: full`).
17. `CameraPreview` (`components/cameras/CameraPreview.tsx`) reads the
    camera's state itself. Props: `camera` (id), `width` (default 320),
    `overlay` (default true), `clickToCentre` (default false). The box has
    the source's shape (source pixels, or for an agent crop the picture's
    shape scaled by the window's), and shows "1.6×" for crop cameras.
    Agent PTZ and VISCA cameras show their `obs_source` if set.

### Fake OBS, rig, files

18. The fake OBS (`fakes/fake_obs_sources.py`) models the Crop/Pad filter
    from OBS's `crop-filter.c` and screenshot sizes from obs-websocket's
    source (read, not measured: the bench confirms them), the filter and
    input events, OBS's error codes, and draws a checkerboard so a crop
    shows which part it holds. It answers every screenshot in PNG (no JPEG
    encoder), and says so in its data URI. New flags: `--video-source
    NAME=WxH`, `--crop-filter NAME`. The rig starts it with a 4K webcam and
    its filter, and the bundle has an `obs_crop` camera on it ("rig-crop").
19. `cameras/config.py` stays at 300 lines: the crop settings are
    `cameras/crop_config.py`. Over the ~300-line guideline:
    `cameras/registry.py` (340, was 320) and `cameras/drivers/agent_proxy.py`
    (368, was 351); the additions there are hooks into new files.

## obs-websocket requests and fields relied on

Checked against the 5.x protocol document; João's OBS is 33.0.1 with
obs-websocket 5.7.6.

- `GetInputList` → `inputs[].inputName`, `inputKind` (5.0).
- `GetSourceFilter {sourceName, filterName}` → `filterEnabled`,
  `filterKind`, `filterSettings` (5.0); 600 when the source or filter is
  missing.
- `SetSourceFilterSettings {sourceName, filterName, filterSettings,
  overlay: true}` (5.0).
- `CreateSourceFilter {sourceName, filterName, filterKind: "crop_filter",
  filterSettings}` (5.0); 601 if the name exists, 607 for an unknown kind.
- `GetSourceScreenshot {sourceName, imageFormat: "jpg", imageWidth?}` →
  `imageData` as a data URI (5.0). Without `imageWidth`: full size after
  filters. `imageWidth` 8–4096.
- Events (FILTERS and INPUTS subscriptions, both in playout's `ALL`):
  `SourceFilterSettingsChanged` (5.4), `SourceFilterCreated`,
  `SourceFilterRemoved`, `SourceFilterNameChanged`,
  `SourceFilterEnableStateChanged`, `InputSettingsChanged` (5.4),
  `InputNameChanged`, `InputRemoved`.
- Crop/Pad filter settings: `relative` (bool, default true), `left`, `top`,
  `right`, `bottom` (ints).

## Left open

- **Bench not run.** To confirm on OBS 33.0.1: that a screenshot is the
  size after the crop (decision 4), that `SourceFilterSettingsChanged`
  comes back for our own writes, the JPEG quality and cost of 320 px
  previews, and moves looking smooth at 25 Hz on stream and in projectors.
- Pan and tilt speeds do not scale with zoom (contract units); if the bench
  finds moves too fast when zoomed in, a "slower when zoomed" option is a
  small change in `CropFrame.drive`.
- WP6a's image page has `preview-slot` divs; wiring `CameraPreview` into
  them is left for after both merge.
