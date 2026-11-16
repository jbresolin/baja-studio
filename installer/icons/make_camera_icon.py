"""Makes camera-node.ico (the camera node's tray icon, installer and shortcuts): a plain camera.

    pip install pillow==13.0.0
    python installer/icons/make_camera_icon.py

Writes tray/baja_studio_tray/assets/camera-node.ico, which is committed: the
build only copies it. Unlike baja-studio.ico it has no artwork behind it: the
camera is drawn here with Pillow, so changing it means changing this file.

- 256, 48 and 32 px: a camera body in the Baja blue, a viewfinder bump on
  top, and a lens (white ring, dark glass, a small highlight).
- 24, 20 and 16 px (the tray at 150%, 125% and 100% scaling): the same
  camera without the highlight and with a thicker ring, so the lens still
  reads as a lens at 16 px.

Each frame is drawn four times larger and scaled down, which smooths the edges.
"""

from pathlib import Path

from PIL import Image, ImageDraw

HERE = Path(__file__).parent
TARGET = HERE.parent.parent / "tray" / "baja_studio_tray" / "assets" / "camera-node.ico"
DETAILED_SIZES = (256, 48, 32)
SIMPLE_SIZES = (24, 20, 16)
OVERSAMPLE = 4
BODY_COLOUR = (56, 109, 180, 255)  # the Baja artwork's blue, #386db4, as in baja-studio.ico
RING_COLOUR = (255, 255, 255, 255)
GLASS_COLOUR = (29, 42, 58, 255)
HIGHLIGHT_COLOUR = (150, 190, 235, 255)


def draw_camera(size: int, simple: bool) -> Image.Image:
    """One frame, `size` x `size`, on a transparent background."""
    big = size * OVERSAMPLE
    image = Image.new("RGBA", (big, big), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)

    def box(left: float, top: float, right: float, bottom: float) -> tuple[int, int, int, int]:
        """A box given as fractions of the frame."""
        return (round(left * big), round(top * big), round(right * big), round(bottom * big))

    # The viewfinder bump, then the body over its lower half.
    draw.rounded_rectangle(box(0.28, 0.16, 0.58, 0.34), radius=round(0.05 * big), fill=BODY_COLOUR)
    draw.rounded_rectangle(box(0.04, 0.26, 0.96, 0.86), radius=round(0.12 * big), fill=BODY_COLOUR)
    # The lens: a white ring around dark glass; the ring is thicker on small frames.
    centre_x, centre_y, outer = 0.50, 0.56, 0.24
    ring = 0.07 if simple else 0.045
    draw.ellipse(
        box(centre_x - outer, centre_y - outer, centre_x + outer, centre_y + outer),
        fill=RING_COLOUR,
    )
    inner = outer - ring
    draw.ellipse(
        box(centre_x - inner, centre_y - inner, centre_x + inner, centre_y + inner),
        fill=GLASS_COLOUR,
    )
    if not simple:
        # A small highlight on the glass, top left.
        spot = 0.045
        draw.ellipse(
            box(centre_x - 0.08 - spot, centre_y - 0.08 - spot, centre_x - 0.08 + spot,
                centre_y - 0.08 + spot),
            fill=HIGHLIGHT_COLOUR,
        )  # fmt: skip
    return image.resize((size, size), Image.Resampling.LANCZOS)


def main() -> None:
    """Draw every frame and write the multi-size ICO."""
    frames = {size: draw_camera(size, simple=False) for size in DETAILED_SIZES}
    frames.update({size: draw_camera(size, simple=True) for size in SIMPLE_SIZES})
    sizes = sorted(frames, reverse=True)
    frames[sizes[0]].save(
        TARGET,
        format="ICO",
        sizes=[(size, size) for size in sizes],
        append_images=[frames[size] for size in sizes[1:]],
    )
    print(f"wrote {TARGET} ({', '.join(str(size) for size in sizes)} px)")


if __name__ == "__main__":
    main()
