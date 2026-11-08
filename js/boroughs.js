// Which London boroughs your runs pass through, and how far you ran in each.

import { distance } from "./geo.js";
import { LONDON_BOROUGHS } from "./london-boroughs.js";

const BANDS = 64;          // horizontal strips per borough, so a point test only looks at nearby edges
const MIN_STEP_M = 20;     // GPS points closer than this to the last tested one are skipped

/** A polygon prepared for fast point-in-polygon tests. */
class Area {
  constructor(name, ring) {
    this.name = name;
    this.ring = ring;
    let s = Infinity, n = -Infinity, w = Infinity, e = -Infinity;
    for (let i = 0; i < ring.length; i += 2) {
      s = Math.min(s, ring[i]); n = Math.max(n, ring[i]);
      w = Math.min(w, ring[i + 1]); e = Math.max(e, ring[i + 1]);
    }
    Object.assign(this, { s, n, w, e });
    this.bandH = (n - s) / BANDS || 1e-9;
    this.bands = Array.from({ length: BANDS }, () => []);
    const len = ring.length;
    for (let i = 0; i < len; i += 2) {
      const j = (i + 2) % len;
      const lo = Math.min(ring[i], ring[j]), hi = Math.max(ring[i], ring[j]);
      const b0 = this.band(lo), b1 = this.band(hi);
      for (let b = b0; b <= b1; b++) this.bands[b].push(i, j);
    }
  }

  band(lat) {
    return Math.max(0, Math.min(BANDS - 1, Math.floor((lat - this.s) / this.bandH)));
  }

  contains(lat, lon) {
    if (lat < this.s || lat > this.n || lon < this.w || lon > this.e) return false;
    const r = this.ring, edges = this.bands[this.band(lat)];
    let inside = false;
    for (let k = 0; k < edges.length; k += 2) {
      const i = edges[k], j = edges[k + 1];
      const yi = r[i], xi = r[i + 1], yj = r[j], xj = r[j + 1];
      if ((yi > lat) !== (yj > lat) && lon < ((xj - xi) * (lat - yi)) / (yj - yi) + xi) inside = !inside;
    }
    return inside;
  }

  /** [[lat, lon], ...] for Leaflet. */
  latLngs() {
    const out = [];
    for (let i = 0; i < this.ring.length; i += 2) out.push([this.ring[i], this.ring[i + 1]]);
    return out;
  }
}

export const AREAS = LONDON_BOROUGHS.map((b) => new Area(b.name, b.ring));
/** The City of London is not a borough, so it is shown but not counted in the 32. */
export const isBorough = (name) => name !== "City of London";
export const BOROUGH_COUNT = AREAS.filter((a) => isBorough(a.name)).length; // 32

const LONDON = AREAS.reduce((b, a) => ({
  s: Math.min(b.s, a.s), n: Math.max(b.n, a.n), w: Math.min(b.w, a.w), e: Math.max(b.e, a.e),
}), { s: Infinity, n: -Infinity, w: Infinity, e: -Infinity });

/** The area containing this point, or null. */
export function areaAt(lat, lon, hint = null) {
  if (lat < LONDON.s || lat > LONDON.n || lon < LONDON.w || lon > LONDON.e) return null;
  if (hint && hint.contains(lat, lon)) return hint;
  for (const a of AREAS) if (a !== hint && a.contains(lat, lon)) return a;
  return null;
}

/**
 * For each borough (and the City), how many activities went through it and
 * how many metres were covered inside it.
 * Returns Map name -> { runs, metres }.
 */
export function boroughStats(acts) {
  const stats = new Map(AREAS.map((a) => [a.name, { runs: 0, metres: 0 }]));
  for (const act of acts) {
    const seen = new Set();
    for (const seg of act.segments) {
      let hint = null, lastLat = NaN, lastLon = NaN;
      for (let i = 0; i < seg.length; i += 2) {
        const lat = seg[i], lon = seg[i + 1];
        const step = Number.isNaN(lastLat) ? 0 : distance(lastLat, lastLon, lat, lon);
        if (step < MIN_STEP_M && i > 0 && i < seg.length - 2) continue;
        const area = areaAt(lat, lon, hint);
        if (area) {
          const st = stats.get(area.name);
          st.metres += step;
          if (!seen.has(area)) { seen.add(area); st.runs++; }
          hint = area;
        }
        lastLat = lat; lastLon = lon;
      }
    }
  }
  return stats;
}
