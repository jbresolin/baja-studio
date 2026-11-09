# WP5b bench session: playout fixes and features

The checklist is §6 of the WP5b brief (Plane page "11 — WP5b brief").
Record the results in a comment on **LIVESTACK-54**. OBS is set up as in
`wp5.md`; start from a fresh OBS (closed and reopened) so the first cue of
the day is tested.

Settings are on **Playout → Settings** (`/playout/settings`): folders (with
*browse*, which lists the streaming PC's own drives), OBS names, tools paths
and on-air actions; the whole document is under *Advanced* as JSON. Where to
go after a clip is the **Return to** control on the playout page.

Without OBS: `python -m baja_studio.fakes.fake_obs --port 4455` (it now
opens files like OBS does, starting them a moment after they are loaded).
Without the XR18: `python -m baja_studio.fakes.fake_xr18` (see `wp4.md`).

## Steps

| # | Do | Expect / record |
|---|---|---|
| 1 | Folders: add the receiver's clip root with *browse*, mark it *receiver root*; add a sponsors folder labelled `Patrocinadores`. Save. Then, under *Advanced*, type `"local_folders": ["D:/clips"]` and Save. | Both folders saved as rows. The string form is refused with `sources.local_folders: … must be an object like {"path": …}`; nothing is saved. A single backslash in the JSON (`D:\clips`) says "Not valid JSON, nothing applied". |
| 2 | Rename `ffprobe` in the tools path to something wrong. Save. Put it back. | The playout page shows one banner ("durations and thumbnails unavailable — ffprobe not found; …"); durations show `?` with that text as tooltip. Fixed path: banner gone, durations fill in. Readiness `tools` red while wrong. |
| 3 | (page 07 §8 step 3) Queue two clips; press **Take** on a freshly started OBS (no Cue first). Then Cue the second while the first plays and Take. | The **first** take works first time (no "media must be playing or paused"). No frame of the previous clip at either take. |
| 4 | (page 07 §8 step 4) With *Return to: previous scene*, start from your camera scene, take a clip and let it end with `post_clip` return, then hold, then fade. Switch to *chosen scene* and pick another scene; repeat once. Pick *chosen* with no scene. | return/fade go back to the scene that was on before the clip; with *chosen*, to the picked scene. With nothing picked the strip shows ⚠ "no return scene chosen…", and at the end the clip holds with a toast saying why. |
| 5 | Auto-play chain: turn **auto-play next** on, queue three clips, Take. | Each clip follows the previous with one cut; after the third, program returns to the scene that was on before the first. The strip's background fills with each clip's progress; right side shows `−m:ss` (white → amber at 10 s → red at 5 s) and `queue −m:ss` (`+?` if a duration is unknown). |
| 6 | Sponsor playlist: in the library, *+ playlist* on the `Patrocinadores` group; tick *shuffle*; **Queue**. Export the aired log after it plays. | The whole folder is queued in one click, shuffled; queue rows show the playlist name. The aired log entries carry `"playlist": "Patrocinadores"`. |
| 7 | On-air actions: in settings, *When a clip starts* → an audio macro that mutes a trackside channel; *When a clip ends* → the macro that opens it. Take a clip and let it end. Then give the sponsor playlist its own actions (Edit → *Own on-air actions*) and queue it. Finally turn audio off (or unplug the XR18) and take a clip. | The channel mutes at the take and opens at the end (audio's command log shows `playout:on_air`). The playlist's own actions replace the settings' for its clips. Audio away: the take still happens; a toast says "on-air action audio.run_macro failed: …". |
| 8 | On the test PC with the old pipeline outputs (`manobrabilidade-001-p2-v2`, `1.v2`, hash names) in the clip root: tick **hide unmatched**. | Those files are grey, marked *unmatched* with the tooltip "file not in the orchestrator manifest", grouped under *Unmatched*, and hidden by the toggle. Sponsor files are under *Patrocinadores*, not *Unmatched*. |

## Also worth noting

- How long OBS takes from loading a file to `MediaInputPlaybackStarted` (a
  cue waits up to 3 s; the playout health goes `degraded` with OBS's words
  if it never starts).
- `playout.hosts_view` in the state (WP9's hosts screen reads it): on air,
  next three, queue remaining; it should change once a second while playing.
