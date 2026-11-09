"""Tests for the hosts screen's now/next summary (modules/playout/hosts_view.py).

The builder is a plain function; the last tests run it inside the module to
check it follows the clip on air and changes about once a second.
"""

import math
from typing import Any

from baja_studio.modules.playout.hosts_view import build_hosts_view
from tests.modules.playout_studio import CLIP_SECONDS, PlayoutStudio, ready_studio


def clip(title: str, duration: float | None) -> dict[str, Any]:
    return {"title": title, "duration_s": duration}


def test_nothing_on_air_and_nothing_queued() -> None:
    assert build_hosts_view(None, None, []) == {
        "on_air": None,
        "next": [],
        "queue_remaining_s": 0.0,
    }


def test_on_air_next_three_and_the_known_remaining_time_of_the_queue() -> None:
    program = {"clip": clip("Enduro · carro 42", 30.0), "remaining_s": 12.3, "duration_s": 30.0}
    cued = clip("Logo", 10.0)
    queue = [{"title": f"clip {n}", "duration_s": 20.0} for n in range(3)] + [
        {"title": "unknown", "duration_s": None}
    ]

    view = build_hosts_view(program, cued, queue)

    # Remaining time is whole seconds, rounded up as a countdown reads.
    assert view["on_air"] == {"label": "Enduro · carro 42", "remaining_s": 13, "duration_s": 30.0}
    # The cued clip comes first, then the queue; three at most.
    assert [item["label"] for item in view["next"]] == ["Logo", "clip 0", "clip 1"]
    # Everything after the clip on air with a known duration (10 + 3 x 20).
    assert view["queue_remaining_s"] == 70.0


def test_the_module_publishes_it_and_counts_down_once_a_second(playout: PlayoutStudio) -> None:
    first, second, _ = ready_studio(playout)
    playout.command("queue_add", clip_ids=[first, second])
    playout.command("take", cue_if_needed=True)
    # Count what the publisher sends under "hosts_view" (it wraps update_state).
    publisher = playout.module.controller.publisher
    send = publisher._update
    updates: list[Any] = []

    def counting(path: str, value: Any) -> None:
        if path == "hosts_view":
            updates.append(value)
        send(path, value)

    publisher._update = counting

    # Let the clip play until about two seconds have gone (polled at 5 Hz).
    playout.soon(lambda: (playout.state("program") or {}).get("remaining_s", 99) <= 8.0)

    view = playout.state("hosts_view")
    assert view["on_air"]["label"] == "a"
    assert view["on_air"]["remaining_s"] == math.ceil(playout.state("program.remaining_s"))
    assert [item["label"] for item in view["next"]] == ["b"]
    assert view["queue_remaining_s"] == CLIP_SECONDS
    # About ten position polls happened, but the view changed only with the
    # whole second: 10 → 9 → 8 (and the take's own publish).
    assert 2 <= len(updates) <= 4
