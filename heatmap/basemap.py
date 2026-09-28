"""Optional background map made from slippy-map tiles.

Tiles are downloaded once and cached under ``~/.cache/run-heatmap``. The
default providers are label-free so street names don't compete with your
routes. Remember to credit the map data (the CLI stamps an attribution line
on the image); OpenStreetMap's tile servers are meant for light use only.
"""

from __future__ import annotations

import hashlib
import io
import math
import os
import re
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Callable

import numpy as np
from PIL import Image, ImageEnhance

from .geo import BBox

TILE = 256

PROVIDERS = {
    "carto-dark": (
        "https://{s}.basemaps.cartocdn.com/dark_nolabels/{z}/{x}/{y}.png?key={key}",
        "© OpenStreetMap contributors © CARTO",
    ),
    "carto-light": (
        "https://{s}.basemaps.cartocdn.com/light_nolabels/{z}/{x}/{y}.png?key={key}",
        "© OpenStreetMap contributors © CARTO",
    ),
    "osm": (
        "https://tile.openstreetmap.org/{z}/{x}/{y}.png",
        "© OpenStreetMap contributors",
    ),
    # Works without a key on http://localhost; elsewhere needs a free key.
    "stadia-dark": (
        "https://tiles.stadiamaps.com/tiles/alidade_smooth_dark/{z}/{x}/{y}.png?api_key={key}",
        "© Stadia Maps © OpenMapTiles © OpenStreetMap contributors",
    ),
    "maptiler-dark": (
        "https://api.maptiler.com/maps/dataviz-dark/256/{z}/{x}/{y}.png?key={key}",
        "© MapTiler © OpenStreetMap contributors",
    ),
}

# Providers that refuse every request without an API key. CARTO's raster maps
# need a free key since 23 September 2026 (https://carto.com/basemaps/apikey/);
# without one every tile is stamped "API KEY REQUIRED".
KEY_REQUIRED = {"carto-dark", "carto-light", "maptiler-dark"}

USER_AGENT = "run-heatmap/0.1 (+https://github.com/woodyjeffay/heatmap)"

Fetcher = Callable[[str], bytes]


def resolve_provider(name: str, key: str | None = None) -> tuple[str, str]:
    """Return ``(url_template, attribution)`` for a provider name or URL.

    A ``{key}`` placeholder in the template is filled with ``key`` (an API key
    from the tile provider). Without a key, an optional ``?api_key={key}``
    style parameter is dropped; providers that insist on a key raise.
    """
    if name in PROVIDERS:
        template, attribution = PROVIDERS[name]
    elif "{z}" in name and "{x}" in name and "{y}" in name:
        template, attribution = name, "Map tiles: " + name.split("/")[2]
    else:
        raise ValueError(f"Unknown map {name!r}; use {', '.join(PROVIDERS)} or a URL template with {{z}}/{{x}}/{{y}}")

    if "{key}" in template:
        if key:
            template = template.replace("{key}", urllib.parse.quote(key, safe=""))
        elif name in KEY_REQUIRED or name not in PROVIDERS:
            raise ValueError(f"{name} needs an API key: pass --map-key YOUR_KEY "
                             "(or set RUN_HEATMAP_MAP_KEY)")
        else:
            template = re.sub(r"[?&][^?&=]+=\{key\}", "", template)
    return template, attribution


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
                  *, brightness: float = 1.0, cache_dir: str | os.PathLike | None = None,
                  fetch: Fetcher | None = None, zoom: int | None = None,
                  key: str | None = None) -> Image.Image:
    """Stitch tiles covering ``bbox`` into a ``width`` x ``height`` image."""
    template, _ = resolve_provider(provider, key)
    fetch = fetch or _http_get
    cache = Path(cache_dir or os.environ.get("RUN_HEATMAP_CACHE")
                 or Path.home() / ".cache" / "run-heatmap")
    z = zoom if zoom is not None else choose_zoom(bbox, width)
    n = 2**z
    tx0, ty0, tx1, ty1 = _tile_range(bbox, z)

    mosaic = Image.new("RGB", ((tx1 - tx0 + 1) * TILE, (ty1 - ty0 + 1) * TILE))
    # Name the cache folder after the provider, never after the API key.
    folder = provider if provider in PROVIDERS else "custom-" + hashlib.sha1(provider.encode()).hexdigest()[:10]
    for ty in range(ty0, ty1 + 1):
        for tx in range(tx0, tx1 + 1):
            wx = tx % n  # wrap around the antimeridian
            path = cache / folder / str(z) / str(wx) / f"{ty}.png"
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
    img = reveal(img)
    if brightness != 1.0:
        img = ImageEnhance.Brightness(img).enhance(brightness)
    return img


def reveal(img: Image.Image, target: float = 0.42) -> Image.Image:
    """Make the streets of a very dark map visible without greying its background.

    Dark tile styles draw streets only a few shades above the background, which
    all but disappears under a glowing heatmap. This stretches the levels so
    the brightest map features reach ``target`` luminance while the typical
    (background) colour stays where it is. Light maps are returned unchanged.
    """
    a = np.asarray(img.convert("RGB"), dtype=np.float32) / 255
    lum = a @ np.array([0.299, 0.587, 0.114], dtype=np.float32)
    mid = float(np.median(lum))
    top = float(np.percentile(lum, 99.5))
    if mid > 0.5 or top >= target or top - mid < 1e-3:
        return img
    gain = min((target - mid) / (top - mid), 8.0)
    a = np.clip(mid + (a - mid) * gain, 0, 1)
    return Image.fromarray((a * 255 + 0.5).astype(np.uint8), "RGB")


def is_light(img: Image.Image) -> bool:
    return float(np.asarray(img.convert("L").resize((64, 64))).mean()) > 128
