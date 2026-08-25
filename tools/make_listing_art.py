r"""Generate the logo and feature-card background the Marketplace listing needs.

GitHub requires both before a listing can be submitted, at specific sizes, and
overlays the app name across the middle of the background itself. So the art
has to stay quiet where that text lands and carry its texture at the edges.

The mark is a depleted timer ring around a keyhole: access, and time running
out on it. Drawn from primitives rather than shipped as a binary, so the
colours can be changed without a design tool.

    .venv\Scripts\python.exe -m tools.make_listing_art
"""

from __future__ import annotations

import math
from pathlib import Path

from PIL import Image, ImageDraw


# Dark slate that reads as neither corporate blue nor alarm red. The accent is
# the amber GitHub itself uses for "needs attention", which is the emotional
# register of the product without being a warning colour.
INK = (13, 17, 23)
SLATE = (22, 27, 34)
LINE = (48, 54, 61)
AMBER = (219, 109, 40)
PAPER = (230, 237, 243)

OUTPUT = Path("listing-art")


def _supersample(size: int, scale: int = 4) -> tuple[Image.Image, ImageDraw.ImageDraw, int]:
    """Draw big, shrink down -- Pillow has no antialiasing on shapes."""

    canvas = Image.new("RGB", (size * scale, size * scale), INK)
    return canvas, ImageDraw.Draw(canvas), scale


def make_logo(size: int = 512) -> Image.Image:
    """A timer ring most of the way around a keyhole."""

    canvas, draw, scale = _supersample(size)
    edge = size * scale
    centre = edge / 2

    draw.ellipse([0, 0, edge, edge], fill=SLATE)

    # The ring: a full faint track, with an amber arc showing time consumed.
    ring_inset = edge * 0.17
    ring_width = int(edge * 0.055)
    box = [ring_inset, ring_inset, edge - ring_inset, edge - ring_inset]
    draw.arc(box, 0, 360, fill=LINE, width=ring_width)
    # Starts at twelve o'clock and runs three quarters round: nearly expired,
    # not yet gone -- the moment the product is useful.
    draw.arc(box, -90, 180, fill=AMBER, width=ring_width)

    # The keyhole: a circle over a tapering stem.
    head_r = edge * 0.105
    head_y = centre - edge * 0.045
    draw.ellipse(
        [centre - head_r, head_y - head_r, centre + head_r, head_y + head_r],
        fill=PAPER,
    )
    stem_top = head_y + head_r * 0.55
    stem_bottom = centre + edge * 0.135
    draw.polygon(
        [
            (centre - head_r * 0.42, stem_top),
            (centre + head_r * 0.42, stem_top),
            (centre + head_r * 0.72, stem_bottom),
            (centre - head_r * 0.72, stem_bottom),
        ],
        fill=PAPER,
    )

    return canvas.resize((size, size), Image.LANCZOS)


def make_background(width: int = 965, height: int = 482) -> Image.Image:
    """The feature card. GitHub writes the app name across the centre."""

    scale = 2
    canvas = Image.new("RGB", (width * scale, height * scale), INK)
    draw = ImageDraw.Draw(canvas)
    w, h = width * scale, height * scale

    # Texture: diagonal hairlines, dense at the edges and thinning toward the
    # middle so the overlaid title stays legible.
    spacing = 26 * scale
    for offset in range(-h, w + h, spacing):
        for step in range(0, h, 3 * scale):
            x = offset + step
            # Distance from the horizontal centre, 0 at the middle, 1 at edges.
            away = abs((x / w) - 0.5) * 2
            if away < 0.28:
                continue
            shade = int(26 + 34 * min(1.0, (away - 0.28) / 0.72))
            draw.line(
                [(x, step), (x + 3 * scale, step + 3 * scale)],
                fill=(shade, shade + 4, shade + 9),
                width=1,
            )

    # Two arcs echoing the logo's ring, bled off the left and right edges.
    for cx, start, end, colour in (
        (-w * 0.08, -60, 60, LINE),
        (w * 1.08, 120, 240, LINE),
    ):
        r = h * 0.78
        draw.arc(
            [cx - r, h / 2 - r, cx + r, h / 2 + r],
            start,
            end,
            fill=colour,
            width=int(3 * scale),
        )

    # A single amber arc, off-centre, so the card has one warm point.
    r = h * 0.55
    cx = w * 0.12
    draw.arc(
        [cx - r, h / 2 - r, cx + r, h / 2 + r], -75, 25, fill=AMBER, width=int(4 * scale)
    )

    # Vignette toward the bottom, so the card sits rather than floats.
    #
    # Composited as a transparent overlay rather than painted as solid lines.
    # Drawing opaque rows over the texture leaves a hard seam exactly where the
    # gradient starts, which reads as a rendering bug rather than a shadow.
    shade = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    shade_draw = ImageDraw.Draw(shade)
    for y in range(h):
        t = max(0.0, (y / h) - 0.45) / 0.55
        shade_draw.line([(0, y), (w, y)], fill=(0, 0, 0, int(150 * t * t)))
    canvas = Image.alpha_composite(canvas.convert("RGBA"), shade).convert("RGB")

    return canvas.resize((width, height), Image.LANCZOS)


def main() -> None:
    OUTPUT.mkdir(exist_ok=True)

    logo = make_logo()
    logo_path = OUTPUT / "logo.png"
    logo.save(logo_path, "PNG", optimize=True)

    background = make_background()
    background_path = OUTPUT / "feature-card.png"
    background.save(background_path, "PNG", optimize=True)

    for path in (logo_path, background_path):
        size_kb = path.stat().st_size / 1024
        image = Image.open(path)
        print(f"{path}  {image.width}x{image.height}  {size_kb:.0f} KB")
        if size_kb > 1024:
            print("  WARNING: over GitHub's 1 MB limit")


if __name__ == "__main__":
    main()
