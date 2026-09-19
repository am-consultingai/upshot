"""The Upshot mark, as geometry.

The mark is two strokes stacked like an equals sign: a wave over a rule, both
running the full width and carrying the same weight. It is an assertion rather
than a picture — the sound and the line of text are the same quantity — so the
two strokes must be *identical* in length and weight. Drift in either breaks the
claim, which is why this file is the single definition and everything else is
generated from it by ``scripts/make_icons.py``.

Two things here exist to protect that equality:

* The wave begins and ends on a **peak**, where its tangent is horizontal. A
  stroke is offset along its normal, so ending anywhere else leaves a slanted
  terminal that overhangs the rule — the first cut of this mark was 1.4 units
  wider at each end than the rule beneath it, which is precisely the error the
  design is about.
* Both strokes are ``WEIGHT`` units thick, taken from the same constant.

Terminals are cut square rather than rounded, which matches the display face:
Frank Ruhl Libre is a high-contrast serif with flat terminals, and a
round-capped mark beside it reads as an icon borrowed from somewhere else.
"""

from __future__ import annotations

import math
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover - import cost only matters at runtime
    from PIL.Image import Image

#: Everything below is on a 32x32 grid, the same grid the design sheets used.
GRID = 32.0

#: The span of *both* strokes. Equal length is half of what makes this an equals
#: sign rather than a waveform with a line under it.
X0, X1 = 2.5, 29.5

#: The weight of both strokes.
WEIGHT = 3.0
HALF = WEIGHT / 2

#: A cosine, in whole half-cycles so both ends land on an extremum and the
#: terminals cut vertically.
#:
#: Two cycles: a peak at each end and one in the middle, a trough between each
#: pair. Symmetric, so it mirrors for RTL without redrawing, and three-up-two-down
#: is the fewest inflections that still reads as a wave rather than a squiggle.
WAVE_CYCLES = 2

#: The amplitude is capped by the stroke, not by taste. A stroke of weight ``w``
#: cannot follow a curve of radius less than ``w/2`` without its inner edge
#: folding back through itself, and the tightest bend here is at the peaks:
#: ``radius = 1 / (amp * (2*pi*cycles/span)**2)``. At amp 4 that radius was 0.99
#: against a half-width of 1.5, so the peaks pinched shut — and Pillow's even-odd
#: polygon fill turns a folded region into a *hole*, which is how the tray icon
#: got notches in it. The span above was widened to 27 to buy back the amplitude
#: this costs. :func:`peak_curvature_radius` states it; a test enforces it.
WAVE_AMP = 2.9
WAVE_Y = 13.1

RULE_Y = 21.8

#: Those numbers put the mark at 8.7-23.3 vertically: centred on 16, with a
#: 2.8-unit channel between the trough of the wave and the top of the rule.

#: The attention badge, in grid units. Big enough to read at 16px, small enough
#: to leave the middle of the mark alone.
BADGE_D = 13.0
BADGE_GAP = 1.4


def peak_curvature_radius() -> float:
    """Radius of the tightest bend in the wave, which is at its peaks.

    Must stay above :data:`HALF`, or the stroke's inner edge self-intersects.
    """
    return 1.0 / (WAVE_AMP * (2 * math.pi * WAVE_CYCLES / (X1 - X0)) ** 2)


def _spine(t: float) -> tuple[float, float]:
    """A point on the wave's centreline, ``t`` running 0 to 1 across the mark."""
    return (
        X0 + (X1 - X0) * t,
        WAVE_Y - WAVE_AMP * math.cos(2 * math.pi * WAVE_CYCLES * t),
    )


def _spine_derivative(t: float) -> tuple[float, float]:
    k = 2 * math.pi * WAVE_CYCLES
    return (X1 - X0, WAVE_AMP * k * math.sin(k * t))


def wave_outline(samples: int = 96) -> list[tuple[float, float]]:
    """The wave as a closed polygon, offset either side of the centreline.

    Offsetting along the *normal* rather than vertically is what keeps the weight
    even through the steep parts of the curve; a vertical offset would thin the
    stroke wherever the wave is climbing, which is the giveaway of a shape that
    was stretched rather than drawn. At both ends the tangent is horizontal, so
    the normal is vertical and the polygon lands exactly on ``X0`` and ``X1``.
    """
    upper: list[tuple[float, float]] = []
    lower: list[tuple[float, float]] = []
    for i in range(samples + 1):
        t = i / samples
        x, y = _spine(t)
        # The analytic derivative, not a difference between neighbours: a forward
        # difference at the first sample is not quite horizontal, which tilted the
        # terminal and put the polygon 0.2 units wider than the rule at each end.
        dx, dy = _spine_derivative(t)
        length = math.hypot(dx, dy) or 1.0
        nx, ny = -dy / length, dx / length
        upper.append((x + nx * HALF, y + ny * HALF))
        lower.append((x - nx * HALF, y - ny * HALF))

    return upper + lower[::-1]


def wave_path(precision: int = 3) -> str:
    """The wave's centreline as cubic béziers, one per quarter cycle.

    Each segment matches the cosine in position and tangent at both ends, which
    for a quarter wave leaves a middle error under half a percent — far below a
    pixel at any size this is drawn at, and a hundredth of the path data a
    sampled polyline needs.
    """
    # Whole half-cycles means 4 * cycles is always an integer number of quarters.
    segments = round(4 * WAVE_CYCLES)
    step = 1.0 / segments
    fmt = f".{precision}f"

    x0, y0 = _spine(0.0)
    out = [f"M{x0:{fmt}} {y0:{fmt}}"]
    for i in range(segments):
        ta, tb = i * step, (i + 1) * step
        ax, ay = _spine(ta)
        bx, by = _spine(tb)
        # Hermite tangents in the segment's own parameter, then Bézier controls.
        dax, day = (d * step for d in _spine_derivative(ta))
        dbx, dby = (d * step for d in _spine_derivative(tb))
        c1 = (ax + dax / 3, ay + day / 3)
        c2 = (bx - dbx / 3, by - dby / 3)
        out.append(
            f"C{c1[0]:{fmt}} {c1[1]:{fmt}} {c2[0]:{fmt}} {c2[1]:{fmt}} {bx:{fmt}} {by:{fmt}}"
        )
    return "".join(out)


def rule_box() -> tuple[float, float, float, float]:
    """The lower stroke, as ``(x0, y0, x1, y1)``."""
    return (X0, RULE_Y - HALF, X1, RULE_Y + HALF)


def svg_elements(indent: str = "  ") -> str:
    """Both strokes as SVG, inheriting whatever fill/stroke colour is in scope.

    The wave is stroked and the rule is filled, which is the honest way round: a
    stroked path keeps the centreline in the file, so the geometry stays legible
    and editable instead of being baked into an outline.
    """
    x0, y0, x1, y1 = rule_box()
    return (
        f'{indent}<path d="{wave_path()}" fill="none" stroke="currentColor"\n'
        f'{indent}      stroke-width="{WEIGHT}" stroke-linecap="butt" />\n'
        f'{indent}<rect x="{x0}" y="{y0}" width="{x1 - x0}" height="{y1 - y0}" />\n'
    )


def svg_document(light: str = "#0f6f68", dark: str = "#3dbdb0") -> str:
    """A standalone SVG favicon that follows the browser's colour scheme."""
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32" width="32" height="32">\n'
        "  <style>\n"
        f"    svg {{ color: {light}; fill: {light}; }}\n"
        "    @media (prefers-color-scheme: dark) {\n"
        f"      svg {{ color: {dark}; fill: {dark}; }}\n"
        "    }\n"
        "  </style>\n"
        f"{svg_elements()}"
        "</svg>\n"
    )


def render(
    size: int,
    rgb: tuple[int, int, int],
    *,
    badge: bool = False,
    badge_rgb: tuple[int, int, int] = (230, 160, 30),
    supersample: int = 4,
) -> Image:
    """The mark as an RGBA image, drawn large and scaled down.

    Pillow has no anti-aliasing of its own, so a 16px icon drawn directly comes
    out as a staircase. Drawing at 4x and resampling down is the whole trick, and
    it is why the wave survives in the tray at all.
    """
    from PIL import Image as PILImage
    from PIL import ImageDraw

    big = int(size * supersample)
    scale = big / GRID
    image = PILImage.new("RGBA", (big, big), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)

    fill = (*rgb, 255)
    draw.polygon([(x * scale, y * scale) for x, y in wave_outline()], fill=fill)
    x0, y0, x1, y1 = rule_box()
    draw.rectangle((x0 * scale, y0 * scale, x1 * scale, y1 * scale), fill=fill)

    if badge:
        # Top-right, the corner Windows leaves alone. The mark runs the full width,
        # so the badge lands on the wave whatever size it is; it gets a transparent
        # ring knocked out around it first, which is how a badge stays legible over
        # artwork instead of merging with it. ImageDraw replaces pixels rather than
        # blending, so filling with alpha 0 really does erase.
        d = BADGE_D * scale
        cx, cy = big - d / 2, d / 2
        gap = BADGE_GAP * scale
        draw.ellipse(
            (cx - d / 2 - gap, cy - d / 2 - gap, cx + d / 2 + gap, cy + d / 2 + gap),
            fill=(0, 0, 0, 0),
        )
        draw.ellipse((cx - d / 2, cy - d / 2, cx + d / 2, cy + d / 2), fill=(*badge_rgb, 255))

    if big != size:
        image = image.resize((size, size), PILImage.Resampling.LANCZOS)
    return image
