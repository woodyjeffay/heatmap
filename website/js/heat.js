// Poster rendering on a <canvas>: count how many runs cross each pixel, turn
// counts into a glowing colour ramp, lay it over a street map, add a caption.

import { toWorld } from "./geo.js";

export const PALETTES = {
  fire: { label: "Fire", stops: [[0, "#1a0633"], [0.25, "#6a0f5c"], [0.45, "#c2262e"], [0.65, "#f26b1d"], [0.85, "#ffc53d"], [1, "#fffbe6"]] },
  ice: { label: "Ice", stops: [[0, "#04143a"], [0.3, "#0d47a1"], [0.6, "#1e88e5"], [0.85, "#6fe3ff"], [1, "#ffffff"]] },
  neon: { label: "Neon", stops: [[0, "#2a0845"], [0.35, "#b0179b"], [0.7, "#ff4fd8"], [0.9, "#7af3ff"], [1, "#ffffff"]] },
  strava: { label: "Orange", stops: [[0, "#3d1300"], [0.4, "#b83700"], [0.75, "#fc5200"], [0.92, "#ffb07a"], [1, "#ffffff"]] },
  mono: { label: "White", stops: [[0, "#303030"], [1, "#ffffff"]] },
  ink: { label: "Ink (light paper)", light: true, stops: [[0, "#9aa5b1"], [0.5, "#34495e"], [1, "#0b1320"]] },
  risograph: { label: "Risograph (light paper)", light: true, stops: [[0, "#f4a3b4"], [0.5, "#e8416f"], [1, "#1b2a6b"]] },
};

export const PAPER_MM = {
  a4: [210, 297], a3: [297, 420], a2: [420, 594], a1: [594, 841], a0: [841, 1189],
  letter: [215.9, 279.4], tabloid: [279.4, 431.8], "18x24": [457.2, 609.6], "24x36": [609.6, 914.4],
  square: [500, 500],
};

export const hexToRgb = (h) => {
  h = h.replace("#", "");
  if (h.length === 3) h = [...h].map((c) => c + c).join("");
  return [0, 2, 4].map((i) => parseInt(h.slice(i, i + 2), 16));
};

function lut(name) {
  const stops = PALETTES[name].stops.map(([p, c]) => [p, hexToRgb(c)]);
  const out = new Uint8ClampedArray(256 * 3);
  for (let i = 0; i < 256; i++) {
    const t = i / 255;
    let k = 0;
    while (k < stops.length - 2 && t > stops[k + 1][0]) k++;
    const [p0, c0] = stops[k], [p1, c1] = stops[k + 1];
    const f = p1 > p0 ? Math.min(1, Math.max(0, (t - p0) / (p1 - p0))) : 0;
    for (let j = 0; j < 3; j++) out[i * 3 + j] = c0[j] + (c1[j] - c0[j]) * f;
  }
  return out;
}

export function paperPixels(size, dpi, landscape) {
  let [w, h] = PAPER_MM[size];
  if (landscape) [w, h] = [Math.max(w, h), Math.min(w, h)];
  return [Math.round((w / 25.4) * dpi), Math.round((h / 25.4) * dpi)];
}

export function posterLayout([W, H], { caption = true, fullBleed = false } = {}) {
  const short = Math.min(W, H);
  const margin = fullBleed ? 0 : Math.round(short * 0.07);
  const captionH = caption ? Math.round(short * 0.16) : 0;
  const bottom = H - (caption ? captionH : margin);
  return { page: [W, H], map: [margin, margin, W - margin, bottom], captionTop: bottom };
}

const tick = () => new Promise((r) => setTimeout(r, 0));

// ---------------------------------------------------------------- accumulate

/**
 * Float32 count of runs through each pixel. Each run is drawn once on a
 * scratch canvas (so looping a street in one run counts once) and its
 * anti-aliased coverage is added in.
 */
export async function accumulate(acts, bbox, W, H, lineWidth, progress) {
  const acc = new Float32Array(W * H);
  const scratch = document.createElement("canvas");
  let ctx = scratch.getContext("2d", { willReadFrequently: true });
  const pad = lineWidth + 2;
  let n = 0;
  for (const act of acts) {
    if (++n % 25 === 0) { progress(n / acts.length); await tick(); }
    const paths = act.segments.map((seg) => {
      const pts = new Float64Array(seg.length);
      for (let i = 0; i < seg.length; i += 2) {
        const [x, y] = toWorld(seg[i], seg[i + 1]);
        pts[i] = ((x - bbox.x0) / bbox.width) * W;
        pts[i + 1] = ((y - bbox.y0) / bbox.height) * H;
      }
      return pts;
    });
    let x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity;
    for (const p of paths) {
      for (let i = 0; i < p.length; i += 2) {
        if (p[i] < x0) x0 = p[i]; if (p[i] > x1) x1 = p[i];
        if (p[i + 1] < y0) y0 = p[i + 1]; if (p[i + 1] > y1) y1 = p[i + 1];
      }
    }
    x0 = Math.max(0, Math.floor(x0 - pad)); y0 = Math.max(0, Math.floor(y0 - pad));
    x1 = Math.min(W, Math.ceil(x1 + pad)); y1 = Math.min(H, Math.ceil(y1 + pad));
    const w = x1 - x0, h = y1 - y0;
    if (w <= 0 || h <= 0) continue;
    if (scratch.width < w || scratch.height < h) {
      scratch.width = Math.max(scratch.width, w);
      scratch.height = Math.max(scratch.height, h);
    }
    ctx.clearRect(0, 0, w, h);
    ctx.strokeStyle = "#fff";
    ctx.lineWidth = lineWidth;
    ctx.lineJoin = ctx.lineCap = "round";
    ctx.beginPath();
    for (const p of paths) {
      let lx = NaN, ly = NaN;
      for (let i = 0; i < p.length; i += 2) {
        const x = p[i] - x0, y = p[i + 1] - y0;
        if (i === 0) ctx.moveTo(x, y);
        else if (Math.abs(x - lx) + Math.abs(y - ly) > 0.4 || i === p.length - 2) ctx.lineTo(x, y);
        else continue;
        lx = x; ly = y;
      }
    }
    ctx.stroke();
    const px = ctx.getImageData(0, 0, w, h).data;
    for (let yy = 0; yy < h; yy++) {
      const row = (y0 + yy) * W + x0;
      const src = yy * w * 4 + 3;
      for (let xx = 0; xx < w; xx++) {
        const a = px[src + xx * 4];
        if (a) acc[row + xx] += a / 255;
      }
    }
  }
  return acc;
}

// ---------------------------------------------------------------- tone & blur

function toneMap(acc) {
  // log scale, clipped at the 99.5th percentile of non-empty pixels
  const sample = [];
  const step = Math.max(1, Math.floor(acc.length / 2e6));
  for (let i = 0; i < acc.length; i += step) if (acc[i] > 1e-3) sample.push(acc[i]);
  const out = new Float32Array(acc.length);
  if (!sample.length) return out;
  sample.sort((a, b) => a - b);
  const top = Math.max(1, sample[Math.floor(sample.length * 0.995)]);
  const k = 1 / Math.log1p(top);
  for (let i = 0; i < acc.length; i++) {
    const v = acc[i];
    if (v > 1e-3) out[i] = Math.min(1, Math.log1p(v) * k);
  }
  return out;
}

function boxBlur(src, W, H, r) {
  const tmp = new Float32Array(src.length), out = new Float32Array(src.length);
  const norm = 1 / (2 * r + 1);
  for (let y = 0; y < H; y++) {
    const row = y * W;
    let s = 0;
    for (let x = -r; x <= r; x++) if (x >= 0 && x < W) s += src[row + x];
    for (let x = 0; x < W; x++) {
      tmp[row + x] = s * norm;
      const add = x + r + 1, rem = x - r;
      if (add < W) s += src[row + add];
      if (rem >= 0) s -= src[row + rem];
    }
  }
  for (let x = 0; x < W; x++) {
    let s = 0;
    for (let y = -r; y <= r; y++) if (y >= 0 && y < H) s += tmp[y * W + x];
    for (let y = 0; y < H; y++) {
      out[y * W + x] = s * norm;
      const add = y + r + 1, rem = y - r;
      if (add < H) s += tmp[add * W + x];
      if (rem >= 0) s -= tmp[rem * W + x];
    }
  }
  return out;
}

/** Gaussian-ish blur; wide blurs run on a downscaled copy (halos are smooth). */
function blur(src, W, H, sigma) {
  const f = Math.max(1, Math.floor(sigma / 3));
  let w = W, h = H, a = src;
  if (f > 1) {
    w = Math.ceil(W / f); h = Math.ceil(H / f);
    a = new Float32Array(w * h);
    for (let y = 0; y < H; y++) {
      const sy = ((y / f) | 0) * w;
      for (let x = 0; x < W; x++) a[sy + ((x / f) | 0)] += src[y * W + x];
    }
    const inv = 1 / (f * f);
    for (let i = 0; i < a.length; i++) a[i] *= inv;
  }
  const s = sigma / f;
  const r = Math.max(1, Math.round((Math.sqrt(4 * s * s + 1) - 1) / 2));
  for (let k = 0; k < 3; k++) a = boxBlur(a, w, h, r);
  if (f === 1) return a;
  const out = new Float32Array(W * H); // bilinear upsample
  for (let y = 0; y < H; y++) {
    const fy = Math.min(h - 1, Math.max(0, (y + 0.5) / f - 0.5));
    const y0 = Math.floor(fy), y1 = Math.min(h - 1, y0 + 1), ty = fy - y0;
    for (let x = 0; x < W; x++) {
      const fx = Math.min(w - 1, Math.max(0, (x + 0.5) / f - 0.5));
      const x0 = Math.floor(fx), x1 = Math.min(w - 1, x0 + 1), tx = fx - x0;
      const top = a[y0 * w + x0] * (1 - tx) + a[y0 * w + x1] * tx;
      const bot = a[y1 * w + x0] * (1 - tx) + a[y1 * w + x1] * tx;
      out[y * W + x] = top * (1 - ty) + bot * ty;
    }
  }
  return out;
}

// ---------------------------------------------------------------- basemap

const TILE = 256;

export const TILE_PROVIDERS = {
  "carto-dark": { label: "CARTO dark", url: "https://{s}.basemaps.cartocdn.com/dark_nolabels/{z}/{x}/{y}.png", attribution: "© OpenStreetMap contributors © CARTO" },
  "carto-light": { label: "CARTO light", url: "https://{s}.basemaps.cartocdn.com/light_nolabels/{z}/{x}/{y}.png", attribution: "© OpenStreetMap contributors © CARTO" },
  "stadia-dark": { label: "Stadia dark", url: "https://tiles.stadiamaps.com/tiles/alidade_smooth_dark/{z}/{x}/{y}.png?api_key={key}", attribution: "© Stadia Maps © OpenMapTiles © OpenStreetMap contributors", optionalKey: true },
  "maptiler-dark": { label: "MapTiler dark (key)", url: "https://api.maptiler.com/maps/dataviz-dark/256/{z}/{x}/{y}.png?key={key}", attribution: "© MapTiler © OpenStreetMap contributors", needsKey: true },
  none: { label: "None", url: null },
};

export function tileUrl(provider, key) {
  const p = TILE_PROVIDERS[provider];
  if (!p || !p.url) return null;
  if (!p.url.includes("{key}")) return p.url;
  if (key) return p.url.replace("{key}", encodeURIComponent(key));
  if (p.needsKey) throw new Error("This map needs an API key");
  return p.url.replace(/[?&][^?&=]+=\{key\}/, "");
}

function loadImage(url) {
  return new Promise((resolve, reject) => {
    const img = new Image();
    img.crossOrigin = "anonymous";
    img.onload = () => resolve(img);
    img.onerror = () => reject(new Error("tile failed to load"));
    img.src = url;
  });
}

/** Stitch map tiles covering bbox into a W x H canvas. */
export async function fetchBasemap(bbox, W, H, template, progress) {
  const needed = W / bbox.width;
  let z = Math.max(0, Math.min(19, Math.ceil(Math.log2(Math.max(needed / TILE, 1)))));
  const range = (zz) => {
    const n = 2 ** zz;
    return [Math.floor(bbox.x0 * n), Math.max(0, Math.floor(bbox.y0 * n)),
      Math.floor(bbox.x1 * n), Math.min(n - 1, Math.floor(bbox.y1 * n)), n];
  };
  while (z > 0) {
    const [a, b, c, d] = range(z);
    if ((c - a + 1) * (d - b + 1) <= 400) break;
    z--;
  }
  const [tx0, ty0, tx1, ty1, n] = range(z);
  const canvas = document.createElement("canvas");
  canvas.width = W; canvas.height = H;
  const ctx = canvas.getContext("2d");
  ctx.imageSmoothingQuality = "high";
  const scale = W / (bbox.width * n * TILE);
  const jobs = [];
  for (let ty = ty0; ty <= ty1; ty++) {
    for (let tx = tx0; tx <= tx1; tx++) jobs.push([tx, ty]);
  }
  let done = 0, failed = 0;
  const worker = async () => {
    while (jobs.length) {
      const [tx, ty] = jobs.shift();
      const wx = ((tx % n) + n) % n;
      const url = template.replace("{s}", "abcd"[(wx + ty) % 4]).replace("{z}", z).replace("{x}", wx).replace("{y}", ty);
      try {
        const img = await loadImage(url);
        const left = ((tx / n - bbox.x0) * n * TILE) * scale;
        const top = ((ty / n - bbox.y0) * n * TILE) * scale;
        ctx.drawImage(img, left, top, TILE * scale + 0.5, TILE * scale + 0.5);
      } catch { failed++; }
      progress(++done / (done + jobs.length));
    }
  };
  await Promise.all(Array.from({ length: 6 }, worker));
  if (failed > done / 2) throw new Error("the street map could not be downloaded");
  return ctx.getImageData(0, 0, W, H); // throws if tiles lacked CORS headers
}

/** Stretch a dark map's levels so its streets show; light maps unchanged. */
export function reveal(img, target = 0.42) {
  const d = img.data;
  const lum = new Float32Array(Math.ceil(d.length / 4 / 16));
  for (let i = 0, j = 0; i < d.length; i += 64, j++) lum[j] = (0.299 * d[i] + 0.587 * d[i + 1] + 0.114 * d[i + 2]) / 255;
  const sorted = Float32Array.from(lum).sort();
  const mid = sorted[sorted.length >> 1], top = sorted[Math.floor(sorted.length * 0.995)];
  if (mid > 0.5 || top >= target || top - mid < 1e-3) return img;
  const gain = Math.min((target - mid) / (top - mid), 8);
  const m = mid * 255;
  for (let i = 0; i < d.length; i += 4) {
    d[i] = m + (d[i] - m) * gain;
    d[i + 1] = m + (d[i + 1] - m) * gain;
    d[i + 2] = m + (d[i + 2] - m) * gain;
  }
  return img;
}

// ---------------------------------------------------------------- colour

export function colorize(inten, W, H, { palette, background, base, glow = 1, lineWidth = 1 }) {
  const table = lut(palette);
  let lightBg;
  if (base) {
    let s = 0, c = 0;
    for (let i = 0; i < base.data.length; i += 4 * 97) { s += base.data[i] + base.data[i + 1] + base.data[i + 2]; c++; }
    lightBg = s / c / 3 > 128;
  } else lightBg = hexToRgb(background).reduce((a, b) => a + b) > 3 * 160;

  let halo = null;
  if (glow > 0) {
    const s = Math.max(1, lineWidth);
    const a = blur(inten, W, H, 2 * s), b = blur(inten, W, H, 7 * s);
    halo = new Float32Array(inten.length);
    const k = lightBg ? 1.6 : 2.2;
    for (let i = 0; i < halo.length; i++) halo[i] = Math.min(1, (0.6 * a[i] + 0.5 * b[i]) * k) * glow;
  }
  const bg = hexToRgb(background);
  const out = new ImageData(W, H);
  const o = out.data;
  const baseData = base ? base.data : null;
  const lift = base && !lightBg;
  for (let i = 0, j = 0; i < inten.length; i++, j += 4) {
    const core = inten[i];
    let alpha = core > 0 ? Math.min(1, core * 1.5 + 0.35) : 0;
    let level = core;
    if (halo) {
      alpha = Math.max(alpha, halo[i] * 0.85);
      level = Math.max(level, halo[i] * 0.6);
    }
    if (lift && core > 0) {
      // over a lit street map, even a street run once must outshine the map
      level = 0.3 + 0.7 * level;
      alpha = Math.max(alpha, 0.8);
    }
    const li = Math.min(255, (level * 255) | 0) * 3;
    const br = baseData ? baseData[j] : bg[0], bgc = baseData ? baseData[j + 1] : bg[1], bb = baseData ? baseData[j + 2] : bg[2];
    let r = table[li], g = table[li + 1], b = table[li + 2];
    if (!lightBg) { r = Math.max(r, br); g = Math.max(g, bgc); b = Math.max(b, bb); }
    o[j] = br + (r - br) * alpha;
    o[j + 1] = bgc + (g - bgc) * alpha;
    o[j + 2] = bb + (b - bb) * alpha;
    o[j + 3] = 255;
  }
  return out;
}

// ---------------------------------------------------------------- poster

function spacedText(ctx, text, cx, y, tracking) {
  const widths = [...text].map((ch) => ctx.measureText(ch).width);
  const total = widths.reduce((a, b) => a + b, 0) + tracking * (widths.length - 1);
  let x = cx - total / 2;
  [...text].forEach((ch, i) => { ctx.fillText(ch, x, y); x += widths[i] + tracking; });
  return total;
}

const FONT = '"Helvetica Neue", Helvetica, Arial, system-ui, sans-serif';

/**
 * Render a full poster into a canvas.
 * opts: {acts, bbox (already fitted to the map box aspect), size, dpi, landscape,
 *        palette, title, subtitle, map: {template, attribution, brightness} | null}
 */
export async function renderPoster(opts, progress = () => {}) {
  const [PW, PH] = paperPixels(opts.size, opts.dpi, opts.landscape);
  const lay = posterLayout([PW, PH], { caption: !!(opts.title || opts.subtitle) });
  const [ml, mt, mr, mb] = lay.map;
  const W = mr - ml, H = mb - mt;
  const bbox = opts.bbox.fitAspect(W / H);
  const pal = PALETTES[opts.palette];
  let background = pal.light ? "#f5f1e8" : "#050508";
  const lineWidth = Math.max(1, Math.min(W, H) / 1600) * (opts.lineScale || 1);
  const warnings = [];

  let base = null;
  if (opts.map) {
    progress("Downloading street map", 0);
    try {
      base = await fetchBasemap(bbox, W, H, opts.map.template, (f) => progress("Downloading street map", f));
      reveal(base);
      if (opts.map.brightness !== 1) {
        const d = base.data, k = opts.map.brightness;
        for (let i = 0; i < d.length; i += 4) { d[i] *= k; d[i + 1] *= k; d[i + 2] *= k; }
      }
    } catch (err) {
      const why = err.name === "SecurityError" ? "that map service doesn't allow its images to be saved" : err.message;
      warnings.push(`Street map left out: ${why}.`);
      base = null;
    }
  }

  progress("Drawing runs", 0);
  const acc = await accumulate(opts.acts, bbox, W, H, lineWidth, (f) => progress("Drawing runs", f));
  progress("Adding glow", 0);
  await tick();
  const inten = toneMap(acc);
  const img = colorize(inten, W, H, { palette: opts.palette, background, base, glow: opts.glow ?? 1, lineWidth });

  if (base) {
    // frame the map in its own background colour
    const d = base.data, vals = [[], [], []];
    for (let i = 0; i < d.length; i += 4 * 211) for (let c = 0; c < 3; c++) vals[c].push(d[i + c]);
    const med = vals.map((v) => v.sort((a, b) => a - b)[v.length >> 1]);
    background = "#" + med.map((v) => Math.round(v).toString(16).padStart(2, "0")).join("");
  }

  progress("Laying out poster", 0);
  await tick();
  const canvas = document.createElement("canvas");
  canvas.width = PW; canvas.height = PH;
  const ctx = canvas.getContext("2d");
  if (!ctx) throw new Error("this poster is too large for your browser; choose a lower resolution");
  ctx.fillStyle = background;
  ctx.fillRect(0, 0, PW, PH);
  ctx.putImageData(img, ml, mt);

  const short = Math.min(PW, PH);
  const bgRgb = hexToRgb(background);
  const light = bgRgb.reduce((a, b) => a + b) > 3 * 160;
  const fg = light ? [30, 30, 34] : [236, 232, 225];
  const muted = fg.map((c, i) => Math.round(c * 0.62 + bgRgb[i] * 0.38));
  ctx.textBaseline = "top";
  let y = lay.captionTop + (PH - lay.captionTop) * 0.2;
  if (opts.title) {
    let size = Math.round(short * 0.055);
    const title = opts.title.toUpperCase();
    ctx.font = `700 ${size}px ${FONT}`;
    const measure = () => [...title].reduce((w, ch) => w + ctx.measureText(ch).width, 0) + size * 0.35 * (title.length - 1);
    while (size > 10 && measure() > PW * 0.86) { size = Math.round(size * 0.92); ctx.font = `700 ${size}px ${FONT}`; }
    ctx.fillStyle = `rgb(${fg})`;
    spacedText(ctx, title, PW / 2, y, size * 0.35);
    y += size * 1.45;
  }
  if (opts.subtitle) {
    const size = Math.round(short * 0.018);
    ctx.font = `400 ${size}px ${FONT}`;
    ctx.fillStyle = `rgb(${muted})`;
    spacedText(ctx, opts.subtitle.toUpperCase(), PW / 2, y, size * 0.3);
  }
  if (base && opts.map.attribution) {
    const size = Math.max(9, Math.round(short * 0.008));
    ctx.font = `400 ${size}px ${FONT}`;
    ctx.fillStyle = `rgb(${muted})`;
    ctx.textAlign = "right";
    ctx.fillText(opts.map.attribution, PW - short * 0.02, PH - size * 2.2);
    ctx.textAlign = "left";
  }
  return { canvas, warnings };
}
