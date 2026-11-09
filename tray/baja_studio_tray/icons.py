"""The tray icon: the Baja Studio artwork with a status dot in the bottom-right corner.

`assets/baja-studio.ico` is made from the mascot artwork by
`installer/icons/make_icons.py`: the full artwork at 256, 48 and 32 px, and
a simplified single-fill silhouette (the car's cage and wheels) at 20 and
16 px, where the thin outline and the small coloured parts vanish. The same
file is the installer's and the shortcuts' icon.

Each poll that changes the badge draws the dot on every frame and writes a
new multi-size ICO, so Windows picks the frame made for the tray's size.
"""

from io import BytesIO
from pathlib import Path

from PIL import IcoImagePlugin, Image, ImageDraw

from baja_studio_tray.status import Badge

ICON_PATH = Path(__file__).parent / "assets" / "baja-studio.ico"
FULL_ARTWORK_SIZES = (256, 48, 32)
SILHOUETTE_SIZES = (24, 20, 16)
ALL_SIZES = FULL_ARTWORK_SIZES + SILHOUETTE_SIZES
# The dot covers this share of the icon's width: big enough to read at 16 px.
DOT_SHARE = 0.5
BADGE_COLOURS: dict[Badge, tuple[int, int, int]] = {
    Badge.GREEN: (46, 158, 68),
    Badge.AMBER: (240, 160, 32),
    Badge.RED: (217, 54, 54),
}
RING_COLOUR = (255, 255, 255)


def load_frames(path: Path = ICON_PATH) -> dict[int, Image.Image]:
    """Every square frame of the ICO, by size in pixels, as RGBA."""
    with path.open("rb") as file:
        ico = IcoImagePlugin.IcoFile(BytesIO(file.read()))
    frames: dict[int, Image.Image] = {}
    for width, height in ico.sizes():
        if width == height:
            frames[width] = ico.getimage((width, height)).convert("RGBA")
    return frames


def with_badge(frame: Image.Image, badge: Badge) -> Image.Image:
    """A copy of `frame` with the status dot (and a white ring) bottom-right."""
    size = frame.width
    diameter = max(6, round(size * DOT_SHARE))
    ring = max(1, round(diameter / 8))
    image = frame.copy()
    draw = ImageDraw.Draw(image)
    box = (size - diameter, size - diameter, size - 1, size - 1)
    draw.ellipse(box, fill=RING_COLOUR)
    inner = (box[0] + ring, box[1] + ring, box[2] - ring, box[3] - ring)
    draw.ellipse(inner, fill=BADGE_COLOURS[badge])
    return image


def badged_frames(frames: dict[int, Image.Image], badge: Badge) -> dict[int, Image.Image]:
    """Every frame with the dot."""
    return {size: with_badge(frame, badge) for size, frame in frames.items()}


def save_ico(frames: dict[int, Image.Image], target: Path) -> None:
    """Write the frames as one ICO, each size from its own frame (never scaled down)."""
    sizes = sorted(frames, reverse=True)
    largest = frames[sizes[0]]
    largest.save(
        target,
        format="ICO",
        sizes=[(size, size) for size in sizes],
        append_images=[frames[size] for size in sizes[1:]],
    )
