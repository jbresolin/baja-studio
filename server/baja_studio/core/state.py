"""The live state tree every browser sees, and its WebSocket broadcast.

There is one JSON-serialisable tree with one subtree per module. Modules call
`update(module, path, value)` from any thread; changes made within a short
window (about 30 ms) are coalesced and sent to every connected client as
patches numbered by `seq`, so a client that misses one can ask for a snapshot.
"""

import asyncio
import contextlib
import copy
import threading
from dataclasses import dataclass, field
from typing import Any

DEFAULT_COALESCE_SECONDS = 0.03
# Beyond this many unsent messages a client is too slow; it gets a fresh snapshot instead.
MAX_QUEUED_MESSAGES_PER_CLIENT = 1000

Message = dict[str, Any]


@dataclass
class ClientChannel:
    """Outgoing messages for one connected browser, drained by its WebSocket task."""

    client_id: str
    queue: asyncio.Queue[Message] = field(default_factory=asyncio.Queue)


def split_path(path: str) -> list[str]:
    """Turn "a.b.c" into ["a", "b", "c"]; the empty string means the whole subtree."""
    return [part for part in path.split(".") if part] if path else []


class StateStore:
    """Holds the state tree and pushes changes to WebSocket clients."""

    def __init__(self, coalesce_seconds: float = DEFAULT_COALESCE_SECONDS) -> None:
        """Create an empty tree; `attach_loop` must be called before clients connect."""
        self._coalesce_seconds = coalesce_seconds
        # Guards the tree, the pending changes and `seq` as one unit: a snapshot's
        # `seq` must match its tree, and a change must not land in `_pending` after
        # a drain has taken it. Removing the lock passes the tests today only because
        # the GIL makes each dict operation atomic (test_main's concurrent root-route
        # test does not catch it); free-threaded Python would not, so keep it.
        self._lock = threading.Lock()
        self._tree: dict[str, Any] = {}
        self._pending: dict[tuple[str, str], Any] = {}
        self._seq = 0
        self._loop: asyncio.AbstractEventLoop | None = None
        self._flush_scheduled = False
        self._clients: dict[str, ClientChannel] = {}

    def attach_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        """Tell the store which event loop owns the WebSocket clients; called at startup."""
        self._loop = loop

    def register_module(self, module: str, initial_state: dict[str, Any]) -> None:
        """Create (or reset) a module's subtree with its initial state."""
        self.update(module, "", copy.deepcopy(initial_state))

    def update(self, module: str, path: str, value: Any) -> None:
        """Set `value` at dotted `path` inside `module`'s subtree; safe from any thread.

        Missing intermediate objects are created. An empty path replaces the
        whole subtree. The change reaches browsers on the next flush.
        """
        value = copy.deepcopy(value)
        with self._lock:
            self._set_in_tree(module, split_path(path), value)
            # Re-insert so the dict order matches the order changes were made in.
            self._pending.pop((module, path), None)
            self._pending[(module, path)] = value
            needs_flush = not self._flush_scheduled and self._loop is not None
            if needs_flush:
                self._flush_scheduled = True
        if needs_flush and self._loop is not None:
            # RuntimeError: the loop closed during shutdown; nobody is listening any more.
            with contextlib.suppress(RuntimeError):
                self._loop.call_soon_threadsafe(self._schedule_flush)

    def get(self, module: str, path: str = "") -> Any:
        """A copy of the value at `path` in `module`'s subtree (None if absent)."""
        with self._lock:
            node: Any = self._tree.get(module)
            for part in split_path(path):
                if not isinstance(node, dict) or part not in node:
                    return None
                node = node[part]
            return copy.deepcopy(node)

    def snapshot(self) -> Message:
        """The whole tree plus the seq it corresponds to, sent to new clients."""
        with self._lock:
            return {"type": "snapshot", "seq": self._seq, "state": copy.deepcopy(self._tree)}

    def drain_pending_patches(self) -> list[Message]:
        """Take the coalesced changes, number them, and return them as patch messages.

        Called by the flush on the event loop; tests call it directly.
        """
        with self._lock:
            pending = self._pending
            self._pending = {}
            self._flush_scheduled = False
            patches = []
            for (module, path), value in pending.items():
                self._seq += 1
                patches.append(
                    {
                        "type": "patch",
                        "module": module,
                        "path": path,
                        "value": value,
                        "seq": self._seq,
                    }
                )
            return patches

    def add_client(self, client_id: str) -> ClientChannel:
        """Register a browser and queue its initial snapshot; call on the event loop.

        Snapshots sent to a client carry its `client_id`, the name its
        commands are logged under (`web:<client_id>`), so the page can show it.
        """
        channel = ClientChannel(client_id)
        channel.queue.put_nowait({**self.snapshot(), "client_id": client_id})
        self._clients[client_id] = channel
        return channel

    def has_client(self, client_id: str) -> bool:
        """True while a browser with this id is connected."""
        return client_id in self._clients

    def remove_client(self, client_id: str) -> None:
        """Forget a browser after its WebSocket closes."""
        self._clients.pop(client_id, None)

    def send_snapshot(self, client_id: str) -> None:
        """Queue a fresh snapshot for one client that detected a gap in `seq`."""
        channel = self._clients.get(client_id)
        if channel is not None:
            channel.queue.put_nowait({**self.snapshot(), "client_id": client_id})

    def flush_now(self) -> None:
        """Send all pending patches to every client immediately; runs on the event loop."""
        patches = self.drain_pending_patches()
        if not patches:
            return
        for channel in list(self._clients.values()):
            if channel.queue.qsize() + len(patches) > MAX_QUEUED_MESSAGES_PER_CLIENT:
                self._replace_backlog_with_snapshot(channel)
                continue
            for patch in patches:
                channel.queue.put_nowait(patch)

    def _schedule_flush(self) -> None:
        assert self._loop is not None
        self._loop.call_later(self._coalesce_seconds, self.flush_now)

    def _replace_backlog_with_snapshot(self, channel: ClientChannel) -> None:
        while not channel.queue.empty():
            channel.queue.get_nowait()
        channel.queue.put_nowait(self.snapshot())

    def _set_in_tree(self, module: str, parts: list[str], value: Any) -> None:
        if not parts:
            self._tree[module] = value
            return
        node = self._tree.setdefault(module, {})
        for part in parts[:-1]:
            child = node.get(part)
            if not isinstance(child, dict):
                child = {}
                node[part] = child
            node = child
        node[parts[-1]] = value
