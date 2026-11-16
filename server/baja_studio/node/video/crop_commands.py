"""Turning a source window into the `crop` filter's options and live commands.

The pipeline's filter graph has one crop filter, named `crop@window`, whose
options are set once on ffmpeg's command line and then changed live through
the `zmq` filter in front of it (ADR 0023). Three choices make every frame
right even when a change arrives in pieces:

- the crop's **height is an expression of its width** (`h=ow*1080/1920`), so
  the picture can never be stretched: a zoom is a single command (`w`);
- its **left and top edges are expressions of the centre** (`x=1700-ow/2`),
  so a zoom keeps the centre without also sending `x` and `y`, and a window
  half-applied (new width, old centre) is still a correct 16:9 window;
- only the parts that changed are sent: a pan is one command, a pan and a
  tilt two.

The crop filter keeps the window inside the frame itself (it clamps `x` and
`y`), and we clamp here too so what we send is what is shown.
"""

from collections.abc import Callable
from dataclasses import dataclass

from baja_studio.node.video.spec import SourceWindow

# The crop filter's instance name in the graph; commands are addressed to it.
CROP_FILTER_TARGET = "crop@window"
# Never crop to less than this many pixels wide (a window of 0 would be refused).
MIN_CROP_WIDTH_PIXELS = 64


@dataclass(frozen=True)
class CropPixels:
    """A window in source pixels: its width (even) and its centre."""

    width: int
    centre_x: int
    centre_y: int


def _clamp(value: float, low: float, high: float) -> float:
    return min(high, max(low, value))


def widest_crop(input_width: int, input_height: int, aspect: float) -> int:
    """The widest output-shaped window the source holds, in pixels, rounded down to even."""
    widest = min(input_width, int(input_height * aspect))
    return widest - widest % 2


def crop_pixels(
    window: SourceWindow,
    input_width: int,
    input_height: int,
    aspect: float,
    min_width: int = MIN_CROP_WIDTH_PIXELS,
) -> CropPixels:
    """The pixels that show `window` on an `input_width` x `input_height` source.

    The width is rounded to an even number (MJPEG and H.264 pictures store
    colour at half resolution, so an odd width would be rounded by ffmpeg
    anyway) and kept between `min_width` (`MIN_CROP_WIDTH_PIXELS`, or what the
    Rockchip RGA can scale from, ADR 0024) and the widest window; the centre
    is kept where the whole window fits.
    """
    widest = widest_crop(input_width, input_height, aspect)
    width = round(_clamp(window.w * input_width, min(min_width, widest), widest))
    width -= width % 2
    height = width / aspect
    centre_x = (window.x + window.w / 2) * input_width
    centre_y = (window.y + window.h / 2) * input_height
    return CropPixels(
        width=width,
        centre_x=round(_clamp(centre_x, width / 2, input_width - width / 2)),
        centre_y=round(_clamp(centre_y, height / 2, input_height - height / 2)),
    )


def _width_option(pixels: CropPixels) -> str:
    return str(pixels.width)


def _left_option(pixels: CropPixels) -> str:
    # `ow` is the crop's output width: the left edge follows the width.
    return f"{pixels.centre_x}-ow/2"


def _top_option(pixels: CropPixels) -> str:
    return f"{pixels.centre_y}-oh/2"


def crop_filter(pixels: CropPixels, output_width: int, output_height: int) -> str:
    """The crop filter as it starts in the graph, e.g. `crop@window=w=1920:h=ow*1080/1920:...`."""
    return (
        f"{CROP_FILTER_TARGET}=w={_width_option(pixels)}"
        f":h=ow*{output_height}/{output_width}"
        f":x={_left_option(pixels)}:y={_top_option(pixels)}"
    )


# How a window change becomes zmq commands: `window_commands` for the laptop's crop
# filter, `rockchip.rga_window_commands` for the RGA's.
WindowCommands = Callable[[CropPixels | None, CropPixels], list[str]]


def window_commands(previous: CropPixels | None, new: CropPixels) -> list[str]:
    """The zmq commands that move the crop from `previous` to `new` (all three when unknown).

    Each is `crop@window <option> <value>`, the zmq filter's syntax. The
    width goes first; any order shows a correct window at every frame.
    """
    commands = []
    if previous is None or previous.width != new.width:
        commands.append(f"{CROP_FILTER_TARGET} w {_width_option(new)}")
    if previous is None or previous.centre_x != new.centre_x:
        commands.append(f"{CROP_FILTER_TARGET} x {_left_option(new)}")
    if previous is None or previous.centre_y != new.centre_y:
        commands.append(f"{CROP_FILTER_TARGET} y {_top_option(new)}")
    return commands
