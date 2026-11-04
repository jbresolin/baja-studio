# WP5 bench session: playout on the streaming PC with OBS

The checklist is §8 of the WP5 brief (Plane page "07 — WP5 brief"). Record
the results in a comment on **LIVESTACK-35**.

Start the service (`python -m baja_studio`) and open
`http://<streaming-pc>:8888/playout`. The page shows its own id in the status
line, e.g. `web:3fa1c2d4`; the command log names that page's actions with
the same id.

Without OBS: `python -m baja_studio.fakes.fake_obs --port 4455` plays an OBS
already set up as below (host `127.0.0.1`; `--password P` to require one,
`--duration 8` for 8 s clips). Without the VPS:
`python -m baja_studio.fakes.fake_orchestrator --token T --from-folder <clips folder>`
announces every `<prova>/<car>/<passagem>.mp4` under that folder (base URL
`http://127.0.0.1:8000`, token `T`).

## Setting up OBS (once, by hand)

Playout creates nothing in OBS. Nothing is read from an existing profile
either: every name below is a playout setting and can be changed.

1. **WebSocket server**: *Tools → WebSocket Server Settings*: tick
   *Enable WebSocket server*, note the port (4455) and the password
   (*Show Connect Info*).
2. **Two scenes**, `Playback A` and `Playback B`.
3. In `Playback A` add a **Media Source** named `baja_program_a`; in
   `Playback B` one named `baja_program_b`. Size them to fill the canvas
   (right-click → *Transform → Fit to screen*). In each source's properties:
   - *Local file* ticked; leave the file empty (playout sets it);
   - **untick** *Loop*, *Restart playback when source becomes active*,
     *Close file when inactive* and *Show nothing when playback ends*
     (playout also sets these four on every cue; untick them anyway so a
     manual test behaves the same);
   - *Use hardware decoding when available* as you prefer.
4. A **return scene** for after a clip (for example your main camera scene).
   Put its name in the playout settings (`obs.return_scene`).
5. Transitions: playout sets a *Cut* transition override on both playback
   scenes when it connects, so a take is always a cut. If OBS runs in
   Portuguese, the built-in transitions are called *Corte* and
   *Desvanecer*: set `obs.cut_transition` and `obs.fade_transition`
   accordingly, or the override fails (logged as a warning) and a take uses
   whatever transition is selected.

Playout settings live on **Settings → Playout**. The `sources`, `obs`,
`tools`, `operators`, `hooks` and `export` sections are JSON boxes there;
edit the whole section and Save. Example `obs`:

```json
{"host": "127.0.0.1", "port": 4455, "password": "********", "scene_a": "Playback A",
 "scene_b": "Playback B", "source_a": "baja_program_a", "source_b": "baja_program_b",
 "return_scene": "Main", "post_clip": "return", "fade_ms": 1000,
 "cut_transition": "Cut", "fade_transition": "Fade"}
```

Example `sources` (the first folder is the delivery folder: the studio
receiver's clip root; `token` is the studio's worker bearer token):

```json
{"orchestrator": {"enabled": true, "base_url": "http://100.64.0.1:8080",
  "token": "…", "poll_interval_s": 10},
 "local_folders": [{"path": "D:\\Clips", "label": "Delivery", "recursive": true},
                   {"path": "D:\\Sponsors", "label": "Sponsors", "recursive": false}]}
```

## Steps

| # | Do | Expect / record |
|---|---|---|
| 1 | Set up OBS as above. Save the `obs` section with host, port, password and return scene. | Status line: OBS `connected`, current scene shown. Home card `ok` (or `degraded` only for a missing folder/orchestrator). Wrong password → `auth_failed` with a message; fixing it reconnects without a restart. |
| 2 | Save `sources` with the delivery folder. Drop three clips in it, one laid out as `<prova>/<car>/<passagem>.mp4` and announced by the orchestrator (real or fake). | All three appear within ~5 s, then get a duration and a thumbnail. The announced one shows prova, car and team and is grouped under its prova; the others under *Manual*, with an editable label. Record ffprobe's speed on a full-length clip. |
| 3 | Queue two clips. **Cue**, then **Take**. Cue the second while the first plays, then **Take**. Watch the program output (and a recording of it, frame by frame) at each take. | **No frame of the previous clip** at either take; the preview card shows the cued clip and its `<video>` scrubs without affecting OBS. Record any flash, even one frame, with the clip lengths and codecs. |
| 4 | Let a clip end with `post_clip` `return`, then `hold`, then `fade` (`fade_ms` 1000). Repeat each with **→ (skip)** mid-clip. Then cue a clip, turn **auto-play next** on and let the one on air end; skip the next one with →. Finally press **Shift+→** (or Backspace) with a clip cued. | return: cut to the return scene. hold: last frame stays (after a skip, the frame where it was skipped). fade: 1 s fade to the return scene. auto-play: the cued clip follows at once; with it off, return still happens even with a clip cued. Skip never starts the next clip, even with auto-play on. Shift+→ drops the cued clip and cues the next; program is untouched. |
| 5 | Kill OBS (Task Manager) mid-clip. Restart it. | Status `offline` within ~2 s, home card `offline`; queue and library intact and editable. After the restart: `connected` within ~4 s, current scene read again; the program strip no longer claims the old clip. A cued clip is loaded again. |
| 6 | Two browsers (desk PC, phone). Settings → `operators.require_take_confirmation` on. Cue on one, Take on the other. | First Take shows "Press Take again within 3 s" on both pages; the second press within 3 s fires; after 3 s the prompt goes away and a press only arms again. Both pages show the same queue and program. |
| 7 | **Mark stream start**, play two clips, **Export aired log**. | Offsets match the time since the mark (±1 s); chapters start with `0:00 Stream start`. Download the JSON and attach it. |
| 8 | Run a listener: `python -c "import http.server as h; h.HTTPServer(('',9999), type('H',(h.BaseHTTPRequestHandler,),{'do_POST':lambda s:(print(s.rfile.read(int(s.headers['Content-Length']))),s.send_response(204),s.end_headers())})).serve_forever()"`. Set `hooks.on_air_webhook_url` to `http://127.0.0.1:9999/`. Play a clip to its end. | Two posts: `{clip, started_at}` at the take and `{clip, ended_at}` at the end. Stop the listener and play again: playout is unaffected; the log warns at most once a minute. |

## Also worth noting

- Whether OBS really starts a media source when its file changes (the fake
  assumes it does, which is why cueing restarts, pauses and rewinds). If a
  cued source shows a few frames of motion off air, that is harmless; if the
  first frame on air is not frame 0, report it.
- Time from **Take** to the first frame on program, by eye or on a recording.
- Whether the position countdown matches the clip (±0.2 s, it is polled at 5 Hz).
