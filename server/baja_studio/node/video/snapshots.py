"""Cutting ffmpeg's snapshot stream (multipart JPEG on stdout) into pictures.

ffmpeg's `mpjpeg` muxer writes each picture as a part with its length:

    --ffmpeg\\r\\n
    Content-type: image/jpeg\\r\\n
    Content-length: 4510\\r\\n
    \\r\\n
    <4510 bytes of JPEG>\\r\\n

so a picture is taken whole by its length, never by searching the JPEG's
bytes. Only the latest picture matters (the studio asks for one about once a
second), so the reader hands each one over as it completes and keeps no
others; what it holds at any time is at most one unfinished picture.
"""

from collections.abc import Callable

# A part bigger than this is not a 320-pixel snapshot: the stream is out of step.
MAX_PART_BYTES = 4 * 1024 * 1024
_HEADER_END = b"\r\n\r\n"
_LENGTH_HEADER = b"content-length:"


class MultipartJpegReader:
    """Fed stdout's bytes in any chunks; calls `on_picture(jpeg)` for each complete picture."""

    def __init__(self, on_picture: Callable[[bytes], None]) -> None:
        """Nothing read yet."""
        self._on_picture = on_picture
        self._buffer = bytearray()
        self.pictures = 0
        self.resyncs = 0

    def feed(self, chunk: bytes) -> None:
        """Add bytes from stdout; complete pictures are handed over at once."""
        self._buffer.extend(chunk)
        while self._take_one():
            pass
        if len(self._buffer) > MAX_PART_BYTES:
            self._resync()

    def _resync(self) -> None:
        """Throw away what we hold and wait for the next part's boundary."""
        self.resyncs += 1
        next_part = self._buffer.find(b"--", 1)
        del self._buffer[: next_part if next_part > 0 else len(self._buffer)]

    def _take_one(self) -> bool:
        """Hand over the first picture in the buffer, if it is all there."""
        header_end = self._buffer.find(_HEADER_END)
        if header_end < 0:
            return False
        length = self._content_length(bytes(self._buffer[:header_end]))
        if length is None or length > MAX_PART_BYTES:
            # Not a header we understand: skip to the next boundary.
            del self._buffer[: header_end + len(_HEADER_END)]
            self.resyncs += 1
            return True
        start = header_end + len(_HEADER_END)
        if len(self._buffer) < start + length:
            return False
        picture = bytes(self._buffer[start : start + length])
        del self._buffer[: start + length]
        self.pictures += 1
        self._on_picture(picture)
        return True

    @staticmethod
    def _content_length(header: bytes) -> int | None:
        for line in header.split(b"\r\n"):
            if line.lower().startswith(_LENGTH_HEADER):
                try:
                    return int(line[len(_LENGTH_HEADER) :].strip())
                except ValueError:
                    return None
        return None
