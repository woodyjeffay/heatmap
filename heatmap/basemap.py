"""Optional background map made from slippy-map tiles.

Tiles are downloaded once and cached under ``~/.cache/run-heatmap``. The
default providers are label-free so street names don't compete with your
routes. Remember to credit the map data (the CLI stamps an attribution line
on the image); OpenStreetMap's tile servers are meant for light use only.
"""

from __future__ import annotations

import io
import math
import os
import urllib.request
from pathlib import Path
from typing import Callable

import numpy as np
from PIL import Image, ImageEnhance

from .geo import BBox

TILE = 256

PROVIDERS = {
    "carto-dark": (
        "https://{s}.basemaps.cartocdn.com/dark_nolabels/{z}/{x}/{y}.png",
        "© OpenStreetMap contributors © CARTO",
    ),
    "carto-light": (
        "https://{s}.basemaps.cartocdn.com/light_nolabels/{z}/{x}/{y}.png",
        "© OpenStreetMap contributors © CARTO",
    ),
    "osm": (
        "https://tile.openstreetmap.org/{z}/{x}/{y}.png",
        "© OpenStreetMap contributors",
    ),
}

USER_AGENT = "run-heatmap/0.1 (+https://github.com/woodyjeffay/heatmap)"

Fetcher = Callable[[str], bytes]


def resolve_provider(name: str) -> tuple[str, str]:
    """Return ``(url_template, attribution)`` for a provider name or URL."""
    if name in PROVIDERS:
        return PROVIDERS[name]
    if "{z}" in name and "{x}" in name and "{y}" in name:
        return name, "Map tiles: " + name.split("/")[2]
    raise ValueError(f"Unknown basemap {name!r}; use {', '.join(PROVIDERS)} or a URL template with {{z}}/{{x}}/{{y}}")


def _http_get(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.read()


def choose_zoom(bbox: BBox, width_px: int, max_tiles: int = 600) -> int:
    """Smallest zoom whose tiles are at least as detailed as the output."""
    needed = width_px / max(bbox.width, 1e-12)  # world width in px
    z = max(0, min(19, math.ceil(math.log2(max(needed / TILE, 1)))))
    while z > 0 and _tile_count(bbox, z) > max_tiles:
        z -= 1
    return z


def _tile_range(bbox: BBox, z: int):
    n = 2**z
    tx0 = int(math.floor(bbox.x0 * n))
    ty0 = max(0, int(math.floor(bbox.y0 * n)))
    tx1 = int(math.floor(bbox.x1 * n))
    ty1 = min(n - 1, int(math.floor(bbox.y1 * n)))
    return tx0, ty0, tx1, ty1


def _tile_count(bbox: BBox, z: int) -> int:
    tx0, ty0, tx1, ty1 = _tile_range(bbox, z)
    return (tx1 - tx0 + 1) * (ty1 - ty0 + 1)


def fetch_basemap(bbox: BBox, width: int, height: int, provider: str = "carto-dark",
                  *, dim: float = 1.0, cache_dir: str | os.PathLike | None = None,
                  fetch: Fetcher | None = None, zoom: int | None = None) -> Image.Image:
    """Stitch tiles covering ``bbox`` into a ``width`` x ``height`` image."""
    template, _ = resolve_provider(provider)
    fetch = fetch or _http_get
    cache = Path(cache_dir or os.environ.get("RUN_HEATMAP_CACHE")
                 or Path.home() / ".cache" / "run-heatmap")
    z = zoom if zoom is not None else choose_zoom(bbox, width)
    n = 2**z
    tx0, ty0, tx1, ty1 = _tile_range(bbox, z)

    mosaic = Image.new("RGB", ((tx1 - tx0 + 1) * TILE, (ty1 - ty0 + 1) * TILE))
    key = provider if provider in PROVIDERS else f"custom-{abs(hash(template)) % 10**8}"
    for ty in range(ty0, ty1 + 1):
        for tx in range(tx0, tx1 + 1):
            wx = tx % n  # wrap around the antimeridian
            path = cache / key / str(z) / str(wx) / f"{ty}.png"
            if path.exists():
                data = path.read_bytes()
            else:
                url = template.format(s="abcd"[(wx + ty) % 4], z=z, x=wx, y=ty)
                data = fetch(url)
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(data)
            tile = Image.open(io.BytesIO(data)).convert("RGB")
            mosaic.paste(tile, ((tx - tx0) * TILE, (ty - ty0) * TILE))

    # Crop the mosaic to the exact bbox, then scale to the output size.
    left = (bbox.x0 * n - tx0) * TILE
    top = (bbox.y0 * n - ty0) * TILE
    right = (bbox.x1 * n - tx0) * TILE
    bottom = (bbox.y1 * n - ty0) * TILE
    img = mosaic.transform((width, height), Image.Transform.EXTENT,
                           (left, top, right, bottom), Image.Resampling.BICUBIC)
    if dim != 1.0:
        img = ImageEnhance.Brightness(img).enhance(dim)
    return img


def is_light(img: Image.Image) -> bool:
    return float(np.asarray(img.convert("L").resize((64, 64))).mean()) > 128
