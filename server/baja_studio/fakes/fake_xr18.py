"""A fake XR18 you can run: real OSC over UDP, for trying the audio page without the desk.

    python -m baja_studio.fakes.fake_xr18 --port 10024

Point the audio settings' mixer address at this machine. Type commands on
standard input while it runs:

    set /ch/01/mix/fader 0.5     a change made "in Mixing Station" (pushed to the studio)
    set /ch/01/mix/on 0          ints are written without a decimal point
    mute 1 on | mute 1 off       shortcuts for what the hosts' audio card reads:
    lr 1 off                       channel 1 off the main mix (LR assign)
    fader 1 0.75                   channel 1's fader
    send 5 bus1 0.75               PROD 1 into host 1's in-ears
    sendon 1 bus5 off              a send's on switch
    tap 1 bus5 pre | post          a send taken before or after the fader
    toggle /ch/01/mix/lr         flip any 0/1 switch
    drop on | drop off           stop/start answering, like a pulled cable
    recover                      un-hang after a flood (more than 113 packets per 500 ms)
    snapshot 3                   store the current mix as snapshot 3 (for /-snap/load 3)
    forget /ch/01/mix/lr         stop reporting an address (a firmware that never answers it)
    show /ch/01                  print values under a prefix
    quit

The behaviour lives in `simulated_xr18.py`; this file only adds the socket,
the real clock and the console.
"""

import argparse
import socket
import sys
import threading
import time

from baja_studio.adapters.osc import OscArgument
from baja_studio.adapters.xr18_addresses import (
    XR18_PORT,
    fader_address,
    lr_assign_address,
    mute_address,
    send_address,
    send_on_address,
    send_tap_address,
)
from baja_studio.fakes.simulated_xr18 import Outgoing, SimulatedXr18

# Tap values the `tap` shortcut writes (see POST_FADER_TAPS in xr18_addresses).
PRE_FADER_TAP = 3
POST_TAP = 4


class UdpFakeXr18:
    """The simulated mixer behind a UDP socket; clients are named by host:port."""

    def __init__(self, mixer: SimulatedXr18, host: str, port: int) -> None:
        """Bind the socket; `serve_forever` starts answering."""
        self.mixer = mixer
        self.lock = threading.Lock()
        self._socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._socket.bind((host, port))
        self._clients: dict[str, tuple[str, int]] = {}

    def serve_forever(self) -> None:
        """Answer datagrams until the socket is closed."""
        while True:
            try:
                datagram, sender = self._socket.recvfrom(65535)
            except OSError:
                return
            client = f"{sender[0]}:{sender[1]}"
            self._clients[client] = sender
            with self.lock:
                outgoing = self.mixer.handle(client, datagram, time.monotonic())
            self.send(outgoing)

    def change(self, address: str, value: OscArgument) -> None:
        """Make a change as if from the desk and push it."""
        with self.lock:
            outgoing = self.mixer.external_change(address, value, time.monotonic())
        self.send(outgoing)

    def send(self, outgoing: Outgoing) -> None:
        """Send datagrams to the clients they are for."""
        for client, datagram in outgoing:
            if client in self._clients:
                self._socket.sendto(datagram, self._clients[client])

    def close(self) -> None:
        """Stop serving."""
        self._socket.close()


def parse_value(text: str) -> OscArgument:
    """ "1" is an int, "0.5" a float, anything else a string."""
    try:
        return int(text)
    except ValueError:
        pass
    try:
        return float(text)
    except ValueError:
        return text


def shortcut_change(
    words: list[str], values: dict[str, OscArgument]
) -> tuple[str, OscArgument] | None:
    """The (address, value) a console shortcut stands for, or None if `words` is not one.

    Channels and buses are written as the studio writes them ("1", "aux",
    "bus5"). `mute 1 on` mutes; `lr`, `sendon` and `toggle` switch 1/0;
    `tap … pre` takes the send before the fader, `post` after it.
    """
    try:
        match words:
            case ["mute", channel, ("on" | "off") as state]:
                return mute_address(channel), 0 if state == "on" else 1
            case ["lr", channel, ("on" | "off") as state]:
                return lr_assign_address(channel), int(state == "on")
            case ["fader", channel, level]:
                return fader_address(channel), float(level)
            case ["send", channel, bus, level]:
                return send_address(channel, bus), float(level)
            case ["sendon", channel, bus, ("on" | "off") as state]:
                return send_on_address(channel, bus), int(state == "on")
            case ["tap", channel, bus, ("pre" | "post") as where]:
                return send_tap_address(channel, bus), PRE_FADER_TAP if where == "pre" else POST_TAP
            case ["toggle", address] if values.get(address) in (0, 1):
                return address, 1 - int(values[address])
    except ValueError:
        return None
    return None


def run_console(fake: UdpFakeXr18) -> None:
    """Read commands from stdin until "quit" or end of input."""
    for line in sys.stdin:
        words = line.split()
        if not words:
            continue
        command = words[0]
        if command == "quit":
            return
        shortcut = shortcut_change(words, fake.mixer.values)
        if command == "set" and len(words) == 3:
            fake.change(words[1], parse_value(words[2]))
        elif shortcut is not None:
            fake.change(*shortcut)
        elif command == "drop" and len(words) == 2:
            fake.mixer.drop_packets = words[1] == "on"
        elif command == "recover":
            fake.mixer.recover()
        elif command == "snapshot" and len(words) == 2:
            fake.mixer.snapshots[int(words[1])] = dict(fake.mixer.values)
        elif command == "forget" and len(words) == 2:
            # An address the mixer does not hold is neither answered nor written.
            with fake.lock:
                fake.mixer.values.pop(words[1], None)
        elif command == "show":
            prefix = words[1] if len(words) > 1 else "/"
            for address, value in sorted(fake.mixer.values.items()):
                if address.startswith(prefix):
                    print(f"{address} {value!r}")
        else:
            print(
                "commands: set ADDRESS VALUE | mute CH on|off | lr CH on|off | fader CH LEVEL | "
                "send CH BUS LEVEL | sendon CH BUS on|off | tap CH BUS pre|post | "
                "toggle ADDRESS | drop on|off | recover | snapshot N | forget ADDRESS | show PREFIX"
            )
        print(f"(hung: {fake.mixer.hung}, dropping: {fake.mixer.drop_packets})", flush=True)


def main(argv: list[str] | None = None) -> None:
    """Command-line entry point."""
    parser = argparse.ArgumentParser(description="A fake Behringer XR18 speaking OSC over UDP.")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=XR18_PORT)
    parser.add_argument("--name", default="XR18-FAKE")
    parser.add_argument("--no-echo", action="store_true", help="do not echo writes to their sender")
    arguments = parser.parse_args(argv)
    mixer = SimulatedXr18(name=arguments.name, echo_to_sender=not arguments.no_echo)
    fake = UdpFakeXr18(mixer, arguments.host, arguments.port)
    threading.Thread(target=fake.serve_forever, name="fake-xr18", daemon=True).start()
    print(f"fake XR18 listening on {arguments.host}:{arguments.port}; type 'help'", flush=True)
    try:
        run_console(fake)
    finally:
        fake.close()


if __name__ == "__main__":
    main()
