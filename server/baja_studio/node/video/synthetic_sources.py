"""Synthetic cameras: ffmpeg-generated pictures that stand in for a webcam.

Linux CI has no DirectShow, and a laptop without its cameras still needs a
benchmark, so a `PipelineSpec.device` of `lavfi:<name>` reads one of these
instead, in real time (`-re`), at the spec's size and rate:

- `testsrc2`: ffmpeg's moving test card (what the brief's CI tests ask for);
- `quadrants`: four flat grey levels, one per quarter of the frame, so a
  test can tell where the window is from the brightness of what comes out:
  top-left black, top-right dark grey, bottom-left light grey, bottom-right
  white.
"""

SYNTHETIC_PREFIX = "lavfi:"
TESTSRC2 = "testsrc2"
QUADRANTS = "quadrants"
NAMES = (TESTSRC2, QUADRANTS)

# The quadrants' grey levels (0..255), as a test reads them back.
QUADRANT_LEVELS = {"top_left": 0, "top_right": 85, "bottom_left": 170, "bottom_right": 255}


def is_synthetic(device: str) -> bool:
    """True for `lavfi:<name>` devices."""
    return device.startswith(SYNTHETIC_PREFIX)


def _grey(level: int) -> str:
    return f"0x{level:02x}{level:02x}{level:02x}"


def synthetic_graph(name: str, width: int, height: int, fps: float) -> str:
    """The lavfi source graph that draws the synthetic camera `name`."""
    size = f"size={width}x{height}:rate={fps:g}"
    if name == TESTSRC2:
        return f"testsrc2={size}"
    if name == QUADRANTS:
        half_w, half_h = width // 2, height // 2
        boxes = [
            (half_w, 0, QUADRANT_LEVELS["top_right"]),
            (0, half_h, QUADRANT_LEVELS["bottom_left"]),
            (half_w, half_h, QUADRANT_LEVELS["bottom_right"]),
        ]
        drawn = ",".join(
            f"drawbox=x={x}:y={y}:w={half_w}:h={half_h}:color={_grey(level)}:t=fill"
            for x, y, level in boxes
        )
        return f"color=c={_grey(QUADRANT_LEVELS['top_left'])}:{size},format=yuv420p,{drawn}"
    raise ValueError(f"unknown synthetic camera {name!r} (known: {', '.join(NAMES)})")


def synthetic_input_arguments(device: str, width: int, height: int, fps: float) -> list[str]:
    """ffmpeg's input options for a `lavfi:<name>` device, read at its own pace like a camera."""
    name = device.removeprefix(SYNTHETIC_PREFIX)
    return ["-re", "-f", "lavfi", "-i", synthetic_graph(name, width, height, fps)]
