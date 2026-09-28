// Page wiring: load files, filter, draw the map, make posters.

import { BBox, bboxOfActivities, densestCluster } from "./geo.js";
import { cleanActivity, collectSources, parseActivity } from "./parse.js";
import { PALETTES, TILE_PROVIDERS, paperPixels, posterLayout, renderPoster, tileUrl } from "./heat.js";
import { RunMap } from "./map.js";

const $ = (id) => document.getElementById(id);
const store = {
  get(k, d) { try { return localStorage.getItem("rh:" + k) ?? d; } catch { return d; } },
  set(k, v) { try { localStorage.setItem("rh:" + k, v); } catch { /* private mode etc. */ } },
};
const tick = () => new Promise((r) => setTimeout(r, 0));

const runMap = new RunMap($("map"));
let raw = [];      // every parsed activity
let shown = [];    // after filters and cleaning
let busy = false;

// ---------------------------------------------------------------- controls setup

for (const [id, p] of Object.entries(TILE_PROVIDERS)) $("tiles").add(new Option(p.label, id));
for (const [id, p] of Object.entries(PALETTES)) $("palette").add(new Option(p.label, id));
$("tiles").value = store.get("tiles", "carto-dark");
$("map-key").value = store.get("key", "");
$("line-color").value = store.get("color", "#ff6a1f");
$("palette").value = store.get("palette", "fire");
applyTiles();

// ---------------------------------------------------------------- loading

async function filesFromDrop(dt) {
  const out = [];
  const entries = [...dt.items].map((i) => i.webkitGetAsEntry && i.webkitGetAsEntry()).filter(Boolean);
  const walk = async (entry, path) => {
    if (entry.isFile) {
      const file = await new Promise((res, rej) => entry.file(res, rej));
      out.push({ file, path: path + entry.name });
    } else if (entry.isDirectory) {
      const reader = entry.createReader();
      let batch;
      do {
        batch = await new Promise((res, rej) => reader.readEntries(res, rej));
        for (const e of batch) await walk(e, path + entry.name + "/");
      } while (batch.length);
    }
  };
  if (entries.length) for (const e of entries) await walk(e, "");
  else for (const file of dt.files) out.push({ file, path: file.name });
  return out;
}

function setStatus(text, frac = null, id = "") {
  $(id + "status").hidden = false;
  $(id + "status-text").textContent = text;
  $(id + "bar").style.width = frac == null ? "100%" : `${Math.round(frac * 100)}%`;
  $(id + "bar").classList.toggle("indeterminate", frac == null);
}

async function load(files) {
  if (busy) return;
  busy = true;
  try {
    setStatus("Looking through your files …");
    const { sources, stravaIndex, problems } = await collectSources(files, (t) => setStatus(t));
    if (!sources.length) {
      setStatus("No GPX, TCX or FIT files found there. Try the folder or .zip from your export.", 0);
      return;
    }
    const added = [];
    let failed = 0, noGps = 0;
    for (let i = 0; i < sources.length; i++) {
      const src = sources[i];
      try {
        const act = parseActivity(src.name, await src.read(), src.label, stravaIndex);
        if (act.segments.length) added.push(act); else noGps++;
      } catch (err) {
        failed++;
        console.warn(`Skipped ${src.label}: ${err.message}`);
      }
      if (i % 20 === 0) { setStatus(`Reading ${i + 1} of ${sources.length} files …`, i / sources.length); await tick(); }
    }
    raw = raw.concat(added);
    const parts = [`Loaded ${added.length.toLocaleString()} activities`];
    if (noGps) parts.push(`${noGps} without GPS (treadmill, indoor)`);
    if (failed + problems.length) parts.push(`${failed + problems.length} unreadable (details in the browser console)`);
    problems.forEach((p) => console.warn(p));
    setStatus(parts.join(" · ") + ".", 1);
    for (const id of ["filter-card", "map-card", "poster-card"]) $(id).hidden = false;
    $("empty").hidden = true;
    refresh(true);
  } catch (err) {
    console.error(err);
    setStatus(`Something went wrong: ${err.message}`, 0);
  } finally {
    busy = false;
  }
}

$("pick-folder").addEventListener("change", (e) => {
  load([...e.target.files].map((f) => ({ file: f, path: f.webkitRelativePath || f.name })));
  e.target.value = "";
});
$("pick-files").addEventListener("change", (e) => {
  load([...e.target.files].map((f) => ({ file: f, path: f.name })));
  e.target.value = "";
});

let dragDepth = 0;
window.addEventListener("dragenter", (e) => { e.preventDefault(); dragDepth++; document.body.classList.add("dragging"); });
window.addEventListener("dragleave", () => { if (--dragDepth <= 0) { dragDepth = 0; document.body.classList.remove("dragging"); } });
window.addEventListener("dragover", (e) => e.preventDefault());
window.addEventListener("drop", async (e) => {
  e.preventDefault();
  dragDepth = 0;
  document.body.classList.remove("dragging");
  load(await filesFromDrop(e.dataTransfer));
});

// ---------------------------------------------------------------- filters

function refresh(fitView = false) {
  const sport = $("sport").value;
  const trim = +$("trim").value;
  const since = $("since").value ? new Date($("since").value + "T00:00:00") : null;
  const until = $("until").value ? new Date($("until").value + "T23:59:59") : null;
  let unknown = 0;
  shown = [];
  for (const a of raw) {
    if (sport !== "all") {
      if (!a.sport) unknown++;
      else if (sport === "walk" ? !["walk", "hike"].includes(a.sport) : a.sport !== sport) continue;
    }
    if (a.start && ((since && a.start < since) || (until && a.start > until))) continue;
    const c = cleanActivity(a, { trim });
    if (c.segments.length) shown.push(c);
  }
  $("unknown-note").hidden = !unknown || sport === "all";
  $("unknown-note").textContent = `${unknown} file${unknown === 1 ? "" : "s"} didn't say what sport ${unknown === 1 ? "it was" : "they were"}, so ${unknown === 1 ? "it's" : "they're"} included.`;
  $("stats").textContent = statsLine(shown, sport) || "Nothing matches these filters.";
  $("subtitle").placeholder = statsLine(shown, sport).toUpperCase();
  runMap.setActivities(shown);
  if (fitView) zoomHome();
  updateFrame();
}

function statsLine(acts, sport) {
  if (!acts.length) return "";
  const noun = { run: "runs", ride: "rides", walk: "walks & hikes" }[sport] || "activities";
  const km = acts.reduce((s, a) => s + a.distance, 0) / 1000;
  const years = acts.filter((a) => a.start).map((a) => a.start.getFullYear()).sort();
  const parts = [`${acts.length.toLocaleString()} ${noun}`, `${Math.round(km).toLocaleString()} km`];
  if (years.length) parts.push(years[0] === years[years.length - 1] ? `${years[0]}` : `${years[0]} – ${years[years.length - 1]}`);
  return parts.join("  ·  ");
}

function zoomHome() {
  if (!shown.length) return;
  runMap.fit(bboxOfActivities(densestCluster(shown)).atLeastKm(1).pad(0.04));
}

for (const id of ["sport", "trim", "since", "until"]) $(id).addEventListener("change", () => refresh(id === "sport"));
$("zoom-home").addEventListener("click", zoomHome);
$("zoom-all").addEventListener("click", () => shown.length && runMap.fit(bboxOfActivities(shown).atLeastKm(1).pad(0.04)));

// ---------------------------------------------------------------- map style

function applyTiles() {
  const provider = $("tiles").value;
  const p = TILE_PROVIDERS[provider];
  $("key-row").hidden = !(p.needsKey || p.optionalKey);
  const err = runMap.setTiles(provider, $("map-key").value.trim());
  const note = $("tiles-note");
  note.hidden = !err && !p.optionalKey;
  note.textContent = err
    ? `${err}: get a free one at maptiler.com, or pick another map.`
    : "Works without a key while testing on localhost; on a public website add a free key from stadiamaps.com.";
  store.set("tiles", provider);
  store.set("key", $("map-key").value.trim());
}
$("tiles").addEventListener("change", applyTiles);
$("map-key").addEventListener("change", applyTiles);
$("opacity").addEventListener("input", (e) => runMap.setStyle({ opacity: +e.target.value }));
$("line-color").addEventListener("change", (e) => { runMap.setStyle({ color: e.target.value }); store.set("color", e.target.value); });
runMap.setStyle({ color: $("line-color").value });

// ---------------------------------------------------------------- poster

function posterAspect() {
  const [W, H] = paperPixels($("paper").value, 50, $("orientation").value === "landscape");
  const caption = !!($("title").value.trim() || subtitleText());
  const [l, t, r, b] = posterLayout([W, H], { caption }).map;
  return (r - l) / (b - t);
}

function subtitleText() {
  const v = $("subtitle").value.trim();
  return v || $("subtitle").placeholder;
}

function updateFrame() {
  runMap.showFrame(shown.length ? runMap.posterBBox(posterAspect()) : null);
}
runMap.map.on("moveend zoomend resize", updateFrame);
for (const id of ["paper", "orientation", "title", "subtitle"]) $(id).addEventListener("input", updateFrame);
$("palette").addEventListener("change", (e) => store.set("palette", e.target.value));

$("make-poster").addEventListener("click", async () => {
  if (busy || !shown.length) return;
  busy = true;
  $("make-poster").disabled = true;
  $("poster-result").hidden = true;
  try {
    let map = null;
    if ($("poster-map").checked) {
      const pal = PALETTES[$("palette").value];
      let provider = $("tiles").value;
      if (provider === "none") provider = "carto-dark";
      // light paper palettes look right on a light street map
      if (pal.light && provider === "carto-dark") provider = "carto-light";
      try {
        map = { template: tileUrl(provider, $("map-key").value.trim()), attribution: TILE_PROVIDERS[provider].attribution, brightness: +$("map-brightness").value };
      } catch (err) {
        map = null;
      }
    }
    const t0 = performance.now();
    const { canvas, warnings } = await renderPoster({
      acts: shown,
      bbox: runMap.posterBBox(posterAspect()),
      size: $("paper").value,
      dpi: +$("dpi").value,
      landscape: $("orientation").value === "landscape",
      palette: $("palette").value,
      title: $("title").value.trim(),
      subtitle: subtitleText(),
      map,
    }, (label, frac) => setStatus(`${label} …`, frac, "poster-"));

    setStatus("Saving image …", null, "poster-");
    const blob = await new Promise((res, rej) => canvas.toBlob((b) => (b ? res(b) : rej(new Error("the browser couldn't save an image this large; choose a lower quality"))), "image/png"));
    const url = URL.createObjectURL(blob);
    const old = $("poster-download").href;
    if (old && old.startsWith("blob:")) URL.revokeObjectURL(old);
    $("poster-img").src = url;
    $("poster-download").href = url;
    const slug = ($("title").value.trim() || "run-heatmap").toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "");
    $("poster-download").download = `${slug || "run-heatmap"}-${$("paper").value}-poster.png`;
    $("poster-note").textContent = [
      `${canvas.width.toLocaleString()} × ${canvas.height.toLocaleString()} px, ${(blob.size / 1e6).toFixed(1)} MB, made in ${((performance.now() - t0) / 1000).toFixed(1)} s.`,
      ...warnings,
    ].join(" ");
    $("poster-result").hidden = false;
    $("poster-status").hidden = true;
  } catch (err) {
    console.error(err);
    const msg = /tainted|insecure|SecurityError/i.test(err.message + err.name)
      ? "this street map doesn't allow its images to be saved; untick the street map or pick another one"
      : err.message;
    setStatus(`Couldn't make the poster: ${msg}.`, 0, "poster-");
  } finally {
    busy = false;
    $("make-poster").disabled = false;
  }
});
