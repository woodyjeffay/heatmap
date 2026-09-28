// Geographic helpers: Web Mercator projection, distances, framing.

export const EARTH_RADIUS_M = 6371008.8;
const MAX_LAT = 85.05112878;

/** lat/lon (degrees) -> normalized Web Mercator [0,1], y growing southwards. */
export function toWorld(lat, lon) {
  const la = Math.max(-MAX_LAT, Math.min(MAX_LAT, lat));
  const s = Math.sin((la * Math.PI) / 180);
  return [(lon + 180) / 360, 0.5 - Math.log((1 + s) / (1 - s)) / (4 * Math.PI)];
}

export function fromWorld(x, y) {
  const lon = x * 360 - 180;
  const lat = (Math.atan(Math.sinh(Math.PI * (1 - 2 * y))) * 180) / Math.PI;
  return [lat, lon];
}

/** Great-circle distance in metres. */
export function distance(lat1, lon1, lat2, lon2) {
  const r = Math.PI / 180;
  const dlat = (lat2 - lat1) * r;
  const dlon = (lon2 - lon1) * r;
  const a = Math.sin(dlat / 2) ** 2 + Math.cos(lat1 * r) * Math.cos(lat2 * r) * Math.sin(dlon / 2) ** 2;
  return 2 * EARTH_RADIUS_M * Math.asin(Math.min(1, Math.sqrt(a)));
}

/** Length of a segment stored as a flat [lat, lon, lat, lon, ...] Float64Array. */
export function segmentLength(seg) {
  let d = 0;
  for (let i = 2; i < seg.length; i += 2) d += distance(seg[i - 2], seg[i - 1], seg[i], seg[i + 1]);
  return d;
}

export class BBox {
  constructor(x0, y0, x1, y1) {
    Object.assign(this, { x0, y0, x1, y1 });
  }
  get width() { return this.x1 - this.x0; }
  get height() { return this.y1 - this.y0; }
  get center() { return [(this.x0 + this.x1) / 2, (this.y0 + this.y1) / 2]; }

  static fromLatLon(south, west, north, east) {
    const [x0, y1] = toWorld(south, west);
    const [x1, y0] = toWorld(north, east);
    return new BBox(x0, y0, x1, y1);
  }

  toLatLonBounds() {
    return [fromWorld(this.x0, this.y1), fromWorld(this.x1, this.y0)];
  }

  pad(f) {
    const dx = this.width * f, dy = this.height * f;
    return new BBox(this.x0 - dx, this.y0 - dy, this.x1 + dx, this.y1 + dy);
  }

  /** Grow (keeping the centre) until width / height == aspect. */
  fitAspect(aspect) {
    const [cx, cy] = this.center;
    let w = Math.max(this.width, 1e-12), h = Math.max(this.height, 1e-12);
    if (w / h < aspect) w = h * aspect; else h = w / aspect;
    return new BBox(cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2);
  }

  /** Grow to cover at least `km` in each direction. */
  atLeastKm(km) {
    const [cx, cy] = this.center;
    const [lat] = fromWorld(cx, cy);
    const size = (km * 1000) / (2 * Math.PI * EARTH_RADIUS_M * Math.cos((lat * Math.PI) / 180));
    const w = Math.max(this.width, size), h = Math.max(this.height, size);
    return new BBox(cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2);
  }
}

export function bboxOfActivities(acts) {
  let x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity;
  for (const a of acts) {
    for (const seg of a.segments) {
      for (let i = 0; i < seg.length; i += 2) {
        const [x, y] = toWorld(seg[i], seg[i + 1]);
        if (x < x0) x0 = x; if (x > x1) x1 = x;
        if (y < y0) y0 = y; if (y > y1) y1 = y;
      }
    }
  }
  return new BBox(x0, y0, x1, y1);
}

/**
 * Activities in the geographic cluster with the most activities, so one
 * holiday run abroad doesn't shrink your home city to a dot.
 */
export function densestCluster(acts, cellDeg = 0.25) {
  const cellOf = (a) => {
    const [lat, lon] = a.center;
    return `${Math.floor(lat / cellDeg)},${Math.floor(lon / cellDeg)}`;
  };
  const counts = new Map();
  for (const a of acts) counts.set(cellOf(a), (counts.get(cellOf(a)) || 0) + 1);

  const seen = new Set();
  let best = new Set(), bestCount = -1;
  for (const start of counts.keys()) {
    if (seen.has(start)) continue;
    const comp = new Set();
    const stack = [start];
    seen.add(start);
    while (stack.length) {
      const c = stack.pop();
      comp.add(c);
      const [cy, cx] = c.split(",").map(Number);
      for (let dy = -1; dy <= 1; dy++) {
        for (let dx = -1; dx <= 1; dx++) {
          const n = `${cy + dy},${cx + dx}`;
          if (counts.has(n) && !seen.has(n)) { seen.add(n); stack.push(n); }
        }
      }
    }
    let total = 0;
    for (const c of comp) total += counts.get(c);
    if (total > bestCount) { best = comp; bestCount = total; }
  }
  return acts.filter((a) => best.has(cellOf(a)));
}
