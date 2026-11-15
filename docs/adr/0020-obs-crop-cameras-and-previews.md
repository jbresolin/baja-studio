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
   `degraded` but still moved. The filter is read with `GetSourceFilterList`,
   so another enabled filter that changes the size on the same source (a
   second Crop/Pad, Scaling/Aspect Ratio `scale_filter`) is seen too: it
   would make the size measurement wrong, so the camera is `degraded` saying
   which filter.
4. **The source's size.** obs-websocket has no request for a source's own
   size: `GetSourceScreenshot` (and a scene item's `sourceWidth`) are the
   size *after* filters (`obs_source_get_width`, read in obs-websocket's
   source). So the driver takes one full-size screenshot and adds the
   filter's edges back: `width = shot + left + right`. OBS applies new edges
   at its next video frame, so it measures only once the filter has not
   changed (by the studio or anyone) for 0.25 s, and reads the filter again
   after the screenshot (a change meanwhile throws the measurement away).
   A screenshot of 1 px in either direction measures nothing: OBS's crop
   filter clamps its output to at least 1x1 (`crop-filter.c`), which is what
   is left when the crop is larger than the picture. It measures on connect,
   after OBS comes back, when the filter appears, on `InputSettingsChanged`,
   and every 30 s at rest in case a change came unannounced
   (`obs_crop_size.py`); motion waits while the size is unknown, and does
   not catch up the waited time afterwards. PNG and JPEG sizes are read
   from the image header (`adapters/obs_images.py`), no image library.
5. **A new resolution.** A capture device reopens on its own thread, so the
   first screenshots after `InputSettingsChanged` can still have the old
   size. If the event names a `resolution` ("1920x1080", a device with a
   custom resolution), that size is trusted at once: the crop is
   re-expressed for it straight away (so a crop larger than the new picture
   does not leave one pixel on air), and measuring goes on until a
   screenshot agrees, for up to 5 s. Without one ("Device default") it is
   measured at once and again 1 s and 3 s later. A picture that shrank
   under its crop with no resolution named is `degraded` ("its crop is
   larger than the picture"), and **Reset crop** (`reset_crop`), which
   needs no size, writes nothing-cut and measures again.
6. **Connecting never moves the picture.** The driver adopts the crop OBS
   has as its window (the nearest allowed one) whenever it differs from
   what the studio last wrote, and writes nothing until a command moves it.
   Our own writes come back as `SourceFilterSettingsChanged` too
   (obs-websocket 5.4+; `obs_crop_edits.py`): an event equal to the last
   write is ignored whenever it comes (an echo, or the filter dialog's
   Cancel); another one within 0.5 s of a write is not trusted, but the
   filter is read again once the 0.5 s have passed; after that it is an
   edit made in OBS and is adopted at once. While the stick moves the
   crop, the next step overwrites an edit made in OBS: the stick wins.
   Settings edits (a larger `min_window`, another aspect) apply at the next
   move, never by themselves.
7. **Units are the contract's crop units** (`cameras/crop_window.py`): centre
   in source fractions from the top-left, positive tilt up, width as a
   fraction of the source's width with the height from the output aspect
   (`crop.aspect`, default 16:9), so the window always has the output's
   shape. Zoom 0 is the widest window that fits (the whole width on a 16:9
   source; less on a wider one), 1 is `crop.min_window` (default 0.5: 1080p
   on 4K); never narrower. Zoom is linear in the width, around the centre;
   the window is clamped inside the frame after every step. Speeds are
   per camera (`crop.pan_speed`/`tilt_speed`: frames per second at full
   deflection, default 0.3; `zoom_speed`: zoom range per second, 0.5).
   **On purpose unlike the contract's crop units**, `crop.speed_follows_zoom`
   (default on) scales pan and tilt by the window's width (relative to the
   widest): at 2x the window moves half as many frame fractions per
   second, so the picture on air moves at the same speed at any zoom (João's
   request). Agent crop cameras keep the contract's units; their agent
   decides.
8. **Velocity integration in the worker** (`camera:<id>:obs`) at
   `crop.update_hz` (default 25) while moving, 1 s idle (commands and OBS
   events wake it). The held velocity has the arbiter's ttl as its
   dead-man (`obs_crop_motion.py`). Pixels are rounded and a write happens
   only when they changed (holding a direction at an edge sends nothing). A
   new velocity applies from the last step, at most one update early.
9. **Presets are stored windows** (`crop.windows`: `n`, `centre_x`,
   `centre_y`, `width`) in the camera's settings, `preset_max` 127.
   `store_preset` saves before the command answers (Camera setup reloads
   its settings after a store, as after a rename), through WP6a's
   `CamerasModule.edit_camera` (`SettingsStore.edit`: the read-modify-write
   under the store's lock), so two stores, or a store and an image-settings
   save, cannot lose each other.
   A recall jumps there, as `PUT /crop` does on an agent. Preset *names*
   stay in `presets`, as for VISCA.
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
15. **OBS is asked only on demand**: per (source, width) at most one
    request is in flight, however long OBS takes (pages asking meanwhile
    wait for it), and its answer (or OBS's refusal) is reused for 1 s
    counted from the answer. With no page showing previews nothing is
    asked. At most 8 previews are served at once (each waits in one of the
    web server's threads); beyond that the page is told to try again, so a
    hung OBS and many phones cannot use up the thread pool. The component stops fetching when
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
    Agent PTZ and VISCA cameras show their `obs_source` if set. A preview
    counts as off screen until the browser's IntersectionObserver first
    says otherwise, so a page of many cards (WP6a's Image page, whose
    preview slots hold it, without click-to-centre) asks only for the
    cards shown.

### Fake OBS, rig, files

18. The fake OBS (`fakes/fake_obs_sources.py`, `fake_obs_video.py`) models
    the Crop/Pad filter from OBS's `crop-filter.c` (output at least 1x1;
    new edges in the picture from the next 30 fps frame) and screenshot
    sizes from obs-websocket's source (read, not measured: the bench
    confirms them), a capture device that takes 0.5 s to reopen at a new
    resolution (assumed; the bench measures it), the filter and input
    events, OBS's error codes, and draws a checkerboard so a crop shows
    which part it holds. It answers every screenshot in PNG (no JPEG
    encoder), and says so in its data URI. New flags: `--video-source
    NAME=WxH`, `--crop-filter NAME`. The rig starts it with a 4K webcam and
    its filter, and the bundle has an `obs_crop` camera on it ("rig-crop").
19. `cameras/config.py` stays at 300 lines: the crop settings are
    `cameras/crop_config.py`. The driver is split: `obs_crop.py` (the
    driver and its worker), `obs_crop_session.py` (each step's talk with
    OBS), `obs_crop_size.py`, `obs_crop_edits.py`, `obs_crop_motion.py`,
    `obs_crop_sync.py`, `obs_crop_state.py`. Over the ~300-line guideline:
    `cameras/registry.py` (340, was 320) and `cameras/drivers/agent_proxy.py`
    (368, was 351); the additions there are hooks into new files.

## obs-websocket requests and fields relied on

Checked against the 5.x protocol document; João's OBS is 33.0.1 with
obs-websocket 5.7.6.

- `GetInputList` → `inputs[].inputName`, `inputKind` (5.0).
- `GetSourceFilterList {sourceName}` → `filters[].filterName`,
  `filterKind`, `filterEnabled`, `filterSettings` (5.0); 600 when the
  source is missing.
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
  size after the crop (decision 4), that a crop larger than the picture
  leaves 1x1, that new edges show from the next frame, that
  `SourceFilterSettingsChanged` comes back for our own writes, the JPEG quality and cost of 320 px
  previews, and moves looking smooth at 25 Hz on stream and in projectors.
- How long a capture device takes to reopen at a new resolution, and
  whether its settings name it (`resolution` with a custom resolution).
- WP6a's image page has `preview-slot` divs; wiring `CameraPreview` into
  them is left for after both merge.
