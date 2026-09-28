"""Rasterize activities into a glowing heatmap image."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from PIL import Image, ImageDraw

from .geo import BBox, to_world

# Colour ramps: (position, "#rrggbb") stops from "never run" to "run constantly".
PALETTES: dict[str, list[tuple[float, str]]] = {
    "fire": [(0.0, "#1a0633"), (0.25, "#6a0f5c"), (0.45, "#c2262e"),
             (0.65, "#f26b1d"), (0.85, "#ffc53d"), (1.0, "#fffbe6")],
    "ice": [(0.0, "#04143a"), (0.3, "#0d47a1"), (0.6, "#1e88e5"),
            (0.85, "#6fe3ff"), (1.0, "#ffffff")],
    "neon": [(0.0, "#2a0845"), (0.35, "#b0179b"), (0.7, "#ff4fd8"),
             (0.9, "#7af3ff"), (1.0, "#ffffff")],
    "strava": [(0.0, "#3d1300"), (0.4, "#b83700"), (0.75, "#fc5200"),
               (0.92, "#ffb07a"), (1.0, "#ffffff")],
    "mono": [(0.0, "#303030"), (1.0, "#ffffff")],
    # For light backgrounds: lines darken towards ink.
    "ink": [(0.0, "#9aa5b1"), (0.5, "#34495e"), (1.0, "#0b1320")],
    "risograph": [(0.0, "#f4a3b4"), (0.5, "#e8416f"), (1.0, "#1b2a6b")],
}

DEFAULT_BACKGROUNDS = {"ink": "#f5f1e8", "risograph": "#f5f1e8"}


def hex_to_rgb(value: str) -> tuple[int, int, int]:
    value = value.lstrip("#")
    if len(value) == 3:
        value = "".join(c * 2 for c in value)
    if len(value) != 6:
        raise ValueError(f"Not a colour: #{value}")
    return tuple(int(value[i:i + 2], 16) for i in (0, 2, 4))  # type: ignore[return-value]


def palette_lut(name: str, n: int = 256) -> np.ndarray:
    """An ``(n, 3)`` float array in [0, 1] sampled from a named palette."""
    try:
        stops = PALETTES[name]
    except KeyError:
        raise ValueError(f"Unknown palette {name!r}; choose from {', '.join(PALETTES)}") from None
    pos = np.array([s[0] for s in stops])
    rgb = np.array([hex_to_rgb(s[1]) for s in stops], dtype=np.float64) / 255
    t = np.linspace(0, 1, n)
    return np.stack([np.interp(t, pos, rgb[:, i]) for i in range(3)], axis=1)


@dataclass(frozen=True)
class Viewport:
    """Maps world coordinates in ``bbox`` onto a ``width`` x ``height`` canvas."""

    bbox: BBox
    width: int
    height: int

    def project(self, latlon: np.ndarray) -> np.ndarray:
        x, y = to_world(latlon[:, 0], latlon[:, 1])
        px = (x - self.bbox.x0) / self.bbox.width * self.width
        py = (y - self.bbox.y0) / self.bbox.height * self.height
        return np.stack([px, py], axis=1)


def accumulate(activities, viewport: Viewport, line_width: float = 1.0,
               supersample: int | None = None) -> np.ndarray:
    """Count how many activities pass through each pixel.

    Every activity is drawn onto its own small mask (clipped to the canvas), so
    running the same street twice in one activity still only counts once but
    each separate run adds one. Lines are drawn at ``supersample``x resolution
    and averaged down, which gives smooth anti-aliased edges; by default only
    for screen-sized canvases, since print resolutions don't need it.
    """
    W, H = viewport.width, viewport.height
    acc = np.zeros((H, W), dtype=np.float32)
    if supersample is None:
        supersample = 2 if W * H <= 12_000_000 else 1
    ss = max(1, int(supersample))
    width_px = max(1, int(round(line_width * ss)))
    pad = line_width + 2

    for act in activities:
        pts = [viewport.project(seg) for seg in act.segments if len(seg) >= 2]
        if not pts:
            continue
        allp = np.concatenate(pts)
        x0 = max(int(np.floor(allp[:, 0].min() - pad)), 0)
        y0 = max(int(np.floor(allp[:, 1].min() - pad)), 0)
        x1 = min(int(np.ceil(allp[:, 0].max() + pad)), W)
        y1 = min(int(np.ceil(allp[:, 1].max() + pad)), H)
        if x1 <= x0 or y1 <= y0:
            continue

        mask = Image.new("L", ((x1 - x0) * ss, (y1 - y0) * ss), 0)
        draw = ImageDraw.Draw(mask)
        for p in pts:
            q = (p - (x0, y0)) * ss
            q = _dedupe(q)
            if len(q) < 2:
                continue
            draw.line([tuple(v) for v in q.tolist()], fill=255, width=width_px, joint="curve")
        m = np.asarray(mask)
        if ss > 1:
            m = m.reshape(y1 - y0, ss, x1 - x0, ss).sum(axis=(1, 3), dtype=np.uint16)
        acc[y0:y1, x0:x1] += m
    acc *= 1.0 / (255 * ss * ss)
    return acc


def _dedupe(q: np.ndarray) -> np.ndarray:
    """Drop consecutive points that fall on the same half-pixel.

    Dense GPS logs have far more points than pixels at city scale; thinning
    them keeps drawing fast without changing the picture.
    """
    if len(q) < 3:
        return q
    cell = np.round(q * 2).astype(np.int64)
    keep = np.ones(len(q), dtype=bool)
    keep[1:] = (cell[1:] != cell[:-1]).any(axis=1)
    keep[-1] = True
    return q[keep]


def box_blur(a: np.ndarray, r: int) -> np.ndarray:
    """Separable box blur with radius ``r`` (edges treated as zero)."""
    if r < 1:
        return a
    out = a
    for axis in (0, 1):
        n = out.shape[axis]
        pad = [(0, 0), (0, 0)]
        pad[axis] = (r + 1, r)
        c = np.cumsum(np.pad(out, pad), axis=axis, dtype=np.float32)
        hi = np.take(c, np.arange(2 * r + 1, 2 * r + 1 + n), axis=axis)
        lo = np.take(c, np.arange(0, n), axis=axis)
        out = ((hi - lo) / (2 * r + 1)).astype(np.float32)
    return out


def gaussian_blur(a: np.ndarray, sigma: float) -> np.ndarray:
    """Approximate a Gaussian with three box blurs (good to a few percent).

    Wide blurs are computed on a downscaled copy and scaled back up: the
    result is smooth anyway, and it is many times faster on poster-size images.
    """
    if sigma < 0.5:
        return a
    f = int(sigma // 3)
    if f >= 2:
        H, W = a.shape
        small = Image.fromarray(a.astype(np.float32, copy=False), "F").reduce(f)
        blurred = gaussian_blur(np.asarray(small), sigma / f)
        # reduce() rounds the size up, so map back exactly the part covering a.
        up = Image.fromarray(blurred, "F").resize((W, H), Image.Resampling.BILINEAR,
                                                  box=(0, 0, W / f, H / f))
        return np.asarray(up)
    r = max(1, int(round((np.sqrt(4 * sigma * sigma + 1) - 1) / 2)))
    for _ in range(3):
        a = box_blur(a, r)
    return a


def tone_map(acc: np.ndarray, scale: str = "log", clip_percentile: float = 99.5) -> np.ndarray:
    """Turn activity counts into intensities in [0, 1].

    ``log`` (default) keeps once-run streets visible while letting the daily
    loop saturate; ``linear`` is harsher; ``equalize`` spreads intensities by
    rank so every level of the ramp gets used.
    """
    out = np.zeros_like(acc, dtype=np.float32)
    nz = acc > 1e-3
    if not nz.any():
        return out
    vals = acc[nz]
    if scale == "equalize":
        order = np.sort(vals)
        out[nz] = (np.searchsorted(order, vals, side="right") / len(order)).astype(np.float32)
        return out
    top = max(float(np.percentile(vals, clip_percentile)), 1.0)
    if scale == "log":
        out[nz] = np.log1p(vals) / np.log1p(top)
    elif scale == "linear":
        out[nz] = vals / top
    else:
        raise ValueError(f"Unknown scale {scale!r}")
    return np.clip(out, 0, 1)


def colorize(intensity: np.ndarray, palette: str = "fire", background: str = "#000000",
             glow: float = 1.0, base: Image.Image | None = None,
             line_scale: float = 1.0) -> Image.Image:
    """Colour an intensity map and composite it over a background.

    ``glow`` adds a soft halo (a blurred copy of the lines) that makes busy
    streets bloom; set it to 0 for crisp lines. ``line_scale`` is the line
    width in pixels, used to size the halo.
    """
    lut = palette_lut(palette).astype(np.float32)
    H, W = intensity.shape
    light_bg = sum(hex_to_rgb(background)) > 3 * 160

    core = intensity
    if glow > 0:
        s = max(1.0, line_scale)
        halo = 0.6 * gaussian_blur(core, 2.0 * s) + 0.5 * gaussian_blur(core, 7.0 * s)
        halo = np.clip(halo * (1.6 if light_bg else 2.2), 0, 1)
        # Halo only brightens; the line itself keeps its colour.
        alpha = np.maximum(np.clip(core * 1.5 + 0.35 * (core > 0), 0, 1), glow * halo * 0.85)
        level = np.maximum(core, glow * halo * 0.6)
    else:
        alpha = np.clip(core * 1.5 + 0.35 * (core > 0), 0, 1)
        level = core

    idx = np.clip((level * (len(lut) - 1)).astype(np.int32), 0, len(lut) - 1)
    color = lut[idx]

    if base is None:
        bg = np.array(hex_to_rgb(background), dtype=np.float32) / 255
    else:
        bg = np.asarray(base.convert("RGB").resize((W, H)), dtype=np.float32) / 255

    a = alpha.astype(np.float32)[..., None]
    if not light_bg:
        # Screen-like blend: light adds up on dark backgrounds.
        np.maximum(color, bg, out=color)
    # rgb = bg * (1 - a) + color * a, computed in place to spare memory
    color -= bg
    color *= a
    color += bg
    np.clip(color, 0, 1, out=color)
    color *= 255
    color += 0.5
    return Image.fromarray(color.astype(np.uint8), "RGB")


def render(activities, viewport: Viewport, *, palette: str = "fire",
           background: str | None = None, line_width: float | None = None,
           glow: float = 1.0, scale: str = "log", base: Image.Image | None = None) -> Image.Image:
    """Full pipeline: accumulate, tone map, colourize."""
    if line_width is None:
        line_width = max(1.0, min(viewport.width, viewport.height) / 1600)
    if background is None:
        background = DEFAULT_BACKGROUNDS.get(palette, "#050508")
    acc = accumulate(activities, viewport, line_width=line_width)
    inten = tone_map(acc, scale=scale)
    return colorize(inten, palette=palette, background=background, glow=glow,
                    base=base, line_scale=line_width)
