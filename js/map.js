// The interactive map: every run as a translucent line, drawn with additive
// blending so overlapping runs pile up light and the busiest streets glow.

import { BBox } from "./geo.js";
import { TILE_PROVIDERS, tileUrl } from "./heat.js";
import { AREAS, areaAt } from "./boroughs.js";

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

    // London boroughs sit just under the runs, so the glow stays bright.
    this.map.createPane("boroughs").style.zIndex = 390;
    this.boroughRenderer = L.svg({ pane: "boroughs", padding: 0.5 });
    this.boroughs = L.layerGroup();
    this.boroughShapes = new Map();
    this.boroughStats = null;
    this.hovered = null;
    this.label = L.control({ position: "topright" });
    this.label.onAdd = () => Object.assign(L.DomUtil.create("div", "borough-label"), { hidden: true });
    this.map.on("mousemove", (e) => this._hover(areaAt(e.latlng.lat, e.latlng.lng, this.hovered)));
    this.map.on("mouseout", () => this._hover(null));
  }

  /** Draw the borough outlines, tinting the ones in `stats` that you've been to. */
  setBoroughs(stats, visible, noun = "runs") {
    this.boroughStats = stats;
    this.boroughNoun = noun;
    if (!this.boroughShapes.size) {
      for (const a of AREAS) {
        this.boroughShapes.set(a.name, L.polygon(a.latLngs(), {
          renderer: this.boroughRenderer, interactive: false, weight: 1.2,
        }).addTo(this.boroughs));
      }
    }
    for (const [name, shape] of this.boroughShapes) {
      const been = stats && stats.get(name).runs > 0;
      shape.setStyle(been
        ? { color: "#3ddc97", opacity: 0.85, fillColor: "#3ddc97", fillOpacity: 0.16, dashArray: null }
        : { color: "#8a8a99", opacity: 0.7, fillColor: "#000", fillOpacity: 0.25, dashArray: "4 4" });
    }
    if (visible) { this.boroughs.addTo(this.map); this.label.addTo(this.map); }
    else { this.boroughs.remove(); this.label.remove(); this._hover(null); }
  }

  fitBorough(name) {
    const shape = this.boroughShapes.get(name);
    if (shape) this.map.fitBounds(shape.getBounds(), { padding: [30, 30] });
    const area = AREAS.find((a) => a.name === name);
    if (area) this._hover(area, true);
  }

  _hover(area, force = false) {
    if (!this.map.hasLayer(this.boroughs)) return;
    if (area === this.hovered && !force) return;
    if (this.hovered) this.boroughShapes.get(this.hovered.name).setStyle({ weight: 1.2 });
    this.hovered = area;
    const el = this.label.getContainer();
    if (!area) { if (el) el.hidden = true; return; }
    this.boroughShapes.get(area.name).setStyle({ weight: 3 });
    const st = this.boroughStats && this.boroughStats.get(area.name);
    if (el) {
      el.hidden = false;
      el.innerHTML = "";
      const b = document.createElement("strong");
      b.textContent = area.name;
      el.append(b, document.createElement("br"),
        st && st.runs ? `${st.runs.toLocaleString()} ${st.runs === 1 ? this.boroughNoun.replace(/s\b/g, "") : this.boroughNoun} · ${(st.metres / 1000).toFixed(1)} km` : "Not been here yet");
    }
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
