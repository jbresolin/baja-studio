"""Tests for the state tree and its broadcast (core/state.py).

The state tree is what every browser sees. Changes are collected ("coalesced")
and sent as numbered patches; `seq` lets a browser notice a missing patch.
"""

import asyncio

from baja_studio.core.state import MAX_QUEUED_MESSAGES_PER_CLIENT, StateStore


def test_update_creates_nested_values_and_get_reads_them_back() -> None:
    state = StateStore()

    # "camera.position.pan" means: in the "camera" object, inside "position", set "pan".
    state.update("heartbeat", "camera.position.pan", 12)

    assert state.get("heartbeat", "camera.position.pan") == 12
    assert state.get("heartbeat", "camera") == {"position": {"pan": 12}}


def test_an_empty_path_replaces_the_whole_module_subtree() -> None:
    state = StateStore()
    state.update("echo", "old", 1)

    state.update("echo", "", {"new": 2})

    assert state.get("echo") == {"new": 2}


def test_get_returns_none_for_a_missing_path() -> None:
    state = StateStore()

    assert state.get("nothing", "here") is None


def test_values_are_copied_so_later_changes_by_the_caller_do_not_leak_in() -> None:
    # If the store kept a reference to our list, changing the list afterwards
    # would change the state without any patch being sent.
    state = StateStore()
    items = [1, 2]
    state.update("demo", "items", items)

    items.append(3)

    assert state.get("demo", "items") == [1, 2]


def test_several_updates_to_the_same_path_are_coalesced_into_one_patch() -> None:
    state = StateStore()

    # Three quick changes to the same value before anything is sent...
    state.update("heartbeat", "count", 1)
    state.update("heartbeat", "count", 2)
    state.update("heartbeat", "count", 3)
    patches = state.drain_pending_patches()

    # ...become a single patch carrying only the latest value.
    assert patches == [
        {"type": "patch", "module": "heartbeat", "path": "count", "value": 3, "seq": 1}
    ]


def test_patches_are_numbered_consecutively_and_keep_the_order_of_changes() -> None:
    state = StateStore()
    state.update("a", "x", 1)
    state.update("b", "y", 2)
    # Changing "a.x" again moves it after "b.y", because that is now the latest change.
    state.update("a", "x", 3)

    patches = state.drain_pending_patches()

    assert [(patch["module"], patch["value"], patch["seq"]) for patch in patches] == [
        ("b", 2, 1),
        ("a", 3, 2),
    ]


def test_the_snapshot_carries_the_whole_tree_and_the_latest_seq() -> None:
    state = StateStore()
    state.register_module("heartbeat", {"count": 0})
    state.drain_pending_patches()  # this used seq 1

    snapshot = state.snapshot()

    assert snapshot == {"type": "snapshot", "seq": 1, "state": {"heartbeat": {"count": 0}}}


def test_a_new_client_first_receives_a_snapshot_then_patches() -> None:
    # Clients live on an asyncio event loop, so this test runs one.
    async def scenario() -> list[dict[str, object]]:
        state = StateStore(coalesce_seconds=0)
        state.attach_loop(asyncio.get_running_loop())
        state.register_module("heartbeat", {"count": 0})
        state.flush_now()

        channel = state.add_client("browser-1")
        state.update("heartbeat", "count", 5)
        state.flush_now()

        return [channel.queue.get_nowait(), channel.queue.get_nowait()]

    snapshot, patch = asyncio.run(scenario())

    assert snapshot["type"] == "snapshot"
    assert snapshot["state"] == {"heartbeat": {"count": 0}}
    assert patch["type"] == "patch"
    assert patch["value"] == 5
    assert patch["seq"] == snapshot["seq"] + 1  # type: ignore[operator]


def test_updates_from_another_thread_are_flushed_on_the_event_loop() -> None:
    # Workers call `update` from their own threads. The store must hand the
    # broadcast over to the event loop by itself.
    async def scenario() -> dict[str, object]:
        state = StateStore(coalesce_seconds=0)
        state.attach_loop(asyncio.get_running_loop())
        channel = state.add_client("browser-1")
        channel.queue.get_nowait()  # throw away the initial snapshot

        await asyncio.to_thread(state.update, "heartbeat", "count", 7)

        return await asyncio.wait_for(channel.queue.get(), timeout=5)

    patch = asyncio.run(scenario())

    assert patch["value"] == 7


def test_a_client_that_falls_far_behind_gets_a_fresh_snapshot_instead() -> None:
    async def scenario() -> list[dict[str, object]]:
        state = StateStore(coalesce_seconds=0)
        state.attach_loop(asyncio.get_running_loop())
        channel = state.add_client("slow-browser")
        # Pile up more patches than a client is allowed to have waiting.
        for number in range(MAX_QUEUED_MESSAGES_PER_CLIENT + 1):
            state.update("demo", f"value{number}", number)
        state.flush_now()
        messages = []
        while not channel.queue.empty():
            messages.append(channel.queue.get_nowait())
        return messages

    messages = asyncio.run(scenario())

    # Instead of a thousand patches it gets one snapshot with everything in it.
    assert len(messages) == 1
    assert messages[0]["type"] == "snapshot"


def test_send_snapshot_queues_a_snapshot_for_that_client_only() -> None:
    async def scenario() -> tuple[int, int]:
        state = StateStore()
        state.attach_loop(asyncio.get_running_loop())
        first = state.add_client("first")
        second = state.add_client("second")

        state.send_snapshot("first")

        return first.queue.qsize(), second.queue.qsize()

    first_count, second_count = asyncio.run(scenario())

    # Each got the snapshot on connect; only "first" got the extra one.
    assert (first_count, second_count) == (2, 1)
