"""The hosts desk: messages, screens, card layout and the pushed card, and their state.

`state.hosts`:

- `seats`: the four seats from the settings, each with `online` (some open
  hosts screen represents it) and `screens` (how many)
- `screens`: how many hosts screens are open
- `messages`: the last 50 messages, oldest first (`messages.py`)
- `layout`: every declared card in display order with its visibility
  (`core/hosts_cards.py`)
- `pushed_card`: {id, by, at} forced to the top of every hosts screen, or null
- `config`: canned_messages, quick_replies, reactions, message_expires_s, chime

Every change runs under one lock and publishes what it changed; the
`screens` worker calls `tick` once a second to drop silent screens and
pick up cards that came or went with their modules.
"""

import threading
from collections.abc import Callable
from typing import Any

from baja_studio.core.clock import Clock
from baja_studio.core.hosts_cards import HostsCard, LayoutChoice, resolve_layout
from baja_studio.modules.hosts.config import REACTIONS, HostsSettings
from baja_studio.modules.hosts.messages import MessageStore
from baja_studio.modules.hosts.screens import ScreenTracker

UpdateState = Callable[[str, Any], None]
CollectCards = Callable[[], list[tuple[str, HostsCard]]]


def initial_hosts_state() -> dict[str, Any]:
    """Before the module has read its settings: no seats, no screens, no messages."""
    return {
        "seats": [],
        "screens": 0,
        "messages": [],
        "layout": [],
        "pushed_card": None,
        "config": {
            "canned_messages": [],
            "quick_replies": [],
            "reactions": list(REACTIONS),
            "message_expires_s": 0,
            "chime": False,
        },
    }


def production_sender(source: str) -> str:
    """`web:ab12` → `production:ab12`: who sent a message, as the hosts see it."""
    client = source.split(":", 1)[1] if source.startswith("web:") else source
    return f"production:{client}"


class HostsDesk:
    """Keeps the hosts module's live parts and publishes them."""

    def __init__(
        self,
        clock: Clock,
        settings: Callable[[], HostsSettings],
        store: MessageStore,
        update_state: UpdateState,
        collect_cards: CollectCards,
    ) -> None:
        """`settings` is read fresh on every use; `collect_cards` asks every running module."""
        self._clock = clock
        self._settings = settings
        self.store = store
        self._update = update_state
        self._collect_cards = collect_cards
        self._lock = threading.Lock()
        self._screens = ScreenTracker()
        self._pushed_card: dict[str, Any] | None = None
        self._layout: list[dict[str, Any]] | None = None
        self._seats_view: list[dict[str, Any]] | None = None

    # --- publishing ------------------------------------------------------------------

    def publish_everything(self) -> None:
        """Publish the whole subtree (at start)."""
        with self._lock:
            self._publish_messages()
            self._publish_config()
            self._publish_seats()
            self._publish_layout()
            self._update("pushed_card", self._pushed_card)

    def settings_changed(self) -> None:
        """Seats, layout or texts were edited: publish what follows from them."""
        with self._lock:
            self._publish_config()
            self._publish_seats()
            self._publish_layout()

    def tick(self) -> None:
        """Once a second: forget silent screens, follow cards that came or went."""
        with self._lock:
            if self._screens.expire(self._clock.monotonic()):
                self._publish_seats()
            self._publish_layout()

    def layout(self) -> list[dict[str, Any]]:
        """The layout as last published."""
        with self._lock:
            return list(self._layout or [])

    def screen_count(self) -> int:
        """How many hosts screens are open now."""
        with self._lock:
            return self._screens.screen_count()

    def online_seats(self) -> dict[int, int]:
        """Seats with at least one open screen, and how many."""
        with self._lock:
            return self._screens.online_seats()

    # --- messages --------------------------------------------------------------------

    def send(
        self, source: str, target: int | str, text: str, kind: str, expires_s: int | None
    ) -> dict[str, Any]:
        """Store and publish a new message from production."""
        with self._lock:
            message = self.store.add(production_sender(source), target, text, kind, expires_s)
            self._publish_messages()
        return message

    def react(self, message_id: int, seat_id: int, reaction: str) -> dict[str, Any]:
        """A host tapped a reaction or a quick reply."""
        with self._lock:
            message = self.store.add_reaction(message_id, seat_id, reaction)
            self._publish_messages()
        return message

    def reply(self, message_id: int, seat_id: int, text: str) -> dict[str, Any]:
        """A host typed an answer."""
        with self._lock:
            message = self.store.set_reply(message_id, seat_id, text)
            self._publish_messages()
        return message

    def mark_shown(self, message_id: int, seat_ids: list[int]) -> dict[str, Any]:
        """A hosts screen showed a message to these seats (delivery)."""
        with self._lock:
            message = self.store.mark_shown(message_id, seat_ids)
            if message is None:
                return self.store.get(message_id)
            self._publish_messages()
        return message

    def dismiss(self, message_id: int) -> dict[str, Any]:
        """Production or a host waved a message away."""
        with self._lock:
            message = self.store.dismiss(message_id)
            self._publish_messages()
        return message

    def clear(self) -> int:
        """Start a new session of messages."""
        with self._lock:
            count = self.store.clear()
            self._publish_messages()
        return count

    # --- screens and cards -----------------------------------------------------------

    def register_screen(self, source: str, seat_ids: list[int]) -> None:
        """A hosts screen said which seats it represents (it repeats this every 2 s)."""
        with self._lock:
            if self._screens.heard_from(source, seat_ids, self._clock.monotonic()):
                self._publish_seats()

    def push_card(self, card_id: str | None, source: str) -> dict[str, Any] | None:
        """Force a card to the top of every hosts screen (None: let the layout decide)."""
        with self._lock:
            if card_id is None:
                self._pushed_card = None
            else:
                self._pushed_card = {
                    "id": card_id,
                    "by": source,
                    "at": self._clock.now_utc().isoformat(),
                }
            self._update("pushed_card", self._pushed_card)
            return self._pushed_card

    # --- under the lock --------------------------------------------------------------

    def _publish_messages(self) -> None:
        self._update("messages", self.store.recent())

    def _publish_config(self) -> None:
        settings = self._settings()
        self._update(
            "config",
            {
                "canned_messages": list(settings.canned_messages),
                "quick_replies": list(settings.quick_replies),
                "reactions": list(REACTIONS),
                "message_expires_s": settings.message_expires_s,
                "chime": settings.chime,
            },
        )

    def _publish_seats(self) -> None:
        online = self._screens.online_seats()
        seats = [
            {**seat.model_dump(), "online": seat.id in online, "screens": online.get(seat.id, 0)}
            for seat in self._settings().seats
        ]
        if seats != self._seats_view:
            self._seats_view = seats
            self._update("seats", seats)
        self._update("screens", self._screens.screen_count())

    def _publish_layout(self) -> None:
        choices = [LayoutChoice(entry.id, entry.visible) for entry in self._settings().layout]
        layout = resolve_layout(self._collect_cards(), choices)
        if layout == self._layout:
            return
        self._layout = layout
        self._update("layout", layout)
        # A pushed card whose module went away has nothing to show any more.
        if self._pushed_card is not None and all(
            card["id"] != self._pushed_card["id"] for card in layout
        ):
            self._pushed_card = None
            self._update("pushed_card", None)
