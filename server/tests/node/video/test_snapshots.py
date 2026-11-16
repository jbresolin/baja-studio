"""Tests for cutting ffmpeg's snapshot stream into pictures (node/video/snapshots.py).

ffmpeg writes the whole-frame pictures one after the other on its standard
output, each with a small header giving its length ("multipart JPEG"). The
reader gets those bytes in pieces of any size, whatever the operating
system hands over, and must hand back each picture whole.
"""

from baja_studio.node.video.snapshots import MAX_PART_BYTES, MultipartJpegReader


def part(jpeg: bytes) -> bytes:
    """One picture as ffmpeg's mpjpeg muxer writes it (measured with ffmpeg 6.1)."""
    return (
        b"--ffmpeg\r\nContent-type: image/jpeg\r\nContent-length: "
        + str(len(jpeg)).encode()
        + b"\r\n\r\n"
        + jpeg
        + b"\r\n"
    )


# Stand-ins for JPEGs: they start and end like one, with any bytes in between
# (including the boundary text, to prove the reader never searches inside a picture).
FIRST = b"\xff\xd8" + b"first picture --ffmpeg\r\n\r\n" + b"\xff\xd9"
SECOND = b"\xff\xd8" + bytes(range(256)) * 4 + b"\xff\xd9"


def collect() -> tuple[MultipartJpegReader, list[bytes]]:
    pictures: list[bytes] = []
    return MultipartJpegReader(pictures.append), pictures


def test_whole_parts_come_out_as_pictures() -> None:
    reader, pictures = collect()
    reader.feed(part(FIRST) + part(SECOND))
    assert pictures == [FIRST, SECOND]
    assert reader.pictures == 2


def test_bytes_arriving_one_at_a_time_still_make_whole_pictures() -> None:
    # The worst case: every read returns a single byte.
    reader, pictures = collect()
    for byte in part(FIRST) + part(SECOND):
        reader.feed(bytes([byte]))
    assert pictures == [FIRST, SECOND]


def test_a_picture_is_handed_over_only_when_complete() -> None:
    reader, pictures = collect()
    data = part(SECOND)
    reader.feed(data[:-20])
    assert pictures == []  # still waiting for the last bytes
    reader.feed(data[-20:])
    assert pictures == [SECOND]


def test_a_header_without_a_length_is_skipped_and_reading_goes_on() -> None:
    reader, pictures = collect()
    reader.feed(b"--ffmpeg\r\nContent-type: image/jpeg\r\n\r\n" + part(FIRST))
    assert pictures == [FIRST]
    assert reader.resyncs == 1


def test_an_impossible_length_does_not_make_the_reader_hoard_memory() -> None:
    # A garbled length of 1 GB: the reader skips that header instead of waiting
    # for (and keeping) a gigabyte of bytes.
    reader, pictures = collect()
    reader.feed(b"--ffmpeg\r\nContent-length: 1000000000\r\n\r\n")
    reader.feed(part(SECOND))
    assert pictures == [SECOND]


def test_garbage_without_any_header_is_dropped_once_it_grows_too_big() -> None:
    reader, pictures = collect()
    reader.feed(b"x" * (MAX_PART_BYTES + 10))
    # Nothing that big is kept: the reader dropped it and waits for the next boundary.
    reader.feed(part(FIRST))
    assert pictures == [FIRST]
    assert reader.resyncs >= 1
