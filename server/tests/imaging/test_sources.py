"""Tests for agent cameras' image controls (imaging/sources.py) against the fake agent.

A laptop agent serves `GET/PUT /cameras/{id}/image` with raw device values
and ranges (contract decision 10). The studio reaches it through the agents
module, so here a fake agent runs on the in-process transport. USB sources
are covered by test_service.py.
"""

import pytest

from baja_studio.agents.contract import AGENT_PORT
from baja_studio.core.module import CommandError
from baja_studio.fakes.in_process import InProcessTransport
from tests.fake_agents import (
    AGENT_HOST,
    DELIVERY_SLACK_SECONDS,
    StartAgent,
    advance_in_steps,
)
from tests.imaging.uvc_harness import image_card, saved_image
from tests.modules.camera_studio import SMTAV_HOST, Studio, add_agent, agent_camera


def agent_camera_ready(studio: Studio, start_agent: StartAgent, **overrides: object) -> None:
    start_agent(ptz_cameras=1)
    add_agent(studio)
    studio.configure(cameras=[agent_camera(**overrides)])
    studio.soon(lambda: image_card(studio, "trackside").get("status") == "ok")


def test_an_agent_camera_card_comes_from_the_agent_with_its_ranges(
    studio: Studio, start_agent: StartAgent
) -> None:
    agent_camera_ready(studio, start_agent)

    card = image_card(studio, "trackside")

    assert card["source"] == "agent"
    assert card["agent"] == "laptop-a"
    # Agents give no device defaults (the contract has none).
    assert card["controls"]["white_balance"] == {
        "value": 4600,
        "auto": True,
        "min": 2800,
        "max": 6500,
        "step": 10,
        "default": None,
        "error": "",
    }


def test_an_agent_write_is_read_back_from_its_answer_and_saved(
    studio: Studio, start_agent: StartAgent
) -> None:
    agent_camera_ready(studio, start_agent)

    result = studio.command(
        "set_image", camera="trackside", controls={"white_balance": {"auto": False, "value": 5200}}
    )

    assert result["controls"]["white_balance"]["value"] == 5200
    assert saved_image(studio, "trackside") == {"white_balance": {"value": 5200, "auto": False}}


def test_reset_is_refused_for_an_agent_camera(studio: Studio, start_agent: StartAgent) -> None:
    agent_camera_ready(studio, start_agent)

    with pytest.raises(CommandError, match="use All auto instead"):
        studio.command("reset_image", camera="trackside")


def test_image_settings_are_refused_while_the_camera_is_driven_directly(
    studio: Studio, start_agent: StartAgent, transport: InProcessTransport
) -> None:
    # The direct VISCA fallback has no image controls: say so instead of failing oddly.
    studio.network.add(SMTAV_HOST)
    agent_camera_ready(
        studio,
        start_agent,
        direct_fallback={"driver": "visca_ip", "address": {"host": SMTAV_HOST}},
    )

    transport.unplug(AGENT_HOST, AGENT_PORT)
    advance_in_steps(studio.clock, 5 + DELIVERY_SLACK_SECONDS + 10.5)
    studio.soon(lambda: studio.camera("trackside")["route"] == "fallback")

    with pytest.raises(CommandError, match="driven directly, which has no image controls"):
        studio.command("set_image", camera="trackside", controls={"gain": {"value": 3}})
    assert image_card(studio, "trackside")["status"] == "offline"
