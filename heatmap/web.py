"""Interactive, zoomable version of the heatmap as a single HTML file.

Routes are drawn with Leaflet on a canvas using additive ("lighter")
blending, so wherever routes overlap the light piles up and the most-run
streets glow, at every zoom level from the whole world down to one block.
"""

from __future__ import annotations

import json

import numpy as np

from .geo import segment_lengths

_TEMPLATE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>__TITLE__</title>
<link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/leaflet@1.9.4/dist/leaflet.css">
<style>
  html, body, #map { height: 100%; margin: 0; background: #050508; }
  .panel { position: absolute; z-index: 1000; left: 12px; bottom: 24px; padding: 10px 14px;
           background: rgba(10,10,14,.78); color: #eee; font: 13px/1.4 system-ui, sans-serif;
           border-radius: 8px; letter-spacing: .02em; }
  .panel b { display: block; font-size: 15px; letter-spacing: .12em; text-transform: uppercase; }
  .panel label { display: block; margin-top: 6px; }
  .panel input { vertical-align: middle; width: 110px; }
</style>
</head>
<body>
<div id="map"></div>
<div class="panel">
  <b>__TITLE__</b>
  <span>__SUBTITLE__</span>
  <label>Brightness <input id="op" type="range" min="0.02" max="0.6" step="0.01" value="__OPACITY__"></label>
</div>
<script src="https://cdn.jsdelivr.net/npm/leaflet@1.9.4/dist/leaflet.js"></script>
<script>
const TRACKS = __TRACKS__;
const map = L.map('map', { preferCanvas: true, worldCopyJump: true });
L.tileLayer('https://{s}.basemaps.cartocdn.com/dark_nolabels/{z}/{x}/{y}{r}.png', {
  attribution: '&copy; OpenStreetMap contributors &copy; CARTO', subdomains: 'abcd', maxZoom: 19
}).addTo(map);

// A canvas renderer whose strokes add light instead of painting over each other.
const Glow = L.Canvas.extend({
  _updatePoly(layer, closed) {
    this._ctx.globalCompositeOperation = 'lighter';
    L.Canvas.prototype._updatePoly.call(this, layer, closed);
  }
});
const renderer = new Glow({ padding: 0.5 });
const style = { color: '__COLOR__', weight: 2, opacity: __OPACITY__, renderer, interactive: false };
const lines = TRACKS.map(t => L.polyline(t, style).addTo(map));
map.fitBounds(__BOUNDS__);

document.getElementById('op').addEventListener('input', e => {
  const o = parseFloat(e.target.value);
  lines.forEach(l => l.setStyle({ opacity: o }));
});
</script>
</body>
</html>
"""


def simplify(seg: np.ndarray, min_step_m: float = 8.0) -> np.ndarray:
    """Keep a point only once we've moved ``min_step_m`` from the last kept one."""
    if len(seg) < 3:
        return seg
    step = segment_lengths(seg)
    keep = [0]
    travelled = 0.0
    for i, s in enumerate(step, start=1):
        travelled += s
        if travelled >= min_step_m:
            keep.append(i)
            travelled = 0.0
    if keep[-1] != len(seg) - 1:
        keep.append(len(seg) - 1)
    return seg[keep]


def build_html(activities, *, title: str, subtitle: str = "", bounds_latlon=None,
               color: str = "#ff6a1f", opacity: float = 0.18, min_step_m: float = 8.0) -> str:
    tracks = []
    for act in activities:
        for seg in act.segments:
            s = simplify(seg, min_step_m)
            if len(s) >= 2:
                tracks.append(np.round(s, 5).tolist())
    if bounds_latlon is None:
        allp = np.concatenate([np.asarray(t) for t in tracks]) if tracks else np.zeros((1, 2))
        bounds_latlon = [allp.min(axis=0).tolist(), allp.max(axis=0).tolist()]

    esc = lambda s: (s or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")  # noqa: E731
    return (_TEMPLATE
            .replace("__TRACKS__", json.dumps(tracks, separators=(",", ":")))
            .replace("__BOUNDS__", json.dumps(bounds_latlon))
            .replace("__TITLE__", esc(title))
            .replace("__SUBTITLE__", esc(subtitle))
            .replace("__COLOR__", color)
            .replace("__OPACITY__", f"{opacity:g}"))
