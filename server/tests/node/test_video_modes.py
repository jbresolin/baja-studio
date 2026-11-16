"""Tests of a camera node's two video modes, through its agent API, as the studio uses it.

A "camera node" runs on a trackside laptop and offers the laptop's USB
cameras to the studio as agent cameras of kind "crop": a window cut out of a
4K picture, moved like a pan/tilt/zoom camera (ADR 0023). Each camera has a
*video mode*:

- "node": the node's own video pipeline (ffmpeg) cuts the window. Here it is a
  `FakeCameraPipeline`, which records every window it is sent.
- "obs": OBS on the same laptop holds the camera and its "Baja crop" filter;
  the node moves that filter. Here OBS is a `FakeObs`, whose filter settings
  (pixel edges cut off the picture) we can read back.

Every test starts a real node in the test process (`node_harness.py`) and
talks to it over HTTP like the studio does, on the real clock: waits are
"until this is true, at most a few seconds", never fixed sleeps.
"""

from pathlib import Path
from typing import Any

from baja_studio.node.video.fake import FakeCameraPipeline
from tests.conftest import eventually
from tests.node.node_harness import (
    CROP_FILTER,
    OBS_WEBCAM_A,
    RunningNode,
    node_mode_camera,
    obs_mode_camera,
    running_node,
)


def pipeline_of(node: RunningNode, camera_id: str = "cam1") -> FakeCameraPipeline:
    """The fake pipeline running for a node-mode camera (the newest one made for its device)."""
    device = node.settings().cameras[0].video.device
    eventually(lambda: node.pipelines.for_device(device) is not None, "no pipeline was started")
    pipeline = node.pipelines.for_device(device)
    assert pipeline is not None
    return pipeline


def window(node: RunningNode, camera_id: str) -> dict[str, Any]:
    """`GET /crop`: where the camera's window is now."""
    answer: dict[str, Any] = node.http.get(f"/cameras/{camera_id}/crop").json()
    return answer


# --- node mode ---------------------------------------------------------------------------


def test_a_crop_put_by_the_studio_reaches_the_pipeline(tmp_path: Path) -> None:
    # One camera in node mode.
    with running_node(tmp_path, [node_mode_camera("cam1")]) as node:
        node.wait_until_ready()

        # The studio jumps the window to the left half of the picture, centred vertically.
        answer = node.http.put("/cameras/cam1/crop", json={"x": 0.0, "y": 0.25, "w": 0.5, "h": 0.5})

        # The node answers with the window it applied, and the pipeline was sent exactly that.
        assert answer.status_code == 200
        assert answer.json() == {"x": 0.0, "y": 0.25, "w": 0.5, "h": 0.5}
        sent = pipeline_of(node).last_window
        assert sent is not None
        assert (sent.x, sent.y, sent.w, sent.h) == (0.0, 0.25, 0.5, 0.5)


def test_a_window_narrower_than_min_window_is_widened(tmp_path: Path) -> None:
    # min_window is 0.5 by default: on a 4K source, 1080p without upscaling (2x).
    with running_node(tmp_path, [node_mode_camera("cam1")]) as node:
        node.wait_until_ready()

        # The studio asks for a window a fifth of the picture wide, in the middle.
        answer = node.http.put("/cameras/cam1/crop", json={"x": 0.4, "y": 0.4, "w": 0.2, "h": 0.2})

        # It gets the narrowest allowed window (half the width) around the same centre.
        assert answer.json() == {"x": 0.25, "y": 0.25, "w": 0.5, "h": 0.5}


def test_a_held_velocity_moves_the_window_and_stops_by_itself(tmp_path: Path) -> None:
    # The dead-man rule (contract §6): a /move lasts `ttl_ms`; with no newer
    # /move the window stops by itself, even if the studio vanished.
    with running_node(tmp_path, [node_mode_camera("cam1")]) as node:
        node.wait_until_ready()
        node.http.put("/cameras/cam1/crop", json={"x": 0.25, "y": 0.25, "w": 0.5, "h": 0.5})

        # Pan right at full speed for 0.3 s.
        node.http.post("/cameras/cam1/move", json={"pan": 1.0, "ttl_ms": 300})

        # The window starts moving right, then comes to rest without being told to.
        eventually(lambda: window(node, "cam1")["x"] > 0.25, "the window never moved right")
        eventually(
            lambda: (
                node.http.get("/cameras/cam1/position").json() is not None
                and not node.state("cameras.cam1.moving")
            ),
            "the window never stopped by itself",
        )
        stopped_at = window(node, "cam1")["x"]
        # Speed 0.3 frame widths a second, halved at 2x (speed follows zoom): 0.3 s
        # move it about 0.045. It must not have gone further than the ttl allows.
        assert 0.25 < stopped_at <= 0.25 + 0.3 * 0.5 * 0.3 + 0.01
        assert window(node, "cam1")["x"] == stopped_at  # and it stays there


def test_a_pipeline_that_restarts_keeps_the_window_and_the_api_up(tmp_path: Path) -> None:
    with running_node(tmp_path, [node_mode_camera("cam1")]) as node:
        node.wait_until_ready()
        node.http.put("/cameras/cam1/crop", json={"x": 0.0, "y": 0.0, "w": 0.5, "h": 0.5})
        pipeline = pipeline_of(node)

        # ffmpeg dies (the fake pretends): the pipeline restarts, saying why.
        pipeline.fail("SRT receiver unreachable: connection refused")

        # The camera is degraded, with the reason in operator words ...
        eventually(lambda: node.state("cameras.cam1.status") == "degraded", "not degraded")
        reason = node.state("cameras.cam1.status_reason")
        assert reason == "pipeline restarting: SRT receiver unreachable: connection refused"
        # ... the agent API still answers, and still takes commands (a degraded
        # camera is not offline: the window is the node's, not ffmpeg's).
        assert node.http.get("/agent/info").status_code == 200
        moved = node.http.put("/cameras/cam1/crop", json={"x": 0.5, "y": 0.5, "w": 0.5, "h": 0.5})
        assert moved.status_code == 200

        # Back up: ok again, and the window is the last one the studio set.
        pipeline.recover()
        eventually(lambda: node.state("cameras.cam1.status") == "ok", "never ok again")
        assert window(node, "cam1") == {"x": 0.5, "y": 0.5, "w": 0.5, "h": 0.5}


def test_a_pipeline_that_breaks_outright_never_takes_the_api_down(tmp_path: Path) -> None:
    # Worse than a restart: the pipeline object itself raises when asked anything
    # (a bug, a broken pipe). The camera's worker fails and is restarted by the
    # supervisor with a new pipeline; meanwhile the API answers as usual.
    with running_node(tmp_path, [node_mode_camera("cam1")]) as node:
        node.wait_until_ready()
        broken = pipeline_of(node)

        def explode() -> Any:
            raise RuntimeError("the pipe to ffmpeg broke")

        # Everything the node reads from it raises (stop() still works, to clean up).
        broken.status = explode  # type: ignore[method-assign]
        broken.latest_snapshot = explode  # type: ignore[method-assign]
        broken.log_tail = explode  # type: ignore[method-assign]

        # A snapshot is "no picture", never a server error ...
        answer = node.http.get("/cameras/cam1/snapshot")
        assert answer.status_code == 404
        assert answer.json()["error"] == "no_snapshot"
        # ... the agent's info and health still answer ...
        assert node.http.get("/agent/info").status_code == 200
        assert node.http.get("/agent/health").status_code == 200
        # ... and the camera comes back on a new pipeline, at the same window.
        eventually(lambda: len(node.pipelines.made) >= 2, "no new pipeline was started")
        eventually(lambda: node.state("cameras.cam1.status") == "ok", "never ok again")
        assert broken.stops == 1


def test_a_camera_without_an_output_url_says_what_to_paste(tmp_path: Path) -> None:
    # A camera whose output URL is still empty starts no pipeline at all.
    camera = node_mode_camera("cam1")
    camera["video"]["output_url"] = ""
    with running_node(tmp_path, [camera]) as node:
        eventually(lambda: node.state("cameras.cam1.status") == "degraded", "not degraded")

        assert "paste this camera's SRT URL" in node.state("cameras.cam1.status_reason")
        assert node.pipelines.made == []


def test_the_output_url_never_leaves_the_node(tmp_path: Path) -> None:
    # The output URL is a Feeds URL: it carries the feed's publish password.
    # It must not appear in anything the node shows: its info, health, state,
    # the masked settings the page reads, or its log.
    with running_node(tmp_path, [node_mode_camera("cam1")]) as node:
        node.wait_until_ready()
        secret_url = node.settings().cameras[0].video.output_url
        password = secret_url.split(":")[-1].split("&")[0]
        pipeline_of(node).fail(f"could not reach {secret_url}")  # a careless library
        eventually(lambda: node.state("cameras.cam1.status") == "degraded", "not degraded")

        shown = " ".join(
            [
                node.http.get("/agent/info").text,
                node.http.get("/agent/health").text,
                node.http.get("/api/settings/node").text,
                str(node.state("")),
                node.http.get("/api/logs").text,
            ]
        )

        assert password not in shown
        assert secret_url not in shown
        # What the page does show (once a second): where the picture goes, without
        # the secret parts.
        eventually(
            lambda: (
                node.state("cameras.cam1.details.output")
                == "srt://127.0.0.1:8890?streamid=***&latency=***"
            ),
            "the page is never shown where the picture goes",
        )


# --- OBS mode ----------------------------------------------------------------------------


def test_obs_mode_moves_the_crop_filter_in_the_laptops_obs(tmp_path: Path) -> None:
    with running_node(tmp_path, [obs_mode_camera("cam2")]) as node:
        node.wait_until_ready()

        # The studio puts the window on the top-right quarter of the 4K picture.
        answer = node.http.put("/cameras/cam2/crop", json={"x": 0.5, "y": 0.0, "w": 0.5, "h": 0.5})

        # The filter in OBS now cuts the left half and the bottom half away
        # (3840 x 2160 source: 1920 pixels from the left, 1080 from the bottom).
        assert answer.json() == {"x": 0.5, "y": 0.0, "w": 0.5, "h": 0.5}
        settings = node.obs.filter_settings(OBS_WEBCAM_A, CROP_FILTER)
        assert (settings["left"], settings["top"], settings["right"], settings["bottom"]) == (
            1920,
            0,
            0,
            1080,
        )


def test_obs_away_makes_the_camera_offline_and_back_again(tmp_path: Path) -> None:
    with running_node(tmp_path, [obs_mode_camera("cam2")]) as node:
        node.wait_until_ready()

        # OBS quits on the laptop.
        node.obs_network.unplug("127.0.0.1", 4455)

        # The camera goes offline, saying it is OBS; motion is refused with the
        # contract's 503, but a stop is still accepted (stops are never refused).
        eventually(lambda: node.state("cameras.cam2.status") == "offline", "never offline")
        assert "OBS" in node.state("cameras.cam2.status_reason")
        refused = node.http.post("/cameras/cam2/move", json={"pan": 0.2})
        assert refused.status_code == 503
        assert refused.json()["error"] == "device_offline"
        assert node.http.post("/cameras/cam2/stop").status_code == 200
        # The rest of the API is unaffected.
        assert node.http.get("/agent/health").json()["cameras"]["cam2"] == "offline"

        # OBS is started again: the node reconnects by itself and the camera is ok.
        node.obs_network.plug("127.0.0.1", 4455, node.obs)
        eventually(lambda: node.state("cameras.cam2.status") == "ok", "never ok again")


def test_an_obs_snapshot_without_a_preview_source_is_the_window(tmp_path: Path) -> None:
    # OBS screenshots a source after its filters: the camera's own source shows
    # only the window. The answer says so, and where the window was.
    with running_node(tmp_path, [obs_mode_camera("cam2")]) as node:
        node.wait_until_ready()
        node.http.put("/cameras/cam2/crop", json={"x": 0.0, "y": 0.0, "w": 0.5, "h": 0.5})

        answer = node.http.get("/cameras/cam2/snapshot", params={"width": 320})

        assert answer.status_code == 200
        assert answer.headers["x-snapshot-shows"] == "window"
        assert answer.headers["x-crop-window"] == "0.000000,0.000000,0.500000,0.500000"


def test_an_obs_snapshot_from_a_preview_source_is_the_whole_frame(tmp_path: Path) -> None:
    # With a preview source (an uncropped view of the camera in OBS), the
    # snapshot is the whole frame, as for a node-mode camera.
    camera = obs_mode_camera("cam2")
    camera["obs"]["preview_source"] = "S600 B"
    with running_node(tmp_path, [camera]) as node:
        node.wait_until_ready()

        answer = node.http.get("/cameras/cam2/snapshot")

        assert answer.status_code == 200
        assert answer.headers["x-snapshot-shows"] == "frame"
        assert "x-crop-window" not in answer.headers


def test_an_obs_camera_without_a_source_says_to_choose_one(tmp_path: Path) -> None:
    camera = obs_mode_camera("cam2")
    camera["obs"]["source"] = ""
    with running_node(tmp_path, [camera]) as node:
        eventually(lambda: node.state("cameras.cam2.status") == "offline", "never offline")

        assert "choose the camera's source" in node.state("cameras.cam2.status_reason")
        assert node.http.get("/agent/info").status_code == 200


# --- switching modes ---------------------------------------------------------------------


def test_switching_a_cameras_mode_swaps_its_video_without_restarting_the_node(
    tmp_path: Path,
) -> None:
    with running_node(tmp_path, [node_mode_camera("cam1")]) as node:
        node.wait_until_ready()
        pipeline = pipeline_of(node)
        uptime_before = node.http.get("/agent/health").json()["uptime_s"]

        # On the node's page, the camera is switched to OBS mode (the source is
        # the laptop OBS's webcam A) and saved.
        switched = node_mode_camera("cam1", mode="obs")
        switched["obs"] = obs_mode_camera("cam1")["obs"]
        node.configure(cameras=[switched])

        # The pipeline is stopped cleanly, and the same camera now moves OBS's filter.
        eventually(lambda: pipeline.stops == 1, "the pipeline was never stopped")
        eventually(lambda: node.state("cameras.cam1.mode") == "obs", "never in OBS mode")
        node.wait_until_ready()
        node.http.put("/cameras/cam1/crop", json={"x": 0.5, "y": 0.5, "w": 0.5, "h": 0.5})
        assert node.obs.filter_settings(OBS_WEBCAM_A, CROP_FILTER)["left"] == 1920
        # The node itself did not restart: same process, uptime still counting up.
        info = node.http.get("/agent/info").json()
        assert [camera["id"] for camera in info["cameras"]] == ["cam1"]
        assert info["cameras"][0]["driver"] == "node-obs"
        assert node.http.get("/agent/health").json()["uptime_s"] >= uptime_before

        # And back to node mode: a new pipeline, given the window the camera had.
        node.configure(cameras=[node_mode_camera("cam1")])
        eventually(lambda: len(node.pipelines.made) == 2, "no new pipeline")
        newest = node.pipelines.made[-1]
        eventually(lambda: newest.last_window is not None, "the new pipeline got no window")
        assert newest.last_window is not None
        assert (newest.last_window.x, newest.last_window.y) == (0.5, 0.5)


def test_a_camera_removed_from_the_settings_leaves_the_agent(tmp_path: Path) -> None:
    with running_node(tmp_path, [node_mode_camera("cam1"), obs_mode_camera("cam2")]) as node:
        node.wait_until_ready()
        pipeline = pipeline_of(node)

        node.configure(cameras=[obs_mode_camera("cam2")])

        eventually(
            lambda: [c["id"] for c in node.http.get("/agent/info").json()["cameras"]] == ["cam2"],
            "cam1 is still listed",
        )
        eventually(lambda: pipeline.stops == 1, "cam1's pipeline was never stopped")
        assert node.http.post("/cameras/cam1/stop").json()["error"] == "unknown_camera"
