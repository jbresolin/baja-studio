"""Makes baja-studio.ico (tray, installer, shortcuts) from the Baja mascot artwork.

    pip install resvg-py==0.5.0 pillow==13.0.0
    python installer/icons/make_icons.py

Writes tray/baja_studio_tray/assets/baja-studio.ico, which is committed: the
build only copies it. Run again when baja-icone.svg changes.

- 256, 48 and 32 px: the whole artwork (the car inside the outline of
  Brazil), centred on a square.
- 24, 20 and 16 px (the tray at 150%, 125% and 100% scaling): the outline's
  thin strokes, the flag and the seat vanish there, so these frames are a
  single-fill silhouette of what stays readable, the car's cage and wheels,
  thickened, in the artwork's blue.

The silhouette is picked from the SVG by path id (Inkscape's), listed in
SILHOUETTE_PATH_IDS; a re-exported SVG may need them updated.
"""

import io
import re
from pathlib import Path

import resvg_py  # type: ignore[import-not-found,unused-ignore]
from PIL import Image, ImageFilter

HERE = Path(__file__).parent
SOURCE = HERE / "baja-icone.svg"
TARGET = HERE.parent.parent / "tray" / "baja_studio_tray" / "assets" / "baja-studio.ico"
FULL_ARTWORK_SIZES = (256, 48, 32)
SILHOUETTE_SIZES = (24, 20, 16)
# path18: the green roll cage; path13-16: the two wheels (tyre and hub each).
SILHOUETTE_PATH_IDS = {"path13", "path14", "path15", "path16", "path18"}
SILHOUETTE_COLOUR = (56, 109, 180)  # the artwork's blue, #386db4
RENDER_SIZE = 1024
# How much the silhouette is thickened at RENDER_SIZE, so its strokes survive 16 px.
SILHOUETTE_GROWTH = 41
# Room left around the artwork, as a share of the frame.
MARGIN = 0.04


def render(svg: str, size: int) -> Image.Image:
    """Rasterise at `size` x `size` (resvg cannot take the file's mm units)."""
    svg = re.sub(r"<color-profile.*?/>", "", svg, flags=re.S)
    svg = re.sub(r'(<svg\b[^>]*?)\swidth="[^"]*"', r"\1", svg, count=1, flags=re.S)
    svg = re.sub(
        r'(<svg\b[^>]*?)\sheight="[^"]*"',
        rf'\1 width="{size}" height="{size}"',
        svg,
        count=1,
        flags=re.S,
    )
    png = bytes(resvg_py.svg_to_bytes(svg_string=svg))
    return Image.open(io.BytesIO(png)).convert("RGBA")


def keep_paths(svg: str, ids: set[str]) -> str:
    """The SVG with every other <path> removed."""

    def keep(match: re.Match[str]) -> str:
        found = re.search(r'\sid="([^"]*)"', match.group(0))
        return match.group(0) if found and found.group(1) in ids else ""

    return re.sub(r"<path\b.*?/>", keep, svg, flags=re.S)


def fit(image: Image.Image, size: int) -> Image.Image:
    """Crop to the drawing, scale it into a square frame with a small margin, centred."""
    box = image.getbbox()
    if box is None:
        raise ValueError("nothing was drawn: check SILHOUETTE_PATH_IDS against the SVG")
    drawing = image.crop(box)
    room = size * (1 - 2 * MARGIN)
    scale = room / max(drawing.size)
    width, height = max(1, round(drawing.width * scale)), max(1, round(drawing.height * scale))
    frame = Image.new("RGBA", (size, size))
    frame.alpha_composite(
        drawing.resize((width, height), Image.Resampling.LANCZOS),
        ((size - width) // 2, (size - height) // 2),
    )
    return frame


def single_fill(image: Image.Image, colour: tuple[int, int, int], growth: int = 1) -> Image.Image:
    """The drawing's shape, thickened by `growth` pixels (odd; 1 keeps it), in one colour."""
    alpha = image.getchannel("A").filter(ImageFilter.MaxFilter(growth))
    flat = Image.new("RGBA", image.size, (*colour, 255))
    flat.putalpha(alpha)
    return flat


def main() -> None:
    """Render every frame and write the ICO."""
    svg = SOURCE.read_text(encoding="utf-8")
    artwork = render(svg, RENDER_SIZE)
    silhouette = single_fill(
        render(keep_paths(svg, SILHOUETTE_PATH_IDS), RENDER_SIZE),
        SILHOUETTE_COLOUR,
        SILHOUETTE_GROWTH,
    )
    frames = {size: fit(artwork, size) for size in FULL_ARTWORK_SIZES}
    # Filled again after scaling: resampling leaves a few shades of the blue.
    frames.update(
        {size: single_fill(fit(silhouette, size), SILHOUETTE_COLOUR) for size in SILHOUETTE_SIZES}
    )
    sizes = sorted(frames, reverse=True)
    TARGET.parent.mkdir(parents=True, exist_ok=True)
    frames[sizes[0]].save(
        TARGET,
        format="ICO",
        sizes=[(size, size) for size in sizes],
        append_images=[frames[size] for size in sizes[1:]],
    )
    print(f"wrote {TARGET} ({', '.join(str(size) for size in sizes)} px)")


if __name__ == "__main__":
    main()
