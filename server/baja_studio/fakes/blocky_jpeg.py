"""A JPEG made of flat 8x8 blocks, for fakes that must serve a picture without an image library.

The fake agent's crop cameras and the camera node's fake pipeline answer
`GET /cameras/{id}/snapshot` (contract 1.1) with a real JPEG that a browser
shows. The service has no image library (previews pass OBS's JPEGs through
untouched), and a fake does not need photographs: a test card of coloured
squares is enough to see which part of the frame a crop window is on.

A JPEG block whose 64 pixels have one colour has only one non-zero DCT
coefficient, the first ("DC") one; every other ("AC") coefficient is zero.
So this encoder writes each block as its DC value followed by "end of
block", with a tiny Huffman table for the AC part that holds just that one
symbol. That is a few dozen lines instead of a full DCT encoder, and the
result is an ordinary baseline JPEG (checked by decoding it with ffmpeg and
in browsers when it was written).

Units: the picture is `blocks_wide * 8` by `blocks_high * 8` pixels.
"""

from collections.abc import Callable

Colour = tuple[int, int, int]
BLOCK_PIXELS = 8
# Quantiser of the DC coefficient: with 8, a block's quantised DC is simply its
# (level-shifted) value, -128..127, so differences fit the standard DC table.
DC_QUANTISER = 8

# The standard luminance DC Huffman table (JPEG spec, table K.3): how many codes
# of each length 1..16, then the symbols (difference categories 0..11).
DC_CODE_COUNTS = (0, 1, 5, 1, 1, 1, 1, 1, 1, 0, 0, 0, 0, 0, 0, 0)
DC_SYMBOLS = tuple(range(12))
# Our own AC table: one code of length 1 for the one symbol we ever write, 0x00
# ("end of block": every remaining coefficient is zero).
AC_CODE_COUNTS = (1, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0)
AC_SYMBOLS = (0x00,)
END_OF_BLOCK = 0x00


def _canonical_codes(
    counts: tuple[int, ...], symbols: tuple[int, ...]
) -> dict[int, tuple[int, int]]:
    """Symbol → (code, length in bits), built the way every JPEG decoder rebuilds them."""
    codes: dict[int, tuple[int, int]] = {}
    code = 0
    symbol_index = 0
    for length, count in enumerate(counts, start=1):
        for _ in range(count):
            codes[symbols[symbol_index]] = (code, length)
            symbol_index += 1
            code += 1
        code <<= 1
    return codes


DC_CODES = _canonical_codes(DC_CODE_COUNTS, DC_SYMBOLS)
AC_CODES = _canonical_codes(AC_CODE_COUNTS, AC_SYMBOLS)


class _BitWriter:
    """Packs bits into bytes, most significant first, with JPEG's 0xFF byte stuffing."""

    def __init__(self) -> None:
        self.output = bytearray()
        self._accumulator = 0
        self._bit_count = 0

    def write(self, value: int, length: int) -> None:
        for shift in range(length - 1, -1, -1):
            self._accumulator = (self._accumulator << 1) | ((value >> shift) & 1)
            self._bit_count += 1
            if self._bit_count == 8:
                self._flush_byte()

    def finish(self) -> bytes:
        """Pad the last byte with 1 bits (as the standard asks) and return everything."""
        while self._bit_count:
            self.write(1, 1)
        return bytes(self.output)

    def _flush_byte(self) -> None:
        self.output.append(self._accumulator)
        if self._accumulator == 0xFF:
            self.output.append(0x00)  # a data 0xFF is followed by 0x00, so it is not a marker
        self._accumulator = 0
        self._bit_count = 0


def _ycbcr(colour: Colour) -> tuple[int, int, int]:
    """JFIF's RGB → YCbCr, each 0..255."""
    red, green, blue = colour
    luma = 0.299 * red + 0.587 * green + 0.114 * blue
    blue_difference = 128 - 0.168736 * red - 0.331264 * green + 0.5 * blue
    red_difference = 128 + 0.5 * red - 0.418688 * green - 0.081312 * blue
    return tuple(min(255, max(0, round(part))) for part in (luma, blue_difference, red_difference))  # type: ignore[return-value]


def _write_dc_difference(bits: _BitWriter, difference: int) -> None:
    """A DC difference: its category's Huffman code, then the value in that many bits."""
    category = abs(difference).bit_length()
    code, length = DC_CODES[category]
    bits.write(code, length)
    if category:
        # Negative values are written as (value - 1) in `category` bits (JPEG's convention).
        raw = difference if difference > 0 else difference + (1 << category) - 1
        bits.write(raw, category)


def _segment(marker: int, payload: bytes) -> bytes:
    """A marker segment: 0xFF, the marker, a big-endian length (counting itself), the payload."""
    return bytes((0xFF, marker)) + (len(payload) + 2).to_bytes(2, "big") + payload


def encode_blocks(
    blocks_wide: int, blocks_high: int, colour_of: Callable[[int, int], Colour]
) -> bytes:
    """A baseline JPEG where block (column, row) is filled with `colour_of(column, row)`."""
    if not (0 < blocks_wide <= 8191 and 0 < blocks_high <= 8191):
        raise ValueError("a picture is 1 to 8191 blocks in each direction")
    width, height = blocks_wide * BLOCK_PIXELS, blocks_high * BLOCK_PIXELS
    quantisers = bytes([DC_QUANTISER] + [1] * 63)
    frame_header = (
        bytes((8,))
        + height.to_bytes(2, "big")
        + width.to_bytes(2, "big")
        + bytes(
            (3, 1, 0x11, 0, 2, 0x11, 0, 3, 0x11, 0)  # Y, Cb, Cr: no subsampling, quantiser table 0
        )
    )
    huffman_tables = (
        bytes((0x00,)) + bytes(DC_CODE_COUNTS) + bytes(DC_SYMBOLS)
        + bytes((0x10,)) + bytes(AC_CODE_COUNTS) + bytes(AC_SYMBOLS)
    )  # fmt: skip
    scan_header = bytes((3, 1, 0x00, 2, 0x00, 3, 0x00, 0, 63, 0))
    bits = _BitWriter()
    previous_dc = [0, 0, 0]
    end_code, end_length = AC_CODES[END_OF_BLOCK]
    for row in range(blocks_high):
        for column in range(blocks_wide):
            for component, value in enumerate(_ycbcr(colour_of(column, row))):
                # A flat block's DC coefficient is 8 * (value - 128); quantised by 8.
                dc = round(BLOCK_PIXELS * (value - 128) / DC_QUANTISER)
                _write_dc_difference(bits, dc - previous_dc[component])
                previous_dc[component] = dc
                bits.write(end_code, end_length)
    return b"".join(
        (
            b"\xff\xd8",  # start of image
            _segment(0xE0, b"JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00"),
            _segment(0xDB, bytes((0x00,)) + quantisers),
            _segment(0xC0, frame_header),
            _segment(0xC4, huffman_tables),
            _segment(0xDA, scan_header),
            bits.finish(),
            b"\xff\xd9",  # end of image
        )
    )


# The test card's quadrant colours: top-left, top-right, bottom-left, bottom-right.
QUADRANT_COLOURS: tuple[Colour, ...] = ((200, 40, 40), (40, 160, 60), (40, 80, 200), (210, 170, 30))


def card_jpeg(width: int, height: int, shade: int = 0) -> bytes:
    """A test card about `width` x `height` pixels (rounded to whole blocks).

    Four coloured quadrants with a checkerboard of 32-pixel squares on them, so
    a picture of the whole frame shows at a glance which part a window covers;
    `shade` (0..255) brightens the dark squares, so successive snapshots can
    differ (a fake pipeline counts its frames with it).
    """
    blocks_wide = max(1, round(width / BLOCK_PIXELS))
    blocks_high = max(1, round(height / BLOCK_PIXELS))

    def colour_of(column: int, row: int) -> Colour:
        quadrant = (2 if row * 2 >= blocks_high else 0) + (1 if column * 2 >= blocks_wide else 0)
        red, green, blue = QUADRANT_COLOURS[quadrant]
        if (column // 4 + row // 4) % 2:
            return red, green, blue
        dark = shade % 256 // 4
        return red // 3 + dark, green // 3 + dark, blue // 3 + dark

    return encode_blocks(blocks_wide, blocks_high, colour_of)


def jpeg_size(data: bytes) -> tuple[int, int] | None:
    """(width, height) from a JPEG's frame header, or None if `data` has none."""
    position = 2
    while position + 9 <= len(data) and data[position] == 0xFF:
        marker = data[position + 1]
        length = int.from_bytes(data[position + 2 : position + 4], "big")
        if marker in (0xC0, 0xC1, 0xC2):
            height = int.from_bytes(data[position + 5 : position + 7], "big")
            width = int.from_bytes(data[position + 7 : position + 9], "big")
            return width, height
        position += 2 + length
    return None
