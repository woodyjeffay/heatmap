"""Poster layout: paper sizes, margins and a typographic caption."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from .render import hex_to_rgb

MM_PER_INCH = 25.4

# Portrait (width, height) in millimetres.
PAPER_SIZES_MM = {
    "a4": (210, 297),
    "a3": (297, 420),
    "a2": (420, 594),
    "a1": (594, 841),
    "a0": (841, 1189),
    "letter": (215.9, 279.4),
    "tabloid": (279.4, 431.8),
    "12x18": (304.8, 457.2),
    "18x24": (457.2, 609.6),
    "24x36": (609.6, 914.4),
    "square": (500, 500),
}

_FONT_CANDIDATES = {
    "regular": ["DejaVuSans.ttf", "Helvetica.ttc", "Arial.ttf", "LiberationSans-Regular.ttf"],
    "bold": ["DejaVuSans-Bold.ttf", "Helvetica.ttc", "Arial Bold.ttf", "LiberationSans-Bold.ttf"],
}
_FONT_DIRS = ["/usr/share/fonts", "/usr/local/share/fonts", "/Library/Fonts",
              "/System/Library/Fonts", "C:/Windows/Fonts", str(Path.home() / ".fonts")]


def paper_pixels(size: str, dpi: int = 300, landscape: bool = False) -> tuple[int, int]:
    """Pixel dimensions of a paper size, e.g. ``paper_pixels("a2", 300)``."""
    key = size.lower()
    if key in PAPER_SIZES_MM:
        w_mm, h_mm = PAPER_SIZES_MM[key]
    elif "x" in key and key.endswith(("mm", "cm", "in")):
        unit = key[-2:]
        w, h = (float(v) for v in key[:-2].split("x"))
        factor = {"mm": 1, "cm": 10, "in": MM_PER_INCH}[unit]
        w_mm, h_mm = w * factor, h * factor
    else:
        raise ValueError(f"Unknown paper size {size!r}; use one of {', '.join(PAPER_SIZES_MM)} "
                         "or a custom size like 50x70cm / 16x20in")
    if landscape:
        w_mm, h_mm = max(w_mm, h_mm), min(w_mm, h_mm)
    px = lambda mm: int(round(mm / MM_PER_INCH * dpi))  # noqa: E731
    return px(w_mm), px(h_mm)


def find_font(weight: str = "regular", override: str | None = None):
    if override:
        return override
    for d in _FONT_DIRS:
        root = Path(d)
        if not root.is_dir():
            continue
        for name in _FONT_CANDIDATES[weight]:
            hits = list(root.rglob(name))
            if hits:
                return str(hits[0])
    return None


def load_font(size: int, weight: str = "regular", override: str | None = None):
    path = find_font(weight, override)
    if path:
        return ImageFont.truetype(path, size)
    return ImageFont.load_default(size=size)


@dataclass
class PosterLayout:
    """Where the map sits on the page and how big the caption is."""

    page: tuple[int, int]
    map_box: tuple[int, int, int, int]  # left, top, right, bottom
    caption_top: int

    @property
    def map_size(self) -> tuple[int, int]:
        l, t, r, b = self.map_box
        return r - l, b - t


def layout(page: tuple[int, int], with_caption: bool = True, full_bleed: bool = False) -> PosterLayout:
    W, H = page
    short = min(W, H)
    margin = 0 if full_bleed else int(short * 0.07)
    caption_h = int(short * 0.16) if with_caption else 0
    top = margin
    bottom = H - (caption_h if with_caption else margin)
    return PosterLayout(page, (margin, top, W - margin, bottom), bottom)


def _spaced(text: str, tracking: float, font) -> tuple[list[tuple[str, float]], float]:
    """Positions for letter-spaced text; returns glyph offsets and total width."""
    x = 0.0
    out = []
    for ch in text:
        out.append((ch, x))
        x += font.getlength(ch) + tracking
    return out, max(0.0, x - tracking)


def draw_centered(draw: ImageDraw.ImageDraw, cx: float, y: float, text: str, font,
                  fill, tracking: float = 0.0) -> None:
    glyphs, width = _spaced(text, tracking, font)
    x0 = cx - width / 2
    for ch, dx in glyphs:
        draw.text((x0 + dx, y), ch, font=font, fill=fill)


def compose(map_img: Image.Image, lay: PosterLayout, *, background: str, title: str | None,
            subtitle: str | None, attribution: str | None, text_color: str | None = None,
            font: str | None = None) -> Image.Image:
    """Place the rendered map on the page and typeset the caption."""
    page = Image.new("RGB", lay.page, hex_to_rgb(background))
    page.paste(map_img, lay.map_box[:2])
    draw = ImageDraw.Draw(page)
    W, H = lay.page
    short = min(W, H)
    light_bg = sum(hex_to_rgb(background)) > 3 * 160
    fg = hex_to_rgb(text_color) if text_color else ((30, 30, 34) if light_bg else (236, 232, 225))
    muted = tuple(int(c * 0.62 + b * 0.38) for c, b in zip(fg, hex_to_rgb(background)))

    if title or subtitle:
        band = H - lay.caption_top
        y = lay.caption_top + band * 0.2
        if title:
            size = int(short * 0.055)
            f = load_font(size, "bold", font)
            # Shrink long titles to fit the page width.
            while size > 10 and _spaced(title.upper(), size * 0.35, f)[1] > W * 0.86:
                size = int(size * 0.92)
                f = load_font(size, "bold", font)
            draw_centered(draw, W / 2, y, title.upper(), f, fg, tracking=size * 0.35)
            y += size * 1.45
        if subtitle:
            size = int(short * 0.018)
            f = load_font(size, "regular", font)
            draw_centered(draw, W / 2, y, subtitle.upper(), f, muted, tracking=size * 0.3)

    if attribution:
        size = max(9, int(short * 0.008))
        f = load_font(size, "regular", font)
        tw = draw.textlength(attribution, font=f)
        draw.text((W - tw - short * 0.02, H - size * 2.2), attribution, font=f, fill=muted)
    return page
