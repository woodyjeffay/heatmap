# run-heatmap

A heatmap of every run you've done. Point it at your exported GPS files and it
draws every route onto one map. Streets you've run once show as a faint trace,
and the ones you run every week glow brightest. It can also lay the map out as
a print-ready poster.

<p align="center">
  <img src="docs/poster-fire.png" width="32%" alt="Fire palette poster">
  <img src="docs/poster-ice.png" width="32%" alt="Ice palette poster">
  <img src="docs/poster-ink.png" width="32%" alt="Ink palette poster on cream paper">
</p>

<sub>These previews use made-up runs from `examples/make_demo_data.py`.</sub>

## Install

```sh
pip install .            # Python 3.9+; installs numpy, Pillow and fitdecode
```

## Quick start

```sh
# Try it with fake data first
python examples/make_demo_data.py demo-runs
run-heatmap demo-runs -o heatmap.png

# Your own runs
run-heatmap ~/Downloads/strava_export -o heatmap.png --html heatmap.html

# A 300 dpi A2 poster
run-heatmap ~/Downloads/strava_export -o poster.png --poster a2 --title "London"
```

`--html` writes a single-page interactive map. You can zoom from the whole
world down to one street. Overlapping routes add their light together, so
the most-run streets glow white. Open it in any browser.

### The map behind the interactive page

The page draws your routes over a dark street map from CARTO, which needs no
key. If the map area shows a message instead of streets (for example "API key
required"), pick another map with `--html-map`:

| `--html-map` | Key? |
|---|---|
| `carto-dark` *(default)* | No key. |
| `stadia-dark` | No key when the page is opened from `http://localhost` (see below); a free key from [stadiamaps.com](https://stadiamaps.com) otherwise. |
| `maptiler-dark` | Always needs a free key from [maptiler.com](https://www.maptiler.com/cloud/). |
| `none` | No background map: just your glowing routes on black. |
| a URL template | Any tile server, e.g. `https://tiles.example.com/{z}/{x}/{y}.png?token={key}`. |

Put the key on the command line with `--map-key`, or set it once in your
shell:

```sh
run-heatmap runs/ -o heatmap.png --html heatmap.html --html-map maptiler-dark --map-key YOUR_KEY

export RUN_HEATMAP_MAP_KEY=YOUR_KEY   # add to ~/.zshrc to keep it
```

The key is written into the HTML file, so don't share that file publicly.
Some tile services also refuse pages opened straight from disk (`file://`).
Serving the folder locally fixes that:

```sh
python -m http.server 8000     # then open http://localhost:8000/heatmap.html
```

## Getting your GPS files

run-heatmap reads **GPX**, **TCX** and **FIT** files, including gzipped
ones (`.gpx.gz`, `.fit.gz`, …). Pass files or folders; folders are searched
recursively.

| Service | How to export |
|---|---|
| **Strava** | Settings → My Account → *Download or Delete Your Account* → *Request your archive*. Unzip it and pass the whole folder. Its `activities.csv` is read automatically, which tells run-heatmap which files are runs. |
| **Garmin Connect** | Account settings → *Data Management* → *Export Your Data*. The FIT files are inside `DI_CONNECT/DI-Connect-Uploaded-Files` (unzip the inner zips). |
| **Apple Health** | Health app → profile picture → *Export All Health Data*. Unzip and pass `apple_health_export/workout-routes`. |
| **Others** (Coros, Polar, Suunto, Runkeeper, Nike Run Club via third-party tools, …) | Anything that gives you GPX, TCX or FIT works. |

By default only runs are drawn (`--sport run`). Files that don't record a
sport are kept. Use `--sport all` to include everything, `--sport ride` for
cycling, or `--strict-sport` to drop files with no sport recorded.

## Options that matter most

| Option | What it does |
|---|---|
| `--region auto` | *(default)* Frames the city where most of your activities are. A single holiday run abroad won't shrink your home streets to a dot. |
| `--region all` | Fits every activity, anywhere in the world. |
| `--region S,W,N,E` | An exact bounding box in degrees, e.g. `51.45,-0.2,51.55,0.0`. |
| `--center LAT,LON --radius KM` | A square map centred on a point. |
| `--poster SIZE` | Page layout with margins, title and a stats line. Sizes: `a4`–`a0`, `letter`, `tabloid`, `12x18`, `18x24`, `24x36`, `square`, or a custom size like `50x70cm` or `16x20in`. Add `--landscape`, `--full-bleed` and `--dpi`. |
| `--palette` | `fire` (default), `ice`, `neon`, `strava`, `mono`, or `ink` / `risograph` for light paper. |
| `--scale` | How run counts become brightness: `log` (default, balanced), `linear` (only the busiest routes stand out), `equalize` (uses the whole colour ramp). |
| `--glow` | Halo strength. `0` gives crisp lines; `1.5` gives more neon. |
| `--line-width` | Line width in pixels. By default it scales with the image size. |
| `--basemap` | Draws streets underneath the PNG: `carto-dark`, `carto-light`, `osm`, `stadia-dark`, `maptiler-dark` (use `--map-key`), or any `{z}/{x}/{y}` tile URL. Tiles are cached in `~/.cache/run-heatmap` and an attribution line is added. Off by default, because the glowing routes usually outline the city on their own. |
| `--trim-ends M` | **Privacy.** Hides the first and last M metres of every activity, so your front door isn't marked on a poster you share. |
| `--since / --until` | Only activities between two dates (`YYYY-MM-DD`), e.g. one poster per year. |
| `--title / --subtitle` | Poster text. The subtitle defaults to e.g. `421 RUNS · 3,310 KM · 2019 – 2022`; pass `--subtitle ""` to hide it. |

Run `run-heatmap --help` for everything else.

## Printing tips

- 300 dpi is standard for prints you'll look at up close. At A2 that's
  about 5000 × 7000 px and takes around 15 seconds to render.
- Dark posters look best on matte paper. For home printers, `ink` on
  a cream background uses much less ink.
- If lines look too thin on a big print, raise `--line-width` (e.g. `4`–`6`
  at A1).

## How it works

1. **Parse**: GPX, TCX and FIT files are read into lists of lat/lon
   segments. Where GPS points jump more than `--max-gap` metres apart (signal
   loss, or pausing and moving), the line is split so no straight jumps
   cross the map.
2. **Project**: points are projected with Web Mercator, the projection web
   maps use, so the result lines up with any tile basemap.
3. **Accumulate**: each activity is drawn once onto its own mask and added
   to a counter image. Each pixel ends up counting how many runs passed
   through it. Running the same loop three times in one run counts once.
4. **Tone map**: counts become intensities on a log scale, clipped at the
   99.5th percentile, so routes you ran once stay visible next to routes you
   ran hundreds of times.
5. **Glow & colour**: two blurred copies of the lines form a halo. The
   intensity picks a colour from the palette, and the result is blended onto
   the background (or basemap).

## Development

```sh
pip install -e '.[dev]'
pytest
```
