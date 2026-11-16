"""Tests for how a pipeline's state becomes a node camera's health (node/pipeline_driver.py).

The studio greys out an offline camera and refuses to move it, so the
mapping matters: a pipeline that restarts (ffmpeg died, the receiver went
away) is *degraded*, because the window is the node's and keeps working; only
a pipeline that cannot run at all is *offline*. The driver's motion itself
is tested through the node (test_video_modes.py).
"""

import pytest

from baja_studio.agents.contract import Status
from baja_studio.node.pipeline_driver import pipeline_health
from baja_studio.node.video.pipeline_types import PipelineState, PipelineStatus


@pytest.mark.parametrize(
    ("state", "reason", "expected"),
    [
        (PipelineState.RUNNING, "", (Status.OK, "")),
        (PipelineState.STARTING, "", (Status.DEGRADED, "pipeline starting")),
        (
            PipelineState.RESTARTING,
            "camera not found: S600",
            (Status.DEGRADED, "pipeline restarting: camera not found: S600"),
        ),
        (
            PipelineState.FAILED,
            "no encoder works",
            (Status.OFFLINE, "pipeline failed: no encoder works"),
        ),
        (PipelineState.STOPPED, "", (Status.OFFLINE, "pipeline stopped")),
    ],
)
def test_a_pipelines_state_is_a_health_in_operator_words(
    state: PipelineState, reason: str, expected: tuple[Status, str]
) -> None:
    assert pipeline_health(PipelineStatus(state, reason)) == expected
