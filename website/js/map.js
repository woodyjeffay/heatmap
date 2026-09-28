// The interactive map: every run as a translucent line, drawn with additive
// blending so overlapping runs pile up light and the busiest streets glow.

import { BBox } from "./geo.js";
import { TILE_PROVIDERS, tileUrl } from "./heat.js";

const L = window.L;

// A canvas renderer whose strokes add light instead of painting over each other.
const GlowCanvas = L.Canvas.extend({
  _updatePoly(layer, closed) {
    this._ctx.globalCompositeOperation = "lighter";
    L.Canvas.prototype._updatePoly.call(this, layer, closed);
  },
});

/** Keep a point only once we've moved ~minStep degrees-ish from the last one. */
function simplify(seg, minStepDeg = 0.00007) {
  const out = [[seg[0], seg[1]]];
  let lastLat = seg[0], lastLon = seg[1];
  for (let i = 2; i < seg.length; i += 2) {
    if (Math.abs(seg[i] - lastLat) + Math.abs(seg[i + 1] - lastLon) >= minStepDeg || i === seg.length - 2) {
      out.push([seg[i], seg[i + 1]]);
      lastLat = seg[i]; lastLon = seg[i + 1];
    }
  }
  return out;
}

export class RunMap {
  constructor(el) {
    this.map = L.map(el, { preferCanvas: true, worldCopyJump: true, zoomControl: true }).setView([30, 0], 2);
    this.renderer = new GlowCanvas({ padding: 0.5 });
    this.lines = L.layerGroup().addTo(this.map);
    this.tiles = null;
    this.frame = null;
    this.style = { color: "#ff6a1f", opacity: 0.2, weight: 2 };
  }

  /** Returns an error message if the chosen map can't be used, else null. */
  setTiles(provider, key) {
    if (this.tiles) { this.tiles.remove(); this.tiles = null; }
    let url;
    try { url = tileUrl(provider, key); } catch (err) { return err.message; }
    if (!url) return null;
    this.tiles = L.tileLayer(url, {
      attribution: TILE_PROVIDERS[provider].attribution, subdomains: "abcd", maxZoom: 19,
    }).addTo(this.map);
    this.tiles.bringToBack();
    return null;
  }

  setActivities(acts) {
    this.lines.clearLayers();
    const opts = { ...this.style, renderer: this.renderer, interactive: false };
    for (const a of acts) {
      for (const seg of a.segments) this.lines.addLayer(L.polyline(simplify(seg), opts));
    }
  }

  setStyle(style) {
    Object.assign(this.style, style);
    this.lines.eachLayer((l) => l.setStyle(style));
  }

  fit(bbox) {
    const [[s, w], [n, e]] = bbox.toLatLonBounds();
    this.map.fitBounds([[s, w], [n, e]], { padding: [20, 20] });
  }

  viewBBox() {
    const b = this.map.getBounds();
    return BBox.fromLatLon(b.getSouth(), b.getWest(), b.getNorth(), b.getEast());
  }

  /** The largest box with the poster's aspect ratio inside the current view. */
  posterBBox(aspect) {
    const v = this.viewBBox().pad(-0.04);
    const [cx, cy] = v.center;
    let w = v.width, h = v.height;
    if (w / h > aspect) w = h * aspect; else h = w / aspect;
    return new BBox(cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2);
  }

  showFrame(bbox) {
    if (this.frame) this.frame.remove();
    this.frame = null;
    if (!bbox) return;
    this.frame = L.rectangle(bbox.toLatLonBounds(), {
      color: "#fff", weight: 1.5, dashArray: "6 6", fill: false, interactive: false, opacity: 0.8,
    }).addTo(this.map);
  }
}
