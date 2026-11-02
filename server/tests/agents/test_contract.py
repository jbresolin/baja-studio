"""Tests for the contract models' own rules (agents/contract.py).

Most of the contract is exercised by the conformance suite; these pin down
the few rules that live in the models themselves.
"""

import pytest
from pydantic import ValidationError

from baja_studio.agents.contract import (
    ERROR_HTTP_STATUS,
    CameraState,
    CropRect,
    ErrorCode,
    ModeRequest,
    MoveRequest,
    contract_major,
)


@pytest.mark.parametrize(
    ("version", "major"), [("1.0", 1), ("1.12", 1), ("2.3", 2), ("1", None), ("v1.0", None)]
)
def test_contract_major_reads_the_major_of_major_dot_minor_only(
    version: str, major: int | None
) -> None:
    assert contract_major(version) == major


def test_a_crop_must_lie_inside_the_frame() -> None:
    # 0.1 + 0.9 is 1.0000000000000002 in floating point: still inside.
    assert CropRect(x=0.1, y=0.0, w=0.9, h=1.0).w == 0.9

    with pytest.raises(ValidationError, match="inside the frame"):
        CropRect(x=0.6, y=0.0, w=0.5, h=0.5)


def test_a_move_defaults_to_no_motion_for_300_ms() -> None:
    assert MoveRequest() == MoveRequest(pan=0, tilt=0, zoom=0, ttl_ms=300)


def test_auto_paused_cannot_be_requested() -> None:
    with pytest.raises(ValidationError):
        ModeRequest.model_validate({"mode": "auto_paused"})


def test_fields_a_newer_minor_version_adds_are_ignored() -> None:
    # An agent on contract 1.3 might send fields this studio has never heard of.
    state = CameraState.model_validate(
        {
            "status": "ok",
            "mode": "manual",
            "tracker": {"state": "idle"},
            "position": None,
            "moving": False,
            "battery_percent": 80,
        }
    )

    assert not hasattr(state, "battery_percent")


def test_every_error_code_has_an_http_status() -> None:
    assert set(ERROR_HTTP_STATUS) == set(ErrorCode)
