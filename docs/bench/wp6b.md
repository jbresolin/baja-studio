# WP6b bench session: OBS crop cameras and previews

The checklist is §5 of the WP6b brief (Plane page "20 — WP6b brief"). Record
the results in a comment on **LIVESTACK-18**. Decisions are in
[ADR 0020](../adr/0020-obs-crop-cameras-and-previews.md).

Set-up on the studio PC: OBS 33 with obs-websocket on, playout connected to
it (Playout setup → OBS; the playout page says *connected*). An EMEET S600
in OBS as a *Video Capture Device* at **3840x2160** (Properties → Resolution/FPS
type: Custom, Resolution 3840x2160), placed in a scene with its transform set
to **Stretch to bounds / Scale to inner bounds** of the canvas, so the
cropped picture fills the frame. A source projector open for it (right-click
→ Open Source Projector).

| # | Step | Expected | Result |
| --- | --- | --- | --- |
| 1 | Camera setup → Add camera: driver *OBS crop*, pick the S600 in *OBS source* (from OBS's list), Id `s600-crop`, Save | Camera listed; on the Cameras page it is **degraded**: "source '…' has no filter 'Baja crop': use Create filter on Camera setup" | |
| 2 | Edit it → **Create filter** | OBS shows a *Crop/Pad* filter "Baja crop" on the S600 (Relative ticked, all 0); the camera turns **ok**; the projector still shows the whole picture | |
| 3 | Cameras page → select it; the preview | A thumbnail of the picture about once a second; "1.0×" in the corner; the window outline around the whole frame | |
| 4 | Hold *Zoom in* (or **Z**) | The projector zooms in smoothly around the centre; it stops at **2.0×** (1920x1080 window, `min_window` 0.5); the preview shows the window outline shrinking | |
| 5 | Web stick and arrow keys, all four directions, then let go | Smooth moves in the projector and on the stream (watch for judder at 25 Hz); stops at once on release; the window never leaves the picture | |
| 6 | Move to a spot, **Shift+2** (store preset 2); move away; **2** | Jumps back to the stored window at once; still there after restarting the service | |
| 7 | Click in the preview on something near the edge of the picture | The window centres there (as far as it fits); the command tail says `centre_window` | |
| 8 | Hold a direction with the joystick, and click the preview from a phone | The click is refused ("being moved by joystick:…") | |
| 9 | Pan around for a while, then look at the preview | Earlier windows stay, dimmed, around the live one (the frame pieced together) | |
| 10 | Open the Cameras page on a phone (portal URL) | Same preview; no extra load on OBS when both pages are open (one screenshot a second) | |
| 11 | Close OBS | Within a few seconds the camera is **offline** "OBS offline: …"; the preview says "OBS offline: …"; nothing else on the page breaks | |
| 12 | Start OBS again | The camera comes back **ok** by itself, at the window it had (nothing moves on connect) | |
| 13 | In OBS, drag the filter's *Left* value by hand | The studio follows (outline moves on the preview) without fighting back | |
| 14 | In the S600's properties, switch to 1920x1080 and back to 3840x2160 | After each switch the camera is ok again and the projector shows a sensible crop (at 1080p the narrowest window is still half the width, now upscaled); if it stays "no picture", **Reset crop** on Camera setup brings it back — note which happened | |
| 15 | Untick the filter's eye in OBS | Camera **degraded** "switched off in OBS"; tick it again: **ok** | |
| 16 | Task Manager → Performance (GPU) and OBS's Stats dock, 4K source cropped, with and without the Cameras page open | Note CPU, GPU and "frames missed due to rendering lag" with the 4K source; and whether previews change them | |

Notes for the record: OBS and obs-websocket versions; whether moves at 25 Hz
look smooth on stream (try `crop.update_hz` 30 if not); whether the pan
speed feels right zoomed in (0.3 frames/s by default).
