"""Generate the installer's wizard artwork from app/brand.py and the app's own fonts.

    uv run python scripts/make_installer_art.py

Writes, all of them committed (the build must not depend on a Python toolchain, for the
same reason as scripts/make_icons.py):

    packaging/wizard-{100,150,200}.bmp        the tall panel on the finish page: Upshot
                                              first, "powered by AM Consulting" at its foot
    packaging/wizard-small-{100,150,200}.bmp  the mark in the top-right of every page

Upshot is the product and the panel says so; AM Consulting signs it at the bottom, the
way a publisher does. The AM wordmark is the white variant from the brand folder on Drive
(Logos & Images/AM_Logo_white_Daytona.png, trimmed and scaled to 600px), kept locally in
packaging/brand/ because an installer is offline: the CDN rule does not apply to it.

Inno Setup picks the file closest to the screen's scaling from each comma-separated list,
so every size is drawn at its own scale instead of stretched from one.
"""

from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import brand  # noqa: E402

PACKAGING = ROOT / "packaging"
FONTS = ROOT / "frontend" / "src" / "fonts"
AM_LOGO = PACKAGING / "brand" / "am-logo-white.png"

#: The accent (frontend/src/tokens.css, restated in scripts/make_icons.py).
ACCENT = (0x0F, 0x6F, 0x68)
WHITE = (255, 255, 255)
#: Quiet text on the accent: white at about 70%.
QUIET = (183, 212, 210)

#: Inno's sizes at 100% scaling; 150% and 200% are these times 1.5 and 2.
PANEL = (164, 314)
SMALL = 55
SCALES = {100: 1.0, 150: 1.5, 200: 2.0}


def font(name: str, size: int, weight: int | None = None) -> ImageFont.FreeTypeFont:
    face = ImageFont.truetype(str(FONTS / name), size)
    if weight is not None:
        face.set_variation_by_axes([weight])
    return face


def centred(draw: ImageDraw.ImageDraw, width: int, y: float, text: str, face, fill) -> None:
    left, _top, right, _bottom = draw.textbbox((0, 0), text, font=face)
    draw.text(((width - (right - left)) / 2 - left, y), text, font=face, fill=fill)


def panel(scale: float) -> Image.Image:
    width, height = round(PANEL[0] * scale), round(PANEL[1] * scale)
    s = lambda v: round(v * scale)  # noqa: E731
    image = Image.new("RGB", (width, height), ACCENT)
    draw = ImageDraw.Draw(image)

    # Upshot: the mark and the name, in the upper half where the eye lands.
    mark = brand.render(s(84), WHITE)
    image.paste(mark, ((width - mark.width) // 2, s(62)), mark)
    centred(draw, width, s(158), "Upshot", font("frank-ruhl-libre-latin.woff2", s(34), 700), WHITE)
    centred(
        draw, width, s(204), "Meetings, transcribed", font("plex-latin-400.woff2", s(11)), QUIET
    )
    centred(draw, width, s(219), "on this computer", font("plex-latin-400.woff2", s(11)), QUIET)

    # AM Consulting signs it at the foot, smaller than Upshot and never competing with it.
    centred(draw, width, s(256), "POWERED BY", font("plex-latin-500.woff2", s(8)), QUIET)
    logo = Image.open(AM_LOGO).convert("RGBA")
    logo_w = s(112)
    logo = logo.resize((logo_w, round(logo.height * logo_w / logo.width)), Image.Resampling.LANCZOS)
    image.paste(logo, ((width - logo.width) // 2, s(270)), logo)
    return image


def small(scale: float) -> Image.Image:
    size = round(SMALL * scale)
    # White, the colour of the page header it sits in; BMP has no transparency.
    image = Image.new("RGB", (size, size), WHITE)
    mark = brand.render(round(size * 0.8), ACCENT)
    offset = (size - mark.width) // 2
    image.paste(mark, (offset, offset), mark)
    return image


def main() -> None:
    for percent, scale in SCALES.items():
        for name, image in (
            (f"wizard-{percent}.bmp", panel(scale)),
            (f"wizard-small-{percent}.bmp", small(scale)),
        ):
            path = PACKAGING / name
            image.save(path, format="BMP")
            print(f"  {path.relative_to(ROOT)}  {image.width}x{image.height}")


if __name__ == "__main__":
    main()
