"""A pretend XR18 as a plain class: OSC datagrams in, datagrams out, time passed in.

Shared by the runnable fake (`fake_xr18.py`, real UDP) and the in-process
network the tests use. It models what the studio relies on:

- Every strip's fader, mute (`mix/on`, 1 = playing), name, pan, LR assign
  and the six aux sends of each input (level, on switch, tap), at the
  addresses in `adapters/xr18_addresses.py`.
- A message without arguments is a query: the current value comes back.
- A message with an argument sets the value and is pushed to every client
  subscribed with `/xremote` within the last 10 s, the sender included
  (the echo the studio's write protection exists for; `echo_to_sender`
  turns it off).
- `/xinfo` answers (ip, name, model, firmware); `/-snap/load N` loads a
  stored snapshot and pushes what changed.
- `external_change` is Mixing Station moving something; `drop_packets`
  makes the mixer deaf and mute (a cable pulled).
- More than 113 packets within 500 ms makes it hang, as the real one did in
  2026, until `recover()`; tests assert the studio never gets there.
"""

from collections import deque
from dataclasses import dataclass, field

from baja_studio.adapters.osc import OscArgument, OscDecodeError, OscMessage, decode_packet
from baja_studio.adapters.osc import encode_message as encode
from baja_studio.adapters.xr18_addresses import (
    AUX_BUSES,
    CHANNEL_IDS,
    INFO,
    SEND_SOURCES,
    SNAPSHOT_LOAD,
    SUBSCRIBE,
    fader_address,
    lr_assign_address,
    mute_address,
    name_address,
    pan_address,
    send_address,
    send_on_address,
    send_tap_address,
)

SUBSCRIPTION_SECONDS = 10.0
FLOOD_PACKETS = 113
FLOOD_WINDOW_SECONDS = 0.5

Outgoing = list[tuple[str, bytes]]


# Where a fresh fake takes its aux sends from: pre-fader, as monitor mixes usually are.
DEFAULT_SEND_TAP = 3


def default_mixer_values() -> dict[str, OscArgument]:
    """A freshly reset mixer: faders at unity, nothing muted, sends off, pans centred.

    Every input is assigned to LR, and every send is switched on and taken
    pre-fader (`DEFAULT_SEND_TAP`); the hosts' audio card reads all three.
    """
    values: dict[str, OscArgument] = {}
    for channel in CHANNEL_IDS:
        values[fader_address(channel)] = 0.75
        values[mute_address(channel)] = 1
        values[name_address(channel)] = ""
    for channel in SEND_SOURCES:
        values[pan_address(channel)] = 0.5
        values[lr_assign_address(channel)] = 1
        for bus in AUX_BUSES:
            values[send_address(channel, bus)] = 0.0
            values[send_on_address(channel, bus)] = 1
            values[send_tap_address(channel, bus)] = DEFAULT_SEND_TAP
    return values


@dataclass
class SimulatedXr18:
    """The mixer's state and its rules; no sockets, no clock of its own."""

    ip: str = "192.168.1.10"
    name: str = "XR18-FAKE"
    model: str = "XR18"
    firmware: str = "1.22"
    echo_to_sender: bool = True
    drop_packets: bool = False
    values: dict[str, OscArgument] = field(default_factory=default_mixer_values)
    snapshots: dict[int, dict[str, OscArgument]] = field(default_factory=dict)
    # Every message received, with the client that sent it (tests count these).
    received: list[tuple[str, OscMessage]] = field(default_factory=list)
    hung: bool = False
    _subscribers: dict[str, float] = field(default_factory=dict)
    _recent_packets: deque[float] = field(default_factory=deque)

    def handle(self, client: str, datagram: bytes, now: float) -> Outgoing:
        """A datagram from `client` arrived; return the datagrams to send, to whom."""
        if self.drop_packets or self.hung:
            return []
        self._note_packet(now)
        if self.hung:
            return []
        try:
            messages = decode_packet(datagram)
        except OscDecodeError:
            return []
        outgoing: Outgoing = []
        for message in messages:
            self.received.append((client, message))
            outgoing += self._handle_message(client, message, now)
        return outgoing

    def external_change(self, address: str, value: OscArgument, now: float) -> Outgoing:
        """Someone at Mixing Station (or the desk) changed a value: push it to subscribers."""
        self.values[address] = value
        return self._push(OscMessage(address, (value,)), now, skip=None)

    def subscribed_clients(self, now: float) -> list[str]:
        """Clients whose `/xremote` is less than 10 s old."""
        return [
            client
            for client, since in self._subscribers.items()
            if now - since < SUBSCRIPTION_SECONDS
        ]

    def recover(self) -> None:
        """Power-cycle after a hang."""
        self.hung = False
        self._recent_packets.clear()

    def queries_received(self, prefix: str = "/") -> list[str]:
        """Addresses queried (sent without arguments) under `prefix`, oldest first."""
        return [
            message.address
            for _, message in self.received
            if not message.arguments
            and message.address.startswith(prefix)
            and message.address not in (SUBSCRIBE, INFO)
        ]

    def _handle_message(self, client: str, message: OscMessage, now: float) -> Outgoing:
        if message.address == SUBSCRIBE:
            self._subscribers[client] = now
            return []
        if message.address == INFO:
            reply = OscMessage(INFO, (self.ip, self.name, self.model, self.firmware))
            return [(client, encode(reply))]
        if message.address == SNAPSHOT_LOAD and message.arguments:
            return self._load_snapshot(int(message.arguments[0]), now)
        if message.address not in self.values:
            return []
        if not message.arguments:
            return [(client, encode(OscMessage(message.address, (self.values[message.address],))))]
        self.values[message.address] = message.arguments[0]
        return self._push(message, now, skip=None if self.echo_to_sender else client)

    def _load_snapshot(self, index: int, now: float) -> Outgoing:
        outgoing: Outgoing = []
        for address, value in self.snapshots.get(index, {}).items():
            if self.values.get(address) != value:
                outgoing += self.external_change(address, value, now)
        return outgoing

    def _push(self, message: OscMessage, now: float, skip: str | None) -> Outgoing:
        datagram = encode(message)
        return [(client, datagram) for client in self.subscribed_clients(now) if client != skip]

    def _note_packet(self, now: float) -> None:
        self._recent_packets.append(now)
        while self._recent_packets and now - self._recent_packets[0] > FLOOD_WINDOW_SECONDS:
            self._recent_packets.popleft()
        if len(self._recent_packets) > FLOOD_PACKETS:
            self.hung = True
