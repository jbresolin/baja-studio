"""Tests for the Rockchip hardware path's pieces (node/video/rockchip.py).

On the NanoPi R3S the camera's picture never goes through the processor:
the VPU decodes the MJPEG, the RGA (a 2D engine) cuts the window out and
scales it to 1080p, and the VPU encodes the H.264 (ADR 0024). This module
only *writes* the ffmpeg options for that and checks the board, so these
tests read what it writes; nothing here needs a Rockchip board or ffmpeg.
"""

from pathlib import Path

from baja_studio.node.video.crop_commands import CropPixels
from baja_studio.node.video.rockchip import (
    RGA_FILTER_TARGET,
    decoder_arguments,
    is_rockchip_board,
    min_rga_crop_width,
    missing_device_nodes,
    resolve_decoder,
    rga_window_commands,
    rga_window_filter,
    runtime_crop_problem,
    runtime_crop_problem_in,
    snapshot_filters,
)

# What `ffmpeg -h filter=vpp_rkrga` prints for the crop options. The fourth
# column is the option's flags; a `T` near its end means "can change while
# running". The patched build has it on cw/ch/cx/cy; stock ffmpeg-rockchip does not.
PATCHED_HELP = """Filter vpp_rkrga
  Rockchip RGA (2D Raster Graphic Acceleration) video post-process (scale/crop/transpose)
vpp_rkrga AVOptions:
   w                 <string>     ..FV.......... Output video width (default "cw")
   h                 <string>     ..FV.......... Output video height (default "w*ch/cw")
   cw                <string>     ..FV.......T.. Set the width crop area expression (default "iw")
   ch                <string>     ..FV.......T.. Set the height crop area expression (default "ih")
"""
STOCK_HELP = PATCHED_HELP.replace(".T..", "....")
WHOLE_4K = CropPixels(3840, 1920, 1080)


def test_auto_means_the_rockchip_decoder_on_a_rockchip_board_and_the_cpu_elsewhere() -> None:
    assert resolve_decoder("auto", on_rockchip=True) == "mjpeg_rkmpp"
    assert resolve_decoder("auto", on_rockchip=False) == "mjpeg"
    # A decoder chosen by hand is kept, whatever the board.
    assert resolve_decoder("mjpeg", on_rockchip=True) == "mjpeg"
    assert resolve_decoder("mjpeg_qsv", on_rockchip=False) == "mjpeg_qsv"


def test_a_board_is_rockchip_when_it_has_both_the_codec_and_the_2d_engine(tmp_path: Path) -> None:
    # Two stand-in "device files" in a temporary folder play /dev/mpp_service and /dev/rga.
    codec, engine = tmp_path / "mpp_service", tmp_path / "rga"
    assert not is_rockchip_board((codec, engine))
    codec.touch()
    # Only one of the two: a board without the 2D engine cannot crop in hardware.
    assert not is_rockchip_board((codec, engine))
    engine.touch()
    assert is_rockchip_board((codec, engine))


def test_device_nodes_the_service_cannot_open_are_named(tmp_path: Path) -> None:
    present = tmp_path / "rga"
    present.touch()
    missing = tmp_path / "mpp_service"
    assert missing_device_nodes((missing, present)) == [str(missing)]


def test_the_narrowest_crop_is_what_the_rga_can_scale_eight_times() -> None:
    # 1920 / 8 = 240 pixels: the RGA scales by at most 8 either way.
    assert min_rga_crop_width(1920) == 240
    # Rounded up to the RGA's 2-pixel grid.
    assert min_rga_crop_width(1922) == 242


def test_the_camera_is_decoded_by_the_vpu_into_hardware_frames() -> None:
    assert decoder_arguments() == [
        "-hwaccel", "rkmpp", "-hwaccel_output_format", "drm_prime", "-c:v", "mjpeg_rkmpp",
    ]  # fmt: skip


def test_the_rga_filter_keeps_a_fixed_1080p_output_and_a_16_by_9_crop() -> None:
    graph_filter = rga_window_filter(CropPixels(1920, 960, 540), 1920, 1080)
    assert graph_filter == (
        f"{RGA_FILTER_TARGET}=w=1920:h=1080"
        # The crop's height follows its width (never stretched) ...
        ":cw=1920:ch=cw*1080/1920"
        # ... and its edges follow its centre, so a zoom keeps the centre.
        ":cx=960-cw/2:cy=540-ch/2"
        ":format=nv12"
    )


def test_a_window_change_is_sent_as_the_rga_options_that_changed() -> None:
    # From nothing known: all three, the width first.
    assert rga_window_commands(None, WHOLE_4K) == [
        "vpp_rkrga@window cw 3840",
        "vpp_rkrga@window cx 1920-cw/2",
        "vpp_rkrga@window cy 1080-ch/2",
    ]
    # A zoom alone: one command; the centre expressions follow the new width.
    assert rga_window_commands(WHOLE_4K, CropPixels(1920, 1920, 1080)) == [
        "vpp_rkrga@window cw 1920"
    ]
    # A pan and a tilt: two commands. Nothing changed: none.
    assert rga_window_commands(WHOLE_4K, CropPixels(3840, 1000, 900)) == [
        "vpp_rkrga@window cx 1000-cw/2",
        "vpp_rkrga@window cy 900-ch/2",
    ]
    assert rga_window_commands(WHOLE_4K, WHOLE_4K) == []


def test_the_snapshot_is_shrunk_by_the_rga_before_it_reaches_the_cpu() -> None:
    filters = snapshot_filters(320, 1).split(",")
    # Once a second, then the RGA makes it 480 wide (4K / 8: within its limit) ...
    assert filters[0] == "fps=1"
    assert filters[1].startswith("vpp_rkrga=w=480:")
    # ... and only then is it copied out of the hardware and made a small JPEG.
    assert filters[2:] == ["hwdownload", "format=nv12", "scale=320:-2", "format=yuvj420p"]


def test_the_patched_ffmpeg_is_recognised_by_its_runtime_crop_options() -> None:
    assert runtime_crop_problem_in(PATCHED_HELP) == ""


def test_a_stock_ffmpeg_rockchip_is_refused_because_its_crop_cannot_move() -> None:
    assert "unpatched ffmpeg-rockchip" in runtime_crop_problem_in(STOCK_HELP)


def test_an_ffmpeg_without_the_rga_filter_is_refused() -> None:
    assert "no Rockchip RGA filter" in runtime_crop_problem_in("Unknown filter 'vpp_rkrga'.")


def test_a_missing_ffmpeg_is_a_problem_not_a_crash(tmp_path: Path) -> None:
    assert "could not ask ffmpeg" in runtime_crop_problem(str(tmp_path / "no-ffmpeg"))
