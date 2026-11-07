"""The XR18 protocol's timing rules, as a plain class told the time on every call.

What it decides (the `mixer` worker around it does the socket I/O):

- **Keep-alive.** `/xremote` every 8 s: the mixer drops subscribers after 10 s.
- **Liveness.** `/xinfo` every 2 s; the mixer is `offline` when none has
  been answered for 5 s (UDP has no connection to lose).
- **Write protection.** After we set an address, the mixer's echoes of it
  are ignored for 300 ms (and while the write is still queued), so a late
  echo cannot overwrite the value just set. After that the mixer is the
  truth again: a change made in Mixing Station shows up.
- **No floods.** Every packet we send (writes, queries, keep-alives) spends
  from one budget of 100 packets/s, bursts of 20. In 2026 the mixer hung
  under ~113 packets per 500 ms. Writes queue per address, so a fader
  swept faster than the budget sends only its latest value. Writes go
  before queries; queries are de-duplicated.
"""

import threading
from collections import OrderedDict
from dataclasses import dataclass

from baja_studio.adapters.osc import OscArgument, OscDecodeError, OscMessage, decode_packet
from baja_studio.adapters.osc import encode_message as encode
from baja_studio.adapters.xr18_addresses import INFO, SUBSCRIBE

KEEPALIVE_INTERVAL_S = 8.0
INFO_INTERVAL_S = 2.0
OFFLINE_AFTER_S = 5.0
WRITE_PROTECTION_S = 0.3
PACKETS_PER_SECOND = 100.0
PACKET_BURST = 20.0
# Above this many waiting queries, new ones are dropped (and logged by the caller).
MAX_QUEUED_QUERIES = 400


@dataclass(frozen=True)
class MixerInfo:
    """What `/xinfo` says: the mixer's IP, its network name, model and firmware."""

    ip: str
    name: str
    model: str
    firmware: str


@dataclass(frozen=True)
class PacketRates:
    """Packets per second over the last whole second, both ways."""

    sent: int
    received: int


class Xr18Link:
    """Decides what to send to the mixer and when, and which pushes to believe."""

    def __init__(self, now: float) -> None:
        """A link that has not heard from the mixer yet (so it is offline)."""
        self._lock = threading.Lock()
        self._next_keepalive = now
        self._next_info = now
        self._last_info_reply: float | None = None
        self._tokens = PACKET_BURST
        self._tokens_at = now
        self._writes: OrderedDict[str, OscArgument] = OrderedDict()
        self._queries: OrderedDict[str, None] = OrderedDict()
        self._protected_until: dict[str, float] = {}
        self.info: MixerInfo | None = None
        self._window_start = now
        self._window_counts = [0, 0]
        self._last_rates = PacketRates(0, 0)

    def write(self, address: str, value: OscArgument) -> None:
        """Queue setting `address` to `value`; a newer value replaces a queued one."""
        with self._lock:
            self._writes[address] = value
            self._writes.move_to_end(address)
            self._queries.pop(address, None)  # the write's echo answers it

    def query(self, addresses: list[str]) -> int:
        """Queue reading `addresses` (sent as messages without arguments).

        Addresses already queued are not queued twice. Returns how many were
        dropped because the queue was full.
        """
        dropped = 0
        with self._lock:
            for address in addresses:
                if address in self._queries or address in self._writes:
                    continue
                if len(self._queries) >= MAX_QUEUED_QUERIES:
                    dropped += 1
                    continue
                self._queries[address] = None
        return dropped

    def due_packets(self, now: float) -> list[bytes]:
        """The datagrams to send now, within the packet budget.

        Keep-alive and `/xinfo` come first when due, then queued writes,
        then queries.
        """
        with self._lock:
            self._refill(now)
            messages: list[OscMessage] = []
            if now >= self._next_keepalive and self._spend():
                messages.append(OscMessage(SUBSCRIBE))
                self._next_keepalive = now + KEEPALIVE_INTERVAL_S
            if now >= self._next_info and self._spend():
                messages.append(OscMessage(INFO))
                self._next_info = now + INFO_INTERVAL_S
            while self._writes and self._spend():
                address, value = self._writes.popitem(last=False)
                self._protected_until[address] = now + WRITE_PROTECTION_S
                messages.append(OscMessage(address, (value,)))
            while self._queries and self._spend():
                address, _ = self._queries.popitem(last=False)
                messages.append(OscMessage(address))
            self._count(now, sent=len(messages))
        return [encode(message) for message in messages]

    def receive(self, datagram: bytes, now: float) -> list[OscMessage]:
        """The messages in a datagram from the mixer that should be believed.

        `/xinfo` replies are taken here (they keep the link online); echoes
        of addresses we wrote within the protection window are dropped;
        undecodable packets are ignored.
        """
        try:
            messages = decode_packet(datagram)
        except OscDecodeError:
            messages = []
        believed = []
        with self._lock:
            self._count(now, received=1)
            for message in messages:
                if message.address == INFO:
                    self._take_info(message, now)
                elif not self._is_protected(message.address, now):
                    believed.append(message)
        return believed

    def is_online(self, now: float) -> bool:
        """True while an `/xinfo` has been answered within the last 5 s."""
        with self._lock:
            last = self._last_info_reply
        return last is not None and now - last < OFFLINE_AFTER_S

    def next_wake(self, now: float) -> float:
        """Seconds until something is due: a keep-alive, an `/xinfo`, budget for the queue."""
        with self._lock:
            deadlines = [self._next_keepalive, self._next_info]
            offline_at = (
                None if self._last_info_reply is None else self._last_info_reply + OFFLINE_AFTER_S
            )
            if offline_at is not None and offline_at > now:
                deadlines.append(offline_at)  # wake to notice it going offline
            if self._writes or self._queries:
                self._refill(now)
                deadlines.append(now + max(0.0, (1.0 - self._tokens) / PACKETS_PER_SECOND))
        return max(0.0, min(deadlines) - now)

    def rates(self, now: float) -> PacketRates:
        """Packets per second over the last whole second (for `audio.mixer.packets_per_s`)."""
        with self._lock:
            self._count(now)
            return self._last_rates

    def pending_counts(self) -> tuple[int, int]:
        """(queued writes, queued queries), for tests and the status line."""
        with self._lock:
            return len(self._writes), len(self._queries)

    def _take_info(self, message: OscMessage, now: float) -> None:
        self._last_info_reply = now
        texts = [str(argument) for argument in message.arguments]
        texts += [""] * (4 - len(texts))
        self.info = MixerInfo(*texts[:4])

    def _is_protected(self, address: str, now: float) -> bool:
        if address in self._writes:
            return True
        until = self._protected_until.get(address)
        if until is None:
            return False
        if now < until:
            return True
        del self._protected_until[address]
        return False

    def _refill(self, now: float) -> None:
        elapsed = max(0.0, now - self._tokens_at)
        self._tokens = min(PACKET_BURST, self._tokens + elapsed * PACKETS_PER_SECOND)
        self._tokens_at = now

    def _spend(self) -> bool:
        # A small tolerance: 0.01 s of refill at 100/s is 0.999..., not 1.
        if self._tokens < 1.0 - 1e-6:
            return False
        self._tokens -= 1.0
        return True

    def _count(self, now: float, sent: int = 0, received: int = 0) -> None:
        if now - self._window_start >= 1.0:
            whole_seconds = int(now - self._window_start)
            if whole_seconds == 1:
                self._last_rates = PacketRates(*self._window_counts)
            else:
                self._last_rates = PacketRates(0, 0)  # a silent second in between
            self._window_start += whole_seconds
            self._window_counts = [0, 0]
        self._window_counts[0] += sent
        self._window_counts[1] += received
