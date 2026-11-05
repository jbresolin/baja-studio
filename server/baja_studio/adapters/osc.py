"""Open Sound Control (OSC 1.0) messages: encode and decode, byte for byte.

The XR18 speaks OSC over UDP. Only the parts it uses are implemented:
messages with int32 (`i`), float32 (`f`), string (`s`) and blob (`b`)
arguments, and bundles on the way in (unpacked into their messages). A
hand-rolled codec, rather than `python-osc`, because the message set is
small, and tests compare exact bytes (ADR 0006).

Wire format, all big-endian, everything padded to a multiple of 4 bytes:
address string (NUL-terminated), type tags (",fi" ...), then each argument.
"""

import struct
from dataclasses import dataclass

OscArgument = int | float | str | bytes

BUNDLE_TAG = b"#bundle\x00"


class OscDecodeError(ValueError):
    """A packet that is not valid OSC (the mixer never sends one; the network might)."""


@dataclass(frozen=True)
class OscMessage:
    """One OSC message: an address such as `/ch/01/mix/fader` and its arguments."""

    address: str
    arguments: tuple[OscArgument, ...] = ()

    @property
    def first(self) -> OscArgument | None:
        """The first argument, or None for a message without arguments (a query)."""
        return self.arguments[0] if self.arguments else None


def _padded(raw: bytes) -> bytes:
    """`raw` followed by 1-4 NULs so the length is a multiple of 4 (OSC strings)."""
    return raw + b"\x00" * (4 - len(raw) % 4)


def _padded_blob(raw: bytes) -> bytes:
    """Blobs are padded with 0-3 NULs (no terminator needed)."""
    return raw + b"\x00" * (-len(raw) % 4)


def encode_message(message: OscMessage) -> bytes:
    """The datagram for `message`.

    Python `int` becomes `i`, `float` becomes `f`, `str` becomes `s` and
    `bytes` becomes `b`. `bool` is refused: the XR18 wants 0/1 ints, so the
    caller must say so.
    """
    tags = ","
    payload = b""
    for argument in message.arguments:
        if isinstance(argument, bool):
            raise TypeError("OSC has no bool for the XR18; send 0 or 1")
        if isinstance(argument, int):
            tags += "i"
            payload += struct.pack(">i", argument)
        elif isinstance(argument, float):
            tags += "f"
            payload += struct.pack(">f", argument)
        elif isinstance(argument, str):
            tags += "s"
            payload += _padded(argument.encode("utf-8"))
        else:
            tags += "b"
            payload += struct.pack(">i", len(argument)) + _padded_blob(argument)
    return _padded(message.address.encode("ascii")) + _padded(tags.encode("ascii")) + payload


def decode_packet(datagram: bytes) -> list[OscMessage]:
    """The messages in one datagram: one, or several if it is a bundle.

    Raises `OscDecodeError` for anything malformed.
    """
    if datagram.startswith(BUNDLE_TAG):
        return _decode_bundle(datagram)
    return [_decode_message(datagram)]


def _decode_bundle(datagram: bytes) -> list[OscMessage]:
    # "#bundle\0", an 8-byte time tag, then (int32 size, element) pairs.
    offset = len(BUNDLE_TAG) + 8
    messages: list[OscMessage] = []
    while offset < len(datagram):
        size = _read_int(datagram, offset)
        offset += 4
        element = datagram[offset : offset + size]
        if size < 0 or len(element) != size:
            raise OscDecodeError("bundle element runs past the end of the packet")
        messages.extend(decode_packet(element))
        offset += size
    return messages


def _decode_message(datagram: bytes) -> OscMessage:
    address, offset = _read_string(datagram, 0)
    if not address.startswith("/"):
        raise OscDecodeError(f"not an OSC address: {address!r}")
    if offset >= len(datagram):
        return OscMessage(address)  # very old senders omit the type tags
    tags, offset = _read_string(datagram, offset)
    if not tags.startswith(","):
        raise OscDecodeError(f"bad type tags {tags!r}")
    arguments: list[OscArgument] = []
    for tag in tags[1:]:
        if tag == "i":
            arguments.append(_read_int(datagram, offset))
            offset += 4
        elif tag == "f":
            arguments.append(_read_float(datagram, offset))
            offset += 4
        elif tag == "s":
            text, offset = _read_string(datagram, offset)
            arguments.append(text)
        elif tag == "b":
            size = _read_int(datagram, offset)
            blob = datagram[offset + 4 : offset + 4 + size]
            if size < 0 or len(blob) != size:
                raise OscDecodeError("blob runs past the end of the packet")
            arguments.append(blob)
            offset += 4 + size + (-size % 4)
        else:
            raise OscDecodeError(f"unsupported OSC type tag {tag!r}")
    return OscMessage(address, tuple(arguments))


def _read_string(datagram: bytes, offset: int) -> tuple[str, int]:
    end = datagram.find(b"\x00", offset)
    if end < 0:
        raise OscDecodeError("string without a terminator")
    text = datagram[offset:end].decode("utf-8", errors="replace")
    # Skip the terminator and the padding up to the next multiple of 4.
    return text, end + 4 - (end - offset) % 4


def _read_int(datagram: bytes, offset: int) -> int:
    if offset + 4 > len(datagram):
        raise OscDecodeError("packet ends inside an int32")
    value: int = struct.unpack_from(">i", datagram, offset)[0]
    return value


def _read_float(datagram: bytes, offset: int) -> float:
    if offset + 4 > len(datagram):
        raise OscDecodeError("packet ends inside a float32")
    value: float = struct.unpack_from(">f", datagram, offset)[0]
    return value
