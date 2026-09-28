// Reading activity files in the browser: GPX, TCX and FIT (optionally
// gzipped), inside folders, .zip archives and zips within zips, plus the
// activities.csv index from a Strava export. Nothing is uploaded anywhere.

import { gunzipSync, inflateSync } from "../vendor/fflate.js";
import { distance, segmentLength } from "./geo.js";

const ACTIVITY_RE = /\.(gpx|tcx|fit)(\.gz)?$/i;

export function isActivityName(path) {
  const base = path.split("/").pop();
  return ACTIVITY_RE.test(base) && !base.startsWith("._") && !path.includes("__MACOSX");
}

const baseName = (path) => path.split("/").pop();
const stripGz = (name) => name.replace(/\.gz$/i, "");

// ---------------------------------------------------------------- sports

const STRAVA_CODES = { 1: "ride", 4: "hike", 9: "run", 10: "walk", 16: "swim" };
const FIT_SPORTS = { 0: null, 1: "run", 2: "ride", 5: "swim", 11: "walk", 17: "hike" };
const VAGUE = new Set(["generic", "all", "other", "workout", "training", "unknown", "activity", "sport"]);

export function normalizeSport(value) {
  if (value == null) return null;
  let v = String(value).trim().toLowerCase();
  if (!v) return null;
  if (STRAVA_CODES[v]) return STRAVA_CODES[v];
  v = v.replace(/_/g, " ");
  if (VAGUE.has(v)) return null;
  if (v.includes("run") || v === "jog") return "run";
  if (v.includes("ride") || v.includes("cycl") || v.includes("bik")) return "ride";
  if (v.includes("walk")) return "walk";
  if (v.includes("hik")) return "hike";
  if (v.includes("swim")) return "swim";
  return v;
}

// ---------------------------------------------------------------- zip

/**
 * List the files in a zip without loading it all into memory: only the
 * central directory is read, and each entry is inflated when asked for.
 */
export async function listZip(blob) {
  const u32 = (dv, o) => dv.getUint32(o, true);
  const u16 = (dv, o) => dv.getUint16(o, true);
  const u64 = (dv, o) => Number(dv.getBigUint64(o, true));
  const readAt = async (start, end) => new Uint8Array(await blob.slice(start, end).arrayBuffer());

  const tailLen = Math.min(blob.size, 65557);
  const tail = await readAt(blob.size - tailLen, blob.size);
  const tdv = new DataView(tail.buffer);
  let eocd = -1;
  for (let i = tail.length - 22; i >= 0; i--) {
    if (u32(tdv, i) === 0x06054b50) { eocd = i; break; }
  }
  if (eocd < 0) throw new Error("not a zip file");
  let count = u16(tdv, eocd + 10);
  let cdSize = u32(tdv, eocd + 12);
  let cdOffset = u32(tdv, eocd + 16);
  if (cdOffset === 0xffffffff || count === 0xffff) {
    // Zip64: the real numbers live in a separate record.
    const loc = eocd - 20;
    if (loc < 0 || u32(tdv, loc) !== 0x07064b50) throw new Error("unsupported zip");
    const z64 = u64(tdv, loc + 8);
    const rec = new DataView((await readAt(z64, z64 + 56)).buffer);
    count = u64(rec, 32);
    cdSize = u64(rec, 40);
    cdOffset = u64(rec, 48);
  }

  const cd = await readAt(cdOffset, cdOffset + cdSize);
  const dv = new DataView(cd.buffer);
  const dec = new TextDecoder();
  const entries = [];
  let p = 0;
  for (let n = 0; n < count && p + 46 <= cd.length; n++) {
    if (u32(dv, p) !== 0x02014b50) break;
    const method = u16(dv, p + 10);
    let compSize = u32(dv, p + 20);
    let size = u32(dv, p + 24);
    const nameLen = u16(dv, p + 28), extraLen = u16(dv, p + 30), commentLen = u16(dv, p + 32);
    let offset = u32(dv, p + 42);
    const name = dec.decode(cd.subarray(p + 46, p + 46 + nameLen));
    // Zip64 extra field carries the sizes/offset that overflowed 32 bits.
    let e = p + 46 + nameLen;
    const eEnd = e + extraLen;
    while (e + 4 <= eEnd) {
      const id = u16(dv, e), len = u16(dv, e + 2);
      if (id === 0x0001) {
        let q = e + 4;
        if (size === 0xffffffff) { size = u64(dv, q); q += 8; }
        if (compSize === 0xffffffff) { compSize = u64(dv, q); q += 8; }
        if (offset === 0xffffffff) { offset = u64(dv, q); }
      }
      e += 4 + len;
    }
    p = eEnd + commentLen;
    if (name.endsWith("/")) continue;
    entries.push({
      name,
      size,
      async read() {
        const head = new DataView(await blob.slice(offset, offset + 30).arrayBuffer());
        const start = offset + 30 + head.getUint16(26, true) + head.getUint16(28, true);
        const data = await readAt(start, start + compSize);
        if (method === 0) return data;
        if (method === 8) return inflateSync(data, { out: new Uint8Array(size) });
        throw new Error(`unsupported zip compression (method ${method})`);
      },
    });
  }
  return entries;
}

// ---------------------------------------------------------------- collecting

/**
 * Turn picked/dropped files into activity sources.
 * `files` is a list of {file: File, path: string}.
 * Returns {sources: [{name, label, read}], stravaIndex: Map, skipped: [..]}
 */
export async function collectSources(files, onProgress = () => {}) {
  const sources = [];
  const stravaIndex = new Map();
  const problems = [];

  const addCsv = (text) => readStravaCsv(text, stravaIndex);

  const walkZip = async (blob, label, depth) => {
    let entries;
    try {
      entries = await listZip(blob);
    } catch (err) {
      problems.push(`${label}: ${err.message}`);
      return;
    }
    for (const entry of entries) {
      const path = `${label}!${entry.name}`;
      if (entry.name.includes("__MACOSX")) continue;
      if (isActivityName(entry.name)) {
        sources.push({ name: baseName(entry.name), label: path, read: () => entry.read() });
      } else if (baseName(entry.name) === "activities.csv") {
        addCsv(new TextDecoder().decode(await entry.read()));
      } else if (/\.zip$/i.test(entry.name) && depth < 2) {
        onProgress(`Opening ${baseName(entry.name)} …`);
        const inner = await entry.read();
        await walkZip(new Blob([inner]), path, depth + 1);
      }
    }
  };

  for (const { file, path } of files) {
    const name = file.name;
    if (/\.zip$/i.test(name)) {
      onProgress(`Opening ${name} …`);
      await walkZip(file, path || name, 0);
    } else if (isActivityName(path || name)) {
      sources.push({ name, label: path || name, read: async () => new Uint8Array(await file.arrayBuffer()) });
    } else if (name === "activities.csv") {
      addCsv(await file.text());
    }
  }
  return { sources, stravaIndex, problems };
}

// ---------------------------------------------------------------- Strava csv

function parseCsv(text) {
  const rows = [];
  let row = [], field = "", quoted = false;
  for (let i = 0; i < text.length; i++) {
    const c = text[i];
    if (quoted) {
      if (c === '"') {
        if (text[i + 1] === '"') { field += '"'; i++; } else quoted = false;
      } else field += c;
    } else if (c === '"') quoted = true;
    else if (c === ",") { row.push(field); field = ""; }
    else if (c === "\n" || c === "\r") {
      if (c === "\r" && text[i + 1] === "\n") i++;
      row.push(field); rows.push(row); row = []; field = "";
    } else field += c;
  }
  if (field || row.length) { row.push(field); rows.push(row); }
  return rows;
}

const MONTHS = { jan: 0, feb: 1, mar: 2, apr: 3, may: 4, jun: 5, jul: 6, aug: 7, sep: 8, oct: 9, nov: 10, dec: 11 };

function parseStravaDate(s) {
  if (!s) return null;
  let m = s.match(/^(\w{3})\w* (\d{1,2}), (\d{4}),? (\d{1,2}):(\d{2}):(\d{2})\s*(AM|PM)?/i);
  if (m) {
    let h = +m[4] % 12;
    if ((m[7] || "").toUpperCase() === "PM") h += 12;
    if (!m[7]) h = +m[4];
    return new Date(Date.UTC(+m[3], MONTHS[m[1].toLowerCase()], +m[2], h, +m[5], +m[6]));
  }
  m = s.match(/^(\d{1,2}) (\w{3})\w* (\d{4}),? (\d{1,2}):(\d{2}):(\d{2})/);
  if (m) return new Date(Date.UTC(+m[3], MONTHS[m[2].toLowerCase()], +m[1], +m[4], +m[5], +m[6]));
  const d = new Date(s);
  return isNaN(d) ? null : d;
}

function readStravaCsv(text, index) {
  const rows = parseCsv(text.replace(/^﻿/, ""));
  if (!rows.length) return;
  const head = rows[0];
  const col = (n) => head.indexOf(n);
  const [iFile, iType, iName, iDate] = ["Filename", "Activity Type", "Activity Name", "Activity Date"].map(col);
  if (iFile < 0) return;
  for (const r of rows.slice(1)) {
    const file = (r[iFile] || "").trim();
    if (!file) continue;
    index.set(stripGz(baseName(file)), {
      sport: normalizeSport(r[iType]),
      name: (r[iName] || "").trim() || null,
      start: parseStravaDate(r[iDate]),
    });
  }
}

// ---------------------------------------------------------------- parsing

export function parseActivity(name, bytes, label = name, stravaIndex = null) {
  if (bytes[0] === 0x1f && bytes[1] === 0x8b) bytes = gunzipSync(bytes);
  const kind = stripGz(name).split(".").pop().toLowerCase();
  let act;
  if (kind === "gpx") act = parseGPX(bytes);
  else if (kind === "tcx") act = parseTCX(bytes);
  else if (kind === "fit") act = parseFIT(bytes);
  else throw new Error(`unsupported file type: ${name}`);
  act.label = label;
  act.name = act.name || stripGz(name);
  const meta = stravaIndex && stravaIndex.get(stripGz(name));
  if (meta) {
    act.sport = meta.sport || act.sport;
    act.name = meta.name || act.name;
    act.start = act.start || meta.start;
  }
  return act;
}

function xmlDoc(bytes) {
  let text = new TextDecoder().decode(bytes).replace(/^[﻿\s]+/, "");
  const doc = new DOMParser().parseFromString(text, "application/xml");
  if (doc.getElementsByTagName("parsererror").length) throw new Error("invalid XML");
  return doc;
}

const byTag = (el, tag) => el.getElementsByTagNameNS("*", tag);
const childText = (el, tag) => {
  for (const c of el.children) if (c.localName === tag) return c.textContent.trim();
  return null;
};

function parseTime(s) {
  if (!s) return null;
  const d = new Date(s);
  return isNaN(d) ? null : d;
}

export function parseGPX(bytes) {
  const doc = xmlDoc(bytes);
  const segments = [];
  let name = null, sport = null, start = null;
  for (const trk of [...byTag(doc, "trk"), ...byTag(doc, "rte")]) {
    name = name || childText(trk, "name");
    sport = sport || normalizeSport(childText(trk, "type"));
  }
  const groups = [...byTag(doc, "trkseg"), ...byTag(doc, "rte")];
  for (const g of groups) {
    const pts = [];
    for (const p of g.children) {
      if (p.localName !== "trkpt" && p.localName !== "rtept") continue;
      const lat = parseFloat(p.getAttribute("lat")), lon = parseFloat(p.getAttribute("lon"));
      if (!isFinite(lat) || !isFinite(lon)) continue;
      pts.push(lat, lon);
      if (!start) start = parseTime(childText(p, "time"));
    }
    if (pts.length >= 4) segments.push(Float64Array.from(pts));
  }
  if (!start) {
    const t = byTag(doc, "time")[0];
    start = t ? parseTime(t.textContent) : null;
  }
  return { segments, name, sport, start };
}

export function parseTCX(bytes) {
  const doc = xmlDoc(bytes);
  const segments = [];
  const actEl = byTag(doc, "Activity")[0];
  const sport = actEl ? normalizeSport(actEl.getAttribute("Sport")) : null;
  const idEl = byTag(doc, "Id")[0];
  let start = idEl ? parseTime(idEl.textContent) : null;
  for (const track of byTag(doc, "Track")) {
    const pts = [];
    for (const tp of track.children) {
      if (tp.localName !== "Trackpoint") continue;
      const la = byTag(tp, "LatitudeDegrees")[0], lo = byTag(tp, "LongitudeDegrees")[0];
      if (!start) start = parseTime(childText(tp, "Time"));
      if (!la || !lo) continue;
      pts.push(parseFloat(la.textContent), parseFloat(lo.textContent));
    }
    if (pts.length >= 4) segments.push(Float64Array.from(pts));
  }
  return { segments, name: null, sport, start };
}

const SEMI = 180 / 2 ** 31;
const FIT_EPOCH_MS = 631065600000;

/**
 * A small FIT decoder: reads GPS records, the sport and the start time.
 * Handles chained files, compressed-timestamp headers, big-endian messages
 * and developer fields; a truncated file keeps the points read so far.
 */
export function parseFIT(bytes) {
  const dv = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
  const pts = [];
  let sport = null, start = null, created = null;
  let pos = 0;
  try {
    while (pos + 12 <= bytes.length) {
      const headerSize = bytes[pos];
      const dataSize = dv.getUint32(pos + 4, true);
      const magic = String.fromCharCode(...bytes.subarray(pos + 8, pos + 12));
      if (magic !== ".FIT") {
        if (pos === 0) throw new Error("not a FIT file");
        break;
      }
      let p = pos + headerSize;
      const end = Math.min(p + dataSize, bytes.length);
      const defs = new Array(16);
      while (p < end) {
        const h = bytes[p++];
        let local, isDef = false, dev = false;
        if (h & 0x80) local = (h >> 5) & 3;
        else { local = h & 0x0f; isDef = !!(h & 0x40); dev = !!(h & 0x20); }
        if (isDef) {
          const little = bytes[p + 1] === 0;
          const global = dv.getUint16(p + 2, little);
          const n = bytes[p + 4];
          p += 5;
          const fields = [];
          let size = 0;
          for (let i = 0; i < n; i++) {
            fields.push({ num: bytes[p], size: bytes[p + 1], offset: size });
            size += bytes[p + 1];
            p += 3;
          }
          if (dev) {
            const nd = bytes[p++];
            for (let i = 0; i < nd; i++) { size += bytes[p + 1]; p += 3; }
          }
          defs[local] = { global, little, fields, size };
          continue;
        }
        const def = defs[local];
        if (!def) throw new Error("FIT data before its definition");
        if (p + def.size > bytes.length) throw new RangeError("truncated");
        const f = (num, bytesWanted) => {
          const fd = def.fields.find((x) => x.num === num && x.size === bytesWanted);
          return fd ? p + fd.offset : -1;
        };
        if (def.global === 20) { // record
          const iLat = f(0, 4), iLon = f(1, 4);
          if (iLat >= 0 && iLon >= 0) {
            const lat = dv.getInt32(iLat, def.little), lon = dv.getInt32(iLon, def.little);
            if (lat !== 0x7fffffff && lon !== 0x7fffffff) pts.push(lat * SEMI, lon * SEMI);
          }
          const iTs = f(253, 4);
          if (!start && iTs >= 0) start = new Date(FIT_EPOCH_MS + dv.getUint32(iTs, def.little) * 1000);
        } else if ((def.global === 18 || def.global === 12) && !sport) { // session, sport
          const i = f(def.global === 18 ? 5 : 0, 1);
          if (i >= 0 && bytes[i] !== 0xff) sport = FIT_SPORTS[bytes[i]] !== undefined ? FIT_SPORTS[bytes[i]] : "other";
        } else if (def.global === 0 && !created) { // file_id
          const i = f(4, 4);
          if (i >= 0) created = new Date(FIT_EPOCH_MS + dv.getUint32(i, def.little) * 1000);
        }
        p += def.size;
      }
      pos = pos + headerSize + dataSize + 2; // skip CRC; another FIT file may follow
    }
  } catch (err) {
    if (!pts.length) throw err instanceof RangeError ? new Error("invalid or truncated FIT file") : err;
  }
  return { segments: pts.length >= 4 ? [Float64Array.from(pts)] : [], name: null, sport, start: start || created };
}

// ---------------------------------------------------------------- cleaning

/**
 * Drop bad points, split where the GPS jumps more than maxGap metres, and
 * optionally hide the first/last trim metres (privacy around home).
 */
export function cleanActivity(act, { maxGap = 250, trim = 0 } = {}) {
  let segs = [];
  for (const seg of act.segments) {
    let cur = [];
    const flush = () => { if (cur.length >= 4) segs.push(Float64Array.from(cur)); cur = []; };
    for (let i = 0; i < seg.length; i += 2) {
      const lat = seg[i], lon = seg[i + 1];
      if (!isFinite(lat) || !isFinite(lon) || Math.abs(lat) > 90 || Math.abs(lon) > 180 || (lat === 0 && lon === 0)) continue;
      const n = cur.length;
      if (n && maxGap > 0 && distance(cur[n - 2], cur[n - 1], lat, lon) > maxGap) flush();
      cur.push(lat, lon);
    }
    flush();
  }
  if (trim > 0 && segs.length) {
    segs = trimStart(segs, trim);
    segs = trimStart(segs.reverse().map(reverseSeg), trim).reverse().map(reverseSeg);
  }
  const out = { ...act, segments: segs };
  out.distance = segs.reduce((d, s) => d + segmentLength(s), 0);
  out.center = medianPoint(segs);
  return out;
}

function reverseSeg(seg) {
  const r = new Float64Array(seg.length);
  for (let i = 0; i < seg.length; i += 2) {
    r[seg.length - 2 - i] = seg[i];
    r[seg.length - 1 - i] = seg[i + 1];
  }
  return r;
}

function trimStart(segs, metres) {
  const out = [];
  let remaining = metres;
  for (const seg of segs) {
    if (remaining <= 0) { out.push(seg); continue; }
    let i = 2;
    for (; i < seg.length; i += 2) {
      remaining -= distance(seg[i - 2], seg[i - 1], seg[i], seg[i + 1]);
      if (remaining <= 0) break;
    }
    if (remaining <= 0 && seg.length - i >= 4) out.push(seg.slice(i));
  }
  return out;
}

function medianPoint(segs) {
  const lats = [], lons = [];
  for (const s of segs) {
    const step = Math.max(2, Math.floor(s.length / 200) * 2);
    for (let i = 0; i < s.length; i += step) { lats.push(s[i]); lons.push(s[i + 1]); }
  }
  if (!lats.length) return [0, 0];
  const med = (a) => a.sort((x, y) => x - y)[a.length >> 1];
  return [med(lats), med(lons)];
}
