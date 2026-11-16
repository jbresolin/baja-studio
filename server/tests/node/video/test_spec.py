"""Tests for the pipeline's spec and status types (node/video/spec.py)."""

from baja_studio.node.video.spec import (
    AUTO_ENCODER,
    SOFTWARE_DECODER,
    PipelineSpec,
    PipelineState,
    SourceWindow,
)


def make_spec(label: str = "") -> PipelineSpec:
    """A 4K30 camera spec as the node would build it, with an optional friendly name."""
    return PipelineSpec(
        device="@device_pnp_\\\\?\\usb#vid_328f&pid_00b8#1",
        input_width=3840,
        input_height=2160,
        input_fps=30,
        output_url="srt://studio:8890?streamid=publish:laptop1",
        label=label,
    )


def test_the_defaults_are_the_brief_s_1080p_8_mbit_1_s_keyframes_auto_encoder() -> None:
    spec = make_spec()
    assert (spec.output_width, spec.output_height) == (1920, 1080)
    assert spec.bitrate_kbps == 8000
    assert spec.keyframe_seconds == 1
    assert spec.encoder == AUTO_ENCODER
    # MJPEG is decoded on the CPU unless the settings name a hardware decoder.
    assert spec.decoder == SOFTWARE_DECODER == "mjpeg"


def test_messages_name_the_camera_by_its_label_when_there_is_one() -> None:
    # The DirectShow path is unreadable for an operator; the friendly name is used if given.
    assert make_spec(label="EMEET S600 left").camera_name == "EMEET S600 left"
    assert make_spec().camera_name.startswith("@device_pnp_")


def test_the_output_is_16_by_9_and_the_whole_window_is_the_whole_frame() -> None:
    assert make_spec().output_aspect == 16 / 9
    assert SourceWindow.whole() == SourceWindow(0.0, 0.0, 1.0, 1.0)


def test_states_read_as_plain_words() -> None:
    # The states go into JSON for the node's page, so they must be plain strings.
    assert [state.value for state in PipelineState] == [
        "stopped",
        "starting",
        "running",
        "restarting",
        "failed",
    ]
